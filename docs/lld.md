# Low-level design

All APIs are JSON over HTTP. Errors always have the shape `{"detail": "<plain sentence>"}`
(FastAPI validation errors: `{"detail": [{"msg": ..., "loc": ...}]}`).
Interactive docs: http://localhost:8000/docs (backend) and http://localhost:8100/docs (runner).

## 1. Backend API (backend/app.py, port 8000; the web page reaches it through nginx at /api)

Every response carries `X-Process-Time-ms` (server time).
Admin endpoints need the header `X-Admin-Token: <ADMIN_TOKEN from .env>`.

| Method and path | Purpose | Auth |
|---|---|---|
| `POST /api/v1/predict` | Estimate both players' ratings from one game | none |
| `GET /api/v1/examples` | A few validated games to try | none |
| `GET /healthz` | Health and the model version being served | none |
| `GET /api/v1/model` | Registry versions and aliases | none |
| `POST /api/v1/model/rollback` | Swap `@champion` and `@previous` | admin |
| `GET /api/v1/pipeline/status` | Run in progress and last run (proxied to runner) | none |
| `GET /api/v1/pipeline/runs?limit=20` | Run history (proxied) | none |
| `GET /api/v1/pipeline/runs/{id}/log` | Log of one run (proxied) | none |
| `POST /api/v1/pipeline/run` | Start a run (proxied) | admin |
| `GET /api/v1/alerts` | Last 50 alerts received | none |
| `POST /api/v1/alerts` | Alertmanager webhook | Docker network only |
| `GET /metrics` | Prometheus metrics | none |

### POST /api/v1/predict

Request (exactly one of the two fields):

| Field | Type | Rules |
|---|---|---|
| `pgn` | string | One game with `[%clk]` comments, at most 100,000 characters |
| `url` | string | Lichess game link (`https://lichess.org/<8 or 12 chars>[/white or /black]`) or bare game ID, at most 200 characters |

Optional header `X-Request-Source: smoke` keeps the request out of live statistics (used by the smoke test).

Response 200:

```json
{
  "game_id": "bcRJNu4b",
  "time_control": "180+0",
  "model_version": "3",
  "served_by": "backend-container-hostname",
  "analysis_ms": 1311,
  "players": [
    {
      "color": "white",
      "name": "player name from the PGN",
      "actual": 1592,
      "low": 1300, "mid": 1593, "high": 1881,
      "reasons": [{"feature": "best_move_rate", "effect": 164.2, "value": 0.548}],
      "features": {"acpl": 22.929, "acpl_endgame": null, "...": "all 17 inputs"}
    },
    {"color": "black", "...": "same fields"}
  ]
}
```

`actual` is the rating in the PGN header, or `null`. `effect` is the feature's contribution to `mid`
in rating points (positive raises the estimate). `null` feature values mean "not reached" (for example no endgame).

| Status | When |
|---|---|
| 422 | Both or neither field; not a readable game; not standard; not blitz; under 20 plies; missing clocks; odd starting clocks; malformed link |
| 404 | Lichess has no game with that ID |
| 502 | Lichess or the model server returned an error |
| 503 | All Stockfish engines busy for 30 s; model server not reachable (for example while loading a new version) |
| 504 | Lichess timed out (after 3 attempts) |

### GET /api/v1/examples?limit=4

Response: `[{"white", "black", "white_elo", "black_elo", "time_control", "pgn"}]`, up to 10 games from
`data/valid/games.pgn`; `[]` before the first pipeline run.

### GET /healthz

Response: `{"status": "ok" | "degraded", "model_version": "3" | null, "served_by": "...",
"components": {"frontend": true, "mlflow": true, "pipeline": true, "model": true}}`.
`model_version` comes from a live call to the model server; `components` from the last 15-second probe.

### GET /api/v1/model

Response: `{"name": "chess-rating", "served_version": "3", "aliases": {"champion": "3", "previous": "2",
"challenger": "3"}, "versions": [{"version", "aliases", "test_mae", "promotion"}]}`.
502 if MLflow cannot be reached.

### POST /api/v1/model/rollback

Response: `{"champion": "2", "previous": "3"}`. 401 without a valid token; 409 if there is no `@previous`.

### POST /api/v1/pipeline/run

Request: `{"month": null | "next" | "YYYY-MM"}` (null keeps the month in params.yaml).
Response 202: the run record (see runner). 409 if a run is in progress; 401 without token.

### POST /api/v1/alerts (Alertmanager webhook, version 4 payload)

Request: `{"alerts": [{"status": "firing" | "resolved", "labels": {"alertname", "severity"},
"annotations": {"summary"}, "startsAt"}]}`. Response `{"ok": true}`. A firing `FeatureDrift` or
`LiveErrorHigh` starts a run on the next month when `AUTO_RETRAIN=true` and the last automatic run was
more than 24 h ago. `GET /api/v1/alerts` returns `[{"name", "status", "summary",
"received", "action"}]`, newest first.

## 2. Pipeline runner API (src/runner.py, port 8100, internal)

