"""Test helpers: build small legal blitz games as PGN text."""

import chess
import chess.pgn

# A legal 24-ply game (both knights shuffle out and back), used to build test games.
SHUFFLE = ["g1f3", "g8f6", "f3g1", "f6g8"] * 6


def make_game(plies=24, base=180, first_white=None, drop_clock_at=None, **header_overrides):
    """Build a blitz game as PGN text, with clocks, that passes every rule by default."""
    game = chess.pgn.Game()
    game.headers.update({
        "Event": "Rated Blitz game", "Site": "https://lichess.org/test0001",
        "WhiteElo": "1500", "BlackElo": "1520", "TimeControl": f"{base}+0",
        "Termination": "Normal", "Result": "1/2-1/2",
    })
    game.headers.update(header_overrides)
    node, clock = game, float(base)
    for i, uci in enumerate(SHUFFLE[:plies]):
        node = node.add_variation(chess.Move.from_uci(uci))
        if i == 0 and first_white is not None:
            node.set_clock(first_white)
        elif i != drop_clock_at:
            node.set_clock(clock if i < 2 else clock - i)
    return str(game)
