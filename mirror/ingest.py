"""일괄 수집: 과거 기록 JSONL → observe 파이프라인 (계획서 P1).

입력 JSONL 한 줄:
{"source_id": "...", "text": "...", "occurred_at": "...",
 "raw_ref": "...", "visibility": "private"}

실행:
  python -m mirror.ingest data/sample_observations.jsonl --db data/local/mirror.kuzu
"""

from __future__ import annotations

import argparse
import json
import sys

from . import extract as ex
from . import observe as observe_mod
from .store import MirrorStore


def ingest_file(path: str, store: MirrorStore, extractor: ex.Extractor,
               dry_run: bool = False, limit: int = 0) -> dict:
    stats = {"ok": 0, "duplicate": 0, "failed": 0, "nodes": 0, "edges": 0,
             "failures": []}
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            if limit and lineno > limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                stats["failed"] += 1
                stats["failures"].append({"line": lineno, "error": f"json: {e}"})
                continue
            source_id = record.get("source_id", "")
            event = {
                "text": record.get("text", ""),
                "occurred_at": record.get("occurred_at", ""),
                "raw_ref": record.get("raw_ref", f"{path}:{lineno}"),
                "visibility": record.get("visibility", "private"),
            }
            if dry_run:
                try:
                    result = extractor.extract(ex.ObservationInput(
                        source_id=source_id, text=event["text"],
                        occurred_at=event["occurred_at"] or "unknown",
                        raw_ref=event["raw_ref"],
                        visibility=event["visibility"]))
                    errors = ex.validate_extraction(result)
                except (ex.ExtractionError, ex.NotConfigured) as e:
                    errors = [str(e)]
                if errors:
                    stats["failed"] += 1
                    stats["failures"].append(
                        {"line": lineno, "source_id": source_id,
                         "error": "; ".join(errors)})
                else:
                    stats["ok"] += 1
                    stats["nodes"] += len(result.nodes)
                    stats["edges"] += len(result.edges)
                continue
            res = observe_mod.observe(store, extractor, source_id, event,
                                      store.get_revision())
            if not res.ok:
                stats["failed"] += 1
                stats["failures"].append(
                    {"line": lineno, "source_id": source_id, "error": res.error})
            elif res.duplicate:
                stats["duplicate"] += 1
            else:
                stats["ok"] += 1
                stats["nodes"] += res.nodes_added
                stats["edges"] += res.edges_added
    stats["revision"] = store.get_revision()
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mirror batch ingest (JSONL)")
    parser.add_argument("input", help="input JSONL path")
    parser.add_argument("--db", default="data/local/mirror.kuzu",
                        help="Kuzu DB directory")
    parser.add_argument("--extractor", default="spark",
                        choices=["spark", "Muse", "ollama"])
    parser.add_argument("--model", default="", help="extractor model override")
    parser.add_argument("--dry-run", action="store_true",
                        help="extract+validate only, no writes")
    parser.add_argument("--limit", type=int, default=0,
                        help="max lines to process (0 = all)")
    args = parser.parse_args(argv)

    kwargs = {}
    if args.model:
        kwargs["model"] = args.model
    try:
        extractor = ex.get_extractor(args.extractor, **kwargs)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    store = MirrorStore(args.db)
    try:
        stats = ingest_file(args.input, store, extractor,
                            dry_run=args.dry_run, limit=args.limit)
    finally:
        store.close()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
