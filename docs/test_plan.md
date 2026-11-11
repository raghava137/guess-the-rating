# Test plan

## Scope and levels

| Level | What | How it runs |
|---|---|---|
| Unit | Rules, parsing, features, split, drift, promotion gate, packaged model, ingest sampling, validation merge, runner helpers | `pytest` (tests/), on every push via the pre-push hook |
| API | Backend endpoints with fakes for Stockfish, the model server and the runner; runner endpoints | `pytest` with FastAPI's TestClient |
| Style | PEP 8 with 120-column lines | `flake8 src backend tests scripts` |
| Config | Prometheus config and rules, Alertmanager config, Compose file | `promtool check`, `amtool check-config`, `docker compose config` |
| Integration (end to end) | Whole stack: pipeline run, promotion, model reload, smoke test, predictions, alerts, retraining, rollback | Console and scripts against the running stack (see "End-to-end checks") |
| UI | The three pages on desktop and phone widths, error messages | Manual run-through (or a browser script) |
| Load | Throughput and latency at 1 to 32 concurrent clients | `scripts/load_test.py` |
| Reproducibility | Fresh clone + `dvc pull` + forced `dvc repro` gives the same features and metrics | DVC commands |

Out of scope: browsers other than Chromium-based and Safari; HTTPS; multi-user accounts.

## Unit and API test cases

70 test cases in 36 test functions (parametrised cases counted separately).

| ID | Test (file::function) | Cases | Checks |
|---|---|---|---|
| U1 | test_rules::test_is_blitz | 13 | Lichess blitz rule at its boundaries (179/180, 479/480), increments, bullet, rapid, missing values |
| U2 | test_rules::test_valid_game_passes_and_duplicate_is_rejected | 1 | A good game passes; the same ID twice is a duplicate |
| U3 | test_rules::test_header_rules | 7 | Each header rule rejects for exactly its reason |
| U4 | test_rules::test_move_rules | 3 | Too short, missing clock, berserk clock |
| U5 | test_parse::test_bulk_and_export_give_identical_moves_and_clocks | 1 | A real game from both Lichess sources parses to the same plies |
| U6 | test_parse::test_clocks_are_read_in_seconds | 1 | Clock values, increment, last move |
| U7 | test_parse::test_header_differences_do_not_change_the_rules | 1 | Header differences between sources do not change the verdict |
| U8 | test_parse::test_unreadable_text_is_rejected | 1 | Non-PGN text raises a clear error |
| U9 | test_parse::test_iter_games_streams_several_games | 1 | Streaming several games from one file |
| U10 | test_features::test_no_rating_information_in_model_inputs | 1 | No label leakage into inputs |
| U11 | test_features::test_move_quality_counts_losses_for_the_mover_only | 1 | ACPL, blunder and best-move rates on hand-computed evaluations |
| U12 | test_features::test_time_features_use_increment_and_skip_the_first_move | 1 | Clock features on hand-computed clocks |
| U13 | test_features::test_checkmate_is_a_full_loss_for_the_side_to_move | 1 | Terminal evaluation |
| U14 | test_features::test_small_helpers | 1 | Result and opening-family helpers |
| U15 | test_features::test_analysis_is_deterministic_and_rows_are_complete | 1 | Same evaluations from a warm and a fresh Stockfish; complete rows (needs Stockfish; skipped without it) |
| U16 | test_model::test_split_is_deterministic_grouped_and_about_70_15_15 | 1 | Split by player, repeatable, right shares |
| U17 | test_model::test_psi_is_small_for_same_data_and_large_after_a_shift | 1 | Drift measure behaves |
| U18 | test_model::test_promotion_gate | 5 | First model, dishonest range, worse than constant, better and not better than the champion by the margin |
| U19 | test_model::test_packaged_model_returns_ordered_range_reasons_and_version | 1 | pyfunc output: low <= mid <= high, three reasons, version from the environment |
| U20 | test_pipeline::test_ingest_keeps_blitz_games_up_to_the_band_quota | 1 | Stratified sampling and rejection counts |
| U21 | test_pipeline::test_validation_merge_drops_duplicates_across_chunks | 1 | Duplicates across Ray tasks |
| U22 | test_pipeline::test_next_month_rolls_over_the_year | 1 | Month arithmetic |
| U23 | test_pipeline::test_set_month_edits_only_the_month_line | 1 | params.yaml edit keeps everything else, including comments |
| U24 | test_pipeline::test_dvc_stage_lines_are_recognised | 4 | Stage timing from DVC output, including run-cache hits |
| U25 | test_pipeline::test_failure_counters_start_at_zero | 1 | PipelineFailed alert can see the first failure |
| U26 | test_pipeline::test_runner_refuses_a_second_run_and_bad_ids | 1 | 409 while busy; 422 for bad input; no path traversal |
| A1 | test_api::test_predict_returns_a_range_per_player | 1 | 200, all fields, timing header, null for missing features |
| A2 | test_api::test_predict_needs_exactly_one_input | 2 | 422 for neither or both inputs |
| A3 | test_api::test_bad_games_are_refused_with_a_reason | 3 | 422 with a readable reason |
| A4 | test_api::test_lichess_links_are_parsed_and_fetched | 1 | Link to game ID; bad link is 422 |
| A5 | test_api::test_game_id_forms | 4 | Every link form Lichess uses |
| A6 | test_api::test_model_server_down_gives_503 | 1 | Clear 503 when the model is unavailable |
| A7 | test_api::test_admin_actions_need_the_token | 2 | 401 without or with a wrong token |
| A8 | test_api::test_drift_alert_starts_retraining_once_per_cooldown | 1 | Off, started, then cooldown |
| A9 | test_api::test_model_info_before_the_first_training_run | 1 | "No model yet" is a normal answer, not an error |
| A10 | test_api::test_metrics_are_exposed | 1 | Prometheus metrics present |

