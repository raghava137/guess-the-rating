"""Small helpers shared by the pipeline stages: params, reports, logging."""

import json
import logging
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_params():
    with open(ROOT / "params.yaml") as handle:
        return yaml.safe_load(handle)


def write_json(path, data):
    path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


def read_json(path):
    return json.loads((ROOT / path).read_text())


def get_logger(name, filename="pipeline.log"):
    """Log to stdout (seen by DVC, the runner and `docker compose logs`) and to logs/<filename>."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        (ROOT / "logs").mkdir(exist_ok=True)
        fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
        for handler in (logging.StreamHandler(), logging.FileHandler(ROOT / "logs" / filename)):
            handler.setFormatter(fmt)
            logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
