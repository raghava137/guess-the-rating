"""Stage 3 - featurize: analyse every valid game with Stockfish on a pool of Ray actors.

Each actor owns one Stockfish process (engines cannot be shared between processes), so
N actors use N CPU cores. Progress and throughput are logged while it runs.
Output: data/features/features.parquet (two rows per game) and results/featurize.json
"""

import time

import pandas as pd
import ray
from ray.util import ActorPool

from chessfeat.features import Analyzer
from chessfeat.parse import iter_game_texts
from common import ROOT, get_logger, load_params, write_json

log = get_logger("featurize")


@ray.remote(num_cpus=1)
class FeatureWorker:
    """One Stockfish engine; turns batches of game texts into feature rows."""

    def __init__(self, cfg):
        self.analyzer = Analyzer(depth=cfg["depth"], hash_mb=cfg["hash_mb"],
                                 max_plies=cfg["max_plies"], endgame_material=cfg["endgame_material"])

    def engine_version(self):
        return self.analyzer.version

    def run(self, texts):
        from chessfeat.features import player_rows
        from chessfeat.parse import parse_pgn_text
        rows, errors = [], []
        for text in texts:
            try:
                rows += player_rows(parse_pgn_text(text), self.analyzer)
            except Exception as exc:     # one bad game must not stop the stage
                errors.append(repr(exc)[:200])
        return rows, errors


def main():
    params = load_params()
    cfg = params["featurize"]
    with open(ROOT / "data" / "valid" / "games.pgn") as handle:
        texts = [text for _, text in iter_game_texts(handle)]
    batches = [texts[i:i + cfg["batch_size"]] for i in range(0, len(texts), cfg["batch_size"])]

    ray.init(num_cpus=cfg["workers"], include_dashboard=cfg["ray_dashboard"], object_store_memory=500 * 2**20,
             dashboard_host="0.0.0.0", log_to_driver=False)
    actors = [FeatureWorker.remote(cfg) for _ in range(cfg["workers"])]
    engine_version = ray.get(actors[0].engine_version.remote())
    start = time.time()
    rows, errors, done = [], [], 0
    for batch_rows, batch_errors in ActorPool(actors).map_unordered(lambda a, b: a.run.remote(b), batches):
        rows += batch_rows
        errors += batch_errors
        done += len(batch_rows) // 2 + len(batch_errors)
        rate = done / (time.time() - start)
        if done % 1000 < cfg["batch_size"]:
            log.info("%d / %d games (%.2f games/s)", done, len(texts), rate)
    ray.shutdown()
    seconds = time.time() - start

    out_path = ROOT / "data" / "features" / "features.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(out_path, index=False)
    write_json("results/featurize.json", {
        "games": len(texts), "rows": len(rows), "errors": len(errors), "error_samples": errors[:5],
        "workers": cfg["workers"], "depth": cfg["depth"], "stockfish": engine_version,
        "seconds": round(seconds, 1), "games_per_sec": round(len(texts) / max(seconds, 1e-9), 2),
    })
    log.info("%d rows from %d games in %.0f s (%d errors)", len(rows), len(texts), seconds, len(errors))


if __name__ == "__main__":
    main()
