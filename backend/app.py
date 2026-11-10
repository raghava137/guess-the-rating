"""Backend API for Guess the Rating (FastAPI).

The web page talks only to this service. For a prediction it reads the game (pasted PGN or a
Lichess link), checks it, runs Stockfish to compute the features with the same code as training,
asks the MLflow model server for the rating range, and returns it with the reasons.
It also serves the pipeline console (proxy to the runner), model rollback, the alert webhook,
drift and live-accuracy monitoring, and Prometheus metrics. Endpoint I/O is in docs/lld.md.

Endpoints are plain `def` functions: FastAPI runs them in a thread pool, which suits the blocking
Stockfish and HTTP calls they make.
"""

import hmac
import json
import os
import queue
import re
import socket
import tempfile
import threading
import time
from collections import deque
from contextlib import asynccontextmanager

import mlflow
import numpy as np
import requests
from cryptography.fernet import Fernet
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, Info, Summary, generate_latest
from pydantic import BaseModel, Field

from chessfeat.drift import DRIFT_FEATURES, psi
from chessfeat.features import FEATURES, Analyzer, player_rows
from chessfeat.parse import iter_game_texts, parse_pgn_text
from chessfeat.rules import read_rating, serving_problems
from common import ROOT, get_logger, load_params

log = get_logger("backend", "backend.log")

# ---------------------------------------------------------------- configuration (environment)
MODEL_URL = os.environ.get("MODEL_URL", "http://model:5001")
RUNNER_URL = os.environ.get("RUNNER_URL", "http://pipeline:8100")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://frontend")
MLFLOW_URL = os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow:5000")
MODEL_NAME = os.environ.get("MODEL_NAME", "chess-rating")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
FERNET_KEY = os.environ.get("FERNET_KEY", "")
AUTO_RETRAIN = os.environ.get("AUTO_RETRAIN", "false").lower() == "true"
STOCKFISH = os.environ.get("STOCKFISH_PATH", "stockfish")
ANALYZERS = int(os.environ.get("ANALYZERS", "2"))
PROBE_SECONDS = int(os.environ.get("PROBE_SECONDS", "15"))
LICHESS_EXPORT = "https://lichess.org/game/export/{}?clocks=true&evals=false"
WINDOW = 200                 # live monitoring looks at the last 200 players
DRIFT_MIN, ACCURACY_MIN = 100, 30     # below these sizes the numbers are too noisy to report
RETRAIN_ALERTS = {"FeatureDrift", "LiveErrorHigh"}
RETRAIN_COOLDOWN_S = 24 * 3600
RETRAIN_FILE = ROOT / "logs" / "last_auto_retrain.json"    # on disk, so the cooldown survives restarts
HOST = socket.gethostname()

MESSAGES = {
    "not_standard": "Only standard chess is supported, not variants.",
    "not_blitz": "This is not a blitz game. The model knows blitz only (3 to 8 minutes, e.g. 3+0, 3+2, 5+0, 5+3).",
    "too_short": "The game is too short. At least 10 moves by each side are needed.",
    "missing_clock": "The PGN has no clock times. Paste the Lichess link instead, or export the PGN with clocks.",
    "berserk_or_odd_clock": "The clocks do not start at the time control (berserk in a tournament?).",
}

# ---------------------------------------------------------------- Prometheus metrics
REQUESTS = Counter("chess_http_requests_total", "HTTP requests", ["endpoint", "method", "status"])
LATENCY = Histogram("chess_http_request_seconds", "HTTP request latency", ["endpoint"],
                    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32])
ANALYSIS = Histogram("chess_analysis_seconds", "Stockfish analysis time per game", buckets=[0.5, 1, 2, 4, 8, 16, 32])
MODEL_CALL = Summary("chess_model_call_seconds", "Time of calls to the model server")
PREDICTIONS = Counter("chess_predictions_total", "Games analysed", ["source"])
REJECTED = Counter("chess_rejected_games_total", "Games refused, by reason", ["reason"])
PSI = Gauge("chess_feature_psi", "Population stability index of live inputs vs training", ["feature"])
LIVE_MAE = Gauge("chess_live_mae", "MAE on the last 200 players whose real rating is in the PGN")
LIVE_COVERAGE = Gauge("chess_live_coverage", "Share of those real ratings inside the predicted range")
TEST_MAE = Gauge("chess_model_test_mae", "Test MAE of the served model (from the registry)")
MODEL_INFO = Info("chess_model", "Model version being served")
UP = Gauge("chess_component_up", "1 if a component answered the last health probe", ["component"])


