"""Unit tests for the data stages (ingest sampling, validation merge) and the pipeline runner."""

import io

import pytest
from fastapi.testclient import TestClient
from prometheus_client import generate_latest

import runner
from chessfeat.parse import iter_game_texts
from helpers import make_game
from ingest import sample_games
from validate import merge


def games_text(*games):
    return "".join(g.rstrip("\n") + "\n\n" for g in games)


def test_ingest_keeps_blitz_games_up_to_the_band_quota():
    stream = games_text(
        make_game(WhiteElo="1500", BlackElo="1500", Site="https://lichess.org/aaaaaaa1"),
        make_game(WhiteElo="1510", BlackElo="1490", Site="https://lichess.org/aaaaaaa2"),
        make_game(WhiteElo="1500", BlackElo="1500", Site="https://lichess.org/aaaaaaa3"),   # band full
        make_game(base=60, Site="https://lichess.org/aaaaaaa4"),                            # bullet
        make_game(WhiteElo="2500", BlackElo="2500", Site="https://lichess.org/aaaaaaa5"),
    )
    out = io.StringIO()
    read, kept, rejected = sample_games(io.StringIO(stream), out, [1000, 2000], quota=2, max_read=100)
    assert read == 5
    assert kept == {"<1000": 0, "1000-1999": 2, "2000+": 1}
    assert rejected == {"not_blitz": 1}
    assert len(list(iter_game_texts(io.StringIO(out.getvalue())))) == 3


def test_validation_merge_drops_duplicates_across_chunks():
    chunk1 = ([("g1", "text1"), ("g2", "text2")], {"checked": 2, "valid": 2})
    chunk2 = ([("g2", "text2")], {"checked": 2, "valid": 1, "too_short": 1})
    games, counts = merge([chunk1, chunk2])
    assert games == ["text1", "text2"]
    assert counts == {"checked": 4, "valid": 2, "too_short": 1, "duplicate": 1}


def test_next_month_rolls_over_the_year():
    assert runner.next_month("2026-07") == "2026-08"
    assert runner.next_month("2026-12") == "2027-01"


def test_set_month_edits_only_the_month_line(tmp_path):
    params = tmp_path / "params.yaml"
    params.write_text('# comment\nmonth: "2026-07"   # which month\nmodel_name: chess-rating\n')
    assert runner.set_month("next", params) == "2026-08"
    assert params.read_text() == '# comment\nmonth: "2026-08"   # which month\nmodel_name: chess-rating\n'
    assert runner.set_month(None, params) == "2026-08"


@pytest.mark.parametrize("line, expected", [
    ("Running stage 'featurize':\n", ("featurize", "running")),
    ("Stage 'ingest' didn't change, skipping\n", ("ingest", "skipped")),
    ("Stage 'ingest' is cached - skipping run, checking out outputs\n", ("ingest", "skipped")),
    ("> python src/train.py\n", None),
])
def test_dvc_stage_lines_are_recognised(line, expected):
    assert runner.parse_stage_line(line) == expected


def test_failure_counters_start_at_zero():
    # Prometheus increase() needs a 0 sample before the first failure, or the PipelineFailed alert never fires
    text = generate_latest().decode()
    assert 'chess_pipeline_runs_total{status="failed",trigger="alert"} 0.0' in text


def test_runner_refuses_a_second_run_and_bad_ids():
    client = TestClient(runner.app)
    runner.lock.acquire()                     # pretend a run is in progress
    try:
        assert client.post("/run", json={"trigger": "manual"}).status_code == 409
    finally:
        runner.lock.release()
    assert client.post("/run", json={"month": "July"}).status_code == 422
    assert client.get("/runs/../../etc/log").status_code == 404
    assert client.get("/runs/not-a-run/log").status_code == 422
