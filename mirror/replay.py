"""리플레이 하네스 P3: 시간순 공개·예측 고정·상태 갱신.

에피소드를 occurred_at 순으로 공개한다. 각 에피소드에서 에이전트는 예측을
고정한 뒤에야 정답을 보고, 정답은 새 관찰로 공개되어 다음 예측에 쓰인다.
분모·보류·미관측을 별도로 보고하고, 작은 표본에서 우위를 주장하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass, field

from . import agents as agents_mod
from . import extract as ex
from . import hypotheses as hyp_mod
from . import observe as observe_mod
from . import schema
from . import simulate as simulate_mod
from .store import MirrorStore


@dataclass
class Episode:
    id: str
    occurred_at: str
    text: str
    options: list[str]
    actual: str | None
    conditions: list[dict] = field(default_factory=list)


def load_episodes(path: str) -> list[Episode]:
    episodes = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            options = list(d.get("options", []))
            actual = d.get("actual")
            if not options:
                raise ValueError(f"{path}:{lineno}: empty options")
            if actual is not None and actual not in options:
                raise ValueError(f"{path}:{lineno}: actual not in options")
            episodes.append(Episode(
                id=str(d.get("episode_id", f"ep-{lineno}")),
                occurred_at=str(d.get("occurred_at", "")),
                text=str(d.get("text", "")),
                options=options, actual=actual,
                conditions=list(d.get("conditions", []))))
    episodes.sort(key=lambda e: e.occurred_at)
    return episodes


def disclosure_text(ep: Episode) -> str:
    return f"{ep.text} 선택: {ep.actual}"


def run_replay(episode_path: str, extractor_name: str = "mock",
               agent_names: tuple[str, ...] = ("A", "M1", "M2"),
               db_path: str | None = None, promote: bool = True,
               use_index: bool = True) -> dict:
    episodes = load_episodes(episode_path)
    extractor = ex.get_extractor(extractor_name)
    own_db = db_path is None
    if own_db:
        db_path = tempfile.mkdtemp() + "/replay.kuzu"
    assert db_path is not None
    store = MirrorStore(db_path)
    index = None
    if use_index:
        from . import search as search_mod  # 지연 임포트

        index = search_mod.HybridIndex.build(store)
    agents = [agents_mod.get_agent(name, index=index) for name in agent_names]
    try:
        return _run(store, episodes, extractor, agents, promote, index)
    finally:
        store.close()


def _run(store, episodes, extractor, agents, promote, index) -> dict:
    per_agent: dict[str, dict] = {
        a.name: {"episodes": [], "hits": 0, "misses": 0, "abstains": 0,
                 "unobserved": 0, "errors": 0} for a in agents
    }
    disclosures = 0
    for ep in episodes:
        as_of = schema.utcnow_iso()
        for agent in agents:
            rec = per_agent[agent.name]
            try:
                pred = agent.predict(ep, store, as_of)
            except Exception as e:  # 한 에이전트의 실패가 하네스를 멈추지 않음
                rec["errors"] += 1
                rec["episodes"].append({"episode": ep.id, "error": str(e)[:200]})
                continue
            entry: dict = {"episode": ep.id, "choice": pred.choice,
                           "rationale": pred.rationale,
                           "evidence": pred.evidence_ids}
            if ep.actual is None:
                rec["unobserved"] += 1
            elif pred.choice is None:
                rec["abstains"] += 1
            elif pred.choice == ep.actual:
                rec["hits"] += 1
            else:
                rec["misses"] += 1
            rec["episodes"].append(entry)
            if ep.actual is not None:
                for hid, voted in pred.hyp_votes.items():
                    store.log_prediction(hid, ep.id, voted, ep.actual)
        if ep.actual is not None:
            res = observe_mod.observe(
                store, extractor, f"ep:{ep.id}",
                {"text": disclosure_text(ep), "occurred_at": ep.occurred_at},
                store.get_revision())
            if res.ok and not res.duplicate:
                disclosures += 1
                if index is not None:
                    index.refresh(store)
            if promote:
                hyp_mod.promote_candidates(store)
    report = {"agents": {}, "disclosures": disclosures,
              "n_episodes": len(episodes)}
    for name, rec in per_agent.items():
        predicted = rec["hits"] + rec["misses"]
        acc = round(rec["hits"] / predicted, 4) if predicted else None
        report["agents"][name] = {
            "episodes": rec["episodes"], "hits": rec["hits"],
            "misses": rec["misses"], "abstains": rec["abstains"],
            "unobserved": rec["unobserved"], "errors": rec["errors"],
            "predicted": predicted, "accuracy": acc,
        }
    base = report["agents"].get("A", {}).get("accuracy")
    for name, stats in report["agents"].items():
        if name == "A" or stats["accuracy"] is None or base is None:
            stats["delta_vs_A"] = None
        else:
            stats["delta_vs_A"] = round(stats["accuracy"] - base, 4)
    report["note"] = ("작은 표본에서는 통계적 우위를 주장하지 않는다. "
                      "A 대비 차이와 표본 수를 그대로 보고.")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mirror replay harness")
    parser.add_argument("episodes", help="episodes JSONL path")
    parser.add_argument("--extractor", default="mock",
                        choices=["mock", "spark", "Muse", "ollama"])
    parser.add_argument("--agents", default="A,M1,M2")
    parser.add_argument("--db", default="")
    parser.add_argument("--no-promote", action="store_true")
    args = parser.parse_args(argv)
    report = run_replay(
        args.episodes, args.extractor,
        tuple(a for a in args.agents.split(",") if a),
        args.db or None, promote=not args.no_promote)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
