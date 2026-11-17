"""PulseDash entry point.

Usage:
    python run.py            collect every enabled source, then render the dashboard
    python run.py --collect  collect only
    python run.py --report   render only, using what is already in the database
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import sources
import storage

ROOT = Path(__file__).resolve().parent


def load_config() -> dict:
    with (ROOT / "config.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


def collect(config: dict, store: storage.Store) -> dict:
    http_cfg = config.get("http") or {}
    http = sources.HttpClient(
        timeout=int(http_cfg.get("timeout", 15)),
        retries=int(http_cfg.get("retries", 3)),
        backoff_ms=int(http_cfg.get("backoff_ms", 800)),
        min_interval_ms=int(http_cfg.get("min_interval_ms", 300)),
    )
    collectors = sources.build_collectors(config, http)

    started = storage.now_ts()
    ok_sources = 0
    failed: list[str] = []
    inserted = 0
    results = []

    for collector in collectors:
        begin = time.monotonic()
        try:
            points = collector.fetch()
            gained = store.add_points(points)
            elapsed = int((time.monotonic() - begin) * 1000)
            results.append({"name": collector.name, "ok": True, "points": len(points),
                            "inserted": gained, "elapsed_ms": elapsed, "error": ""})
            ok_sources += 1
            inserted += gained
            print("[ok]   %-8s points=%-4d new=%-4d %dms" % (
                collector.name, len(points), gained, elapsed))
        except Exception as exc:  # noqa: BLE001 - source isolation is the point
            elapsed = int((time.monotonic() - begin) * 1000)
            message = "%s: %s" % (type(exc).__name__, str(exc)[:200])
            results.append({"name": collector.name, "ok": False, "points": 0,
                            "inserted": 0, "elapsed_ms": elapsed, "error": message})
            failed.append(collector.name)
            print("[fail] %-8s %s (%dms)" % (collector.name, message, elapsed))

    finished = storage.now_ts()
    detail = json.dumps(results, ensure_ascii=False)
    store.log_run(started, finished, ok_sources, len(failed), inserted, detail)

    summary = {"ok_sources": ok_sources, "failed": failed, "inserted": inserted,
               "results": results, "stats": store.stats()}
    print("[run]  ok=%d failed=%d new_rows=%d total=%d" % (
        ok_sources, len(failed), inserted, summary["stats"]["points"]))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="PulseDash collector and dashboard")
    parser.add_argument("--collect", action="store_true", help="collect only")
    parser.add_argument("--report", action="store_true", help="render only")
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()

    config = load_config()
    output_dir = ROOT / config.get("output_dir", "out")
    store = storage.Store(output_dir / "pulsedash.sqlite3")

    try:
        if not args.report:
            collect(config, store)
        if not args.collect:
            import report
            paths = report.render(config, store, output_dir)
            for path in paths:
                print("[out]  %s" % path)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