## End-to-end checks

| ID | Check | Pass when |
|---|---|---|
| E1 | `docker compose up -d --build` then `docker compose ps` | All nine services running; `/healthz` answers |
| E2 | First pipeline run from the console | All six stages done; run listed with times and speed; promotion decision recorded |
| E3 | Model reload and smoke test | `/healthz` reports the new version within a minute; promotion report says smoke test passed |
| E4 | Predict through the web page (example game, Lichess link, pasted PGN) | Range, reasons and reveal work; bad inputs show a readable message |
| E5 | Drift loop | Drifted traffic (`scripts/traffic.py --drift` or another month's file) raises PSI; FeatureDrift fires; with `AUTO_RETRAIN=true`, a run starts on the next month |
| E6 | Forced alerts | Each of the 7 rules fires in a forced test (stop a container, a failed run, heavy load, drifted traffic) |
| E7 | Broken model rollback | A model that cannot load is promoted by a forced gate; the smoke test fails and the previous champion is restored automatically |
| E8 | Load test | `load_test.py` at 1, 2, 4, 8, 16 clients: no 5xx except "busy" 503 under saturation; record p50/p95 |
| E9 | Reproducibility | Fresh clone, `dvc pull`, `dvc repro -f evaluate`: same features hash and same metrics |
| E10 | Failed run recovery | A run on a month that does not exist fails, params.yaml and outputs are restored, the next run succeeds |

## Acceptance criteria

| ID | Criterion | How it is measured |
|---|---|---|
| AC1 | Model MAE beats both baselines on the next-month test set | `champion_mae` in the next month's promotion.json vs the baselines in that month's metrics.json |
| AC2 | Range coverage between 75% and 85% | `coverage` in metrics.json (also enforced by the promotion gate) |
| AC3 | p95 prediction time under 5 s for an 80-ply game | Load test at low concurrency on the target machine |
| AC4 | All unit tests pass and flake8 reports no errors | pytest and flake8 |
| AC5 | Every invalid input returns a readable 4xx message; no 500 errors | A2-A7, E4, E8 |
| AC6 | The whole stack starts with one `docker compose up` | E1 |
| AC7 | `dvc repro` on a fresh clone reproduces the logged metrics | E9 |
| AC8 | Every component shows as up in Prometheus, and each alert rule fires in a forced test | E6 and the Prometheus targets page |
| AC9 | A deliberately broken model is rolled back automatically by the smoke test | E7 |

## Environment

Tests run in the project's Docker image (Python 3.11, Stockfish from Debian) or a local Python 3.11
environment with `requirements.txt`. The JUnit report for the test report comes from
`python -m pytest --junitxml=results/tests.xml`.
