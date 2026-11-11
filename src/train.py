"""Stage 4 - train: fit the three quantile models, package them, register a new model version.

DVC runs this through `mlflow run . -e train` (see MLproject), so the MLflow run records the
entry point, the Git commit and the parameters. The new version gets the alias @challenger.
Output: models/candidate (local copy, versioned by DVC) and results/train.json
"""

import hashlib
import json
import shutil
import subprocess
from importlib.metadata import version as pkg_version

import lightgbm as lgb
import mlflow
import pandas as pd
from mlflow.models import infer_signature

from chessfeat.drift import make_reference
from chessfeat.features import FEATURES
from chessfeat.model import QUANTILES, RatingRangeModel, assign_split
from common import ROOT, get_logger, load_params, read_json, write_json

log = get_logger("train")


def file_md5(path):
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def fit(name, alpha, train, val, cfg):
    """One LightGBM model: L1 (median) for mid, quantile loss for low and high."""
    if name == "mid":
        objective = {"objective": "regression_l1", "metric": "l1"}
    else:
        objective = {"objective": "quantile", "alpha": alpha, "metric": "quantile"}
    params = {**objective, "learning_rate": cfg["learning_rate"], "num_leaves": cfg["num_leaves"],
              "min_data_in_leaf": cfg["min_child_samples"], "seed": cfg["seed"],
              "deterministic": True, "force_row_wise": True, "verbosity": -1}
    return lgb.train(params, lgb.Dataset(train[FEATURES], train["rating"]),
                     num_boost_round=cfg["n_estimators"],
                     valid_sets=[lgb.Dataset(val[FEATURES], val["rating"])],
                     callbacks=[lgb.early_stopping(cfg["early_stopping_rounds"], verbose=False)])


def main():
    params = load_params()
    cfg, name = params["train"], params["model_name"]
    features_path = ROOT / "data" / "features" / "features.parquet"
    df = pd.read_parquet(features_path).dropna(subset=["rating"])
    df["split"] = assign_split(df["player"], cfg["seed"])
    train, val = df[df["split"] == "train"], df[df["split"] == "val"]
    log.info("training on %d rows, validating on %d", len(train), len(val))

    boosters_dir = ROOT / "models" / "boosters"
    boosters_dir.mkdir(parents=True, exist_ok=True)
    mlflow.set_experiment("chess-rating")     # ignored when `mlflow run` already started a run
    with mlflow.start_run() as run:
        featurize = read_json("results/featurize.json")
        mlflow.log_params({
            "month": params["month"], "games_per_band": params["ingest"]["games_per_band"],
            "stockfish_depth": params["featurize"]["depth"], "stockfish": featurize["stockfish"],
            "git_commit": git_commit(), "features_md5": file_md5(features_path),
            "train_rows": len(train), "val_rows": len(val), **{f"lgbm_{k}": v for k, v in cfg.items()},
        })
        artifacts = {}
        for model_name, alpha in QUANTILES.items():
            booster = fit(model_name, alpha, train, val, cfg)
            path = boosters_dir / f"{model_name}.txt"
            booster.save_model(str(path))
            artifacts[model_name] = str(path)
            mlflow.log_metric(f"best_iteration_{model_name}", booster.best_iteration)
            if model_name == "mid":
                val_mae = float((booster.predict(val[FEATURES]) - val["rating"]).abs().mean())
                mlflow.log_metric("val_mae", val_mae)

        reference_path = boosters_dir / "drift_reference.json"
        reference_path.write_text(json.dumps(make_reference(train)))
        mlflow.log_artifact(str(reference_path))

        example = train[FEATURES].head(5).astype("float64")
        out_example = pd.DataFrame({"low": [1400], "mid": [1500], "high": [1600],
                                    "reasons": ['[["acpl", -35.0]]'], "model_version": ["1"]})
        package = dict(
            python_model=RatingRangeModel(), artifacts=artifacts,
            code_paths=[str(ROOT / "src" / "chessfeat")],
            signature=infer_signature(example, out_example), input_example=example,
            pip_requirements=[f"{p}=={pkg_version(p)}" for p in ("lightgbm", "pandas", "numpy", "chess", "mlflow")],
        )
        shutil.rmtree(ROOT / "models" / "candidate", ignore_errors=True)
        mlflow.pyfunc.save_model(path=str(ROOT / "models" / "candidate"), **package)
        info = mlflow.pyfunc.log_model(name="model", registered_model_name=name, **package)
        version = info.registered_model_version
        mlflow.MlflowClient().set_registered_model_alias(name, "challenger", version)

    write_json("results/train.json", {"run_id": run.info.run_id, "version": version, "val_mae": round(val_mae, 2)})
    log.info("registered %s version %s as @challenger (val MAE %.1f)", name, version, val_mae)


if __name__ == "__main__":
    main()
