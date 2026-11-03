"""observe 파이프라인: 관찰 수집 → 추출 → 검증 → 그래프 기록 (계획서 6장).

실패 시 상태를 바꾸지 않는다 (검증 실패·revision 불일치·추출기 오류 모두).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import extract as ex
from . import schema
from .schema import (
    CORRECTION,
    CRITERION,
    ENTITY,
    HYPOTHESIS,
    OBSERVATION,
    PREFERENCE,
    RESPONSE,
    STYLE,
    CONTEXT,
)
from .store import MirrorStore


@dataclass
class ObserveResult:
    ok: bool
    revision: int
    obs_id: str = ""
    duplicate: bool = False
    nodes_added: int = 0
    edges_added: int = 0
    reused_nodes: int = 0
    skipped_blocked: list[str] = field(default_factory=list)
    error: str = ""


def _node_signature(kind: str, ref: str, attrs: dict) -> str:
    """차단 목록 대조용 시그니처. 차단 대상이 아니면 ''."""
    if kind == HYPOTHESIS:
        return attrs.get("statement", "")
    if kind == PREFERENCE:
        return attrs.get("target", "")
    if kind == CRITERION:
        return attrs.get("statement", "")
    if kind == ENTITY:
        return attrs.get("name", "")
    return ""


def observe(
    store: MirrorStore,
    extractor: ex.Extractor,
    source_id: str,
    event: dict,
    base_revision: int,
) -> ObserveResult:
    """관찰 1건을 그래프에 반영.

    event: {"text": 필수, "occurred_at"?, "raw_ref"?, "visibility"?}
    """
    revision = store.get_revision()
    if base_revision != revision:
        return ObserveResult(
            ok=False, revision=revision,
            error=f"stale_revision: base={base_revision} current={revision}",
        )
    text = (event.get("text") or "").strip()
    if not text:
        return ObserveResult(ok=False, revision=revision, error="empty_text")
    if not source_id:
        return ObserveResult(ok=False, revision=revision, error="empty_source_id")

    existing = store.get_observation_by_source(source_id)
    if existing is not None:
        return ObserveResult(
            ok=True, revision=revision, obs_id=existing.id, duplicate=True
        )

    obs_in = ex.ObservationInput(
        source_id=source_id, text=text,
        occurred_at=event.get("occurred_at") or schema.utcnow_iso(),
        raw_ref=event.get("raw_ref", ""),
        visibility=event.get("visibility", "private"),
    )
    try:
        if hasattr(extractor, "known_hypotheses"):
            # LLM 추출기가 같은 뜻의 가설 문구를 재사용하도록 기존 가설을 알려준다
            extractor.known_hypotheses = [r[0] for r in store._rows(
                "MATCH (h:Hypothesis) RETURN h.statement")]
        result = extractor.extract(obs_in)
    except (ex.ExtractionError, ex.NotConfigured) as e:
        return ObserveResult(ok=False, revision=revision, error=f"extract: {e}")
    errors = ex.validate_extraction(result)
    if errors:
        return ObserveResult(
            ok=False, revision=revision, error="validate: " + "; ".join(errors)
        )

    # 차단된 이해(삭제된 것의 재출현)는 저장 전에 걸러낸다.
    kinds: dict[str, str] = {"obs": OBSERVATION}
    for node in result.nodes:
        kinds[node.ref] = node.kind
    dropped: set[str] = set()
    skipped: list[str] = []
    live_nodes = []
    for node in result.nodes:
        sig = _node_signature(node.kind, node.ref, node.attrs)
        if sig and store.is_blocked(signature_text=sig):
            dropped.add(node.ref)
            skipped.append(f"{node.ref} ({sig[:30]})")
        else:
            live_nodes.append(node)
    live_edges = [
        e for e in result.edges
        if e.from_ref not in dropped and e.to_ref not in dropped
    ]
    skipped += [
        f"edge {e.rel} {e.from_ref}->{e.to_ref}"
        for e in result.edges
        if e.from_ref in dropped or e.to_ref in dropped
    ]

    # -- 기록 (여기서부터는 실패하지 않는 연산만) --
    obs = schema.Observation(
        id=schema.new_id("obs"), source_id=source_id, text=text,
        occurred_at=obs_in.occurred_at, raw_ref=obs_in.raw_ref,
        visibility=obs_in.visibility,
    )
    store.add_observation(obs)
    refmap = {"obs": obs.id}
    nodes_added = 1
    reused_nodes = 0

    def _reuse(existing_id: str | None, ref: str) -> bool:
        nonlocal reused_nodes
        if existing_id is None:
            return False
        refmap[ref] = existing_id
        reused_nodes += 1
        return True

    for node in live_nodes:
        a = node.attrs
        if node.kind == ENTITY:
            found = store.find_entity(a["kind"], a["name"],
                                      a.get("aliases", []))
            if found is not None:
                store.upsert_entity(a["kind"], a["name"],
                                    a.get("aliases", []), mention=text)
                _reuse(found.id, node.ref)
                continue
            ent = store.upsert_entity(a["kind"], a["name"], a.get("aliases", []),
                                      mention=text)
            refmap[node.ref] = ent.id
        elif node.kind == CONTEXT:
            if _reuse(store.find_context(a["kind"], a["value"]), node.ref):
                continue
            cid = schema.new_id("ctx")
            store.add_context(schema.Context(id=cid, kind=a["kind"],
                                             value=a["value"]))
            refmap[node.ref] = cid
        elif node.kind == PREFERENCE:
            if _reuse(store.find_preference(a["target"],
                                            a.get("direction", "+")), node.ref):
                continue
            pid = schema.new_id("pref")
            store.add_preference(schema.Preference(
                id=pid, target=a["target"], direction=a.get("direction", "+"),
                strength_evidence=a.get("strength_evidence", "")))
            refmap[node.ref] = pid
        elif node.kind == CRITERION:
            if _reuse(store.find_criterion(a["statement"]), node.ref):
                continue
            cid = schema.new_id("crit")
            store.add_criterion(schema.Criterion(
                id=cid, statement=a["statement"],
                applies_to=a.get("applies_to", "")))
            refmap[node.ref] = cid
        elif node.kind == HYPOTHESIS:
            if _reuse(store.find_hypothesis_by_statement(a["statement"]),
                      node.ref):
                continue
            hid = schema.new_id("hyp")
            store.add_hypothesis(schema.Hypothesis(
                id=hid, conditions=list(a.get("conditions", [])),
                statement=a["statement"],
                status=a.get("status", schema.STATUS_CANDIDATE),
                use_policy=a.get("use_policy") or schema.default_use_policy(
                    a.get("domain", "")),
                domain=a.get("domain", "")))
            refmap[node.ref] = hid
        elif node.kind == STYLE:
            if _reuse(store.find_style(a["pattern"], a.get("intent", "")),
                      node.ref):
                continue
            sid = schema.new_id("sty")
            store.add_style(schema.Style(
                id=sid, pattern=a["pattern"],
                example_refs=list(a.get("example_refs", [])),
                intent=a.get("intent", "")))
            refmap[node.ref] = sid
        elif node.kind in (CORRECTION, RESPONSE):
            # 추출기가 만들지 않는 종류 (검증 단계에서 이미 차단)
            continue
        nodes_added += 1
    edges_added = 0
    for edge in live_edges:
        store.add_edge(schema.Edge(
            rel=edge.rel, from_kind=kinds[edge.from_ref],
            from_id=refmap[edge.from_ref], to_kind=kinds[edge.to_ref],
            to_id=refmap[edge.to_ref],
            evidence=refmap.get(edge.evidence_ref, "") if edge.evidence_ref else "",
            phrase=edge.phrase or "",
        ))
        edges_added += 1

    revision = store.bump_revision()
    return ObserveResult(
        ok=True, revision=revision, obs_id=obs.id,
        nodes_added=nodes_added, edges_added=edges_added,
        reused_nodes=reused_nodes, skipped_blocked=skipped,
    )
