"""Stage 1 - ingest: stream one month of Lichess games and keep a rating-stratified blitz sample.

Only tag lines are inspected here (fast). Games are never fully downloaded or decompressed to
disk: the stream stops as soon as every rating band is full or the read cap is reached.
Output: data/raw/games.pgn and results/ingest.json
"""

import io
import time

import requests
import zstandard

from chessfeat.parse import iter_game_texts
from chessfeat.rules import band_names, band_of, header_problems, read_rating
from common import ROOT, get_logger, load_params, write_json

log = get_logger("ingest")


def open_lines(source):
    """Open a URL or local path (plain .pgn or .pgn.zst) as a stream of text lines."""
    if source.startswith(("http://", "https://")):
        response = requests.get(source, stream=True, timeout=60)
        response.raise_for_status()
        raw = response.raw
    else:
        raw = open(source, "rb")
    if source.endswith(".zst"):
        raw = zstandard.ZstdDecompressor().stream_reader(raw)
    return io.TextIOWrapper(raw, encoding="utf-8", errors="replace")


def sample_games(lines, out, edges, quota, max_read, progress=None):
    """Write blitz games that pass the header rules until each band has `quota` games.

    Returns (games_read, kept_per_band, rejected_per_reason).
    """
    kept = {name: 0 for name in band_names(edges)}
    rejected = {}
    read = 0
    for headers, text in iter_game_texts(lines):
        if read >= max_read or min(kept.values()) >= quota:
            break
        read += 1
        problems = header_problems(headers)
        if problems:
            for reason in problems:
                rejected[reason] = rejected.get(reason, 0) + 1
            continue
        avg = (read_rating(headers["WhiteElo"]) + read_rating(headers["BlackElo"])) / 2
        band = band_of(avg, edges)
        if kept[band] >= quota:
            continue
        out.write(text.rstrip("\n") + "\n\n")
        kept[band] += 1
        if progress and read % 100_000 == 0:
            progress(read, sum(kept.values()))
    return read, kept, rejected


def main():
    params = load_params()
    cfg = params["ingest"]
    source = cfg["source"].format(month=params["month"])
    out_path = ROOT / "data" / "raw" / "games.pgn"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("streaming %s", source)
    start = time.time()

    def progress(read, kept):
        rate = read / (time.time() - start)
        log.info("read %d games, kept %d (%.0f games/s)", read, kept, rate)

    with open_lines(source) as lines, open(out_path, "w") as out:
        read, kept, rejected = sample_games(
            lines, out, params["bands"], cfg["games_per_band"], cfg["max_games_read"], progress)
    seconds = time.time() - start
    report = {
        "month": params["month"], "source": source, "games_read": read,
        "games_kept": sum(kept.values()), "kept_per_band": kept, "rejected": rejected,
        "seconds": round(seconds, 1), "games_read_per_sec": round(read / max(seconds, 1e-9), 1),
    }
    write_json("results/ingest.json", report)
    log.info("kept %d of %d games read in %.0f s", report["games_kept"], read, seconds)
    short = [band for band, n in kept.items() if n < cfg["games_per_band"]]
    if short:
        log.warning("bands below quota: %s", short)


if __name__ == "__main__":
    main()
