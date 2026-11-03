"""The body map: what Mirror knows, sorted by the part of the body it belongs to.

Brain thinks and chooses, ears listen, mouth eats, eyes watch and read, hands write
and make, feet go places, heart feels. Every pattern, thing and style in the
knowledge base becomes a star in one of these regions. Region choice is rule-based
(keywords in English and Korean), so it is predictable and cheap. Anything that
matches nothing falls back to the brain.

It also holds the "about you" layer: statements the person declared about
themselves. Those are stored like any other hypothesis, backed by one observation whose
source id starts with "declared:", so they flow through retrieval and the edge server
like everything else.
"""

from __future__ import annotations

import hashlib
import re

from . import profile as profile_mod
from . import schema
from . import ui_export
from .store import MirrorStore

REGIONS = [
    ("brain", "Brain", "Thinks and chooses"),
    ("ears", "Ears", "Listens to"),
    ("eyes", "Eyes", "Watches and reads"),
    ("mouth", "Mouth", "Eats and drinks"),
    ("hands", "Hands", "Writes and makes"),
    ("heart", "Heart", "Feels and relates"),
    ("feet", "Feet", "Goes places"),
]

# Checked in this order. ASCII words match on word boundaries, Korean as substrings.
KEYWORDS = {
    "ears": ["곡", "음악", "선곡", "믹스", "플레이리스트", "노래", "dj", "music", "track",
             "song", "mix", "playlist", "listen", "album", "podcast"],
    "mouth": ["먹", "식당", "배달", "메뉴", "음식", "김밥", "치킨", "제육", "쌀국수", "커피",
              "카페", "라면", "분식", "편의점", "이자카야", "eat", "food", "meal", "lunch",
              "dinner", "restaurant", "coffee", "cafe", "menu", "takeout", "snack", "drink"],
    "eyes": ["영화", "책", "시청", "넷플릭스", "드라마", "movie", "book", "read", "watch",
             "film", "series", "video", "article"],
    "hands": ["블로그", "메모", "메일", "정리", "기록", "투두", "작성", "문서", "초안", "write",
              "writing", "blog", "memo", "mail", "email", "note", "notes", "todo", "draft",
              "document", "code", "edit", "plan"],
    "feet": ["공원", "헬스", "도서관", "산책", "달리기", "운동", "여행", "출근", "방탈출", "park",
             "gym", "walk", "run", "trip", "travel", "commute", "visit", "hike"],
    "heart": ["피곤", "지쳐", "부담", "설렜", "개운", "기분", "친구", "팀장", "동료", "가족",
              "스트레스", "tired", "stress", "feel", "friend", "colleague", "manager",
              "family", "mood", "anxious", "happy", "lonely"],
}
_PATTERNS = {
    region: re.compile("|".join(
        (rf"\b{re.escape(w)}(?:s|es|d|ed|ing)?\b" if w.isascii() else re.escape(w)) for w in words),
        re.IGNORECASE)
    for region, words in KEYWORDS.items()}
ORDER = ["ears", "mouth", "eyes", "hands", "feet", "heart"]

DOMAIN_DEFAULT = {"관계·지원": "heart"}
ENTITY_KIND_REGION = {"음식": "mouth", "곡": "ears", "사람": "heart", "도구": "hands"}

CHOICE = re.compile(
    r"choose|pick|select|decid|prefer|order|avoid|repeat|default|usually|"
    r"선택|고르|시키|결정|선호|회피|반복|익숙|우선", re.IGNORECASE)

DECLARED_PREFIX = "declared:"


def _match(text: str) -> str | None:
    for region in ORDER:
        if _PATTERNS[region].search(text or ""):
            return region
    return None


def classify(text: str, default: str = "brain") -> str:
    return _match(text) or default


def classify_pattern(statement: str, evidence: list[str], default: str = "brain") -> str:
    """The statement decides. With no clue there, the evidence votes, and it needs a
    clear majority, so a general pattern is not pulled around by a few memories."""
    direct = _match(statement)
    if direct:
        return direct
    votes: dict[str, int] = {}
    for t in evidence:
        r = _match(t)
        if r:
            votes[r] = votes.get(r, 0) + 1
    if votes and evidence:
        top = max(votes, key=votes.get)
        if votes[top] / len(evidence) >= 0.6:
            return top
    return default


def _is_declared(store: MirrorStore, supports: list[dict]) -> bool:
    for s in supports:
        obs = store.get_observation(s["id"])
        if obs is not None and obs.source_id.startswith(DECLARED_PREFIX):
            return True
    return False


