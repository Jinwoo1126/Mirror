"""가설 엔진 P2: 승격·주간 점검·야간 통합 (계획서 4·5장).

- 승격: 서로 다른 날의 관찰 2개 이상이 받치고 반례가 없을 때 후보→활성.
- 주간 점검: 대안들보다 적중률이 낮은 활성 가설을 보류로.
- 야간 통합: 중복 엔티티 병합, 오래된 근거 점검, 대안 연결.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import schema
from .store import MirrorStore


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def promote_candidates(store: MirrorStore, as_of: str | None = None) -> dict:
    """승격 조건을 만족하는 후보를 활성으로. {promoted, evaluated} 반환."""
    as_of = as_of or schema.utcnow_iso()
    ids = store.hypothesis_ids_by_status([schema.STATUS_CANDIDATE], as_of)
    promoted = []
    for hyp_id in ids:
        supports = store.supporting_observations(hyp_id, as_of=as_of)
        if store.contradicting_observations(hyp_id, as_of=as_of):
            continue
        days = set()
        for obs_id in supports:
            obs = store.get_observation(obs_id)
            if obs is not None and obs.occurred_at:
                days.add(obs.occurred_at[:10])
        if len(days) >= 2:
            store.set_hypothesis_status(hyp_id, schema.STATUS_ACTIVE)
            promoted.append(hyp_id)
    return {"promoted": promoted, "evaluated": len(ids)}


def weekly_review(store: MirrorStore, as_of: str | None = None,
                  min_n: int = 2) -> dict:
    """대안보다 못 맞힌 활성 가설을 보류로. 예측 기록이 min_n 미만이면 유지."""
    as_of = as_of or schema.utcnow_iso()
    active = store.active_hypotheses(as_of=as_of)
    suspended = []
    for hyp in active:
        stats = store.hit_stats(hyp.id)
        if stats["n"] < min_n or stats["rate"] is None:
            continue
        alt_rates = []
        for alt_id in store.alternatives(hyp.id):
            alt = store.hit_stats(alt_id)
            if alt["n"] >= min_n and alt["rate"] is not None:
                alt_rates.append(alt["rate"])
        if alt_rates and stats["rate"] < min(alt_rates):
            store.set_hypothesis_status(hyp.id, schema.STATUS_SUSPENDED)
            suspended.append(hyp.id)
    return {"suspended": suspended, "evaluated": len(active)}


def nightly_maintenance(store: MirrorStore, as_of: str | None = None,
                        stale_days: int = 180) -> dict:
    """야간 통합: 엔티티 병합·오래된 근거 보고·대안 연결."""
    as_of = as_of or schema.utcnow_iso()
    merged = _merge_duplicate_entities(store)
    stale = _stale_hypotheses(store, as_of, stale_days)
    linked = _link_shared_alternatives(store)
    return {"merged_entities": merged, "stale_candidates": stale,
            "linked_alternatives": linked}


def _merge_duplicate_entities(store: MirrorStore) -> list[dict]:
    rows = store.entity_ids()
    groups: dict[tuple[str, str], list] = {}
    for eid, kind, name, rec in rows:
        norm = "".join((name or "").strip().lower().split())
        groups.setdefault((kind or "", norm), []).append((rec or "", eid))
    merged = []
    for (_, _norm), members in groups.items():
        if len(members) < 2:
            continue
        members.sort()
        keep = members[0][1]
        for _, drop in members[1:]:
            if store.merge_entities(keep, drop):
                merged.append({"keep": keep, "drop": drop})
    return merged


def _stale_hypotheses(store: MirrorStore, as_of: str,
                      stale_days: int) -> list[str]:
    cutoff = (_parse_dt(as_of) or datetime.now(timezone.utc)) - timedelta(
        days=stale_days)
    stale = []
    for hyp in store.active_hypotheses(as_of=as_of):
        supports = store.supporting_observations(hyp.id, as_of=as_of)
        if not supports:
            continue
        dates = []
        for obs_id in supports:
            obs = store.get_observation(obs_id)
            dt = _parse_dt(obs.occurred_at) if obs else None
            if dt is not None:
                dates.append(dt)
        if dates and max(dates) < cutoff:
            stale.append(hyp.id)
    return stale


def _link_shared_alternatives(store: MirrorStore) -> int:
    rows = store.cosupported_pairs()
    linked = 0
    keep_statuses = {schema.STATUS_CANDIDATE, schema.STATUS_ACTIVE,
                     schema.STATUS_SUSPENDED}
    for h1, h2, obs_id, s1, s2 in rows:
        if s1 not in keep_statuses or s2 not in keep_statuses:
            continue
        for a, b in ((h1, h2), (h2, h1)):
            if not store.has_alternative(a, b):
                store.add_edge(schema.Edge(
                    rel=schema.ALTERNATIVE_TO, from_kind=schema.HYPOTHESIS,
                    from_id=a, to_kind=schema.HYPOTHESIS, to_id=b,
                    evidence=obs_id))
                linked += 1
    return linked
