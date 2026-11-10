"""Send games from data/valid/games.pgn to the running API, to feed the monitoring dashboards.

    python scripts/traffic.py --games 100                     # normal traffic
    python scripts/traffic.py --games 100 --drift             # only long games: shifts the input
                                                              # distribution so the drift alert fires
    python scripts/traffic.py --file other_month.pgn          # games from another file
"""

import argparse
import re
import sys
import time

import requests

sys.path.insert(0, "src")
from chessfeat.parse import iter_game_texts  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8080")
    parser.add_argument("--games", type=int, default=50)
    parser.add_argument("--drift", action="store_true", help="send only games longer than 100 plies")
    parser.add_argument("--pause", type=float, default=0.5, help="seconds between requests")
    parser.add_argument("--file", default="data/valid/games.pgn", help="PGN file to take games from")
    args = parser.parse_args()

    sent = ok = 0
    with open(args.file) as handle:
        for _, text in iter_game_texts(handle):
            if args.drift and len(re.findall(r"\d+\.\s", text)) < 50:
                continue
            response = requests.post(f"{args.api}/api/v1/predict", json={"pgn": text}, timeout=180)
            sent += 1
            ok += response.ok
            print(sent, response.status_code, response.headers.get("X-Process-Time-ms"), "ms")
            if sent == args.games:
                break
            time.sleep(args.pause)
    print(f"{ok}/{sent} requests succeeded")


if __name__ == "__main__":
    main()
