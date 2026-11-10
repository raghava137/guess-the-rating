"""Pipeline runner: runs `dvc repro` on request and keeps a record of every run.

The pipeline console in the web app talks to this service (through the backend):
  GET  /status            the run in progress (if any) and the last finished run
  GET  /runs              run history, newest first
  GET  /runs/{id}/log     the console output of one run
  POST /run               start a run: {"month": "2026-08" | "next" | null, "trigger": "manual"}
  GET  /metrics           Prometheus metrics (runs by status, stage durations, throughput)
Only one run at a time; a second POST /run while busy gets 409.
"""

import json
import re
import subprocess
import threading
import time
from datetime import datetime

from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from pydantic import BaseModel, Field

from common import ROOT, get_logger

log = get_logger("runner")
RUNS_DIR = ROOT / "logs" / "runs"
HISTORY = ROOT / "logs" / "runs.jsonl"
STAGES = ["ingest", "validate", "featurize", "train", "evaluate", "promote"]

RUNS = Counter("chess_pipeline_runs_total", "Finished pipeline runs", ["status", "trigger"])
RUNNING = Gauge("chess_pipeline_running", "1 while a pipeline run is in progress")
STAGE_SECONDS = Gauge("chess_pipeline_stage_duration_seconds", "Duration of each stage in the last run", ["stage"])
THROUGHPUT = Gauge("chess_pipeline_throughput", "Games per second of each data stage in the last run", ["stage"])
TRIGGERS = ["manual", "alert"]
for _status in ("success", "failed"):     # start every series at 0, so increase() sees the first failure
    for _trigger in TRIGGERS:
        RUNS.labels(_status, _trigger)

app = FastAPI(title="Pipeline runner")
lock = threading.Lock()
current = {"run": None}


class RunRequest(BaseModel):
    month: str | None = Field(None, pattern=r"^(next|\d{4}-\d{2})$")
    trigger: str = Field("manual", pattern=r"^(" + "|".join(TRIGGERS) + ")$")


def next_month(month):
    year, mon = map(int, month.split("-"))
    return f"{year + mon // 12}-{mon % 12 + 1:02d}"


def set_month(month, path=ROOT / "params.yaml"):
    """Change `month:` in params.yaml (the only line edited), return the month now in use."""
    text = path.read_text()
    old = re.search(r'^month: "?(\d{4}-\d{2})"?', text, re.M).group(1)
    new = next_month(old) if month == "next" else (month or old)
    path.write_text(re.sub(r'^month: "?\d{4}-\d{2}"?', f'month: "{new}"', text, count=1, flags=re.M))  # keeps comments
    return new


def parse_stage_line(line):
    """Return (stage, "running"|"skipped") for DVC's stage lines, else None.

    "skipped" covers both "didn't change" and "is cached" (outputs restored from DVC's run cache).
    """
    match = re.search(r"Running stage '(\w+)'", line)
    if match:
        return match.group(1), "running"
    match = re.search(r"Stage '(\w+)' (didn't change, skipping|is cached)", line)
    if match:
        return match.group(1), "skipped"
    return None


def run_summary():
    """Throughput of the data stages and the promotion decision, read from the stage reports."""
    def report(name):
        try:
            return json.loads((ROOT / "reports" / f"{name}.json").read_text())
        except (OSError, ValueError):
            return {}
    ingest, validation, featurize = report("ingest"), report("validation"), report("featurize")
    throughput = {"ingest": ingest.get("games_read_per_sec"), "validate": validation.get("games_per_sec"),
                  "featurize": featurize.get("games_per_sec")}
    for stage, rate in throughput.items():
        if rate is not None:
            THROUGHPUT.labels(stage).set(rate)
    return {
        "throughput": throughput,
        "games": {"read": ingest.get("games_read"), "kept": ingest.get("games_kept"),
                  "valid": validation.get("games_valid"), "featurized": featurize.get("games")},
        "test_mae": report("metrics").get("mae"),
        "promotion": report("promotion") or None,
    }


def load_history():
    if not HISTORY.exists():
        return []
    return [json.loads(line) for line in HISTORY.read_text().splitlines() if line.strip()]


