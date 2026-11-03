"""Mirror 개인 온톨로지 스키마 (계획서 4장).

핵심 결정: "나는 X를 좋아한다" 같은 단정 노드를 만들지 않는다.
모든 이해는 원문 관찰에서 출발하고, 해석은 반례·대안·조건을 가진
가설 노드로만 둔다.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# 노드 타입
# ---------------------------------------------------------------------------

OBSERVATION = "Observation"
ENTITY = "Entity"
CONTEXT = "Context"
PREFERENCE = "Preference"
CRITERION = "Criterion"
HYPOTHESIS = "Hypothesis"
STYLE = "Style"
CORRECTION = "Correction"
RESPONSE = "Response"  # USED_IN 평가용 경량 노드

NODE_TYPES = frozenset(
    {
        OBSERVATION,
        ENTITY,
        CONTEXT,
        PREFERENCE,
        CRITERION,
        HYPOTHESIS,
        STYLE,
        CORRECTION,
        RESPONSE,
    }
)

# ---------------------------------------------------------------------------
# 엣지 타입
# ---------------------------------------------------------------------------

OBSERVED_IN = "OBSERVED_IN"
SUPPORTS = "SUPPORTS"
CONTRADICTS = "CONTRADICTS"
CONDITIONED_ON = "CONDITIONED_ON"
ALTERNATIVE_TO = "ALTERNATIVE_TO"
DERIVED_FROM = "DERIVED_FROM"
CORRECTS = "CORRECTS"
USED_IN = "USED_IN"
RELATED_TO = "RELATED_TO"

EDGE_TYPES = frozenset(
    {
        OBSERVED_IN,
        SUPPORTS,
        CONTRADICTS,
        CONDITIONED_ON,
        ALTERNATIVE_TO,
        DERIVED_FROM,
        CORRECTS,
        USED_IN,
        RELATED_TO,
    }
)

# 허용된 (from_kind, to_kind) 쌍
EDGE_ENDPOINTS: dict[str, frozenset[tuple[str, str]]] = {
    OBSERVED_IN: frozenset({(ENTITY, OBSERVATION), (CONTEXT, OBSERVATION)}),
    SUPPORTS: frozenset({(OBSERVATION, HYPOTHESIS), (OBSERVATION, PREFERENCE)}),
    CONTRADICTS: frozenset({(OBSERVATION, HYPOTHESIS), (OBSERVATION, PREFERENCE)}),
    CONDITIONED_ON: frozenset({(HYPOTHESIS, CONTEXT)}),
    ALTERNATIVE_TO: frozenset({(HYPOTHESIS, HYPOTHESIS)}),
    DERIVED_FROM: frozenset({(CRITERION, HYPOTHESIS), (PREFERENCE, HYPOTHESIS)}),
    CORRECTS: frozenset({(CORRECTION, OBSERVATION), (CORRECTION, HYPOTHESIS)}),
    USED_IN: frozenset({(HYPOTHESIS, RESPONSE), (STYLE, RESPONSE)}),
    RELATED_TO: frozenset({(ENTITY, ENTITY), (ENTITY, CONTEXT)}),
}

# 근거 관찰 ID가 필수인 엣지. USED_IN은 사용 기록 자체가 근거라 제외.
EVIDENCE_REQUIRED = frozenset(
    {
        OBSERVED_IN,
        SUPPORTS,
        CONTRADICTS,
        CONDITIONED_ON,
        ALTERNATIVE_TO,
        DERIVED_FROM,
        CORRECTS,
        RELATED_TO,
    }
)

# ---------------------------------------------------------------------------
# 열거값
# ---------------------------------------------------------------------------

# 가설 상태: 후보 → 활성 → (보류 | 폐기), 사용 중지는 진위와 무관하게 최우선
STATUS_CANDIDATE = "candidate"
STATUS_ACTIVE = "active"
STATUS_SUSPENDED = "suspended"
STATUS_RETIRED = "retired"
STATUS_SUPPRESSED = "suppressed"

HYPOTHESIS_STATUSES = frozenset(
    {
        STATUS_CANDIDATE,
        STATUS_ACTIVE,
        STATUS_SUSPENDED,
        STATUS_RETIRED,
        STATUS_SUPPRESSED,
    }
)

# 가설 사용 정책
POLICY_INTERNAL_ONLY = "internal_only"
POLICY_APPLY_SILENTLY = "apply_silently"
POLICY_PRESENTABLE = "presentable"

USE_POLICIES = frozenset({POLICY_INTERNAL_ONLY, POLICY_APPLY_SILENTLY, POLICY_PRESENTABLE})

# 관계·자기이해 영역 가설의 기본 정책
SENSITIVE_DOMAINS = frozenset({"관계·지원", "자기이해·반복 주제"})

# 정정 종류: 사실 정정 / 해석 이견 / 실제 변화 / 현재 요청(일회성)
CORRECTION_FACT = "fact"
CORRECTION_INTERPRETATION = "interpretation"
CORRECTION_CHANGE = "change"
CORRECTION_REQUEST = "request"

CORRECTION_KINDS = frozenset(
    {CORRECTION_FACT, CORRECTION_INTERPRETATION, CORRECTION_CHANGE, CORRECTION_REQUEST}
)

DIRECTION_POSITIVE = "+"
DIRECTION_NEGATIVE = "-"

# ---------------------------------------------------------------------------
# 시간 (이중 시간 모델: valid_* = 현실 유효 구간, recorded_* = 알게 된 시점)
# ---------------------------------------------------------------------------


def utcnow_iso() -> str:
    """현재 UTC 시각 (ISO-8601, 초 단위)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def is_effective(
    valid_from: str | None, valid_to: str | None, as_of: str, recorded_at: str | None = None
) -> bool:
    """as_of 시점에 노드/엣지가 유효하고 이미 알려진 상태인가.

    recorded_at > as_of 이면 "미래 정보"이므로 보이지 않는다 (평가 시 차단용).
    """
    if recorded_at is not None and recorded_at > as_of:
        return False
    if valid_from is not None and valid_from > as_of:
        return False
    if valid_to is not None and as_of >= valid_to:
        return False
    return True


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# 노드 dataclass
# ---------------------------------------------------------------------------


