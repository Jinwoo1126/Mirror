"""어시스턴트 배선 P2: 라우터 → retrieve → LLM 호출.

실제 모델 연결은 llm_call 주입으로 대체한다. 명시 조건·스타일이 있으면
라우터 판정보다 항상 우선한다.
"""

from __future__ import annotations

from typing import Callable

from . import retrieve as retrieve_mod
from . import router as router_mod
from . import schema
from .store import MirrorStore


def _resolve_style(store: MirrorStore, explicit_style: str | None,
                   decision: router_mod.RouterDecision) -> tuple[str | None, str]:
    """명시 스타일 우선 해석. (style_id, note) 반환."""
    if not explicit_style:
        return decision.style_id, ""
    node = store.get_node(schema.STYLE, explicit_style)
    if node is not None:
        return explicit_style, "explicit style id overrides router"
    rows = store._rows("MATCH (s:Style {pattern: $p}) RETURN s.id",
                       {"p": explicit_style})
    if rows:
        return rows[0][0], "explicit style pattern overrides router"
    return decision.style_id, "explicit style not found; router kept"


def _example_texts(store: MirrorStore, refs: list[str]) -> list[str]:
    texts = []
    for ref in refs[:3]:
        obs = store.get_observation(ref)
        if obs is not None and obs.text:
            texts.append(obs.text[:300])
    return texts


def answer(store: MirrorStore, router, llm_call: Callable[[str], str] | None,
           request: str, explicit_conditions: list[dict] | None = None,
           explicit_style: str | None = None,
           as_of: str | None = None, budget: int = 10,
           index=None, options: list[str] | None = None) -> dict:
    """요청 1건에 답한다. llm_call이 None이면 프롬프트만 반환한다."""
    decision = router.decide(request, store)
    style_id, style_note = _resolve_style(store, explicit_style, decision)
    examples: list[str] = []
    if style_id is not None:
        node = store.get_node(schema.STYLE, style_id)
        if node is not None:
            examples = _example_texts(
                store, schema.parse_json_list(node["example_refs"]))
    retrieval = retrieve_mod.retrieve(
        store, request, explicit_conditions or [], as_of, budget,
        domains=decision.domains or None, entity_names=decision.entities or None,
        index=index, options=options)
    proposal = retrieval.pop("proposal", None)
    lines = []
    for item in retrieval["items"]:
        if item["kind"] == schema.OBSERVATION:
            lines.append(f"- 관찰 {item['id']}: {item['text']}")
        else:
            lines.append(
                f"- 가설 {item['id']} [{item['status']}/{item['use_policy']}]: "
                f"{item['statement']} (근거 {','.join(item['supports']) or '없음'})")
    context = "\n".join(lines) if lines else "(관련 이해 없음)"
    style_block = ""
    if examples:
        style_block = "문체 예시:\n" + "\n---\n".join(examples) + "\n"
    prompt = (f"관련 이해:\n{context}\n{style_block}\n"
              f"요청: {request}\n"
              "위 이해를 참고해 답하고, 쓴 근거는 관찰 ID로 인용하라.")
    citations = []
    for item in retrieval["items"]:
        if item["kind"] == schema.OBSERVATION:
            citations.append(item["id"])
        else:
            citations += item["supports"]
    base = {"citations": sorted(set(citations)),
            "decision": schema.to_jsonable(decision),
            "retrieval": retrieval["items"], "style_id": style_id,
            "style_note": style_note, "proposal": proposal}
    if llm_call is None:
        return {"text": "", "prompt": prompt, **base}
    try:
        return {"text": llm_call(prompt), **base}
    except Exception as e:
        return {"text": "", "error": f"llm_failed: {e}", **base}


def extractive_answer(retrieval_items: list[dict],
                      proposal: dict | None = None) -> str:
    """LLM 없이 검색 결과로 만드는 답변 (로컬 모드).

    생성하지 않고 관측·가설을 그대로 보여준다. 해석은 가설 인용으로만.
    """
    obs_items = [i for i in retrieval_items if i["kind"] == "Observation"]
    hyp_items = [i for i in retrieval_items if i["kind"] == "Hypothesis"]
    if proposal is not None:
        intervention = proposal.get("intervention", {})
        if intervention.get("level") == 1:
            lines = ["관점에 따라 예측이 갈려서 후보를 나란히 둡니다.\n"]
            for opt in intervention.get("options_presented", []):
                lines.append(f"- {opt}")
            ev = intervention.get("evidence_ids", [])
            if ev:
                lines.append(f"\n근거 관찰: {', '.join(ev)}")
            return "\n".join(lines)
        if (intervention.get("level") == 0
                and proposal.get("simulation", {}).get("majority")):
            return (f"관련 이해가 일치합니다: "
                    f"{proposal['simulation']['majority']}\n"
                    f"(근거 관찰: "
                    f"{', '.join(intervention.get('evidence_ids', [])) or '없음'})")
    if not obs_items and not hyp_items:
        return ("관련 기억이 없습니다. "
                "채팅 내용을 '기억에 저장'으로 쌓으면 다음부터 참고합니다.")
    lines = []
    if hyp_items:
        lines.append("관련 이해:")
        for h in hyp_items[:3]:
            n = len(h.get("supports", []))
            lines.append(f"- {h.get('statement')} "
                         f"(근거 {n}건, {h.get('use_policy')})")
    if obs_items:
        lines.append("관련 장면:")
        for o in obs_items[:3]:
            lines.append(f"- [{(o.get('occurred_at') or '')[:10]}] "
                         f"{o.get('text')}")
    return "\n".join(lines)
