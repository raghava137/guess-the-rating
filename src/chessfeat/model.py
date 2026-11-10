"""The packaged model: three LightGBM boosters in one MLflow pyfunc.

low = 10th percentile, mid = median, high = 90th percentile, so low-high is an 80% range.
The output also carries the top three reasons (feature contributions of the median model)
and the registry version, so every answer can be traced to the model that produced it.
"""

import hashlib
import json
import os

import mlflow.pyfunc
import numpy as np
import pandas as pd

from chessfeat.features import FEATURES

QUANTILES = {"low": 0.1, "mid": 0.5, "high": 0.9}


def assign_split(players, seed, train_pct=70, val_pct=15):
    """Deterministic train/val/test split by player, so no player appears in two splits."""
    def bucket(player):
        return int(hashlib.md5(f"{seed}:{player}".encode()).hexdigest(), 16) % 100

    return [("train" if b < train_pct else "val" if b < train_pct + val_pct else "test")
            for b in map(bucket, players)]


class RatingRangeModel(mlflow.pyfunc.PythonModel):
    """Loads the three boosters and returns low / mid / high, reasons and model_version."""

    def load_context(self, context):
        import lightgbm as lgb
        self.boosters = {name: lgb.Booster(model_file=context.artifacts[name]) for name in QUANTILES}
        # Set by serve_champion.py when it starts the server for a registry version.
        self.version = os.environ.get("MODEL_VERSION", "local")

    def predict(self, context, model_input, params=None):
        features = pd.DataFrame(model_input).reindex(columns=FEATURES).astype("float64")
        low, mid, high = (self.boosters[name].predict(features) for name in QUANTILES)
        low, high = np.minimum(low, mid), np.maximum(high, mid)     # quantile models can cross
        contributions = self.boosters["mid"].predict(features, pred_contrib=True)[:, :-1]
        reasons = []
        for row in contributions:
            top = np.argsort(-np.abs(row))[:3]
            reasons.append(json.dumps([[FEATURES[i], round(float(row[i]), 1)] for i in top]))
        return pd.DataFrame({
            "low": np.round(low).astype(int), "mid": np.round(mid).astype(int),
            "high": np.round(high).astype(int), "reasons": reasons, "model_version": self.version,
        })
