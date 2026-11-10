#!/bin/sh
# Smoke test of the running app: the champion model must answer real games through the backend.
# Usage: docker compose exec pipeline sh scripts/smoke_test.sh
set -e
python src/promote.py --smoke-only
