# Test report

Results of the tests in [test_plan.md](test_plan.md). Every `FILL IN` below must be replaced with a
value from my own run on real Lichess data, on my machine, with Docker.

Machine: FILL IN (model, CPUs, memory given to Docker). Date: FILL IN. Data month: FILL IN.

## 1. Unit and API tests

`docker compose exec backend python -m pytest -q --junitxml=reports/tests.xml`

| Tests | Passed | Failed | Skipped |
|---|---|---|---|
| FILL IN (expected 69) | FILL IN | FILL IN | FILL IN |

## 2. Static checks

| Check | Result |
|---|---|
| `flake8 src backend tests scripts` | FILL IN (expected 0 errors) |
| `docker compose config --quiet` | FILL IN |
| Prometheus targets page (http://localhost:9090/targets) | FILL IN (expected 7 jobs up) |

## 3. End-to-end checks

| ID | Check | Result |
|---|---|---|
| E1 | All nine services running after `docker compose up -d --build` | FILL IN |
| E2 | First pipeline run from the console | FILL IN (games read / kept / valid, total time) |
| E3 | Model reload and smoke test | FILL IN (text of `smoke_test` in reports/promotion.json) |
| E4 | Web page: example game, Lichess link, pasted PGN, bad inputs | FILL IN |
| E5 | Drift loop (`scripts/traffic.py --drift`) | FILL IN |
| E6 | Forced alerts (7 rules) | FILL IN (which fired) |
| E7 | Broken model rolled back | FILL IN |
| E8 | Load test | table below |
| E9 | Fresh clone + `dvc pull` + `dvc repro -f evaluate` gives the same metrics | FILL IN |
| E10 | A failed run leaves the workspace usable | FILL IN |

### Pipeline speed

| Stage | Workers | Throughput (games per second) |
|---|---|---|
| ingest | 1 | FILL IN |
| validate (Ray tasks) | FILL IN | FILL IN |
| featurize (Ray actors + Stockfish) | FILL IN | FILL IN |

### Load test (`scripts/load_test.py`)

| Clients | Requests | Throughput | p50 | p95 | Status codes |
|---|---|---|---|---|---|
| 1 | FILL IN | FILL IN | FILL IN | FILL IN | FILL IN |
| 4 | FILL IN | FILL IN | FILL IN | FILL IN | FILL IN |
| 16 | FILL IN | FILL IN | FILL IN | FILL IN | FILL IN |

## 4. Model results

| Metric | Value | Source |
|---|---|---|
| Test MAE | FILL IN | reports/metrics.json |
| Baseline MAE (constant median) | FILL IN | reports/metrics.json |
| Baseline MAE (line on ACPL) | FILL IN | reports/metrics.json |
| Coverage of the range | FILL IN | reports/metrics.json |
| Champion MAE on the next month | FILL IN | next month's reports/promotion.json |

## 5. Acceptance criteria

| ID | Criterion | Met? |
|---|---|---|
| AC1 | MAE beats both baselines on the next-month test set | FILL IN |
| AC2 | Coverage between 75% and 85% | FILL IN |
| AC3 | p95 under 5 s for an 80-ply game | FILL IN |
| AC4 | All unit tests pass, flake8 clean | FILL IN |
| AC5 | Readable 4xx for invalid input, no 500s | FILL IN |
| AC6 | Stack starts with one `docker compose up` | FILL IN |
| AC7 | Fresh clone reproduces the metrics | FILL IN |
| AC8 | Every component up in Prometheus; each alert fires in a forced test | FILL IN |
| AC9 | Broken model rolled back automatically | FILL IN |

## 6. Known limitations

- A promotion restarts the single model server, so predictions fail for a few seconds while the new
  version loads, and for longer if the new version is broken (until the smoke test times out and rolls back).
- The drift measure is noisy on small samples, so it is only reported once 100 players are in the window.

## Appendix: checks done during development (not results)

Before any real data was used, the code was checked in the AI assistant's sandbox (2 CPUs, no Docker,
no access to Lichess) with computer-generated games. These checks show that the machinery works; the
numbers from them say nothing about the model and are not reported here.

- 69 of 69 tests passed; flake8 reported 0 errors; `promtool`, `amtool` and `docker compose config` accepted the configs.
- A full pipeline run, promotion, model reload and smoke test worked; a drift alert started a retraining
  run that was promoted; a deliberately broken model was rolled back automatically; a fresh clone
  reproduced identical features and metrics.
- Bugs found and fixed that way include: a missing feature value (NaN) caused HTTP 500; a failed run left
  `params.yaml` on the bad month; the failed-run alert could not see the first failure.