| Method and path | Response |
|---|---|
| `GET /healthz` | `{"status": "ok", "running": false}` |
| `GET /status` | `{"running": <run or null>, "last": <run or null>, "stages": [six names]}` |
| `GET /runs?limit=20` | List of runs, newest first |
| `GET /runs/{id}/log?tail=400` | `{"id", "lines": [...]}`; 422 for a malformed ID, 404 if unknown |
| `POST /run` | Body `{"month": null or "next" or "YYYY-MM", "trigger": "manual" or "alert"}`; 202 with the run; 409 if busy |
| `GET /metrics` | Prometheus metrics |

Run record (one JSON line per run in `logs/runs.jsonl`):

```json
{"id": "20261026-155804", "trigger": "manual", "month": "2026-07", "status": "success",
 "started": "...", "finished": "...", "seconds": 332.8, "returncode": 0,
 "stages": {"ingest": {"status": "done", "seconds": 0.3}, "train": {"status": "skipped", "seconds": 0}},
 "throughput": {"ingest": 74137.1, "validate": 16.5, "featurize": 0.94},
 "games": {"read": 360, "kept": 236, "valid": 235, "featurized": 235},
 "test_mae": 212.5, "promotion": {"...": "contents of results/promotion.json"}}
```

The log of each run is `logs/runs/<id>.log`. `throughput` is games per second of each data stage.

## 3. Model server (mlflow models serve, port 5001, internal)

`POST /invocations` with `{"dataframe_split": {"columns": [17 feature names], "data": [[...], ...]}}`
(missing values as `null`). Response: `{"predictions": [{"low": 1300, "mid": 1593, "high": 1881,
"reasons": "[[\"acpl\", -35.0], ...]", "model_version": "3"}]}`. `GET /ping` for readiness.

## 4. Model inputs (in this order, all float)

| # | Feature | Meaning |
|---|---|---|
| 1 | `acpl` | Average centipawn loss per move (losses capped at 1000; mate = 1000) |
| 2-4 | `acpl_opening`, `acpl_middlegame`, `acpl_endgame` | Same, by phase (opening = first 20 plies; endgame = both sides with non-pawn material of 13 or less) |
| 5-7 | `blunder_rate`, `mistake_rate`, `inaccuracy_rate` | Share of moves losing over 300, 100-300, 50-100 centipawns |
| 8 | `best_move_rate` | Share of moves equal to Stockfish's first choice |
| 9 | `time_used_frac` | Time spent / time available (base + increment x moves) |
| 10 | `mean_move_time` | Seconds per move (first move counts as the clock reading difference) |
| 11 | `move_time_cv` | Standard deviation / mean of move times |
| 12 | `low_clock_moves` | Moves made with under 10 s left |
| 13 | `lost_on_time` | 1 if this player lost on time |
| 14 | `color` | 1 white, 0 black |
| 15 | `plies` | Game length in half-moves |
| 16 | `result` | 1 win, 0.5 draw, 0 loss for this player |
| 17 | `eco_group` | Opening family A-E as 0-4, -1 unknown |

## 5. Pipeline stages and files

| Stage | Command | Inputs | Outputs |
|---|---|---|---|
| ingest | `python src/ingest.py` | Lichess month (URL in params) | `data/raw/games.pgn`, `results/ingest.json` |
| validate | `python src/validate.py` | raw games | `data/valid/games.pgn`, `results/validation.json` |
| featurize | `python src/featurize.py` | valid games | `data/features/features.parquet` (2 rows per game), `results/featurize.json` |
| train | `mlflow run . -e train --env-manager local` | features | `models/candidate`, `models/boosters`, `results/train.json`; registry version with `@challenger` |
| evaluate | `python src/evaluate.py` | features, candidate | `results/metrics.json`, `results/plots/*.csv` and PNGs; MLflow test metrics and tags |
| promote | `python src/promote.py` | metrics, registry | `results/promotion.json`; alias changes |

## 6. Prometheus metrics (names as exposed)

| Component | Metric (type) | Labels |
|---|---|---|
| backend | `chess_http_requests_total` (counter) | endpoint, method, status |
| backend | `chess_http_request_seconds` (histogram) | endpoint |
| backend | `chess_analysis_seconds` (histogram), `chess_model_call_seconds` (summary) | |
| backend | `chess_predictions_total` (counter) | source |
| backend | `chess_rejected_games_total` (counter) | reason |
| backend | `chess_feature_psi` (gauge) | feature |
| backend | `chess_live_mae`, `chess_live_coverage`, `chess_model_test_mae` (gauges) | |
| backend | `chess_model_info` (info) | version, run_id, test_mae |
| backend | `chess_component_up` (gauge) | component = model, frontend, mlflow, pipeline |
| runner | `chess_pipeline_runs_total` (counter) | status, trigger |
| runner | `chess_pipeline_running` (gauge) | |
| runner | `chess_pipeline_stage_duration_seconds`, `chess_pipeline_throughput` (gauges) | stage |
| mlflow | `mlflow_http_request_total`, `mlflow_http_request_duration_seconds` | method, status |
| node-exporter | `node_cpu_seconds_total`, `node_memory_*`, `node_filesystem_*` | |
