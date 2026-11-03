"""데모 DB에서 "기록 -> 지식 베이스 -> 시나리오 결과"를 JSON으로 뽑는다.

가상 인물 기록(data/demo/persona_observations.jsonl)을 추출기로 채운 DB를 읽어서
(1) 기록마다 무엇이 뽑혔는지, (2) 지식 베이스가 어떻게 자랐는지,
(3) 승격된 가설과 프로필, (4) 시나리오 실행 결과를 모은다.

실행: META_API_KEY=... python -m mirror.demo_report --db data/demo/mirror.kuzu \
        --mock-db data/demo/mock.kuzu --out data/demo/results.json
"""

from __future__ import annotations

import argparse
import json
import os

from . import hypotheses as hyp_mod
from . import profile as profile_mod
from . import router as router_mod
from . import scenarios as scen_mod
from . import schema
from . import search as search_mod
from .store import MirrorStore

SCENARIO_RUNS = [
    ("doc_draft", {"topic": "How I put together this month's DJ sets", "kind": "Blog post",
                   "points": "Why I lean on repetitive tracks\nCalmer tracks on rainy nights"}),
    ("music_recommend", {"situation": "Mixing late on a rainy night",
                         "candidates": "Nuvole Bianche\nStrobe\nOpus\nAround the World",
                         "avoid": ""}),
    ("day_plan", {"state": "Tired",
                  "tasks": "Finish the blog post\nReply to my manager\nPractice the DJ set\nWorkout\nGrocery shopping"}),
    ("choice_help", {"request": "Dinner order on a tired night",
                     "options": "My usual fried chicken place\nThe new noodle shop"}),
    ("code_review", {"subject": "A pull request that refactors the billing module"}),
    ("deadline_pressure", {"situation": "The release moved up by a week", "deadline": "Friday noon"}),
]


def per_observation(store: MirrorStore) -> list[dict]:
    rows = store._rows(
        "MATCH (o:Observation) RETURN o.id, o.source_id, o.occurred_at, o.text "
        "ORDER BY o.occurred_at")
    out = []
    for oid, sid, at, text in rows:
        ents = [r[0] for r in store._rows(
            "MATCH (e:Entity)-[:OBSERVED_IN]->(o:Observation {id: $i}) RETURN e.name",
            {"i": oid})]
        ctxs = [r[0] for r in store._rows(
            "MATCH (c:Context)-[:OBSERVED_IN]->(o:Observation {id: $i}) RETURN c.value",
            {"i": oid})]
        hyps = [r[0] for r in store._rows(
            "MATCH (o:Observation {id: $i})-[:SUPPORTS]->(h:Hypothesis) RETURN h.statement",
            {"i": oid})]
        out.append({"id": oid, "source_id": sid, "occurred_at": at, "text": text,
                    "entities": sorted(set(ents)), "contexts": sorted(set(ctxs)),
                    "hypotheses": sorted(set(hyps))})
    return out


def growth(rows: list[dict]) -> list[dict]:
    seen = {"entities": set(), "contexts": set(), "hypotheses": set()}
    out = []
    for n, r in enumerate(rows, 1):
        for k in seen:
            seen[k].update(r[k])
        out.append({"n": n, **{k: len(v) for k, v in seen.items()}})
    return out


def kb_counts(store: MirrorStore) -> dict:
    return {k: (store._one(f"MATCH (n:{k}) RETURN count(*)") or [0])[0]
            for k in ("Observation", "Entity", "Context", "Hypothesis",
                      "Preference", "Style", "Criterion")}


def hypotheses_detail(store: MirrorStore) -> list[dict]:
    out = []
    for h in profile_mod.ui_export.export_graph(store)["hypotheses"]:
        out.append({"statement": h["statement"], "status": h["status"],
                    "domain": h["domain"], "supports": len(h["supports"]),
                    "contradicts": len(h["contradicts"]),
                    "evidence": [s["text"] for s in h["supports"]][:3]})
    out.sort(key=lambda x: -x["supports"])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--mock-db", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    from .app import chat_generate  # LLM 백엔드 자동 선택

    store = MirrorStore(args.db)
    promoted = hyp_mod.promote_candidates(store)
    nightly = hyp_mod.nightly_maintenance(store)
    base = os.path.splitext(args.db)[0]
    index = search_mod.HybridIndex.build(
        store, vector_path=base + ".vectors.json", bm25_path=base + ".bm25.json")
    rows = per_observation(store)
    result = {
        "persona": "가상 인물 (data/demo/README.md)",
        "llm": {"counts": kb_counts(store), "growth": growth(rows),
                "observations": rows, "hypotheses": hypotheses_detail(store),
                "promoted": promoted, "nightly": nightly},
        "profile": profile_mod.build_profile(store),
        "index_mode": index.mode,
        "scenarios": [],
    }
    router = router_mod.MockRouter()
    for sid, inputs in SCENARIO_RUNS:
        out = scen_mod.run_scenario(
            store, router, index, sid, inputs,
            lambda p: chat_generate(p, timeout=240))
        result["scenarios"].append({"id": sid, "name": scen_mod.SCENARIOS[sid].name,
                                    "inputs": inputs, **out})
    if args.mock_db and os.path.exists(args.mock_db):
        mstore = MirrorStore(args.mock_db)
        hyp_mod.promote_candidates(mstore)
        mrows = per_observation(mstore)
        result["mock"] = {"counts": kb_counts(mstore), "growth": growth(mrows),
                          "hypotheses": hypotheses_detail(mstore)}
        mstore.close()
    store.close()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(schema.to_jsonable(result), f, ensure_ascii=False, indent=1)
    print("saved", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
