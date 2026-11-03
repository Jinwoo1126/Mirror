import pytest

from mirror import schema
from mirror.schema import Edge


def _obs(source_id="s1", text="t"):
    return schema.Observation(id=schema.new_id("obs"), source_id=source_id,
                              text=text, occurred_at="2026-10-16T20:00:00+09:00")


def _hyp(statement="s", **kw):
    args = {"id": schema.new_id("hyp"), "statement": statement}
    args.update(kw)
    return schema.Hypothesis(**args)


def test_add_observation_and_dedupe(store):
    assert store.add_observation(_obs()) is True
    assert store.add_observation(_obs()) is False  # 같은 source_id
    assert store.get_observation_by_source("s1").text == "t"


def test_update_observation_text(store):
    obs = _obs()
    store.add_observation(obs)
    assert store.update_observation_text(obs.id, "fixed") is True
    assert store.get_observation(obs.id).text == "fixed"
    assert store.update_observation_text("nope", "x") is False


def test_upsert_entity_merges_aliases(store):
    e1 = store.upsert_entity("장소", "A카페")
    e2 = store.upsert_entity("장소", "그 카페", aliases=["A카페"])
    assert e1.id == e2.id
    assert "그 카페" in e2.aliases
    e3 = store.upsert_entity("장소", "B카페")
    assert e3.id != e1.id


def test_add_edge_rejects_evidenceless(store):
    obs = _obs()
    store.add_observation(obs)
    hyp = _hyp()
    store.add_hypothesis(hyp)
    with pytest.raises(schema.SchemaError, match="evidence"):
        store.add_edge(Edge(rel="SUPPORTS", from_kind="Observation",
                            from_id=obs.id, to_kind="Hypothesis",
                            to_id=hyp.id, evidence=""))
    with pytest.raises(schema.SchemaError, match="endpoint"):
        store.add_edge(Edge(rel="SUPPORTS", from_kind="Observation",
                            from_id="missing", to_kind="Hypothesis",
                            to_id=hyp.id, evidence=obs.id))
    with pytest.raises(schema.SchemaError, match="evidence"):
        store.add_edge(Edge(rel="SUPPORTS", from_kind="Observation",
                            from_id=obs.id, to_kind="Hypothesis",
                            to_id=hyp.id, evidence="ghost-obs"))
    # 정상 엣지는 저장
    store.add_edge(Edge(rel="SUPPORTS", from_kind="Observation",
                        from_id=obs.id, to_kind="Hypothesis",
                        to_id=hyp.id, evidence=obs.id))
    assert store.supporting_observations(hyp.id) == [obs.id]


def test_revision_and_blocklist(store):
    assert store.get_revision() == 0
    assert store.bump_revision() == 1
    store.block("hyp-1", "Some Statement", "user delete")
    assert store.is_blocked(node_id="hyp-1")
    assert store.is_blocked(signature_text="  some   statement ")
    assert not store.is_blocked(signature_text="other")
    assert store.unblock("hyp-1") is True
    assert not store.is_blocked(node_id="hyp-1")


def test_active_hypotheses_as_of(store):
    old = _hyp("old", status="active",
               recorded_at="2020-01-01T00:00:00+00:00")
    expired = _hyp("expired", status="active",
                   recorded_at="2020-01-01T00:00:00+00:00",
                   valid_to="2021-01-01T00:00:00+00:00")
    cand = _hyp("cand", status="candidate",
                recorded_at="2020-01-01T00:00:00+00:00")
    for h in (old, expired, cand):
        store.add_hypothesis(h)
    got = store.active_hypotheses(as_of="2022-01-01T00:00:00+00:00")
    assert [h.id for h in got] == [old.id]
    assert store.active_hypotheses(as_of="2019-01-01T00:00:00+00:00") == []


def test_search_observations(store):
    store.add_observation(_obs("s1", "피곤한 저녁에 주문했다"))
    store.add_observation(_obs("s2", "아침에 산책했다"))
    got = store.search_observations(["피곤"])
    assert [o.source_id for o in got] == ["s1"]
    assert store.search_observations(["피곤"],
                                     as_of="2000-01-01T00:00:00+00:00") == []


def test_set_status_and_delete(store):
    hyp = _hyp(status="active")
    store.add_hypothesis(hyp)
    assert store.set_hypothesis_status(hyp.id, "suspended") is True
    assert store.get_hypothesis(hyp.id).status == "suspended"
    assert store.set_hypothesis_status("nope", "active") is False
    assert store.delete_node("Hypothesis", hyp.id) is True
    assert store.get_node("Hypothesis", hyp.id) is None


def test_neighbors(store):
    obs = _obs()
    store.add_observation(obs)
    ent = store.upsert_entity("장소", "집")
    store.add_edge(Edge(rel="OBSERVED_IN", from_kind="Entity", from_id=ent.id,
                        to_kind="Observation", to_id=obs.id, evidence=obs.id))
    nb = store.neighbors("Observation", obs.id)
    assert nb["node"]["id"] == obs.id
    assert len(nb["in_edges"]) == 1
    assert nb["in_edges"][0]["rel"] == "OBSERVED_IN"
    assert store.neighbors("Observation", "nope")["node"] is None


def test_upsert_entity_found_by_alias_with_different_name(store):
    first = store.upsert_entity("장소", "단골 식당", aliases=["식당"])
    again = store.upsert_entity("장소", "식당", aliases=["우리 식당"])
    assert again.id == first.id
    assert again.name == "단골 식당"
    assert "식당" in again.aliases and "우리 식당" in again.aliases
    assert "단골 식당" not in again.aliases
