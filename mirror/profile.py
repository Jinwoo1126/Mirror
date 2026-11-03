"""나의 프로필: 그래프에 쌓인 이해를 사람이 읽는 한 장으로 모은다.

진단·단정 문장은 만들지 않는다. 가설은 근거 건수와 함께 "기록에서 반복된
것"으로만 보여주고, 글 스타일은 저장된 관찰 텍스트에서 센 값만 쓴다.
"""

from __future__ import annotations

import re

from . import schema
from .store import MirrorStore
from . import ui_export

_SENT_SPLIT = re.compile(r"(?<=[.!?。])\s+")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if s.strip()]


def _ending(sentence: str) -> str:
    s = sentence.rstrip(".!?。 ")
    if s.endswith(("니다", "습니다", "입니다")):
        return "Formal (-ㅂ니다)"
    if s.endswith("요"):
        return "Polite (-요)"
    if s.endswith("다"):
        return "Plain (-다)"
    return "Other"


def writing_stats(texts: list[str]) -> dict:
    """관찰 텍스트에서 센 문장 길이와 어미 분포."""
    sents = [s for t in texts for s in _sentences(t)]
    if not sents:
        return {"observations": len(texts), "sentences": 0}
    lengths = [len(s) for s in sents]
    endings: dict[str, int] = {}
    for s in sents:
        e = _ending(s)
        endings[e] = endings.get(e, 0) + 1
    return {
        "observations": len(texts),
        "sentences": len(sents),
        "avg_sentence_chars": round(sum(lengths) / len(lengths), 1),
        "longest_sentence_chars": max(lengths),
        "avg_observation_chars": round(
            sum(len(t or "") for t in texts) / len(texts), 1),
        "endings": [{"name": k, "count": v, "ratio": round(v / len(sents), 3)}
                    for k, v in sorted(endings.items(), key=lambda kv: -kv[1])],
    }


def _evidence_label(n: int) -> str:
    return "Candidate (1 evidence)" if n <= 1 else f"Repeated ({n} evidence)"


def build_profile(store: MirrorStore) -> dict:
    obs_rows = store.observation_texts()
    texts = [r[0] or "" for r in obs_rows]
    dates = [r[1] for r in obs_rows if r[1] and r[1] != "unknown"]

    tendencies = []
    for h in ui_export.export_graph(store)["hypotheses"]:
        if h["status"] not in ("active", "candidate"):
            continue
        n = len(h["supports"])
        tendencies.append({
            "id": h["id"], "statement": h["statement"], "status": h["status"],
            "domain": h["domain"], "supports": n,
            "contradicts": len(h["contradicts"]),
            "evidence_label": _evidence_label(n),
            "latest_support": max((s["occurred_at"] for s in h["supports"]),
                                  default=""),
        })
    tendencies.sort(key=lambda t: (-t["supports"], t["statement"]))

    entities: dict[str, list[dict]] = {}
    for name, kind, n in store.entity_observation_counts():
        entities.setdefault(kind or "기타", []).append(
            {"name": name, "count": n})
    for lst in entities.values():
        lst.sort(key=lambda e: (-e["count"], e["name"]))

    situations: dict[str, list[dict]] = {}
    for kind, value, n in store.context_observation_counts():
        situations.setdefault(kind or "기타", []).append(
            {"value": value, "count": n})
    for lst in situations.values():
        lst.sort(key=lambda e: (-e["count"], e["value"]))

    preferences = [
        {"target": t, "direction": d, "basis": b}
        for t, d, b in store.preference_rows()]
    criteria = [
        {"statement": s, "applies_to": a}
        for s, a in store.criterion_rows()]
    styles = [
        {"pattern": p, "intent": i, "examples": len(schema.parse_json_list(e))}
        for p, i, e in store.style_rows()]

    return {
        "observations": len(texts),
        "period": {"from": min(dates) if dates else "",
                   "to": max(dates) if dates else ""},
        "tendencies": tendencies,
        "entities": entities,
        "situations": situations,
        "preferences": preferences,
        "criteria": criteria,
        "writing": {"styles": styles, "stats": writing_stats(texts)},
    }
