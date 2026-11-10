"""API tests for the backend, with the model server, Stockfish and the runner replaced by fakes.

They check the interface in docs/lld.md: status codes, response fields, headers and admin protection.
"""

import os

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

os.environ["DISABLE_PROBER"] = "1"
from backend import app as backend  # noqa: E402
from chessfeat.features import FEATURES  # noqa: E402
from helpers import make_game  # noqa: E402

TOKEN = "test-token"


def fake_rows(parsed):
    rows = []
    for color, rating in (("white", 1500), ("black", None)):
        row = {f: 0.5 for f in FEATURES}
        row.update({"color": 1.0 if color == "white" else 0.0, "player": color, "rating": rating,
                    "acpl": 40.0, "mean_move_time": 4.0, "plies": 60.0, "acpl_endgame": float("nan")})
        rows.append(row)
    return rows


def fake_model(rows):
    return [{"low": 1300, "mid": 1450, "high": 1600, "model_version": "3",
             "reasons": '[["acpl", -80.0], ["acpl_endgame", 12.5], ["result", 5.0]]'} for _ in rows]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(backend.pool, "rows", fake_rows)
    monkeypatch.setattr(backend, "invoke_model", fake_model)
    monkeypatch.setattr(backend, "ADMIN_TOKEN", TOKEN)
    monkeypatch.setattr(backend, "RETRAIN_FILE", tmp_path / "last_auto_retrain.json")
    return TestClient(backend.app)


def test_predict_returns_a_range_per_player(client):
    response = client.post("/api/v1/predict", json={"pgn": make_game()})
    assert response.status_code == 200
    body = response.json()
    assert body["model_version"] == "3" and body["served_by"]
    assert float(response.headers["X-Process-Time-ms"]) >= 0
    white, black = body["players"]
    assert (white["low"], white["mid"], white["high"]) == (1300, 1450, 1600)
    assert white["actual"] == 1500 and black["actual"] is None
    assert white["reasons"][0] == {"feature": "acpl", "effect": -80.0, "value": 40.0}
    # a missing feature (no endgame reached) is null in JSON, also when it is one of the reasons
    assert white["features"]["acpl_endgame"] is None and white["reasons"][1]["value"] is None


@pytest.mark.parametrize("payload", [{}, {"pgn": make_game(), "url": "https://lichess.org/abcdefgh"}])
def test_predict_needs_exactly_one_input(client, payload):
    assert client.post("/api/v1/predict", json=payload).status_code == 422


@pytest.mark.parametrize("pgn, words", [
    ("hello world", "Could not read"),
    (make_game(base=60), "not a blitz game"),
    (make_game(plies=12), "too short"),
])
def test_bad_games_are_refused_with_a_reason(client, pgn, words):
    response = client.post("/api/v1/predict", json={"pgn": pgn})
    assert response.status_code == 422
    assert words in response.json()["detail"]


def test_lichess_links_are_parsed_and_fetched(client, monkeypatch):
    fetched = []
    monkeypatch.setattr(backend, "fetch_lichess", lambda game_id: fetched.append(game_id) or make_game())
    assert client.post("/api/v1/predict", json={"url": "https://lichess.org/bcRJNu4b/black"}).status_code == 200
    assert fetched == ["bcRJNu4b"]
    assert client.post("/api/v1/predict", json={"url": "https://example.com/x"}).status_code == 422


@pytest.mark.parametrize("text, game_id", [
    ("https://lichess.org/bcRJNu4b", "bcRJNu4b"), ("lichess.org/bcRJNu4bWXYZ", "bcRJNu4b"),
    ("bcRJNu4b", "bcRJNu4b"), ("https://lichess.org/bcRJNu4b/white#12", "bcRJNu4b"),
])
def test_game_id_forms(text, game_id):
    assert backend.lichess_game_id(text) == game_id


def test_model_server_down_gives_503(client, monkeypatch):
    def down(rows):
        raise HTTPException(503, "The model server is not answering")
    monkeypatch.setattr(backend, "invoke_model", down)
    assert client.post("/api/v1/predict", json={"pgn": make_game()}).status_code == 503


@pytest.mark.parametrize("method, path, body", [
    ("post", "/api/v1/pipeline/run", {"month": "next"}),
    ("post", "/api/v1/model/rollback", None),
])
def test_admin_actions_need_the_token(client, method, path, body):
    assert getattr(client, method)(path, json=body).status_code == 401
    assert getattr(client, method)(path, json=body, headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_drift_alert_starts_retraining_once_per_cooldown(client, monkeypatch):
    calls = []

    def fake_runner(method, path, **kwargs):
        calls.append(kwargs["json"])
        return {"id": "20261026-120000"}
    monkeypatch.setattr(backend, "runner", fake_runner)
    alert = {"alerts": [{"status": "firing", "labels": {"alertname": "FeatureDrift"},
                         "annotations": {"summary": "drift"}}]}
    client.post("/api/v1/alerts", json=alert)
    assert client.get("/api/v1/alerts").json()[0]["action"].startswith("automatic retraining is off")

    monkeypatch.setattr(backend, "AUTO_RETRAIN", True)
    client.post("/api/v1/alerts", json=alert)
    client.post("/api/v1/alerts", json=alert)
    actions = [a["action"] for a in client.get("/api/v1/alerts").json()[:2]]
    assert actions[1].startswith("retraining started") and actions[0].startswith("skipped")
    assert calls == [{"month": "next", "trigger": "alert"}]


def test_metrics_are_exposed(client):
    client.post("/api/v1/predict", json={"pgn": make_game()})
    text = client.get("/metrics").text
    assert 'chess_http_requests_total{endpoint="predict",method="POST",status="200"}' in text
    assert "chess_live_mae" in text
