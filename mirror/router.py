"""의도·스타일 라우터 P2 (계획서 7장).

요청 1건에 대해 (1) 요청 의도 (2) 출력 스타일+예시 글 (3) 조회 범위를 정한다.
명시 조건이 있으면 라우터 판정보다 항상 우선한다 (assistant.py에서 적용).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import llm as llm_mod
from . import schema
from .store import MirrorStore

INTENT_INFO = "정보 정리"
INTENT_DECISION = "결정 도움"
INTENT_EMPATHY = "감정 대화"
INTENT_ACTION = "실행 요청"
INTENTS = (INTENT_INFO, INTENT_DECISION, INTENT_EMPATHY, INTENT_ACTION)

_DECISION_KW = ("고르", "선택", "추천", "비교", "결정", "고를까", "뭐가")
_EMPATHY_KW = ("힘들", "걱정", "기분", "외로", "스트레스", "속상", "불안")
_ACTION_KW = ("예약", "보내", "신청", "실행", "주문해", "결제")
_DOMAIN_KW: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("취향·경험", ("음식", "맛집", "카페", "식당", "메뉴", "요리", "DJ", "곡", "영화")),
    ("가치·판단", ("원칙", "판단", "기준", "옳", "그르")),
    ("동기·실행", ("목표", "습관", "계획", "투두", "루틴", "미루")),
    ("관계·지원", ("친구", "가족", "팀장", "동료", "관계")),
    ("자기이해·반복 주제", ("성향", "패턴", "버릇", "나를", "내가")),
)


@dataclass
class RouterDecision:
    intent: str
    style_id: str | None = None
    style_examples: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    confidence: float = 0.5
    routed_by: str = "mock"
    fallback_reason: str = ""


def _match_style(store: MirrorStore, intent: str) -> tuple[str | None, list[str]]:
    rows = store.styles_by_intent(intent)
    if not rows:
        return None, []
    return rows[0][0], schema.parse_json_list(rows[0][1])[:3]


def _match_scope(store: MirrorStore, request: str) -> tuple[list[str], list[str]]:
    domains = [d for d, kws in _DOMAIN_KW if any(k in request for k in kws)]
    entities = []
    for row in store.entity_aliases():
        name, aliases = row[0] or "", schema.parse_json_list(row[1])
        if name and name in request and name not in entities:
            entities.append(name)
        for alias in aliases:
            if alias and alias in request and name not in entities:
                entities.append(name)
        if len(entities) >= 5:
            break
    return domains, entities[:5]


class MockRouter:
    """결정적 규칙 라우터. 같은 요청에 항상 같은 판정."""

    name = "mock"

    def decide(self, request: str, store: MirrorStore) -> RouterDecision:
        text = request or ""
        if any(k in text for k in _DECISION_KW):
            intent, conf = INTENT_DECISION, 0.9
        elif any(k in text for k in _EMPATHY_KW):
            intent, conf = INTENT_EMPATHY, 0.9
        elif any(k in text for k in _ACTION_KW):
            intent, conf = INTENT_ACTION, 0.9
        else:
            intent, conf = INTENT_INFO, 0.5
        style_id, examples = _match_style(store, intent)
        domains, entities = _match_scope(store, text)
        return RouterDecision(intent=intent, style_id=style_id,
                              style_examples=examples, domains=domains,
                              entities=entities, confidence=conf,
                              routed_by="mock")


ROUTER_SYSTEM_PROMPT = """You route one user request for a personal understanding agent.
Output JSON only: {"intent": "<정보 정리|결정 도움|감정 대화|실행 요청>",
"style_id": "<id or null>", "domains": ["<domain>", ...],
"entities": ["<entity name>", ...], "confidence": <0..1>}.
Rules: pick style_id only from the catalog below and only when its intent
matches; domains only from the allowed list; entities only names mentioned in
the request. When unsure use intent "정보 정리" with low confidence."""


def build_router_prompt(request: str, styles: list[dict],
                        domains: list[str]) -> str:
    catalog = "\n".join(
        f"- {s['id']}: {s.get('pattern', '')} (intent {s.get('intent', '')})"
        for s in styles)
    return (f"styles:\n{catalog or '(none)'}\n"
            f"domains: {', '.join(domains)}\nrequest: {request}\n"
            "Route as JSON.")


class LlmRouter:
    """LLM 판정 + 실패 시 Mock 폴백. 라우터 실패가 요청을 막지 않는다."""

    name = "llm"

    def __init__(self, backend: str = "spark", model: str | None = None,
                 fallback: MockRouter | None = None, **llm_kwargs):
        self.backend = backend
        self.model = model
        self.fallback = fallback or MockRouter()
        self.llm_kwargs = llm_kwargs

    def _call(self, system: str, user: str) -> dict:
        if self.backend == "spark":
            out = llm_mod.run_muse_exec(
                system + "\n\n" + user,
                model=self.model or "muse-spark-1.3", **self.llm_kwargs)
            return llm_mod.parse_json_output(out)
        if self.backend == "Muse":
            return llm_mod.call_claude_json(
                system, user, model=self.model or "claude-haiku-4-5")
        if self.backend == "ollama":
            return llm_mod.call_ollama_json(
                system, user, model=self.model or "llama3.1")
        raise llm_mod.LlmError(f"unknown backend: {self.backend}")

    def decide(self, request: str, store: MirrorStore) -> RouterDecision:
        styles = [{"id": r[0], "pattern": r[1], "intent": r[2]} for r in
                  store.style_brief()]
        domains = [d for d, _ in _DOMAIN_KW]
        try:
            data = self._call(ROUTER_SYSTEM_PROMPT,
                              build_router_prompt(request, styles, domains))
            return self._coerce(data, styles, domains, store)
        except (llm_mod.LlmError, ValueError, KeyError, TypeError) as e:
            decision = self.fallback.decide(request, store)
            decision.routed_by = f"llm+fallback:{self.backend}"
            decision.fallback_reason = str(e)[:200]
            return decision

    def _coerce(self, data: dict, styles: list[dict], domains: list[str],
                store: MirrorStore) -> RouterDecision:
        intent = str(data.get("intent", ""))
        if intent not in INTENTS:
            raise ValueError(f"bad intent: {intent}")
        style_ids = {s["id"] for s in styles}
        style_id = data.get("style_id")
        if style_id is not None and style_id not in style_ids:
            style_id = None
        style_domains = [str(d) for d in data.get("domains", [])]
        style_domains = [d for d in style_domains if d in domains]
        examples: list[str] = []
        if style_id is not None:
            node = store.get_node(schema.STYLE, style_id)
            if node is not None:
                examples = schema.parse_json_list(node["example_refs"])[:3]
        try:
            confidence = float(data.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        return RouterDecision(
            intent=intent, style_id=style_id, style_examples=examples,
            domains=style_domains,
            entities=[str(e) for e in data.get("entities", [])][:5],
            confidence=min(max(confidence, 0.0), 1.0),
            routed_by=f"llm:{self.backend}")


def get_router(name: str, **kwargs) -> MockRouter | LlmRouter:
    if name == "mock":
        return MockRouter()
    if name == "llm":
        return LlmRouter(**kwargs)
    raise ValueError(f"unknown router: {name}")
