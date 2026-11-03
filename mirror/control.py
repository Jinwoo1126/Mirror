"""control 파이프라인: 조회·정정·사용 중지·삭제·재허용 (계획서 5·6장).

추론을 거치지 않고 저장소에 바로 적용된다. 실패 시 상태를 바꾸지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import schema
from .schema import CORRECTION, HYPOTHESIS, OBSERVATION
from .store import MirrorStore


@dataclass
class ControlResult:
    ok: bool
    action: str
    revision: int
    detail: dict = field(default_factory=dict)
    error: str = ""


def _need_target(target: dict | None) -> tuple[str, str] | str:
    if not isinstance(target, dict) or "kind" not in target or "id" not in target:
        return "target_required: {kind, id}"
    if target["kind"] not in schema.NODE_TYPES:
        return f"unknown_kind: {target['kind']}"
    return (target["kind"], target["id"])


def control(
    store: MirrorStore,
    action: str,
    target: dict | None = None,
    payload: dict | None = None,
) -> ControlResult:
    """제어 동작 1건을 적용. payload는 동작별 추가 인자."""
    payload = payload or {}
    revision = store.get_revision()

    if action == "query":
        parsed = _need_target(target)
        if isinstance(parsed, str):
            return ControlResult(False, action, revision, error=parsed)
        kind, node_id = parsed
        found = store.neighbors(kind, node_id)
        if found["node"] is None:
            return ControlResult(False, action, revision, error="not_found")
        return ControlResult(True, action, revision, detail=found)

    if action == "correct":
        return _correct(store, target, payload, revision)

    if action == "suppress":
        parsed = _need_target(target)
        if isinstance(parsed, str):
            return ControlResult(False, action, revision, error=parsed)
        kind, node_id = parsed
        if kind != HYPOTHESIS:
            return ControlResult(
                False, action, revision,
                error=f"suppress_supports_hypothesis_only: {kind}",
            )
        if store.get_node(kind, node_id) is None:
            return ControlResult(False, action, revision, error="not_found")
        store.set_hypothesis_status(node_id, schema.STATUS_SUPPRESSED)
        return ControlResult(
            True, action, store.bump_revision(),
            detail={"id": node_id, "status": schema.STATUS_SUPPRESSED},
        )

    if action == "delete":
        parsed = _need_target(target)
        if isinstance(parsed, str):
            return ControlResult(False, action, revision, error=parsed)
        kind, node_id = parsed
        node = store.get_node(kind, node_id)
        if node is None:
            return ControlResult(False, action, revision, error="not_found")
        sig_source = (
            node.get("statement") or node.get("target") or node.get("name")
            or node.get("text") or node_id
        )
        store.delete_node(kind, node_id)
        store.block(node_id, str(sig_source),
                    payload.get("reason", "user delete"))
        return ControlResult(
            True, action, store.bump_revision(),
            detail={"deleted": {"kind": kind, "id": node_id}, "blocked": True},
        )

    if action == "reallow":
        parsed = _need_target(target)
        if isinstance(parsed, str):
            return ControlResult(False, action, revision, error=parsed)
        kind, node_id = parsed
        lifted = store.unblock(node_id)
        restored = False
        if kind == HYPOTHESIS:
            hyp = store.get_hypothesis(node_id)
            if hyp is not None and hyp.status == schema.STATUS_SUPPRESSED:
                # 복원은 후보부터 (재평가 후 활성 승격은 P2 가설 엔진)
                store.set_hypothesis_status(node_id, schema.STATUS_CANDIDATE)
                restored = True
        if not lifted and not restored:
            return ControlResult(False, action, revision, error="nothing_to_reallow")
        return ControlResult(
            True, action, store.bump_revision(),
            detail={"unblocked": lifted, "restored_to_candidate": restored},
        )

    return ControlResult(False, action, revision, error=f"unknown_action: {action}")


def _correct(
    store: MirrorStore, target: dict | None, payload: dict, revision: int
) -> ControlResult:
    parsed = _need_target(target)
    if isinstance(parsed, str):
        return ControlResult(False, "correct", revision, error=parsed)
    kind, node_id = parsed
    if store.get_node(kind, node_id) is None:
        return ControlResult(False, "correct", revision, error="not_found")

    corr_kind = payload.get("kind", "")
    if corr_kind not in schema.CORRECTION_KINDS:
        return ControlResult(
            False, "correct", revision,
            error=f"unknown_correction_kind: {corr_kind}",
        )
    text = payload.get("text", "")

    # 정정 발언 자체를 관찰·정정 노드로 남긴다 (출처 추적).
    corr = schema.Correction(id=schema.new_id("corr"), kind=corr_kind,
                             target=f"{kind}:{node_id}", text=text)
    corr_obs = schema.Observation(
        id=schema.new_id("obs"), source_id=f"correction:{corr.id}",
        text=text or f"[{corr_kind} correction]",
        occurred_at=payload.get("occurred_at") or schema.utcnow_iso(),
    )
    store.add_observation(corr_obs)
    store.add_correction(corr)
    store.add_edge(schema.Edge(rel=schema.CORRECTS, from_kind=CORRECTION,
                               from_id=corr.id, to_kind=kind, to_id=node_id,
                               evidence=corr_obs.id))
    detail: dict = {"correction_id": corr.id, "observation_id": corr_obs.id}

    if corr_kind == schema.CORRECTION_FACT and kind == OBSERVATION:
        # 사실 정정: 정정된 관찰에 기댄 가설을 모두 후보로 강등
        if payload.get("corrected_text"):
            store.update_observation_text(node_id, payload["corrected_text"])
            detail["text_updated"] = True
        dependents = store.hypotheses_supported_by(node_id)
        demoted = []
        for dep_id in dependents:
            if store.get_hypothesis(dep_id) is not None:
                store.set_hypothesis_status(dep_id, schema.STATUS_CANDIDATE)
                demoted.append(dep_id)
        detail["demoted_to_candidate"] = demoted
        detail["note"] = "preferences have no status; hypotheses demoted"
    elif corr_kind == schema.CORRECTION_INTERPRETATION and kind == HYPOTHESIS:
        # 해석 이견: 해당 가설 보류. 대안의 우선순위는 조회 시점에 상승
        # (retrieve가 보류 가설의 ALTERNATIVE_TO를 가산점).
        store.set_hypothesis_status(node_id, schema.STATUS_SUSPENDED)
        detail["suspended"] = node_id
        detail["alternatives_boosted_at_retrieval"] = store.alternatives(node_id)
    elif corr_kind == schema.CORRECTION_CHANGE and kind == HYPOTHESIS:
        # 실제 변화: 기존 가설에 valid_to 후 폐기, 새 후보 생성
        now = schema.utcnow_iso()
        store.set_hypothesis_status(node_id, schema.STATUS_RETIRED, valid_to=now)
        detail["retired"] = node_id
        new_statement = payload.get("new_statement", "")
        if new_statement:
            old = store.get_hypothesis(node_id)
            assert old is not None
            new_hyp = schema.Hypothesis(
                id=schema.new_id("hyp"), statement=new_statement,
                status=schema.STATUS_CANDIDATE,
                use_policy=schema.default_use_policy(old.domain),
                domain=old.domain, valid_from=now,
            )
            store.add_hypothesis(new_hyp)
            store.add_edge(schema.Edge(
                rel=schema.SUPPORTS, from_kind=OBSERVATION, from_id=corr_obs.id,
                to_kind=HYPOTHESIS, to_id=new_hyp.id, evidence=corr_obs.id))
            detail["new_candidate"] = new_hyp.id
    elif corr_kind == schema.CORRECTION_REQUEST:
        # 현재 요청: 이번 응답용 Context만 생성, 가설 상태 불변
        ctx = schema.Context(
            id=schema.new_id("ctx"),
            kind=payload.get("context_kind", "일시 요청"),
            value=payload.get("context_value", text),
            valid_from=schema.utcnow_iso(),
        )
        store.add_context(ctx)
        store.add_edge(schema.Edge(
            rel=schema.OBSERVED_IN, from_kind=schema.CONTEXT, from_id=ctx.id,
            to_kind=OBSERVATION, to_id=corr_obs.id, evidence=corr_obs.id))
        detail["ephemeral_context"] = ctx.id
    else:
        return ControlResult(
            False, "correct", revision,
            error=f"correction kind '{corr_kind}' does not apply to {kind}",
        )

    return ControlResult(True, "correct", store.bump_revision(), detail=detail)
