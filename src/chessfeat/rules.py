"""Time-control and validation rules shared by the pipeline and the backend.

Lichess sorts games into speed classes by an estimated duration:
base seconds + 40 x increment seconds. Blitz is 180 to 479 seconds.
Source: lichess-org/scalachess, core/src/main/scala/Speed.scala and Clock.scala.
"""

BLITZ_MIN_SECONDS = 180
BLITZ_MAX_SECONDS = 479
MIN_PLIES = 20                      # 10 moves each; shorter games carry little signal
RATING_RANGE = (400, 3500)          # anything outside is treated as a corrupt header
ALLOWED_TERMINATIONS = {"Normal", "Time forfeit"}


def parse_time_control(tc):
    """Turn a PGN TimeControl such as "180+2" into (base_seconds, increment_seconds).

    Returns None for "-" (correspondence) or anything unreadable.
    """
    try:
        base, inc = tc.split("+")
        return int(base), int(inc)
    except (AttributeError, ValueError):
        return None


def estimated_seconds(base, inc):
    """Lichess's estimate of a game's total duration in seconds."""
    return base + 40 * inc


def is_blitz(tc):
    """True if a TimeControl string falls in Lichess's blitz class."""
    parsed = parse_time_control(tc)
    if parsed is None:
        return False
    return BLITZ_MIN_SECONDS <= estimated_seconds(*parsed) <= BLITZ_MAX_SECONDS


def is_rated(event):
    """True for rated games. The bulk dump writes "Rated Blitz game", the export API "rated blitz game"."""
    return bool(event) and event.lower().startswith("rated")


def is_standard(headers):
    """Bulk dumps of standard chess omit the Variant tag; the export API writes "Standard"."""
    return headers.get("Variant", "Standard") == "Standard"


def read_rating(value):
    """Parse an Elo header; returns None when missing or "?"."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def header_problems(headers):
    """Return the reasons a game fails the header rules (empty list = passes).

    Cheap enough to run on raw header text during ingest, before any move is parsed.
    """
    problems = []
    if not is_rated(headers.get("Event", "")):
        problems.append("not_rated")
    if not is_standard(headers):
        problems.append("not_standard")
    if not is_blitz(headers.get("TimeControl", "")):
        problems.append("not_blitz")
    if "BOT" in (headers.get("WhiteTitle"), headers.get("BlackTitle")):
        problems.append("bot_player")
    for side in ("WhiteElo", "BlackElo"):
        rating = read_rating(headers.get(side))
        if rating is None or not RATING_RANGE[0] <= rating <= RATING_RANGE[1]:
            problems.append("bad_rating")
            break
    if headers.get("Termination") not in ALLOWED_TERMINATIONS:
        problems.append("bad_termination")
    return problems


def move_problems(plies, base_seconds):
    """Return the reasons a game's moves fail the rules (empty list = passes).

    plies: list of Ply objects from parse.extract_plies.
    base_seconds: starting time from the TimeControl tag.
    """
    problems = []
    if len(plies) < MIN_PLIES:
        problems.append("too_short")
    if any(p.clock is None for p in plies):
        problems.append("missing_clock")
        return problems  # the berserk check needs clocks
    # Lichess adds no increment on each side's first move, so the first reading equals the
    # starting time unless the player berserked in a tournament (which halves the clock).
    first_white = next((p.clock for p in plies if p.color == "white"), None)
    first_black = next((p.clock for p in plies if p.color == "black"), None)
    if first_white != base_seconds or first_black != base_seconds:
        problems.append("berserk_or_odd_clock")
    return problems


def serving_problems(parsed):
    """Rules for games sent to the live app.

    Ratings, rated status and termination are not model inputs, so casual or unrated games
    are allowed; everything the features depend on is still checked.
    """
    headers = parsed.headers
    problems = []
    if not is_standard(headers):
        problems.append("not_standard")
    tc = headers.get("TimeControl", "")
    if not is_blitz(tc):
        problems.append("not_blitz")
        return problems
    return problems + move_problems(parsed.plies, parse_time_control(tc)[0])


def band_names(edges):
    """Rating band labels for sorted edges, e.g. [800, 1000] -> ["<800", "800-999", "1000+"]."""
    names = [f"<{edges[0]}"]
    names += [f"{lo}-{hi - 1}" for lo, hi in zip(edges, edges[1:])]
    return names + [f"{edges[-1]}+"]


def band_of(rating, edges):
    """Name of the band a rating falls in."""
    names = band_names(edges)
    for i, edge in enumerate(edges):
        if rating < edge:
            return names[i]
    return names[-1]


class GameValidator:
    """Applies every rule to parsed games and counts rejections by reason.

    Stateful on purpose: it remembers game IDs to drop duplicates and keeps the counts
    that go into the validation report.
    """

    def __init__(self):
        self.seen_ids = set()
        self.counts = {"checked": 0, "valid": 0}

    def problems(self, parsed):
        """Return every reason this ParsedGame is rejected; an empty list means valid."""
        found = header_problems(parsed.headers)
        if parsed.game_id in self.seen_ids:
            found.append("duplicate")
        parsed_tc = parse_time_control(parsed.headers.get("TimeControl", ""))
        if parsed_tc is not None:
            found += move_problems(parsed.plies, parsed_tc[0])
        return found

    def check(self, parsed):
        """Validate one game, update the counts, and return True if it is valid."""
        found = self.problems(parsed)
        self.counts["checked"] += 1
        for reason in found:
            self.counts[reason] = self.counts.get(reason, 0) + 1
        if found:
            return False
        self.seen_ids.add(parsed.game_id)
        self.counts["valid"] += 1
        return True
