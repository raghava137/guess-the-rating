"""Stage 2 - validate: fully parse every ingested game in parallel Ray tasks and drop bad ones.

Each task validates one chunk; duplicates across chunks are removed when results are merged.
Output: data/valid/games.pgn and reports/validation.json (rejections counted by reason)
"""

import time

import ray

from chessfeat.parse import iter_game_texts, parse_pgn_text
from chessfeat.rules import GameValidator
from common import ROOT, get_logger, load_params, write_json

log = get_logger("validate")


@ray.remote
def validate_chunk(texts):
    """Validate one chunk of game texts; returns ([(game_id, text)], counts)."""
    validator = GameValidator()
    valid = []
    for text in texts:
        try:
            parsed = parse_pgn_text(text)
        except ValueError:
            validator.counts["unreadable"] = validator.counts.get("unreadable", 0) + 1
            validator.counts["checked"] += 1
            continue
        if validator.check(parsed):
            valid.append((parsed.game_id, text))
    return valid, validator.counts


def merge(results):
    """Combine chunk results, dropping games whose ID was already kept by another chunk."""
    seen, games, counts = set(), [], {}
    for valid, chunk_counts in results:
        for key, value in chunk_counts.items():
            counts[key] = counts.get(key, 0) + value
        for game_id, text in valid:
            if game_id in seen:
                counts["duplicate"] = counts.get("duplicate", 0) + 1
                counts["valid"] -= 1
                continue
            seen.add(game_id)
            games.append(text)
    return games, counts


def main():
    params = load_params()
    workers = params["validate"]["workers"]
    with open(ROOT / "data" / "raw" / "games.pgn") as handle:
        texts = [text for _, text in iter_game_texts(handle)]
    start = time.time()
    ray.init(num_cpus=workers, include_dashboard=False, log_to_driver=False,
             object_store_memory=500 * 2**20)    # 500 MB is plenty for batches of PGN text
    size = max(1, len(texts) // (workers * 4) + 1)
    chunks = [texts[i:i + size] for i in range(0, len(texts), size)]
    games, counts = merge(ray.get([validate_chunk.remote(chunk) for chunk in chunks]))
    ray.shutdown()
    seconds = time.time() - start

    out_path = ROOT / "data" / "valid" / "games.pgn"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(text.rstrip("\n") + "\n\n" for text in games))
    rate = len(texts) / max(seconds, 1e-9)
    write_json("reports/validation.json", {
        "games_in": len(texts), "games_valid": len(games), "counts": counts,
        "workers": workers, "seconds": round(seconds, 1), "games_per_sec": round(rate, 1),
    })
    log.info("%d of %d games valid (%.0f games/s with %d workers)", len(games), len(texts), rate, workers)


if __name__ == "__main__":
    main()