def execute(run):
    """Run `dvc repro`, stream its output to the run's log file, time each stage."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RUNS_DIR / f"{run['id']}.log"
    started, stage, stage_start = time.time(), None, None

    def close_stage(status):
        if stage and stage_start:
            seconds = round(time.time() - stage_start, 1)
            run["stages"][stage] = {"status": status, "seconds": seconds}
            STAGE_SECONDS.labels(stage).set(seconds)

    previous_month = set_month(None)
    with open(log_path, "w") as out:
        try:
            run["month"] = set_month(run["month"])
            process = subprocess.Popen(["dvc", "repro"], cwd=ROOT, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in process.stdout:
                out.write(f"{datetime.now():%H:%M:%S} {line}")
                out.flush()
                found = parse_stage_line(line)
                if found:
                    close_stage("done")
                    stage, state = found
                    if state == "skipped":
                        run["stages"][stage] = {"status": "skipped", "seconds": 0}
                        stage, stage_start = None, None
                    else:
                        stage_start = time.time()
                        run["stages"][stage] = {"status": "running"}
            run["returncode"] = process.wait()
            close_stage("done" if run["returncode"] == 0 else "failed")
            if run["returncode"] == 0 and (ROOT / ".dvc" / "config").exists():
                push = subprocess.run(["dvc", "push"], cwd=ROOT, capture_output=True, text=True)
                out.write(f"{datetime.now():%H:%M:%S} dvc push: exit {push.returncode}\n{push.stdout}{push.stderr}")
        except Exception as exc:      # a crash of the runner itself is recorded as a failed run
            run["returncode"] = -1
            out.write(f"runner error: {exc!r}\n")
            log.exception("run %s crashed", run["id"])

    run["status"] = "success" if run["returncode"] == 0 else "failed"
    if run["status"] == "failed":      # go back to the last good state: month in params.yaml and DVC outputs
        set_month(previous_month)
        subprocess.run(["dvc", "checkout"], cwd=ROOT, capture_output=True)
    run["seconds"] = round(time.time() - started, 1)
    run["finished"] = datetime.now().isoformat(timespec="seconds")
    if run["status"] == "success":
        run.update(run_summary())
    with open(HISTORY, "a") as history:
        history.write(json.dumps(run) + "\n")
    RUNS.labels(run["status"], run["trigger"]).inc()
    log.info("run %s %s in %.0f s", run["id"], run["status"], run["seconds"])
    return run


def start_run(month, trigger):
    """Claim the lock and build the run record; raises RuntimeError when a run is in progress."""
    if not lock.acquire(blocking=False):
        raise RuntimeError("a pipeline run is already in progress")
    run = {"id": datetime.now().strftime("%Y%m%d-%H%M%S"), "trigger": trigger, "month": month,
           "status": "running", "started": datetime.now().isoformat(timespec="seconds"), "stages": {}}
    current["run"] = run
    RUNNING.set(1)
    return run


def finish(run):
    try:
        execute(run)
    finally:
        current["run"] = None
        RUNNING.set(0)
        lock.release()


@app.get("/healthz")
def healthz():
    return {"status": "ok", "running": current["run"] is not None}


@app.get("/status")
def status():
    history = load_history()
    return {"running": current["run"], "last": history[-1] if history else None, "stages": STAGES}


@app.get("/runs")
def runs(limit: int = 20):
    return list(reversed(load_history()))[:limit]


@app.get("/runs/{run_id}/log")
def run_log(run_id: str, tail: int = 400):
    if not re.fullmatch(r"\d{8}-\d{6}", run_id):
        raise HTTPException(422, "bad run id")
    path = RUNS_DIR / f"{run_id}.log"
    if not path.exists():
        raise HTTPException(404, "no such run")
    return {"id": run_id, "lines": path.read_text(errors="replace").splitlines()[-tail:]}


@app.post("/run", status_code=202)
def run_pipeline(request: RunRequest):
    try:
        run = start_run(request.month, request.trigger)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    threading.Thread(target=finish, args=(run,), daemon=True).start()
    return run


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)
