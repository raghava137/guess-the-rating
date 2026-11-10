# One Python image for the MLflow server, the model server, the backend and the pipeline.
# Each service in docker-compose.yml runs a different command from it.
FROM python:3.11-slim-bookworm

# Stockfish for the features, git so MLflow and DVC can record the commit.
RUN apt-get update \
    && apt-get install -y --no-install-recommends stockfish git \
    && rm -rf /var/lib/apt/lists/*
ENV PATH="/usr/games:${PATH}" \
    PYTHONPATH=/app/src:/app \
    PYTHONUNBUFFERED=1 \
    MLFLOW_DISABLE_AGENT_HINT=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# The repository is mounted at /app by docker compose; this copy makes the image usable on its own.
COPY . .
RUN git config --global --add safe.directory /app
