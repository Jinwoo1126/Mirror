"""개입 정책 P4: 언제, 어떻게 먼저 말하나 (계획서 7장).

0 조용한 반영 / 1 선택지 확장 / 2 관찰 나란히 놓기 / 3 짚고 확인.
2단계 이상은 같은 주제로 2주에 1회. 성격·관계 진단 문장은 금지한다.
P4 초기에는 0~1단계만 켠다 (기본 설정).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import schema
from .store import MirrorStore

LEVEL_SILENT = 0
LEVEL_EXPAND = 1
LEVEL_OBSERVE_SIDE = 2
LEVEL_CONFIRM = 3

CAP_DAYS = 14

# 진단·단정 금지 패턴 (해당 시 PolicyError로 fail closed)
FORBIDDEN_PATTERNS = (
    r"당신은 .* (타입|유형|성격|체질)입?니다",
    r"성격장애", r"애착유형", r"우울증", r"불안장애", r"ADHD", r"진단",
    r"당신은 .*(내향|외향)적",
)


class PolicyError(ValueError):
    """개입 정책 위반 (진단 문장 등)."""


@dataclass
class Intervention:
    level: int
    title: str
    body: str
    options_presented: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    topic: str = "일반"
    hyp_id: str = ""
    downgraded_from: int | None = None


def check_copy(text: str) -> None:
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, text or ""):
            raise PolicyError(f"forbidden claim pattern: {pattern}")


def _cap_hit(store: MirrorStore, topic: str, as_of: str) -> bool:
    dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    since = (dt - timedelta(days=CAP_DAYS)).isoformat()
    recent = store.recent_interventions(topic, since)
    return any(r["level"] >= LEVEL_OBSERVE_SIDE for r in recent)


def _distinct_support_days(store: MirrorStore, hyp_id: str,
                           as_of: str) -> list[str]:
    days = []
    for obs_id in store.supporting_observations(hyp_id, as_of=as_of):
        obs = store.get_observation(obs_id)
        if obs is not None and obs.occurred_at:
            days.append(obs.occurred_at[:10])
    return sorted(set(days))


def decide(store: MirrorStore, request: str, sim_result=None,
           context: dict | None = None, settings: dict | None = None,
           as_of: str | None = None) -> Intervention:
    """개입 1건을 결정. L1 이상은 이력을 남긴다."""
    context = context or {}
    settings = settings or {}
    as_of = as_of or schema.utcnow_iso()
    topic = str(context.get("topic", "일반"))
    level: int | None = None
    title, body = "", ""
    options_presented: list[str] = []
    evidence_ids: list[str] = []
    hyp_id = str(context.get("hyp_id", ""))
    downgraded_from = None

    omission = int(context.get("omission_evidence", 0) or 0)
    versions = list(context.get("versions", []) or [])
    if (settings.get("irreversible_action") and omission >= 2
            and len(versions) == 2):
        level = LEVEL_CONFIRM
        title = "진행 전 확인"
        body = (f"비슷한 누락이 {omission}번 관측됐습니다. "
                f"두 가지 안을 나란히 둡니다.\n안 A: {versions[0]}\n"
                f"안 B: {versions[1]}")
    elif hyp_id:
        hyp = store.get_hypothesis(hyp_id)
        if (hyp is not None and hyp.use_policy == schema.POLICY_PRESENTABLE
                and len(_distinct_support_days(store, hyp_id, as_of)) >= 2
                and settings.get("l2_enabled")):
            obs_ids = store.supporting_observations(hyp_id, as_of=as_of)[:2]
            scenes = []
            for oid in obs_ids:
                obs = store.get_observation(oid)
                if obs is not None:
                    scenes.append(f"- {obs.occurred_at[:10]}: {obs.text}")
                    evidence_ids.append(oid)
            if len(scenes) == 2:
                level = LEVEL_OBSERVE_SIDE
                title = "관찰 나란히 놓기"
                body = "해석 없이 두 장면을 나란히 둡니다.\n" + "\n".join(scenes)

    if level in (LEVEL_CONFIRM, LEVEL_OBSERVE_SIDE) and _cap_hit(
            store, topic, as_of):
        downgraded_from = level
        level = LEVEL_EXPAND
        title, body, options_presented, evidence_ids = "", "", [], []

    if level is None:
        disagreement = (
            sim_result is not None and not sim_result.agree
            and bool(sim_result.votes))
        if disagreement or context.get("expansion_candidate"):
            level = LEVEL_EXPAND
            if sim_result is not None and sim_result.votes:
                options_presented = sorted(sim_result.votes)
                title = "선택지 확장"
                body = ("관점에 따라 예측이 갈립니다. "
                        f"후보: {', '.join(options_presented)}")
                for p in sim_result.predictors:
                    evidence_ids += p.evidence_ids
                evidence_ids = sorted(set(evidence_ids))[:5]
            else:
                title = "선택지 확장"
                body = "익숙한 것과 새 가능성을 함께 둡니다."
        else:
            level = LEVEL_SILENT
            if sim_result is not None and sim_result.majority:
                title = "조용한 반영"
                body = f"합의된 이해를 제안 구성에 반영: {sim_result.majority}"
                for p in sim_result.predictors:
                    evidence_ids += p.evidence_ids
                evidence_ids = sorted(set(evidence_ids))[:5]
            else:
                title = "조용한 반영"
                body = "관련 이해 없음. 일반 답변으로 진행."

    check_copy(title + "\n" + body)
    if level >= LEVEL_EXPAND:
        store.log_intervention(topic, level, as_of)
    return Intervention(level=level, title=title, body=body,
                        options_presented=options_presented,
                        evidence_ids=evidence_ids, topic=topic,
                        hyp_id=hyp_id if level == LEVEL_OBSERVE_SIDE else "",
                        downgraded_from=downgraded_from)


def propose(store: MirrorStore, request: str, options: list[str],
            conditions: list[dict] | None = None,
            context: dict | None = None, settings: dict | None = None,
            as_of: str | None = None, scorer=None) -> dict:
    """시뮬레이션+개입을 한 번에. retrieve의 decision 모드가 사용한다.

    scorer(의미 점수)가 있으면 어휘 동점을 해소하고, 없으면 동점은 기권한다.
    """
    from . import simulate as simulate_mod  # 순환 참조 회피

    predictor = (simulate_mod.MockPredictor(scorer=scorer)
                 if scorer is not None else simulate_mod.MockPredictor())
    sim = simulate_mod.simulate(store, request, options, conditions or [],
                                as_of=as_of, predictor=predictor)
    intervention = decide(store, request, sim, context, settings, as_of)
    return {"simulation": schema.to_jsonable(sim),
            "intervention": schema.to_jsonable(intervention)}
