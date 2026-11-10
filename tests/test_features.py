"""Unit tests for chessfeat.features: move quality, clock use, leakage, and Stockfish determinism."""

import math
import shutil
from pathlib import Path

import chess
import pytest

from chessfeat.features import (FEATURES, Analyzer, eco_group, move_quality, player_rows, result_for,
                                terminal_eval, time_features)
from chessfeat.parse import Ply, parse_pgn_text

FIXTURE = (Path(__file__).parent / "fixtures" / "bcRJNu4b_bulk.pgn").read_text()
needs_stockfish = pytest.mark.skipif(shutil.which("stockfish") is None, reason="Stockfish not installed")


def test_no_rating_information_in_model_inputs():
    # The label must never leak into the inputs: no ratings, rating gaps or player names.
    for name in FEATURES:
        assert not any(word in name for word in ("elo", "rating", "player", "name", "diff"))


def test_move_quality_counts_losses_for_the_mover_only():
    evals = [20, 20, 10, -300, -280]          # white's point of view, one more entry than moves
    moves = ["m0", "m1", "m2", "m3"]
    best = ["m0", "x", "x", "m3"]
    phases = ["opening"] * 4
    white = move_quality(evals, best, moves, phases, "white")
    black = move_quality(evals, best, moves, phases, "black")
    assert white["acpl"] == 155               # moves 0 and 2 lose 0 and 310
    assert white["blunder_rate"] == 0.5
    assert white["best_move_rate"] == 0.5
    assert black["acpl"] == 10                # moves 1 and 3 lose 0 and 20
    assert black["blunder_rate"] == 0
    assert math.isnan(white["acpl_endgame"])  # no endgame moves


def test_time_features_use_increment_and_skip_the_first_move():
    plies = [Ply(0, "white", "", "", 180.0), Ply(1, "black", "", "", 180.0),
             Ply(2, "white", "", "", 175.0), Ply(3, "black", "", "", 178.0),
             Ply(4, "white", "", "", 171.0), Ply(5, "black", "", "", 8.0)]
    white = time_features(plies, base=180, inc=2, color="white")
    assert white["mean_move_time"] == pytest.approx((0 + 7 + 6) / 3)
    assert white["time_used_frac"] == pytest.approx(13 / 184)
    assert time_features(plies, 180, 2, "black")["low_clock_moves"] == 1


def test_checkmate_is_a_full_loss_for_the_side_to_move():
    board = chess.Board()
    for san in ["f3", "e5", "g4", "Qh4#"]:
        board.push_san(san)
    assert terminal_eval(board) == -1000


def test_small_helpers():
    assert result_for("1-0", "white") == 1.0 and result_for("1-0", "black") == 0.0
    assert result_for("1/2-1/2", "black") == 0.5
    assert eco_group("C45") == 2 and eco_group("") == -1


@needs_stockfish
def test_analysis_is_deterministic_and_rows_are_complete():
    parsed = parse_pgn_text(FIXTURE)
    first = Analyzer(depth=6)
    try:
        run1 = first.analyse_game(parsed.game)
        run2 = first.analyse_game(parsed.game)     # same engine, warm hash
    finally:
        first.close()
    second = Analyzer(depth=6)
    try:
        run3 = second.analyse_game(parsed.game)    # fresh engine
        rows = player_rows(parsed, second)
    finally:
        second.close()
    assert run1[0] == run2[0] == run3[0]
    assert len(run1[0]) == len(parsed.plies) + 1
    assert [row["rating"] for row in rows] == [1058, 1029]
    assert all(set(FEATURES) <= set(row) for row in rows)
    assert rows[1]["result"] == 1.0                # black delivered mate
