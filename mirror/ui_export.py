"""이해 보기 UI용 그래프 내보내기 P4 (정적 웹 페이지 + Cytoscape.js).

사용: python -m mirror.ui_export --db data/local/mirror.kuzu --out ui/
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from . import report as report_mod
from . import schema
from .store import MirrorStore


def _label(kind: str, node: dict) -> str:
    if kind == schema.OBSERVATION:
        return (node.get("text") or "")[:40]
    if kind == schema.ENTITY:
        return node.get("name", "")
    if kind == schema.CONTEXT:
        return f"{node.get('kind')}:{node.get('value')}"
    if kind == schema.PREFERENCE:
        return f"{node.get('target')}({node.get('direction')})"
    if kind == schema.CRITERION:
        return (node.get("statement") or "")[:40]
    if kind == schema.HYPOTHESIS:
        return (node.get("statement") or "")[:40]
    if kind == schema.STYLE:
        return node.get("pattern", "")
    if kind == schema.CORRECTION:
        return f"{node.get('kind')}:{node.get('target')}"
    return node.get("id", "")


def export_graph(store: MirrorStore) -> dict:
    nodes, edges = [], []
    for kind in sorted(schema.NODE_TYPES):
        if kind == schema.RESPONSE:
            continue
        for node_id in store.node_ids(kind):
            node = store.get_node(kind, node_id)
            assert node is not None
            nodes.append({"id": node_id, "kind": kind,
                          "label": _label(kind, node),
                          "status": node.get("status", ""),
                          "detail": node})
    for rel, pairs in sorted(schema.EDGE_ENDPOINTS.items()):
        for from_kind, to_kind in sorted(pairs):
            for row in store.edge_rows(rel, from_kind, to_kind):
                edges.append({"source": row[0], "target": row[1],
                              "rel": rel, "evidence": row[2]})
    hyps = []
    for hyp_id in store.node_ids(schema.HYPOTHESIS):
        hyp = store.get_hypothesis(hyp_id)
        assert hyp is not None
        supports, contradicts = [], []
        for oid in store.supporting_observations(hyp.id):
            obs = store.get_observation(oid)
            if obs is not None:
                supports.append({"id": oid, "text": obs.text,
                                 "occurred_at": obs.occurred_at})
        for oid in store.contradicting_observations(hyp.id):
            obs = store.get_observation(oid)
            if obs is not None:
                contradicts.append({"id": oid, "text": obs.text,
                                    "occurred_at": obs.occurred_at})
        hyps.append({"id": hyp.id, "statement": hyp.statement,
                     "status": hyp.status, "use_policy": hyp.use_policy,
                     "domain": hyp.domain, "supports": supports,
                     "contradicts": contradicts,
                     "alternatives": store.alternatives(hyp.id)})
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    return {"nodes": nodes, "edges": edges, "hypotheses": hyps,
            "report_markdown": report_mod.weekly_report(store, since)["markdown"],
            "generated_at": schema.utcnow_iso()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mirror UI export")
    parser.add_argument("--db", default="data/local/mirror.kuzu")
    parser.add_argument("--out", default="ui/mirror-graph.json")
    args = parser.parse_args(argv)
    store = MirrorStore(args.db)
    try:
        data = export_graph(store)
    finally:
        store.close()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(json.dumps({"path": args.out, "nodes": len(data["nodes"]),
                      "edges": len(data["edges"]),
                      "hypotheses": len(data["hypotheses"])},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
