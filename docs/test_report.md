# Test report

Results of the tests in [test_plan.md](test_plan.md), from my own runs on real Lichess data, on my machine, with Docker.

Machine: Mac with a 12-core CPU and 18 GB RAM; Docker Desktop given 12 CPUs and 13.9 GiB. Dates: 10, 11 and 18 November 2026.
Data months: 2026-07 (training) and 2026-08 (next-month check).

## 1. Unit and API tests

`docker compose exec backend python -m pytest -q --junitxml=results/tests.xml`

| Tests | Passed | Failed | Skipped |
|---|---|---|---|
| 70 | 70 | 0 | 0 |

The pre-push hook also runs these tests before every push.

## 2. Static checks

| Check | Result |
|---|---|
| `flake8 src backend tests scripts` | 0 errors (pre-push hook passed) |
| `docker compose config --quiet` | Valid: the stack started with it |
| Prometheus targets page (http://localhost:9090/targets) | All 7 jobs up |

## 3. End-to-end checks

| ID | Check | Result |
|---|---|---|
| E1 | All nine services running after `docker compose up -d --build` | Pass: 9 services up (after adding `libgomp1` to the Dockerfile, which LightGBM needs) |
| E2 | First pipeline run from the console | Pass. Trial run (200 games per band): 10 min, model rejected (coverage 0.66). Real run (2,000 per band): 512,504 games read, 20,000 kept, 18,917 valid, 86 min, model promoted |
| E3 | Model reload and smoke test | Pass: "passed: 3 games answered by version 2" |
| E4 | Web page: example game, Lichess link, pasted PGN, bad inputs | Pass. Plain text ("hello") and a non-Lichess link (`https://example.com/x`) both got HTTP 422 with "That does not look like a Lichess game link (e.g. https://lichess.org/bcRJNu4b)."; other invalid inputs also gave readable messages |
| E5 | Drift loop (`scripts/traffic.py --drift`) | Pass. 120 long games (over 100 half-moves) sent, all answered. Max PSI 6.25; FeatureDrift went to pending and fired after 10 minutes ([screenshot](images/feature-drift-firing.png)). `AUTO_RETRAIN` was off, so no retraining was started |
| E6 | Forced alerts (7 rules) | 4 of 7 fired: ComponentDown (no model served before the first promotion, resolved after it), FeatureDrift (E5), PipelineFailed (E10), TargetDown (node-exporter stopped for 2 minutes; fired after its 1-minute wait, [screenshot](images/targetdown-firing.png)). HighErrorRate, SlowPredictions and LiveErrorHigh were not forced: they need sustained 5xx errors, p95 above 15 s, or 30 real ratings with a large error |
| E7 | Broken model rolled back | Pass. Simulated a deployment that never comes up: the promotion margin was set to -10 so version 3 passed the gate, and the model server was stopped. Promote moved @champion to 3, the smoke test failed ("backend still serves version None after 180 s") and @champion went back to 2 by itself (`"rolled_back": true`). The margin was then set back to 5 and promote re-run, which rejected version 3 as before |
| E8 | Load test | table below |
| E9 | Fresh clone + `dvc pull` + `dvc repro -f -s evaluate` gives the same metrics | Pass. Fresh clone inside the pipeline container, data pulled from the DVC remote, evaluate re-run: MAE 277.1, coverage 0.77, baselines 484.8 and 411.0, identical to the committed `results/metrics.json` |
| E10 | A failed run leaves the workspace usable | Pass. A run on month 2030-01 failed at ingest after 2 s (Lichess 404, shown in the console log, [screenshot](images/failed-run-log.png)); `params.yaml` went back to 2026-08 and the next run succeeded in 1 s with every stage skipped. PipelineFailed fired |

### Pipeline speed

| Stage | Workers | Throughput (games per second) |
|---|---|---|
| ingest | 1 | 3,374 games read per second (3 min) |
| validate (Ray tasks) | 8 | 764 (27 s) |
| featurize (Ray actors + Stockfish 15.1, depth 10) | 10 | 3.82 (83 min, 0 errors) |

### Load test (`scripts/load_test.py`)

| Clients | Requests | Throughput | p50 | p95 | Status codes |
|---|---|---|---|---|---|
| 1 | 10 | 0.56 req/s | 1.60 s | 2.90 s | 200 x 10 |
| 4 | 40 | 1.08 req/s | 3.70 s | 5.24 s | 200 x 40 |

The backend has 2 Stockfish engines, so it analyses at most two games at a time; with 4 clients the
rest queue, which is why p95 rises. Normal traffic (100 games) and drift traffic (120 games) also got HTTP 200 every time.

## 4. Model results

| Metric | Value | Source |
|---|---|---|
| Test MAE (v2, July) | 276.6 (RMSE 352.7, within 200 points 45.3%) | results/metrics.json |
| Baseline MAE (constant median) | 483.2 | results/metrics.json |
| Baseline MAE (line on ACPL) | 414.6 | results/metrics.json |
| Coverage of the range | 0.767 (average width 851) | results/metrics.json |
| Champion MAE on the next month (v2 on August players) | 280.4 (August baselines 484.8 and 411.0) | August results/promotion.json |
| New model on August (v3) | MAE 277.1, coverage 0.766; not promoted: "MAE 277.1 not better than champion 280.4 by 5" | August reports |

## 5. Acceptance criteria

| ID | Criterion | Met? |
|---|---|---|
| AC1 | MAE beats both baselines on the next-month test set | Met: 280.4 vs 484.8 and 411.0 |
| AC2 | Coverage between 75% and 85% | Met: 0.767 (July), 0.766 (August) |
| AC3 | p95 under 5 s for an 80-ply game | Met: 2.90 s with one client (real games of mixed length) |
| AC4 | All unit tests pass, flake8 clean | Met: 70/70, 0 flake8 errors |
| AC5 | Readable 4xx for invalid input, no 500s | Met: readable 422 messages for bad inputs (E4), no 500s in 270 requests |
| AC6 | Stack starts with one `docker compose up` | Met |
| AC7 | Fresh clone reproduces the metrics | Met (E9) |
| AC8 | Every component up in Prometheus; each alert fires in a forced test | Partly: all 7 targets up; 4 of 7 alerts fired in forced tests (E6) |
| AC9 | Broken model rolled back automatically | Met (E7) |

## 6. Known limitations

- A promotion restarts the single model server, so predictions fail for a few seconds while the new
  version loads, and for longer if the new version is broken (until the smoke test times out and rolls back).
- The drift measure is noisy on small samples, so it is only reported once 100 players are in the window.

## Appendix: checks done during development (not results)

Before any real data was used, the code was checked in the AI assistant's sandbox (2 CPUs, no Docker,
no access to Lichess) with computer-generated games. These checks show that the machinery works; the
numbers from them say nothing about the model and are not reported here.

- 70 of 70 tests passed; flake8 reported 0 errors; `promtool`, `amtool` and `docker compose config` accepted the configs.
- A full pipeline run, promotion, model reload and smoke test worked; a drift alert started a retraining
  run that was promoted; a deliberately broken model was rolled back automatically; a fresh clone
  reproduced identical features and metrics.
- Bugs found and fixed that way include: a missing feature value (NaN) caused HTTP 500; a failed run left
  `params.yaml` on the bad month; the failed-run alert could not see the first failure.
