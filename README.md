# Guess the Rating

Paste one Lichess blitz game and the app estimates each player's blitz rating as a range
(for example "1450, most likely 1300 to 1600"), with the three things that moved the estimate most.
Stockfish checks every move, the clock times show how the player used their time, and three
LightGBM models give the low, middle and high estimate.

DA3408 (AI Operations) end-semester project. Everything runs locally with Docker Compose.

| Document | What it covers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Architecture diagram and what each block does |
| [docs/hld.md](docs/hld.md) | High-level design: choices and why |
| [docs/lld.md](docs/lld.md) | Low-level design: every API endpoint with inputs and outputs |
| [docs/test_plan.md](docs/test_plan.md) | Test plan, test cases, acceptance criteria |
| [docs/test_report.md](docs/test_report.md) | Test results |
| [docs/user_manual.md](docs/user_manual.md) | User manual for non-technical users |
| [AI_DISCLOSURE.md](AI_DISCLOSURE.md) | How AI assistance was used |

## Repository layout

```
backend/app.py          FastAPI backend: the only service the web page talks to
frontend/               web page (HTML, CSS, JS) and its nginx config and Dockerfile
src/chessfeat/          shared library: PGN parsing, game rules, Stockfish features, model, drift
src/ingest.py ...       one file per pipeline stage: ingest, validate, featurize, train, evaluate, promote
src/runner.py           pipeline runner service behind the pipeline console
src/serve_champion.py   MLflow model server that follows the @champion alias
tests/                  unit and API tests (pytest)
monitoring/             Prometheus config and alert rules, Alertmanager, Grafana dashboard
scripts/                git pre-push hook (CI), smoke test, traffic and load test
notebooks/eda.ipynb     exploratory data analysis
dvc.yaml, params.yaml   the pipeline DAG and all its settings
MLproject, python_env.yaml   MLflow project (same environment for training everywhere)
Dockerfile, docker-compose.yml   one Python image + nginx frontend + monitoring images
```

## Setup (macOS or Linux)

You need Docker Desktop and Git. In Docker Desktop, Settings > Resources: give Docker at least
12 GB memory and 10 CPUs (featurize runs one Stockfish per CPU).
On a smaller machine (for example a VM with 6 CPUs and 5 GB), lower the parallelism first:
`validate.workers: 4` and `featurize.workers: 5` in `params.yaml`, and `shm_size: 1gb` in `docker-compose.yml`.

1. Create your secrets file. The admin token protects starting runs and rollback; the Fernet key
   encrypts the prediction log.
   ```
   cp .env.example .env
   python3 -c "import secrets; print(secrets.token_urlsafe(24))"
   python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
   ```
   Paste the first value after `ADMIN_TOKEN=` and the second after `FERNET_KEY=` in `.env`.
   `AUTO_RETRAIN=true` lets a drift alert start retraining by itself; leave it `false` to begin with.
   (A Fernet key is just 32 random bytes in URL-safe base64, so no extra package is needed.)

2. Create the folder DVC uses as its remote storage (outside the repo, so data is versioned but not in Git):
   ```
   mkdir -p ~/dvc-store
   ```

3. Put the project under Git and install the pre-push hook. Before every push, the hook runs flake8,
   the tests and `dvc status` inside the project's Docker image (built in step 4), so broken code cannot be pushed.
   ```
   git init && git add . && git commit -m "Initial commit"
   cp scripts/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push
   ```

4. Build and start all nine services:
   ```
   docker compose up -d --build
   docker compose ps
   ```

5. Run the pipeline for the first time. Open http://localhost:8080/pipeline.html, paste the admin
   token, keep "Same month" and press **Start pipeline run**. The stages, the log and the speed of
   each stage appear on the same page. When it finishes, the new model is promoted and the
   model server loads it within a minute.

   The first run streams one month of Lichess games (`month` in `params.yaml`). At
   `games_per_band: 5000` this is up to 50,000 games for Stockfish, which takes hours. To measure
   your Mac's speed first, set `games_per_band: 200`, run once, and read "Stockfish features ... games/s"
   on the console. Then raise it.

