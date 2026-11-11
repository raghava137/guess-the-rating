"""Stage 5 - evaluate: score the candidate model on the held-out test players.

Metrics go to results/metrics.json (DVC metrics), CSVs to results/plots/ (DVC plots), and
the same metrics plus PNG charts to the MLflow run that trained the model. The test MAE is
also stored as a tag on the registered version so the backend can compare live error to it.
"""

import json

import matplotlib
import mlflow
import mlflow.pyfunc
import numpy as np
import pandas as pd

from chessfeat.features import FEATURES
from chessfeat.model import assign_split
from chessfeat.rules import band_names, band_of
from common import ROOT, get_logger, load_params, read_json, write_json

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (backend must be chosen before pyplot is imported)

log = get_logger("evaluate")
PLOTS = ROOT / "results" / "plots"


def score(actual, low, mid, high):
    """The metrics used everywhere: MAE, RMSE, share within 200 points, range coverage and width."""
    actual, low, mid, high = (np.asarray(a, dtype=float) for a in (actual, low, mid, high))
    error = mid - actual
    return {
        "mae": float(np.mean(np.abs(error))),
        "rmse": float(np.sqrt(np.mean(error ** 2))),
        "within_200": float(np.mean(np.abs(error) <= 200)),
        "coverage": float(np.mean((low <= actual) & (actual <= high))),
        "mean_width": float(np.mean(high - low)),
    }


def baselines(train, test):
    """Two simple baselines the model has to beat: a constant and a straight line on ACPL."""
    constant = np.full(len(test), train["rating"].median())
    has_acpl = train["acpl"].notna()
    slope, intercept = np.polyfit(train.loc[has_acpl, "acpl"], train.loc[has_acpl, "rating"], 1)
    line = np.where(test["acpl"].notna(), intercept + slope * test["acpl"].fillna(0), constant)
    return {
        "baseline_median_mae": float(np.mean(np.abs(constant - test["rating"]))),
        "baseline_acpl_line_mae": float(np.mean(np.abs(line - test["rating"]))),
    }


def save_charts(test, band_mae, importance):
    """Three PNGs for the MLflow run: predicted vs actual, error per band, feature importance."""
    paths = []
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.scatter(test["rating"], test["mid"], s=6, alpha=0.4)
    lims = [test["rating"].min(), test["rating"].max()]
    ax.plot(lims, lims, color="black", linewidth=1, label="perfect estimate")
    ax.set(title="Estimated vs actual rating (test players)", xlabel="Actual rating",
           ylabel="Estimated rating (median model)")
    ax.legend()
    paths.append(PLOTS / "pred_vs_actual.png")
    fig.savefig(paths[-1], dpi=120, bbox_inches="tight")

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(band_mae["band"], band_mae["mae"])
    ax.set(title="Mean absolute error by rating band", xlabel="Rating band", ylabel="MAE (rating points)")
    ax.tick_params(axis="x", rotation=45)
    paths.append(PLOTS / "band_mae.png")
    fig.savefig(paths[-1], dpi=120, bbox_inches="tight")

    fig, ax = plt.subplots(figsize=(6, 5))
    ordered = importance.sort_values("gain")
    ax.barh(ordered["feature"], ordered["gain"])
    ax.set(title="Feature importance (median model, total gain)", xlabel="Total gain")
    paths.append(PLOTS / "feature_importance.png")
    fig.savefig(paths[-1], dpi=120, bbox_inches="tight")
    plt.close("all")
    return paths


def main():
    params = load_params()
    df = pd.read_parquet(ROOT / "data" / "features" / "features.parquet").dropna(subset=["rating"])
    df["split"] = assign_split(df["player"], params["train"]["seed"])
    train, test = df[df["split"] == "train"], df[df["split"] == "test"].copy()

    model = mlflow.pyfunc.load_model(str(ROOT / "models" / "candidate"))
    out = model.predict(test[FEATURES].astype("float64"))
    test["low"], test["mid"], test["high"] = out["low"].to_numpy(), out["mid"].to_numpy(), out["high"].to_numpy()

    metrics = score(test["rating"], test["low"], test["mid"], test["high"])
    metrics.update(baselines(train, test))
    metrics["test_rows"] = len(test)

    names = band_names(params["bands"])
    test["band"] = [band_of(r, params["bands"]) for r in test["rating"]]
    test["abs_error"] = (test["mid"] - test["rating"]).abs()
    band_mae = (test.groupby("band")["abs_error"].mean().reindex(names).dropna().round(1)
                .rename("mae").rename_axis("band").reset_index())
    metrics["band_mae"] = dict(zip(band_mae["band"], band_mae["mae"]))

    import lightgbm as lgb
    booster = lgb.Booster(model_file=str(ROOT / "models" / "boosters" / "mid.txt"))
    importance = pd.DataFrame({"feature": FEATURES, "gain": booster.feature_importance("gain")})

    PLOTS.mkdir(parents=True, exist_ok=True)
    test[["rating", "mid"]].rename(columns={"rating": "actual", "mid": "estimated"}).to_csv(
        PLOTS / "predictions.csv", index=False)
    band_mae.to_csv(PLOTS / "band_mae.csv", index=False)
    importance.to_csv(PLOTS / "feature_importance.csv", index=False)
    charts = save_charts(test, band_mae, importance)

    rounded = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in metrics.items()}
    write_json("results/metrics.json", rounded)

    trained = read_json("results/train.json")
    with mlflow.start_run(run_id=trained["run_id"]):
        mlflow.log_metrics({k if k.startswith("test_") else f"test_{k}": v
                            for k, v in metrics.items() if isinstance(v, (int, float))})
        mlflow.log_dict(metrics["band_mae"], "band_mae.json")
        for path in charts:
            mlflow.log_artifact(str(path), "charts")
    client, name, version = mlflow.MlflowClient(), params["model_name"], trained["version"]
    client.set_model_version_tag(name, version, "test_mae", f"{metrics['mae']:.1f}")
    client.set_model_version_tag(name, version, "test_coverage", f"{metrics['coverage']:.3f}")
    log.info("test MAE %.1f, coverage %.2f, baselines %s", metrics["mae"], metrics["coverage"],
             json.dumps({k: round(v, 1) for k, v in metrics.items() if k.startswith("baseline")}))


if __name__ == "__main__":
    main()
