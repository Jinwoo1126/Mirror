import pytest

from mirror import extract as ex
from mirror import observe as observe_mod
from mirror import schema


def test_related_to_requires_phrase_and_evidence(store):
    obs = schema.Observation(id=schema.new_id("obs"), source_id="s",
                             text="t", occurred_at="2026-10-16T00:00:00+09:00")
    store.add_observation(obs)
    e1 = store.upsert_entity("사람", "친구 J")
    e2 = store.upsert_entity("프로젝트", "DJ 선곡")
    with pytest.raises(schema.SchemaError):
        store.add_edge(schema.Edge(rel="RELATED_TO", from_kind="Entity",
                                   from_id=e1.id, to_kind="Entity",
                                   to_id=e2.id, evidence=obs.id, phrase=""))
    with pytest.raises(schema.SchemaError):
        store.add_edge(schema.Edge(rel="RELATED_TO", from_kind="Entity",
                                   from_id=e1.id, to_kind="Entity",
                                   to_id=e2.id, evidence="", phrase="좋아한다"))
    store.add_edge(schema.Edge(rel="RELATED_TO", from_kind="Entity",
                               from_id=e1.id, to_kind="Entity",
                               to_id=e2.id, evidence=obs.id,
                               phrase="좋아한다"))
    nb = store.neighbors("Entity", e1.id)
    assert nb["out_edges"][0]["phrase"] == "좋아한다"


def test_relation_vocabulary_and_expansion(store):
    obs = schema.Observation(id=schema.new_id("obs"), source_id="s",
                             text="t", occurred_at="2026-10-16T00:00:00+09:00")
    store.add_observation(obs)
    a = store.upsert_entity("장소", "A카페")
    b = store.upsert_entity("장소", "B카페")
    store.add_edge(schema.Edge(rel="RELATED_TO", from_kind="Entity",
                               from_id=a.id, to_kind="Entity",
                               to_id=b.id, evidence=obs.id,
                               phrase="옆에 있다"))
    vocab = store.relation_vocabulary()
    assert vocab == [{"phrase": "옆에 있다", "count": 1,
                      "examples": [{"from": a.id, "to": b.id,
                                    "evidence": obs.id}]}]
    assert store.related_entity_names(["A카페"]) == ["B카페"]
    assert store.related_entity_names(["B카페"]) == ["A카페"]


def test_entity_description_accumulates(store):
    e1 = store.upsert_entity("장소", "A카페", mention="조용한 A카페에서 아침을 먹었다")
    assert "조용한" in e1.description
    e2 = store.upsert_entity("장소", "A카페", mention="A카페는 저녁에도 조용하다")
    assert e1.id == e2.id
    assert "저녁에도" in e2.description
    # 중복 언급은 누적하지 않음
    e3 = store.upsert_entity("장소", "A카페", mention="조용한 A카페에서 아침을 먹었다")
    assert e3.description == e2.description


def test_mock_extracts_relations(mock):
    result = mock.extract(ex.ObservationInput(
        source_id="s", text="DJ 선곡을 친구 J가 좋아했다",
        occurred_at="2026-10-19T22:00:00+09:00"))
    assert ex.validate_extraction(result) == []
    rels = [e for e in result.edges if e.rel == "RELATED_TO"]
    assert len(rels) == 1 and rels[0].phrase == "좋아한다"
    morning = mock.extract(ex.ObservationInput(
        source_id="m", text="아침에 조용한 A카페에서 새로운 메뉴를 시도했다",
        occurred_at="2026-10-22T08:30:00+09:00"))
    assert ex.validate_extraction(morning) == []
    phrases = [e.phrase for e in morning.edges if e.rel == "RELATED_TO"]
    assert "아침에 이용한다" in phrases


def test_observe_persists_relations_and_descriptions(store, mock):
    res = observe_mod.observe(store, mock, "s1", {
        "text": "DJ 선곡을 친구 J가 좋아했다",
        "occurred_at": "2026-10-19T22:00:00+09:00"}, 0)
    assert res.ok
    vocab = store.relation_vocabulary()
    assert [v["phrase"] for v in vocab] == ["좋아한다"]
    ent = store.find_entity("사람", "친구 J")
    assert ent is not None and "좋아했다" in ent.description


def test_llm_relation_output_validates():
    data = {"nodes": [
        {"kind": "Entity", "ref": "a",
         "attrs": {"kind": "장소", "name": "A카페"}},
        {"kind": "Entity", "ref": "b",
         "attrs": {"kind": "장소", "name": "B카페"}}],
        "edges": [
        {"rel": "OBSERVED_IN", "from_ref": "a", "to_ref": "obs",
         "evidence_ref": "obs"},
        {"rel": "RELATED_TO", "from_ref": "a", "to_ref": "b",
         "evidence_ref": "obs", "phrase": "옆에 있다"}]}
    assert ex.validate_extraction(ex.parse_extraction_json(data)) == []
    data["edges"][1]["phrase"] = ""
    assert ex.validate_extraction(ex.parse_extraction_json(data)) != []
