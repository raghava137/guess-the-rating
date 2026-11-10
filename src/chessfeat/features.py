"""Stockfish and clock features for each player in a game.

Used by the pipeline (featurize stage) and by the backend for live requests, so
training and serving compute features with exactly the same code and settings.
"""

import statistics

import chess
import chess.engine

# Model inputs, in a fixed order. No ratings, rating differences or names (see test_leakage).
FEATURES = [
    "acpl", "acpl_opening", "acpl_middlegame", "acpl_endgame",
    "blunder_rate", "mistake_rate", "inaccuracy_rate", "best_move_rate",
    "time_used_frac", "mean_move_time", "move_time_cv", "low_clock_moves", "lost_on_time",
    "color", "plies", "result", "eco_group",
]

CAP = 1000                      # evaluations capped at +-1000 centipawns; a forced mate counts as 1000
OPENING_PLIES = 20              # first 10 moves of each side
PIECE_VALUES = {chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
LOW_CLOCK_SECONDS = 10


class Analyzer:
    """Owns one Stockfish process. Not thread-safe: use one per Ray actor or per request slot."""

    def __init__(self, path="stockfish", depth=10, hash_mb=16, max_plies=120, endgame_material=13):
        self.engine = chess.engine.SimpleEngine.popen_uci(path)
        self.engine.configure({"Threads": 1, "Hash": hash_mb})
        self.depth = depth
        self.max_plies = max_plies
        self.endgame_material = endgame_material
        self.version = self.engine.id.get("name", "unknown")

    def analyse_game(self, game):
        """Evaluate every position once (white's point of view) up to max_plies.

        Returns (evals, best_moves, moves, phases): evals has one more entry than moves.
        """
        new_game = object()   # a fresh key makes python-chess send "ucinewgame", which clears the hash
        board = game.board()
        moves = list(game.mainline_moves())[: self.max_plies]
        evals, best, phases = [], [], []
        for i in range(len(moves) + 1):
            if board.is_game_over():
                evals.append(terminal_eval(board))
                best.append(None)
            else:
                info = self.engine.analyse(board, chess.engine.Limit(depth=self.depth), game=new_game)
                evals.append(cap(info["score"].white().score(mate_score=CAP)))
                pv = info.get("pv")
                best.append(pv[0] if pv else None)
            if i < len(moves):
                phases.append(phase_of(board, i, self.endgame_material))
                board.push(moves[i])
        return evals, best, moves, phases

    def close(self):
        self.engine.quit()


def cap(value):
    return max(-CAP, min(CAP, value))


def terminal_eval(board):
    """Checkmate is a full loss for the side to move; any other finished position is a draw."""
    if board.is_checkmate():
        return -CAP if board.turn == chess.WHITE else CAP
    return 0


def non_pawn_material(board, color):
    return sum(value * len(board.pieces(piece, color)) for piece, value in PIECE_VALUES.items())


def phase_of(board, ply_index, endgame_material):
    """Opening for the first 20 plies; endgame once both sides have little non-pawn material."""
    if ply_index < OPENING_PLIES:
        return "opening"
    if all(non_pawn_material(board, c) <= endgame_material for c in (chess.WHITE, chess.BLACK)):
        return "endgame"
    return "middlegame"


def mean_or_nan(values):
    return sum(values) / len(values) if values else float("nan")


def move_quality(evals, best, moves, phases, color):
    """Centipawn loss and mistake rates for one side's analysed moves."""
    sign = 1 if color == "white" else -1
    losses = {"all": [], "opening": [], "middlegame": [], "endgame": []}
    best_hits = 0
    for i, move in enumerate(moves):
        if (i % 2 == 0) != (color == "white"):
            continue
        loss = max(0, sign * (evals[i] - evals[i + 1]))   # how much worse than before, for the mover
        losses["all"].append(loss)
        losses[phases[i]].append(loss)
        best_hits += move == best[i]
    n = len(losses["all"])
    rate = (lambda cond: sum(cond(x) for x in losses["all"]) / n) if n else (lambda cond: float("nan"))
    return {
        "acpl": mean_or_nan(losses["all"]),
        "acpl_opening": mean_or_nan(losses["opening"]),
        "acpl_middlegame": mean_or_nan(losses["middlegame"]),
        "acpl_endgame": mean_or_nan(losses["endgame"]),
        "blunder_rate": rate(lambda x: x > 300),
        "mistake_rate": rate(lambda x: 100 < x <= 300),
        "inaccuracy_rate": rate(lambda x: 50 < x <= 100),
        "best_move_rate": best_hits / n if n else float("nan"),
    }


def time_features(plies, base, inc, color):
    """Clock use for one side. On Lichess a side's first move does not use the clock."""
    clocks = [p.clock for p in plies if p.color == color]
    if not clocks:
        return {"time_used_frac": float("nan"), "mean_move_time": float("nan"),
                "move_time_cv": float("nan"), "low_clock_moves": 0}
    spent = [max(0.0, base - clocks[0])]
    spent += [max(0.0, prev - cur + inc) for prev, cur in zip(clocks, clocks[1:])]
    mean_spent = sum(spent) / len(spent)
    available = base + inc * (len(clocks) - 1)
    return {
        "time_used_frac": sum(spent) / available if available else float("nan"),
        "mean_move_time": mean_spent,
        "move_time_cv": statistics.pstdev(spent) / mean_spent if mean_spent > 0 else 0.0,
        "low_clock_moves": sum(c < LOW_CLOCK_SECONDS for c in clocks),
    }


def result_for(result, color):
    """1 for a win, 0.5 for a draw, 0 for a loss, from this player's side."""
    if result == "1/2-1/2":
        return 0.5
    if result in ("1-0", "0-1"):
        return float((result == "1-0") == (color == "white"))
    return float("nan")


def eco_group(eco):
    """Opening family A-E as 0-4; -1 when the ECO tag is missing."""
    return "ABCDE".index(eco[0]) if eco and eco[0] in "ABCDE" else -1


def player_rows(parsed, analyzer):
    """Two rows per game (white, black): features plus game_id, player and rating for training.

    The rating and player columns are labels and grouping keys; they are never model inputs.
    """
    from chessfeat.rules import parse_time_control, read_rating

    base, inc = parse_time_control(parsed.headers.get("TimeControl", "")) or (0, 0)
    evals, best, moves, phases = analyzer.analyse_game(parsed.game)
    rows = []
    for color in ("white", "black"):
        result = result_for(parsed.headers.get("Result"), color)
        row = {
            "game_id": parsed.game_id,
            "player": parsed.headers.get("White" if color == "white" else "Black", "?"),
            "rating": read_rating(parsed.headers.get("WhiteElo" if color == "white" else "BlackElo")),
            **move_quality(evals, best, moves, phases, color),
            **time_features(parsed.plies, base, inc, color),
            "lost_on_time": float(parsed.headers.get("Termination") == "Time forfeit" and result == 0),
            "color": 1.0 if color == "white" else 0.0,
            "plies": float(len(parsed.plies)),
            "result": result,
            "eco_group": float(eco_group(parsed.headers.get("ECO", ""))),
        }
        rows.append(row)
    return rows
