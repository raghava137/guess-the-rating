"""PGN parsing shared by the pipeline and the backend.

Bulk dumps and the game export API write the same moves and clocks but slightly
different headers, so everything here reads tags by name and never by position.
"""

import io
from dataclasses import dataclass, field

import chess.pgn


@dataclass(frozen=True)
class Ply:
    """One half-move: who played it, what was played, and the clock left afterwards."""
    index: int          # 0-based ply number
    color: str          # "white" or "black"
    san: str            # move in standard notation, e.g. "Nf3"
    uci: str            # move in UCI notation, e.g. "g1f3"
    clock: float | None  # seconds left after the move, from the %clk comment


@dataclass
class ParsedGame:
    """A game reduced to what the rules and features need."""
    game_id: str
    headers: dict
    plies: list = field(default_factory=list)
    game: chess.pgn.Game | None = None   # kept so features can replay the positions


def game_id_from_headers(headers):
    """Use the GameId tag if present (export API), else the last part of the Site URL (bulk dump)."""
    if headers.get("GameId"):
        return headers["GameId"]
    return headers.get("Site", "").rstrip("/").rsplit("/", 1)[-1]


def extract_plies(game):
    """List every ply of the main line with its clock reading."""
    plies = []
    for index, node in enumerate(game.mainline()):
        plies.append(Ply(
            index=index,
            color="white" if index % 2 == 0 else "black",
            san=node.san(),
            uci=node.move.uci(),
            clock=node.clock(),
        ))
    return plies


def parse_game(game):
    """Convert a python-chess Game into a ParsedGame."""
    headers = dict(game.headers)
    return ParsedGame(game_id_from_headers(headers), headers, extract_plies(game), game)


def parse_pgn_text(text):
    """Parse a single game from PGN text, as pasted by a user or returned by the export API.

    Raises ValueError when the text holds no readable game.
    """
    game = chess.pgn.read_game(io.StringIO(text))
    if game is None or game.errors or not list(game.mainline_moves()):
        raise ValueError("not a readable PGN game")
    return parse_game(game)


def iter_games(handle):
    """Yield every game in an open PGN file, one ParsedGame at a time (constant memory)."""
    while True:
        game = chess.pgn.read_game(handle)
        if game is None:
            return
        yield parse_game(game)


def iter_game_texts(lines):
    """Split a stream of PGN lines into (headers, text) per game, without parsing moves.

    Much faster than python-chess, so ingest can read millions of games and only fully
    parse the ones it keeps. Lichess files hold tag lines, a blank line, the moves, a blank line.
    """
    headers, buf, in_moves = {}, [], False
    for line in lines:
        if line.startswith("["):
            if in_moves:                      # a tag after moves means the next game began
                yield headers, "".join(buf)
                headers, buf, in_moves = {}, [], False
            key, _, value = line[1:].partition(" ")
            headers[key] = value.strip().rstrip("]").strip('"')
        elif line.strip():
            in_moves = True
        buf.append(line)
    if headers:
        yield headers, "".join(buf)