# ---------------------------------------------------------------- stateful helpers (OO part)
class AnalyzerPool:
    """A fixed number of Stockfish engines shared by request threads, one game per engine at a time."""

    def __init__(self, size, settings):
        self.size, self.settings = size, settings
        self.free = None
        self.lock = threading.Lock()

    def rows(self, parsed, wait_s=30):
        """Feature rows for both players. Raises queue.Empty if every engine stays busy."""
        with self.lock:                               # start the engines on first use
            if self.free is None:
                self.free = queue.Queue()
                for _ in range(self.size):
                    self.free.put(Analyzer(STOCKFISH, **self.settings))
        analyzer = self.free.get(timeout=wait_s)
        try:
            return player_rows(parsed, analyzer)
        except Exception:
            analyzer.close()                          # a crashed engine is replaced, not reused
            analyzer = Analyzer(STOCKFISH, **self.settings)
            raise
        finally:
            self.free.put(analyzer)


class LiveMonitor:
    """Rolling windows of live inputs (for drift) and of errors on known ratings (for accuracy)."""

    def __init__(self):
        self.inputs = deque(maxlen=WINDOW)
        self.errors = deque(maxlen=WINDOW)            # (abs_error, real rating inside the range?)
        self.reference = None
        self.lock = threading.Lock()

    def add(self, rows, players):
        with self.lock:
            self.inputs.extend({f: row[f] for f in DRIFT_FEATURES} for row in rows)
            for p in players:
                if p["actual"] is not None:
                    self.errors.append((abs(p["mid"] - p["actual"]), p["low"] <= p["actual"] <= p["high"]))
            self.update_gauges()

    def set_reference(self, reference):
        with self.lock:
            self.reference = reference
            self.errors.clear()                       # a new model starts with a fresh accuracy window
            self.update_gauges()

    def update_gauges(self):
        if self.reference is not None and len(self.inputs) >= DRIFT_MIN:
            for feature in DRIFT_FEATURES:
                PSI.labels(feature).set(psi(self.reference[feature], [row[feature] for row in self.inputs]))
        enough = len(self.errors) >= ACCURACY_MIN     # NaN never triggers an alert
        LIVE_MAE.set(float(np.mean([e for e, _ in self.errors])) if enough else float("nan"))
        LIVE_COVERAGE.set(float(np.mean([inside for _, inside in self.errors])) if enough else float("nan"))


class PredictionLog:
    """Append-only log of predictions, encrypted with Fernet (AES-128 + HMAC). Player names are not stored."""

    def __init__(self, key, path):
        self.fernet = Fernet(key.encode()) if key else None
        self.path = path
        self.lock = threading.Lock()
        if not self.fernet:
            log.warning("FERNET_KEY not set: predictions will not be logged")

    def write(self, record):
        if not self.fernet:
            return
        token = self.fernet.encrypt(json.dumps(record, default=str).encode())
        with self.lock, open(self.path, "ab") as handle:
            handle.write(token + b"\n")


def serving_settings():
    cfg = load_params()["featurize"]
    return {k: cfg[k] for k in ("depth", "hash_mb", "max_plies", "endgame_material")}


pool = AnalyzerPool(ANALYZERS, serving_settings())
monitor = LiveMonitor()
prediction_log = PredictionLog(FERNET_KEY, ROOT / "logs" / "predictions.enc")
alerts = deque(maxlen=50)
state = {"model_version": None, "components": {}}


# ---------------------------------------------------------------- outgoing calls (timeouts + retries)
def json_number(value):
    """Round a feature for the response; NaN is not valid JSON, so it becomes None (null)."""
    value = float(value)
    return None if value != value else round(value, 3)


def call_with_retries(method, url, attempts=3, timeout=10, **kwargs):
    """HTTP call that retries connection errors, timeouts, 5xx and 429 with a short backoff."""
    error = None
    for attempt in range(attempts):
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
            if response.status_code < 500 and response.status_code != 429:
                return response
            error = HTTPException(502, f"{url.split('/')[2]} answered HTTP {response.status_code}")
        except requests.Timeout:
            error = HTTPException(504, f"{url.split('/')[2]} timed out")
        except requests.ConnectionError:
            error = HTTPException(503, f"{url.split('/')[2]} is not reachable")
        if attempt < attempts - 1:
            time.sleep(0.5 * 2 ** attempt)
    raise error