6. Use the app at http://localhost:8080 (see the [user manual](docs/user_manual.md)).

Stop everything with `docker compose down`. Data, models, logs and the MLflow database stay in
the repo folder (`data/`, `models/`, `logs/`, `mlflow-data/`) and are reused next time.

## Where to look

| What | Address |
|---|---|
| Web app (analyse a game) | http://localhost:8080 |
| Pipeline console | http://localhost:8080/pipeline.html |
| MLflow (experiments, model registry) | http://localhost:5050 |
| Grafana dashboard "Guess the Rating" | http://localhost:3000 (admin / admin for editing) |
| Prometheus (metrics, alert rules) | http://localhost:9090 |
| Alertmanager | http://localhost:9093 |
| Ray dashboard (only while featurize runs) | http://localhost:8265 (token: `docker compose exec pipeline ray get-auth-token`) |
| Backend API docs (Swagger) | http://localhost:8000/docs |

## Everyday commands

All of these run inside the `pipeline` container, where DVC, MLflow and the data live.

| Command | What it does |
|---|---|
| `docker compose exec pipeline dvc dag` | Draws the pipeline DAG |
| `docker compose exec pipeline dvc repro` | Runs only the stages whose code, data or params changed (the console does the same) |
| `docker compose exec pipeline dvc metrics show` | Prints the metrics of every stage report |
| `docker compose exec pipeline dvc plots show` | Writes `dvc_plots/index.html` with the evaluation plots |
| `docker compose exec pipeline dvc exp run evaluate -S train.num_leaves=15` | Tries a hyperparameter as a DVC experiment (stops before promote) |
| `docker compose exec pipeline dvc params diff HEAD~1` | Which settings changed since the previous commit |
| `docker compose exec pipeline dvc metrics diff HEAD~1` | How the metrics changed since the previous commit |
| `docker compose exec pipeline dvc plots diff HEAD~1` | Plots of this commit next to the previous one (`dvc_plots/index.html`) |
| `docker compose exec pipeline dvc exp show` | Compares experiments |
| `docker compose exec pipeline mlflow run . -e train --env-manager local` | Runs training as an MLflow project |
| `docker compose exec pipeline sh scripts/smoke_test.sh` | Checks that the champion answers real games through the backend |
| `docker compose exec backend python -m pytest -q` | Runs the unit and API tests |
| `docker compose exec backend python -m flake8 src backend tests scripts` | Checks coding style (PEP 8, 120 columns) |
| `docker compose exec pipeline python scripts/traffic.py --api http://frontend` | Sends real games to the app so the dashboards fill |
| `docker compose exec pipeline python scripts/load_test.py --api http://frontend --clients 4 --requests 40` | Load test: throughput and latency percentiles |

Commit your code before a run, and commit `dvc.lock` and `reports/` after it: `dvc.lock` records the exact
data and model hashes, so any run can be reproduced from a Git commit plus its MLflow run ID
(`git_commit` is logged as a parameter, so it only identifies the code if the code was committed).

## Automatic retraining and rollback

- Prometheus raises **FeatureDrift** when live games differ from the training data (PSI above 0.2) and
  **LiveErrorHigh** when the error on games with a known rating is 1.5 times the test error.
- Alertmanager sends alerts to the backend, which lists them on the console. With `AUTO_RETRAIN=true` in `.env`
  (then `docker compose up -d backend`), the backend also starts a pipeline run on the next month of data,
  at most once every 24 hours.
- A new model replaces the current one only if it passes the promotion gate and then a smoke test
  through the running app. If the smoke test fails, the previous model is restored automatically.
  The console also has a manual **Roll back** button.

## Notes and limitations

- One game is a small sample: a player having an unusually good or bad game will fool the model.
- Only Lichess blitz, standard chess, with clock times. Other sites' ratings are on different scales.
- `docker compose` mounts the repo into the containers, so code changes take effect after
  `docker compose restart <service>`.
- On macOS, node-exporter reports the Docker Desktop virtual machine, not the Mac itself.
