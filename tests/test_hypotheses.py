import pytest

from mirror import hypotheses as hyp_mod
from mirror import observe as observe_mod
from mirror import schema


def _observe(store, mock, source_id, text, occurred_at, revision):
    res = observe_mod.observe(store, mock, source_id,
                              {"text": text, "occurred_at": occurred_at},
                              revision)
    assert res.ok
    return res


@pytest.fixture
def two_day(store, mock):
    _observe(store, mock, "s1", "피곤한 저녁에 익숙한 곳에서 먹었다.",
             "2026-10-16T20:00:00+09:00", 0)
    res = _observe(store, mock, "s2", "지친 밤에 단골집에 또 갔다.",
                   "2026-10-17T20:00:00+09:00", 1)
    hyp_id = store.find_hypothesis_by_statement("피곤할 때는 고르는 수고를 회피한다")
    assert hyp_id is not None
    return {"hyp_id": hyp_id, "revision": res.revision}


def test_promote_two_distinct_days(store, two_day):
    assert store.get_hypothesis(two_day["hyp_id"]).status == "candidate"
    out = hyp_mod.promote_candidates(store)
    assert two_day["hyp_id"] in out["promoted"]
    assert store.get_hypothesis(two_day["hyp_id"]).status == "active"


def test_promote_blocked_by_contradiction(store, mock):
    _observe(store, mock, "s1", "피곤한 저녁에 익숙한 곳에서 먹었다.",
             "2026-10-16T20:00:00+09:00", 0)
    _observe(store, mock, "s2", "지친 밤에 단골집에 또 갔다.",
             "2026-10-17T20:00:00+09:00", 1)
    hyp_id = store.find_hypothesis_by_statement("피곤할 때는 고르는 수고를 회피한다")
    obs = schema.Observation(id=schema.new_id("obs"), source_id="counter",
                             text="피곤해도 새 곳을 갔다",
                             occurred_at="2026-10-18T20:00:00+09:00")
    store.add_observation(obs)
    store.add_edge(schema.Edge(rel="CONTRADICTS", from_kind="Observation",
                               from_id=obs.id, to_kind="Hypothesis",
                               to_id=hyp_id, evidence=obs.id))
    assert hyp_id not in hyp_mod.promote_candidates(store)["promoted"]
    assert store.get_hypothesis(hyp_id).status == "candidate"


def test_promote_needs_two_days(store, mock):
    _observe(store, mock, "s1", "피곤한 저녁에 익숙한 곳에서 먹었다.",
             "2026-10-16T20:00:00+09:00", 0)
    out = hyp_mod.promote_candidates(store)
    assert out["promoted"] == [] and out["evaluated"] >= 1


def test_weekly_review_suspends_worse_alternative(store, mock, two_day):
    hyp_mod.promote_candidates(store)
    good = two_day["hyp_id"]
    bad = schema.new_id("hyp")
    store.add_hypothesis(schema.Hypothesis(id=bad, statement="반대 이해",
                                           status="active"))
    ev = store.get_observation_by_source("s1").id
    for a, b in ((good, bad), (bad, good)):
        store.add_edge(schema.Edge(rel="ALTERNATIVE_TO",
                                   from_kind="Hypothesis", from_id=a,
                                   to_kind="Hypothesis", to_id=b, evidence=ev))
    for i in range(3):
        store.log_prediction(good, f"ep-{i}", "A", "A")
        store.log_prediction(bad, f"ep-{i}", "B", "A")
    # 기록 부족 가설은 유지
    fresh = schema.new_id("hyp")
    store.add_hypothesis(schema.Hypothesis(id=fresh, statement="자료 부족",
                                           status="active"))
    out = hyp_mod.weekly_review(store)
    assert out["suspended"] == [bad]
    assert store.get_hypothesis(good).status == "active"
    assert store.get_hypothesis(fresh).status == "active"


def test_nightly_links_shared_alternatives(store):
    o1 = schema.Observation(id=schema.new_id("obs"), source_id="o1",
                            text="t", occurred_at="2026-10-16T00:00:00+09:00")
    store.add_observation(o1)
    h1, h2 = schema.new_id("hyp"), schema.new_id("hyp")
    store.add_hypothesis(schema.Hypothesis(id=h1, statement="A"))
    store.add_hypothesis(schema.Hypothesis(id=h2, statement="B"))
    for h in (h1, h2):
        store.add_edge(schema.Edge(rel="SUPPORTS", from_kind="Observation",
                                   from_id=o1.id, to_kind="Hypothesis",
                                   to_id=h, evidence=o1.id))
    out = hyp_mod.nightly_maintenance(store)
    assert out["linked_alternatives"] == 2
    assert store.alternatives(h1) == [h2]


def test_nightly_merges_duplicate_entities(store):
    e1 = store.upsert_entity("장소", "A카페")
    e2 = store.upsert_entity("장소", "B카페")
    # 별칭이 겹치지 않아 자동 병합되지 않은 표기 변형
    store._conn.execute("MATCH (e:Entity {id: $id}) SET e.name = $n",
                        {"id": e2.id, "n": "A 카페"})
    out = hyp_mod.nightly_maintenance(store)
    assert len(out["merged_entities"]) == 1
    survivors = [e for e in (e1.id, e2.id)
                 if store.get_node("Entity", e) is not None]
    assert len(survivors) == 1


def test_nightly_flags_stale(store, mock):
    _observe(store, mock, "s1", "피곤한 저녁에 익숙한 곳에서 먹었다.",
             "2020-01-01T20:00:00+09:00", 0)
    _observe(store, mock, "s2", "지친 밤에 단골집에 또 갔다.",
             "2020-01-02T20:00:00+09:00", 1)
    hyp_mod.promote_candidates(store)
    out = hyp_mod.nightly_maintenance(store, stale_days=180)
    # 피곤+익숙 문장은 경쟁 가설 2개(피로 회피 / 익숙함 선호)를 만든다
    assert len(out["stale_candidates"]) == 2
