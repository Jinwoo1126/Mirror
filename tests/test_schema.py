import pytest

from mirror import schema


def test_validate_edge_ok():
    schema.validate_edge(schema.Edge(
        rel="SUPPORTS", from_kind="Observation", from_id="o1",
        to_kind="Hypothesis", to_id="h1", evidence="o1"))


def test_validate_edge_unknown_rel():
    with pytest.raises(schema.SchemaError):
        schema.validate_edge(schema.Edge(
            rel="LIKES", from_kind="Observation", from_id="o1",
            to_kind="Hypothesis", to_id="h1", evidence="o1"))


def test_validate_edge_bad_endpoints():
    with pytest.raises(schema.SchemaError):
        schema.validate_edge(schema.Edge(
            rel="SUPPORTS", from_kind="Hypothesis", from_id="h1",
            to_kind="Observation", to_id="o1", evidence="o1"))


def test_validate_edge_requires_evidence():
    with pytest.raises(schema.SchemaError, match="evidence"):
        schema.validate_edge(schema.Edge(
            rel="SUPPORTS", from_kind="Observation", from_id="o1",
            to_kind="Hypothesis", to_id="h1", evidence=""))
    # USED_IN은 사용 기록 자체가 근거라 면제
    schema.validate_edge(schema.Edge(
        rel="USED_IN", from_kind="Hypothesis", from_id="h1",
        to_kind="Response", to_id="r1", evidence=""))


def test_sensitive_domain_requires_internal_only():
    with pytest.raises(schema.SchemaError):
        schema.validate_hypothesis(schema.Hypothesis(
            id="h", statement="s", domain="관계·지원",
            use_policy="apply_silently"))
    schema.validate_hypothesis(schema.Hypothesis(
        id="h", statement="s", domain="관계·지원",
        use_policy="internal_only"))
    assert schema.default_use_policy("관계·지원") == "internal_only"
    assert schema.default_use_policy("취향·경험") == "apply_silently"


def test_is_effective_blocks_future_knowledge():
    assert schema.is_effective(None, None, "2026-10-20T00:00:00+00:00",
                               "2026-10-19T00:00:00+00:00")
    # recorded_at이 as_of보다 뒤면 미래 정보 → 차단
    assert not schema.is_effective(None, None, "2026-10-18T00:00:00+00:00",
                                   "2026-10-19T00:00:00+00:00")
    # valid_to가 지나면 만료
    assert not schema.is_effective("2026-10-01T00:00:00+00:00",
                                   "2026-10-10T00:00:00+00:00",
                                   "2026-10-20T00:00:00+00:00")
