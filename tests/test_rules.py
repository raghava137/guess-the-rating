"""Unit tests for chessfeat.rules: the blitz rule and every validation rule."""

import pytest

from chessfeat.parse import parse_pgn_text
from chessfeat.rules import GameValidator, header_problems, is_blitz
from helpers import make_game


@pytest.mark.parametrize("tc, expected", [
    ("180+0", True), ("180+2", True), ("300+0", True), ("300+3", True),
    ("0+5", True),                       # 0 + 40*5 = 200 s -> blitz
    ("179+0", False), ("479+0", True), ("480+0", False),  # class boundaries
    ("120+1", False),                    # 160 s -> bullet
    ("600+0", False),                    # rapid
    ("-", False), ("", False), (None, False),
])
def test_is_blitz(tc, expected):
    assert is_blitz(tc) is expected


def test_valid_game_passes_and_duplicate_is_rejected():
    validator = GameValidator()
    parsed = parse_pgn_text(make_game())
    assert validator.check(parsed) is True
    assert validator.check(parsed) is False
    assert validator.counts == {"checked": 2, "valid": 1, "duplicate": 1}


@pytest.mark.parametrize("overrides, reason", [
    ({"Event": "Casual Blitz game"}, "not_rated"),
    ({"Variant": "Crazyhouse"}, "not_standard"),
    ({"TimeControl": "120+1"}, "not_blitz"),
    ({"BlackTitle": "BOT"}, "bot_player"),
    ({"WhiteElo": "?"}, "bad_rating"),
    ({"BlackElo": "5000"}, "bad_rating"),
    ({"Termination": "Abandoned"}, "bad_termination"),
])
def test_header_rules(overrides, reason):
    headers = {"Event": "Rated Blitz game", "WhiteElo": "1500", "BlackElo": "1520",
               "TimeControl": "180+0", "Termination": "Normal"}
    assert header_problems(headers) == []
    headers.update(overrides)
    assert header_problems(headers) == [reason]


@pytest.mark.parametrize("kwargs, reason", [
    ({"plies": 12}, "too_short"),
    ({"drop_clock_at": 7}, "missing_clock"),
    ({"first_white": 90.0}, "berserk_or_odd_clock"),
])
def test_move_rules(kwargs, reason):
    parsed = parse_pgn_text(make_game(**kwargs))
    assert GameValidator().problems(parsed) == [reason]