def build_map(store: MirrorStore) -> dict:
    regions = {r[0]: {"id": r[0], "name": r[1], "verb": r[2], "stars": []}
               for r in REGIONS}

    def add(region: str, star: dict) -> None:
        regions[region]["stars"].append(star)

    declared, choices = [], []
    for h in ui_export.export_graph(store)["hypotheses"]:
        if h["status"] not in ("active", "candidate"):
            continue
        default = DOMAIN_DEFAULT.get(h["domain"], "brain")
        region = classify_pattern(h["statement"], [s["text"] for s in h["supports"]], default)
        n = len(h["supports"])
        star = {"id": h["id"], "kind": "pattern", "label": h["statement"],
                "weight": max(n, 1), "evidence": n, "status": h["status"],
                "counter": len(h["contradicts"]),
                "declared": _is_declared(store, h["supports"]),
                "evidence_texts": [{"date": (s["occurred_at"] or "")[:10], "text": s["text"]}
                                   for s in h["supports"][:4]]}
        add(region, star)
        if star["declared"]:
            declared.append({**star, "region": region})
        if CHOICE.search(h["statement"]):
            choices.append({**star, "region": region})

    prof = profile_mod.build_profile(store)
    for kind, items in prof["entities"].items():
        for e in items[:6]:
            region = ENTITY_KIND_REGION.get(kind)
            if kind == "장소":
                region = classify(e["name"], "feet")
                if region not in ("mouth", "feet"):
                    region = "feet"
            elif kind == "프로젝트":
                region = classify(e["name"], "hands")
            elif region is None:
                region = classify(e["name"], "brain")
            add(region, {"id": f"ent:{kind}:{e['name']}", "kind": "thing", "label": e["name"],
                         "weight": e["count"], "evidence": e["count"], "status": "seen",
                         "sub": kind})
    for s in prof["situations"].get("상태", []):
        add("heart", {"id": f"state:{s['value']}", "kind": "state", "label": s["value"],
                      "weight": s["count"], "evidence": s["count"], "status": "seen",
                      "sub": "state"})
    for st in prof["writing"]["styles"]:
        add("hands", {"id": f"style:{st['pattern']}", "kind": "style", "label": st["pattern"],
                      "weight": 1, "evidence": 1, "status": "seen", "sub": st["intent"]})
    for p in prof["preferences"]:
        region = classify(p["target"], "heart")
        add(region, {"id": f"pref:{p['target']}", "kind": "preference",
                     "label": f"{p['target']} ({'likes' if p['direction'] == '+' else 'dislikes'})",
                     "weight": 1, "evidence": 1, "status": "seen", "sub": "preference"})
    for c in prof["criteria"]:
        add("brain", {"id": f"crit:{c['statement']}", "kind": "criterion", "label": c["statement"],
                      "weight": 1, "evidence": 1, "status": "seen", "sub": "criterion"})

    out = []
    for r in REGIONS:
        reg = regions[r[0]]
        reg["stars"].sort(key=lambda s: (-s["weight"], s["label"]))
        reg["total"] = sum(s["weight"] for s in reg["stars"])
        out.append(reg)
    choices.sort(key=lambda s: -s["weight"])
    return {"regions": out,
            "person": {"declared": declared, "chooses": choices[:5],
                       "memories": prof["observations"],
                       "patterns": sum(1 for r in out for s in r["stars"] if s["kind"] == "pattern"),
                       "writing": prof["writing"]["stats"]}}


def declare(store: MirrorStore, statement: str, private: bool = False) -> dict:
    """Store a statement the person made about themselves, backed by one observation."""
    statement = " ".join((statement or "").split())
    if not statement:
        return {"ok": False, "error": "empty statement"}
    existing = store.find_hypothesis_by_statement(statement)
    if existing is not None:
        return {"ok": True, "id": existing, "duplicate": True}
    key = hashlib.sha1(statement.encode("utf-8")).hexdigest()[:10]
    obs = schema.Observation(id=schema.new_id("obs"), source_id=f"{DECLARED_PREFIX}{key}",
                             text=statement, occurred_at=schema.utcnow_iso())
    store.add_observation(obs)
    domain = "자기이해·반복 주제" if private else "취향·경험"
    hyp = schema.Hypothesis(
        id=schema.new_id("hyp"), statement=statement, status=schema.STATUS_ACTIVE,
        domain=domain, use_policy=schema.default_use_policy(domain),
        status_changed_at=schema.utcnow_iso())
    store.add_hypothesis(hyp)
    store.add_edge(schema.Edge(rel=schema.SUPPORTS, from_kind=schema.OBSERVATION,
                               from_id=obs.id, to_kind=schema.HYPOTHESIS, to_id=hyp.id,
                               evidence=obs.id))
    return {"ok": True, "id": hyp.id, "observation": obs.id, "private": private}