@dataclass
class Observation:
    id: str
    source_id: str
    text: str
    occurred_at: str
    raw_ref: str = ""
    visibility: str = "private"
    recorded_at: str = field(default_factory=utcnow_iso)


@dataclass
class Entity:
    id: str
    kind: str
    name: str
    aliases: list[str] = field(default_factory=list)
    recorded_at: str = field(default_factory=utcnow_iso)
    description: str = ""


@dataclass
class Context:
    id: str
    kind: str
    value: str
    valid_from: str | None = None
    valid_to: str | None = None
    recorded_at: str = field(default_factory=utcnow_iso)


@dataclass
class Preference:
    id: str
    target: str
    direction: str = DIRECTION_POSITIVE
    strength_evidence: str = ""
    valid_from: str | None = None
    valid_to: str | None = None
    recorded_at: str = field(default_factory=utcnow_iso)


@dataclass
class Criterion:
    id: str
    statement: str
    applies_to: str = ""
    recorded_at: str = field(default_factory=utcnow_iso)


@dataclass
class Hypothesis:
    id: str
    conditions: list[str] = field(default_factory=list)
    statement: str = ""
    status: str = STATUS_CANDIDATE
    use_policy: str = POLICY_APPLY_SILENTLY
    domain: str = ""
    valid_from: str | None = None
    valid_to: str | None = None
    recorded_at: str = field(default_factory=utcnow_iso)
    status_changed_at: str = ""


@dataclass
class Style:
    id: str
    pattern: str
    example_refs: list[str] = field(default_factory=list)
    intent: str = ""
    recorded_at: str = field(default_factory=utcnow_iso)


@dataclass
class Correction:
    id: str
    kind: str
    target: str
    text: str = ""
    recorded_at: str = field(default_factory=utcnow_iso)


@dataclass
class Response:
    id: str
    text: str = ""
    created_at: str = field(default_factory=utcnow_iso)


# ---------------------------------------------------------------------------
# 엣지
# ---------------------------------------------------------------------------


class SchemaError(ValueError):
    """스키마 위반 (타입·엔드포인트·근거 누락 등)."""


@dataclass
class Edge:
    rel: str
    from_kind: str
    from_id: str
    to_kind: str
    to_id: str
    evidence: str = ""  # 근거 관찰 ID (USED_IN 제외 필수)
    valid_from: str | None = None
    valid_to: str | None = None
    recorded_at: str = field(default_factory=utcnow_iso)
    phrase: str = ""  # RELATED_TO 관계 서술 (필수)


def validate_edge(edge: Edge) -> None:
    """엣지 1개를 스키마에 따라 검증. 위반 시 SchemaError."""
    if edge.rel not in EDGE_TYPES:
        raise SchemaError(f"unknown edge type: {edge.rel}")
    if edge.from_kind not in NODE_TYPES:
        raise SchemaError(f"unknown from node type: {edge.from_kind}")
    if edge.to_kind not in NODE_TYPES:
        raise SchemaError(f"unknown to node type: {edge.to_kind}")
    if (edge.from_kind, edge.to_kind) not in EDGE_ENDPOINTS[edge.rel]:
        raise SchemaError(
            f"{edge.rel} cannot connect {edge.from_kind} -> {edge.to_kind}"
        )
    if edge.rel in EVIDENCE_REQUIRED and not edge.evidence:
        raise SchemaError(f"{edge.rel} edge requires evidence observation id")
    if edge.rel == RELATED_TO and not edge.phrase.strip():
        raise SchemaError("RELATED_TO edge requires a phrase")
    if not edge.from_id or not edge.to_id:
        raise SchemaError(f"{edge.rel} edge has empty endpoint id")


def normalize_phrase(phrase: str) -> str:
    """관계 서술 정규화 (어휘 집계용)."""
    return " ".join(phrase.strip().lower().split())


def validate_hypothesis(hyp: Hypothesis) -> None:
    if hyp.status not in HYPOTHESIS_STATUSES:
        raise SchemaError(f"unknown hypothesis status: {hyp.status}")
    if hyp.use_policy not in USE_POLICIES:
        raise SchemaError(f"unknown use_policy: {hyp.use_policy}")
    if hyp.domain in SENSITIVE_DOMAINS and hyp.use_policy != POLICY_INTERNAL_ONLY:
        raise SchemaError(
            f"domain '{hyp.domain}' requires use_policy '{POLICY_INTERNAL_ONLY}'"
        )


def default_use_policy(domain: str) -> str:
    if domain in SENSITIVE_DOMAINS:
        return POLICY_INTERNAL_ONLY
    return POLICY_APPLY_SILENTLY


def to_jsonable(value: object) -> object:
    """dataclass/Kuzu 행을 JSON 직렬화 가능 형태로 변환."""
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        out = {}
        for f in value.__dataclass_fields__:
            out[f] = to_jsonable(getattr(value, f))
        return out
    return str(value)


def json_list(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False)


def parse_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [str(v) for v in parsed] if isinstance(parsed, list) else []
