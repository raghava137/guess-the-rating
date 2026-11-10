"""Load test: N concurrent clients send real games; prints throughput and latency percentiles.

    python scripts/load_test.py --clients 4 --requests 40
"""

import argparse
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

sys.path.insert(0, "src")
from chessfeat.parse import iter_game_texts  # noqa: E402


def one_request(api, text):
    start = time.perf_counter()
    try:
        status = requests.post(f"{api}/api/v1/predict", json={"pgn": text}, timeout=300).status_code
    except requests.RequestException:
        status = 0
    return status, time.perf_counter() - start


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8080")
    parser.add_argument("--clients", type=int, default=4)
    parser.add_argument("--requests", type=int, default=40)
    args = parser.parse_args()

    with open("data/valid/games.pgn") as handle:
        texts = [text for _, text in iter_game_texts(handle)][: args.requests]
    texts = (texts * (args.requests // max(len(texts), 1) + 1))[: args.requests]

    start = time.perf_counter()
    with ThreadPoolExecutor(args.clients) as pool:
        results = list(pool.map(lambda t: one_request(args.api, t), texts))
    elapsed = time.perf_counter() - start

    latencies = sorted(seconds for status, seconds in results if status == 200)
    codes = {}
    for status, _ in results:
        codes[status] = codes.get(status, 0) + 1
    print(f"{len(results)} requests, {args.clients} clients, {elapsed:.1f} s, {len(results) / elapsed:.2f} req/s")
    print("status codes:", codes)
    if latencies:
        p95 = latencies[max(0, int(0.95 * len(latencies)) - 1)]
        print(f"latency p50 {statistics.median(latencies):.2f} s, p95 {p95:.2f} s, max {latencies[-1]:.2f} s")


if __name__ == "__main__":
    main()
