"""Unit tests for chessfeat.parse, using a real game saved from both Lichess sources."""

from pathlib import Path

import pytest

from chessfeat.parse import iter_games, parse_pgn_text
from chessfeat.rules import header_problems

FIXTURES = Path(__file__).parent / "fixtures"
BULK = (FIXTURES / "bcRJNu4b_bulk.pgn").read_text()      # from the monthly dump
EXPORT = (FIXTURES / "bcRJNu4b_export.pgn").read_text()  # from /game/export/<id>?clocks=true


def test_bulk_and_export_give_identical_moves_and_clocks():
    bulk, export = parse_pgn_text(BULK), parse_pgn_text(EXPORT)
    assert bulk.game_id == export.game_id == "bcRJNu4b"
    assert bulk.plies == export.plies
    assert len(bulk.plies) == 86


def test_clocks_are_read_in_seconds():
    plies = parse_pgn_text(BULK).plies
    assert plies[0].clock == 120.0          # 0:02:00 after 1. e4
    assert plies[4].clock == 121.0          # increment shows from the second move on
    assert plies[-1].san == "Rf1#" and plies[-1].clock == 36.0


def test_header_differences_do_not_change_the_rules():
    # "Rated Bullet game" vs "rated bullet game", and an extra Variant tag in the export
    bulk, export = parse_pgn_text(BULK), parse_pgn_text(EXPORT)
    assert header_problems(bulk.headers) == header_problems(export.headers) == ["not_blitz"]


def test_unreadable_text_is_rejected():
    with pytest.raises(ValueError):
        parse_pgn_text("this is not a chess game")


def test_iter_games_streams_several_games(tmp_path):
    path = tmp_path / "two.pgn"
    path.write_text(BULK + "\n\n" + EXPORT)
    with path.open() as handle:
        assert [g.game_id for g in iter_games(handle)] == ["bcRJNu4b", "bcRJNu4b"]
