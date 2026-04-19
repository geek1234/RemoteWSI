from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local WSI viewer endpoint benchmark.")
    parser.add_argument("--base-url", required=True, help="Example: http://127.0.0.1:8011")
    parser.add_argument("--slide-id", default="", help="Optional slide id to benchmark")
    parser.add_argument("--iterations", type=int, default=25, help="Measured request count per endpoint")
    parser.add_argument("--warmup", type=int, default=3, help="Warm-up request count per endpoint")
    return parser.parse_args()


def fetch_bytes(url: str, timeout: float = 30.0) -> tuple[int, bytes, float]:
    req = urllib.request.Request(url, method="GET")
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        status = int(resp.status)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return status, body, elapsed_ms


def fetch_json(url: str, timeout: float = 30.0) -> tuple[int, Any, float]:
    status, body, elapsed_ms = fetch_bytes(url, timeout=timeout)
    return status, json.loads(body.decode("utf-8")), elapsed_ms


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(math.ceil(ratio * len(ordered)) - 1)
    idx = max(0, min(idx, len(ordered) - 1))
    return ordered[idx]


def summarize(values: list[float]) -> dict[str, float]:
    return {
        "count": float(len(values)),
        "avg_ms": statistics.mean(values),
        "p50_ms": statistics.median(values),
        "p95_ms": percentile(values, 0.95),
        "min_ms": min(values),
        "max_ms": max(values),
    }


def bench(name: str, url: str, iterations: int, warmup: int) -> dict[str, float]:
    samples: list[float] = []
    total = warmup + iterations
    for idx in range(total):
        status, _, elapsed_ms = fetch_bytes(url)
        if status != 200:
            raise RuntimeError(f"{name} status={status} url={url}")
        if idx >= warmup:
            samples.append(elapsed_ms)
    return summarize(samples)


def find_slide_id(base_url: str, slide_id: str) -> str:
    if slide_id:
        return slide_id

    url = f"{base_url.rstrip('/')}/api/slides?limit=1"
    status, payload, _ = fetch_json(url)
    if status != 200:
        raise RuntimeError(f"/api/slides status={status}")
    items = payload.get("items", [])
    if not items:
        raise RuntimeError("No slides found for benchmark.")
    return str(items[0]["id"])


def deepzoom_top_level(xml_bytes: bytes) -> int:
    root = ET.fromstring(xml_bytes.decode("utf-8"))
    width = None
    height = None
    for node in root.iter():
        if str(node.tag).endswith("Size"):
            width = int(node.attrib.get("Width", "0"))
            height = int(node.attrib.get("Height", "0"))
            break
    if not width or not height:
        raise RuntimeError("Failed to parse DZI size.")
    return int(math.ceil(math.log2(max(width, height)))) if max(width, height) > 1 else 0


def print_metric(name: str, stats: dict[str, float]) -> None:
    print(
        f"{name}: avg={stats['avg_ms']:.2f}ms p50={stats['p50_ms']:.2f}ms "
        f"p95={stats['p95_ms']:.2f}ms min={stats['min_ms']:.2f}ms max={stats['max_ms']:.2f}ms n={int(stats['count'])}"
    )


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    if args.iterations < 1:
        raise RuntimeError("--iterations must be >= 1")
    if args.warmup < 0:
        raise RuntimeError("--warmup must be >= 0")

    slide_id = find_slide_id(base_url, args.slide_id)
    slide_id_encoded = urllib.parse.quote(slide_id, safe="")
    print(f"TARGET_SLIDE={slide_id}")

    meta_url = f"{base_url}/api/meta/{slide_id_encoded}"
    thumb_url = f"{base_url}/api/thumb/{slide_id_encoded}"
    dzi_url = f"{base_url}/dzi/{slide_id_encoded}.dzi"

    # Ensure DZI endpoint is valid before tile benchmark.
    dzi_status, dzi_body, _ = fetch_bytes(dzi_url)
    if dzi_status != 200:
        raise RuntimeError(f"DZI status={dzi_status}")
    tile_level = deepzoom_top_level(dzi_body)
    tile_url = f"{base_url}/dzi/{slide_id_encoded}_files/{tile_level}/0_0.jpeg"

    meta_stats = bench("meta", meta_url, args.iterations, args.warmup)
    thumb_stats = bench("thumb", thumb_url, args.iterations, args.warmup)
    dzi_stats = bench("dzi", dzi_url, args.iterations, args.warmup)
    tile_stats = bench("tile", tile_url, args.iterations, args.warmup)

    print_metric("meta", meta_stats)
    print_metric("thumb", thumb_stats)
    print_metric("dzi", dzi_stats)
    print_metric("tile", tile_stats)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, urllib.error.URLError, json.JSONDecodeError, ET.ParseError) as exc:
        print(f"BENCHMARK_FAILED: {exc}")
        sys.exit(1)
