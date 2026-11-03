"""retrieve: 관련 이해 조회 (계획서 6장, P1 최소 구현).

P1에서는 벡터 없이 키워드 매칭으로 관련 관찰·가설을 찾는다.
모든 항목에 근거 관찰 ID·반례·use_policy를 붙여 호스트에 반환한다.
"""

from __future__ import annotations

import re

from . import schema
from .store import MirrorStore

_TOKEN_SPLIT = re.compile(r"[^\w가-힣]+")
_STOPWORDS = frozenset(
    {"은", "는", "이", "가", "을", "를", "에", "의", "와", "과", "도", "로", "으로",
     "에서", "에게", "한", "하다", "있다", "없다", "것", "수", "때", "좀", "꽤", "매우"}
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(text)
            if len(t) >= 2 and t not in _STOPWORDS]


def retrieve(
    store: MirrorStore,
    request_text: str,
    explicit_conditions: list[dict] | None = None,
    as_of: str | None = None,
    budget: int = 10,
    domains: list[str] | None = None,
    entity_names: list[str] | None = None,
    options: list[str] | None = None,
    index=None,
) -> dict:
    """관련 사실·가설·스타일을 근거와 함께 조회. 실패해도 빈 결과 반환.

    domains/entity_names가 주어지면 조회 범위를 그 안으로 제한한다
    (라우터의 scope). options(선택지)가 주어지면 관점 시뮬레이션+개입
    제안(proposal, 0~1단계)을 함께 첨부한다. index(HybridIndex)가 있으면
    하이브리드 검색으로 순위화하고, 없으면 키워드 매칭을 쓴다.
    """
    try:
        out = _retrieve(store, request_text, explicit_conditions or [],
                        as_of or schema.utcnow_iso(), budget,
                        domains, entity_names, index)
        if options and len(options) >= 2:
            from . import policy as policy_mod  # 순환 참조 회피

            out["proposal"] = policy_mod.propose(
                store, request_text, list(options),
                explicit_conditions or [], as_of=as_of,
                scorer=index.score if index is not None else None)
        return out
    except Exception as e:  # 호스트가 일반 답변으로 돌아가도록
        return {"items": [], "conditions": [], "revision": store.get_revision(),
                "error": f"retrieve_failed: {e}"}


def _retrieve(store, request_text, explicit_conditions, as_of, budget,
              domains, entity_names, index) -> dict:
    keywords = tokenize(request_text or "")
    cond_values = {str(c.get("value", "")) for c in explicit_conditions
                   if c.get("value")}
    items: list[tuple[float, dict]] = []

    if index is not None:
        candidates = [(h["obs"], h["score"]) for h in index.search_obs(
            request_text, store, as_of=as_of, top_k=max(budget * 3, 10))]
    else:
        candidates = [(o, float(sum(1 for kw in keywords
                                    if kw in (o.text or ""))))
                      for o in store.search_observations(
                          keywords, as_of=as_of, limit=max(budget * 3, 10))]
    scope_names = list(entity_names or [])
    if scope_names:
        try:
            scope_names += [n for n in store.related_entity_names(scope_names)
                            if n not in scope_names]
        except Exception:
            pass
    for obs, score in candidates:
        if scope_names and not any(n in (obs.text or "")
                                   for n in scope_names):
            continue
        items.append((float(score), {
            "kind": schema.OBSERVATION, "id": obs.id,
            "text": (obs.text or "")[:200], "occurred_at": obs.occurred_at,
            "score": round(float(score), 4),
        }))

    for hyp in store.active_hypotheses(as_of=as_of):
        if domains and hyp.domain not in domains:
            continue
        if index is not None:
            score = index.score(request_text, hyp.statement or "")
        else:
            score = float(sum(1 for kw in keywords
                              if kw in (hyp.statement or "")))
        score += 2 * len(set(hyp.conditions) & cond_values)
        if score <= 0 and not cond_values:
            continue
        if score <= 0:
            continue
        items.append((float(score) + 0.5, {
            "kind": schema.HYPOTHESIS, "id": hyp.id, "score": round(float(score), 4),
            "statement": hyp.statement, "conditions": hyp.conditions,
            "status": hyp.status, "use_policy": hyp.use_policy,
            "domain": hyp.domain,
            "supports": store.supporting_observations(hyp.id, as_of=as_of),
            "contradicts": store.contradicting_observations(hyp.id, as_of=as_of),
        }))

    # 보류 가설의 대안은 조회 시점에 가산 (해석 이견의 "우선순위 상승").
    boosted: list[tuple[float, dict]] = []
    for score, item in items:
        if item["kind"] == schema.HYPOTHESIS:
            for alt_id in store.alternatives(item["id"]):
                alt = store.get_hypothesis(alt_id)
                if alt is not None and alt.status == schema.STATUS_SUSPENDED:
                    score += 1.0
                    break
        boosted.append((score, item))

    boosted.sort(key=lambda t: t[0], reverse=True)
    # 명시 조건은 항상 최우선 (라우터 판정보다 우선한다는 원칙)
    conditions = [{"kind": c.get("kind", ""), "value": c.get("value", "")}
                  for c in explicit_conditions]
    return {"items": [item for _, item in boosted[:budget]],
            "conditions": conditions, "revision": store.get_revision()}