def invoke_model(rows):
    """Send feature rows to the MLflow model server; returns its list of predictions."""
    data = [[json_number(row[f]) for f in FEATURES] for row in rows]
    payload = {"dataframe_split": {"columns": FEATURES, "data": data}}
    with MODEL_CALL.time():
        try:
            response = call_with_retries("POST", f"{MODEL_URL}/invocations", json=payload, timeout=10)
        except HTTPException as exc:
            raise HTTPException(503, f"The model server is not answering (it may be loading a new version): "
                                     f"{exc.detail}")
    if not response.ok:
        raise HTTPException(502, f"Model server error: {response.text[:200]}")
    return response.json()["predictions"]


def served_version():
    """Ask the model server which version it is serving (one tiny prediction)."""
    try:
        return str(invoke_model([{f: 0.0 for f in FEATURES}])[0]["model_version"])
    except (HTTPException, KeyError, IndexError, ValueError):
        return None


def runner(method, path, **kwargs):
    response = call_with_retries(method, f"{RUNNER_URL}{path}", attempts=2, timeout=10, **kwargs)
    if not response.ok:
        raise HTTPException(response.status_code, response.json().get("detail", response.text[:200]))
    return response.json()


def lichess_game_id(text):
    """Accept a Lichess game link (any of its forms) or a bare 8/12-character game ID."""
    text = text.strip()
    link = r"(?:https?://)?(?:www\.)?lichess\.org/([A-Za-z0-9]{8})(?:[A-Za-z0-9]{4})?(?:/(?:white|black))?/?(?:[?#].*)?"
    match = re.fullmatch(link, text) or re.fullmatch(r"([A-Za-z0-9]{8})(?:[A-Za-z0-9]{4})?", text)
    if not match:
        raise HTTPException(422, "That does not look like a Lichess game link (e.g. https://lichess.org/bcRJNu4b).")
    return match.group(1)


def fetch_lichess(game_id):
    response = call_with_retries("GET", LICHESS_EXPORT.format(game_id), timeout=10,
                                 headers={"Accept": "application/x-chess-pgn", "User-Agent": "guess-the-rating"})
    if response.status_code == 404:
        raise HTTPException(404, "Lichess has no game with that ID.")
    if not response.ok:
        raise HTTPException(502, f"Lichess answered HTTP {response.status_code}.")
    return response.text


# ---------------------------------------------------------------- model metadata and health probes
def refresh_model_info(version):
    """When a new version is served: load its test MAE and the drift reference saved by training."""
    try:
        client = mlflow.MlflowClient()
        model_version = client.get_model_version(MODEL_NAME, version)
        test_mae = model_version.tags.get("test_mae")
        TEST_MAE.set(float(test_mae) if test_mae else float("nan"))
        with tempfile.TemporaryDirectory() as tmp:
            path = mlflow.artifacts.download_artifacts(run_id=model_version.run_id,
                                                       artifact_path="drift_reference.json", dst_path=tmp)
            with open(path) as handle:
                monitor.set_reference(json.load(handle))
        MODEL_INFO.info({"version": version, "run_id": model_version.run_id, "test_mae": test_mae or "unknown"})
        log.info("serving model version %s (test MAE %s)", version, test_mae)
    except Exception as exc:     # metadata is for monitoring only; serving goes on without it
        log.warning("could not load metadata for version %s: %s", version, exc)


def probe_once():
    checks = {"frontend": f"{FRONTEND_URL}/", "mlflow": f"{MLFLOW_URL}/health", "pipeline": f"{RUNNER_URL}/healthz"}
    for name, url in checks.items():
        try:
            ok = requests.get(url, timeout=3).ok
        except requests.RequestException:
            ok = False
        state["components"][name] = ok
        UP.labels(name).set(int(ok))
    version = served_version()
    state["components"]["model"] = version is not None
    UP.labels("model").set(int(version is not None))
    if version and version != state["model_version"]:
        refresh_model_info(version)
    if version:
        state["model_version"] = version


def prober():
    while True:
        try:
            probe_once()
        except Exception:
            log.exception("health probe failed")
        time.sleep(PROBE_SECONDS)


@asynccontextmanager
async def lifespan(app):
    if os.environ.get("DISABLE_PROBER") != "1":
        threading.Thread(target=prober, daemon=True).start()
    yield


app = FastAPI(title="Guess the Rating API", version="1.0", lifespan=lifespan)
# Only needed when the page is served from another address than the API (config.js apiBase).
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:8080").split(","),
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Admin-Token"])


