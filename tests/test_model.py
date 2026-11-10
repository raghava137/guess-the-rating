"""Unit tests for the split, drift, promotion gate and the packaged model."""

import json

import lightgbm as lgb
import mlflow.pyfunc
import numpy as np
import pandas as pd
import pytest

from chessfeat.drift import DRIFT_FEATURES, make_reference, psi
from chessfeat.features import FEATURES
from chessfeat.model import QUANTILES, RatingRangeModel, assign_split
from promote import decide


def test_split_is_deterministic_grouped_and_about_70_15_15():
    players = [f"player{i}" for i in range(3000)]
    first = assign_split(players, seed=42)
    assert first == assign_split(players, seed=42)
    # the same player always lands in the same split, even when they appear many times
    assert assign_split(["alice"] * 5, seed=42) == [assign_split(["alice"], 42)[0]] * 5
    shares = {name: first.count(name) / len(first) for name in ("train", "val", "test")}
    assert shares["train"] == pytest.approx(0.70, abs=0.03)
    assert shares["test"] == pytest.approx(0.15, abs=0.03)


def test_psi_is_small_for_same_data_and_large_after_a_shift():
    rng = np.random.default_rng(0)
    train = pd.DataFrame({f: rng.normal(50, 10, 5000) for f in DRIFT_FEATURES})
    reference = make_reference(train)
    assert psi(reference["acpl"], list(rng.normal(50, 10, 500))) < 0.05
    assert psi(reference["acpl"], list(rng.normal(65, 10, 500))) > 0.2
    assert np.isnan(psi(reference["acpl"], [float("nan")]))


GATE = {"margin": 5, "coverage_min": 0.75, "coverage_max": 0.85}


@pytest.mark.parametrize("challenger, champion_mae, expected", [
    ({"mae": 200, "coverage": 0.80, "baseline_median_mae": 300}, None, True),     # first model
    ({"mae": 200, "coverage": 0.60, "baseline_median_mae": 300}, None, False),    # dishonest range
    ({"mae": 310, "coverage": 0.80, "baseline_median_mae": 300}, None, False),    # worse than a constant
    ({"mae": 200, "coverage": 0.80, "baseline_median_mae": 300}, 210, True),      # better by 10 > margin
    ({"mae": 200, "coverage": 0.80, "baseline_median_mae": 300}, 203, False),     # better by 3 < margin
])
def test_promotion_gate(challenger, champion_mae, expected):
    assert decide(challenger, champion_mae, GATE)[0] is expected


def test_packaged_model_returns_ordered_range_reasons_and_version(tmp_path, monkeypatch):
    rng = np.random.default_rng(1)
    X = pd.DataFrame(rng.normal(size=(400, len(FEATURES))), columns=FEATURES)
    y = 1500 - 200 * X["acpl"] + rng.normal(0, 50, 400)
    artifacts = {}
    for name, alpha in QUANTILES.items():
        objective = {"objective": "regression_l1"} if name == "mid" else {"objective": "quantile", "alpha": alpha}
        booster = lgb.train({**objective, "verbosity": -1, "num_leaves": 7}, lgb.Dataset(X, y), 30)
        artifacts[name] = str(tmp_path / f"{name}.txt")
        booster.save_model(artifacts[name])
    mlflow.pyfunc.save_model(path=str(tmp_path / "model"), python_model=RatingRangeModel(), artifacts=artifacts)

    monkeypatch.setenv("MODEL_VERSION", "7")
    out = mlflow.pyfunc.load_model(str(tmp_path / "model")).predict(X.head(20))
    assert (out["low"] <= out["mid"]).all() and (out["mid"] <= out["high"]).all()
    assert set(out["model_version"]) == {"7"}
    reasons = json.loads(out["reasons"][0])
    assert len(reasons) == 3 and reasons[0][0] in FEATURES
