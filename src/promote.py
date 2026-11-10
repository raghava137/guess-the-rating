"""Stage 6 - promote: decide whether the new model (@challenger) replaces @champion.

Gate, checked on this month's held-out test players (fresh data for both models):
  1. the 80% range is honest: coverage between coverage_min and coverage_max
  2. the model beats the constant baseline
  3. if a champion exists, challenger MAE <= champion MAE - margin
If the gate passes: @previous = old champion, @champion = challenger. The model server follows
@champion by itself, so a smoke test then sends real games through the running backend and
checks that the new version answers correctly. If it does not, the aliases are rolled back.

`python src/promote.py --smoke-only` runs only the smoke test against the current champion.
Output: reports/promotion.json
"""

import argparse
import os
import sys
import time

import mlflow
import mlflow.pyfunc
import numpy as np
import pandas as pd
import requests
from mlflow.exceptions import MlflowException

from chessfeat.features import FEATURES
from chessfeat.model import assign_split
from chessfeat.parse import iter_game_texts
from common import ROOT, get_logger, load_params, read_json, write_json

log = get_logger("promote")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:8000")


def decide(challenger, champion_mae, cfg):
    """Pure gate logic, so it can be unit tested. Returns (promote?, reason)."""
    if not cfg["coverage_min"] <= challenger["coverage"] <= cfg["coverage_max"]:
        return False, f"coverage {challenger['coverage']:.2f} outside [{cfg['coverage_min']}, {cfg['coverage_max']}]"
    if challenger["mae"] >= challenger["baseline_median_mae"]:
        return False, "does not beat the constant baseline"
    if champion_mae is None:
        return True, "first model"
    if challenger["mae"] > champion_mae - cfg["margin"]:
        return False, f"MAE {challenger['mae']:.1f} not better than champion {champion_mae:.1f} by {cfg['margin']}"
    return True, f"MAE {challenger['mae']:.1f} vs champion {champion_mae:.1f}"


def alias_version(client, name, alias):
    try:
        return client.get_model_version_by_alias(name, alias).version
    except MlflowException:
        return None


def set_alias(client, name, alias, version):
    """Point an alias at a version, or remove it when version is None."""
    if version is None:
        try:
            client.delete_registered_model_alias(name, alias)
        except MlflowException:
            pass
    else:
        client.set_registered_model_alias(name, alias, version)


def load_test_split(params):
    df = pd.read_parquet(ROOT / "data" / "features" / "features.parquet").dropna(subset=["rating"])
    df["split"] = assign_split(df["player"], params["train"]["seed"])
    return df[df["split"] == "test"]


def model_mae(uri, test):
    out = mlflow.pyfunc.load_model(uri).predict(test[FEATURES].astype("float64"))
    return float(np.mean(np.abs(out["mid"].to_numpy() - test["rating"].to_numpy())))


def smoke_games(count):
    with open(ROOT / "data" / "valid" / "games.pgn") as handle:
        texts = []
        for _, text in iter_game_texts(handle):
            texts.append(text)
            if len(texts) == count:
                return texts
    return texts


def backend_reachable():
    try:
        return requests.get(f"{BACKEND_URL}/healthz", timeout=3).ok
    except requests.RequestException:
        return False


def smoke_test(expected_version, games, timeout_s):
    """Wait until the backend serves expected_version, then predict a few real games.

    Returns (passed?, detail).
    """
    deadline = time.time() + timeout_s
    served = None
    while time.time() < deadline:
        try:
            served = requests.get(f"{BACKEND_URL}/healthz", timeout=5).json().get("model_version")
        except (requests.RequestException, ValueError):
            served = None
        if served == expected_version:
            break
        time.sleep(5)
    else:
        return False, f"backend still serves version {served} after {timeout_s} s"
    for text in games:
        try:
            response = requests.post(f"{BACKEND_URL}/api/v1/predict", json={"pgn": text},
                                     headers={"X-Request-Source": "smoke"}, timeout=120)
        except requests.RequestException as exc:
            return False, f"request failed: {exc}"
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}: {response.text[:200]}"
        body = response.json()
        if body.get("model_version") != expected_version:
            return False, f"answered by version {body.get('model_version')}"
        for player in body["players"]:
            if not player["low"] <= player["mid"] <= player["high"]:
                return False, f"bad range {player['low']}-{player['high']}"
    return True, f"{len(games)} games answered by version {expected_version}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-only", action="store_true", help="only smoke-test the current champion")
    args = parser.parse_args()
    params = load_params()
    cfg, name = params["promote"], params["model_name"]
    client = mlflow.MlflowClient()

    if args.smoke_only:
        champion = alias_version(client, name, "champion")
        if champion is None:
            sys.exit("no @champion model registered yet")
        passed, detail = smoke_test(champion, smoke_games(cfg["smoke_games"]), cfg["smoke_timeout_s"])
        log.info("smoke test %s: %s", "passed" if passed else "FAILED", detail)
        sys.exit(0 if passed else 1)

    metrics, challenger = read_json("reports/metrics.json"), read_json("reports/train.json")["version"]
    before = {alias: alias_version(client, name, alias) for alias in ("champion", "previous")}
    if challenger == before["champion"]:             # e.g. only evaluate was re-run: nothing to decide
        write_json("reports/promotion.json", {"challenger": challenger, "champion_before": challenger,
                                              "champion_after": challenger, "promoted": False,
                                              "reason": "challenger is already the champion", "smoke_test": "not run"})
        log.info("version %s is already @champion; nothing to do", challenger)
        return
    champion_mae = None
    if before["champion"] is not None:
        champion_mae = model_mae(f"models:/{name}/{before['champion']}", load_test_split(params))
    promote, reason = decide(metrics, champion_mae, cfg)
    report = {"challenger": challenger, "champion_before": before["champion"], "challenger_mae": metrics["mae"],
              "champion_mae": champion_mae, "promoted": promote, "reason": reason, "smoke_test": "not run"}
    log.info("gate: %s (%s)", "PASS" if promote else "FAIL", reason)

    if promote:
        check_backend = backend_reachable()        # checked before the alias moves
        set_alias(client, name, "previous", before["champion"])
        set_alias(client, name, "champion", challenger)
        if not check_backend:
            report["smoke_test"] = "skipped: backend not reachable"
        else:
            passed, detail = smoke_test(challenger, smoke_games(cfg["smoke_games"]), cfg["smoke_timeout_s"])
            report["smoke_test"] = ("passed: " if passed else "failed: ") + detail
            if not passed:
                set_alias(client, name, "champion", before["champion"])
                set_alias(client, name, "previous", before["previous"])
                report["promoted"], report["rolled_back"] = False, True
                log.error("smoke test failed (%s); rolled back to version %s", detail, before["champion"])
    status = "promoted" if report["promoted"] else ("rolled back" if report.get("rolled_back") else "rejected")
    client.set_model_version_tag(name, challenger, "promotion", f"{status}: {reason}")
    report["champion_after"] = alias_version(client, name, "champion")
    write_json("reports/promotion.json", report)
    log.info("champion is now version %s", report["champion_after"])


if __name__ == "__main__":
    main()
