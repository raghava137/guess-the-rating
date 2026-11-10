"""Model server that follows the @champion alias.

Starts `mlflow models serve` for the version @champion points to, then checks the alias every
POLL_SECONDS. When the alias moves (a promotion or a rollback) it stops the old server and
starts the new version on the same port, so nothing else has to be restarted.
The version number is passed in MODEL_VERSION, and the model returns it with every prediction.
"""

import os
import socket
import subprocess
import time

import mlflow
import requests
from mlflow.exceptions import MlflowException

from common import get_logger

log = get_logger("serve_champion")
NAME = os.environ.get("MODEL_NAME", "chess-rating")
PORT = int(os.environ.get("MODEL_PORT", "5001"))
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "30"))


def champion_version(client):
    try:
        return client.get_model_version_by_alias(NAME, "champion").version
    except (MlflowException, requests.RequestException) as exc:
        if "RESOURCE_DOES_NOT_EXIST" not in str(exc):       # "no model yet" is normal before the first run
            log.warning("cannot read @champion: %s", str(exc)[:200])
        return None


def port_in_use(port):
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def start(version):
    env = {**os.environ, "MODEL_VERSION": version}
    command = ["mlflow", "models", "serve", "-m", f"models:/{NAME}/{version}", "--host", "0.0.0.0",
               "--port", str(PORT), "--env-manager", "local", "--workers", "1"]
    log.info("starting model server for version %s", version)
    return subprocess.Popen(command, env=env)


def wait_ready(process, timeout=300):
    """True once /ping answers; False on timeout or as soon as the server process exits (e.g. a broken model)."""
    deadline = time.time() + timeout
    while time.time() < deadline and process.poll() is None:
        try:
            if requests.get(f"http://127.0.0.1:{PORT}/ping", timeout=2).ok:
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    return False


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    while port_in_use(PORT):
        time.sleep(1)


def main():
    client = mlflow.MlflowClient()
    process, started, serving, waiting_logged = None, None, None, False   # started: version of the process
    while True:
        wanted = champion_version(client)
        crashed = process is not None and process.poll() is not None
        if wanted and (wanted != serving or crashed):
            if crashed:
                log.error("model server for version %s exited with %s", started, process.returncode)
            elif process is not None:
                log.info("@champion moved from %s to %s", serving, wanted)
                stop(process)
            process, started = start(wanted), wanted
            serving = wanted if wait_ready(process) else None
            log.info("version %s is %s", wanted, "ready" if serving else "NOT ready")
        elif not wanted and process is None and not waiting_logged:
            log.info("no @champion yet; waiting for the first promotion")
            waiting_logged = True
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