@app.middleware("http")
async def timing(request: Request, call_next):
    """Timing header and Prometheus request metrics for every call."""
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    endpoint = getattr(request.scope.get("endpoint"), "__name__", "unmatched")
    REQUESTS.labels(endpoint, request.method, str(response.status_code)).inc()
    LATENCY.labels(endpoint).observe(elapsed)
    response.headers["X-Process-Time-ms"] = f"{elapsed * 1000:.1f}"
    return response


@app.exception_handler(Exception)
def unexpected_error(request: Request, exc: Exception):
    log.error("unhandled error on %s", request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal error. It has been logged."})


def require_admin(token):
    if not ADMIN_TOKEN or not hmac.compare_digest((token or "").encode(), ADMIN_TOKEN.encode()):
        raise HTTPException(401, "A valid admin token is required for this action.")


# ---------------------------------------------------------------- request models
class PredictRequest(BaseModel):
    pgn: str | None = Field(None, max_length=100_000, description="PGN text of one game, with clock comments")
    url: str | None = Field(None, max_length=200, description="Lichess game link or game ID")


class RunRequest(BaseModel):
    month: str | None = Field(None, pattern=r"^(next|\d{4}-\d{2})$")


# ---------------------------------------------------------------- prediction endpoints
@app.post("/api/v1/predict")
def predict(body: PredictRequest, request: Request):
    if bool(body.pgn) == bool(body.url):
        raise HTTPException(422, "Send either a PGN or a Lichess link, not both.")
    source = "url" if body.url else "pgn"
    text = fetch_lichess(lichess_game_id(body.url)) if body.url else body.pgn
    try:
        parsed = parse_pgn_text(text)
    except ValueError:
        REJECTED.labels("unreadable").inc()
        raise HTTPException(422, "Could not read a chess game from that text. Paste the full PGN.")
    problems = serving_problems(parsed)
    if problems:
        for reason in problems:
            REJECTED.labels(reason).inc()
        raise HTTPException(422, " ".join(MESSAGES.get(p, p) for p in problems))

    start = time.perf_counter()
    try:
        rows = pool.rows(parsed)
    except queue.Empty:
        raise HTTPException(503, "All analysis engines are busy. Please try again in a few seconds.")
    analysis_s = time.perf_counter() - start
    ANALYSIS.observe(analysis_s)
    outputs = invoke_model(rows)

    players = []
    for row, out in zip(rows, outputs):
        features = {f: json_number(row[f]) for f in FEATURES}     # NaN (e.g. no endgame) becomes null
        players.append({
            "color": "white" if row["color"] == 1.0 else "black", "name": row["player"], "actual": row["rating"],
            "low": out["low"], "mid": out["mid"], "high": out["high"],
            "reasons": [{"feature": f, "effect": e, "value": features[f]} for f, e in json.loads(out["reasons"])],
            "features": features,
        })
    version = str(outputs[0]["model_version"])

    if request.headers.get("X-Request-Source") != "smoke":       # smoke tests stay out of live statistics
        PREDICTIONS.labels(source).inc()
        monitor.add(rows, players)
        prediction_log.write({"time": time.time(), "game_id": parsed.game_id, "model_version": version,
                              "players": [{k: p[k] for k in ("color", "actual", "low", "mid", "high", "features")}
                                          for p in players]})
    log.info("predicted %s (%s) with version %s in %.1f s", parsed.game_id, source, version, analysis_s)
    return {"game_id": parsed.game_id, "time_control": parsed.headers.get("TimeControl"), "players": players,
            "model_version": version, "served_by": HOST, "analysis_ms": round(analysis_s * 1000)}


@app.get("/api/v1/examples")
def examples(limit: int = 4):
    """A few validated games from the current dataset, so first-time users can try the app."""
    path = ROOT / "data" / "valid" / "games.pgn"
    if not path.exists():
        return []
    games = []
    with open(path) as handle:
        for headers, text in iter_game_texts(handle):
            games.append({"white": headers.get("White"), "black": headers.get("Black"),
                          "white_elo": read_rating(headers.get("WhiteElo")),
                          "black_elo": read_rating(headers.get("BlackElo")),
                          "time_control": headers.get("TimeControl"), "pgn": text})
            if len(games) == min(limit, 10):
                break
    return games


@app.get("/healthz")
def healthz():
    version = served_version()
    return {"status": "ok" if version else "degraded", "model_version": version, "served_by": HOST,
            "components": state["components"]}


# ---------------------------------------------------------------- model registry
@app.get("/api/v1/model")
def model_info():
    try:
        client = mlflow.MlflowClient()
        aliases = dict(client.get_registered_model(MODEL_NAME).aliases)      # {"champion": "3", ...}
        versions = client.search_model_versions(f"name='{MODEL_NAME}'", max_results=20,
                                                order_by=["version_number DESC"])
    except Exception as exc:
        if "RESOURCE_DOES_NOT_EXIST" in str(exc):     # before the first training run: no model yet
            return {"name": MODEL_NAME, "served_version": None, "aliases": {}, "versions": []}
        raise HTTPException(502, f"MLflow is not reachable: {exc}")
    return {"name": MODEL_NAME, "served_version": state["model_version"], "aliases": aliases,
            "versions": [{"version": v.version, "aliases": [a for a, n in aliases.items() if n == v.version],
                          "test_mae": v.tags.get("test_mae"), "promotion": v.tags.get("promotion")}
                         for v in versions]}


@app.post("/api/v1/model/rollback")
def rollback(x_admin_token: str | None = Header(None)):
    """Swap @champion and @previous. The model server picks up the change within a minute."""
    require_admin(x_admin_token)
    client = mlflow.MlflowClient()
    try:
        champion = client.get_model_version_by_alias(MODEL_NAME, "champion").version
        previous = client.get_model_version_by_alias(MODEL_NAME, "previous").version
    except Exception:
        raise HTTPException(409, "There is no @previous version to roll back to.")
    client.set_registered_model_alias(MODEL_NAME, "champion", previous)
    client.set_registered_model_alias(MODEL_NAME, "previous", champion)
    log.warning("manual rollback: champion %s -> %s", champion, previous)
    return {"champion": previous, "previous": champion}


# ---------------------------------------------------------------- pipeline console and alerts
@app.get("/api/v1/pipeline/status")
def pipeline_status():
    return runner("GET", "/status")


@app.get("/api/v1/pipeline/runs")
def pipeline_runs(limit: int = 20):
    return runner("GET", f"/runs?limit={min(limit, 100)}")


@app.get("/api/v1/pipeline/runs/{run_id}/log")
def pipeline_log(run_id: str):
    if not re.fullmatch(r"\d{8}-\d{6}", run_id):
        raise HTTPException(422, "bad run id")
    return runner("GET", f"/runs/{run_id}/log")


@app.post("/api/v1/pipeline/run", status_code=202)
def pipeline_run(body: RunRequest, x_admin_token: str | None = Header(None)):
    require_admin(x_admin_token)
    return runner("POST", "/run", json={"month": body.month, "trigger": "manual"})


@app.get("/api/v1/alerts")
def list_alerts():
    return list(reversed(alerts))


@app.post("/api/v1/alerts")
def receive_alerts(payload: dict):
    """Alertmanager webhook. nginx does not forward POSTs here, so only services inside Docker can call it."""
    for alert in payload.get("alerts", []):
        name, status = alert.get("labels", {}).get("alertname", "?"), alert.get("status", "?")
        entry = {"name": name, "status": status, "summary": alert.get("annotations", {}).get("summary"),
                 "received": time.time(), "action": None}
        if status == "firing" and name in RETRAIN_ALERTS:
            entry["action"] = maybe_retrain(name)
        alerts.append(entry)
        log.warning("alert %s %s: %s", name, status, entry["summary"])
    return {"ok": True}


def maybe_retrain(alert_name):
    """Start a pipeline run on the next month's data, if AUTO_RETRAIN is on and the cooldown has passed."""
    if not AUTO_RETRAIN:
        return "automatic retraining is off (AUTO_RETRAIN in .env)"
    try:
        last = json.loads(RETRAIN_FILE.read_text())["time"]
    except (OSError, ValueError, KeyError):
        last = 0.0
    if time.time() - last < RETRAIN_COOLDOWN_S:
        return "skipped: retrained less than 24 h ago"
    try:
        run = runner("POST", "/run", json={"month": "next", "trigger": "alert"})
    except HTTPException as exc:
        return f"could not start: {exc.detail}"
    RETRAIN_FILE.parent.mkdir(exist_ok=True)
    RETRAIN_FILE.write_text(json.dumps({"time": time.time(), "run": run["id"], "alert": alert_name}))
    return f"retraining started (run {run['id']}) because of {alert_name}"


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
