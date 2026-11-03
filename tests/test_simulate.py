from mirror import schema
from mirror import simulate as simulate_mod


def _seed_competing(store):
    """경쟁 가설 2개 + 근거. H1=익숙, H2=새로움."""
    o1 = schema.Observation(id=schema.new_id("obs"), source_id="o1",
                            text="피곤해서 익숙한 집에 주문했다",
                            occurred_at="2026-10-16T20:00:00+09:00")
    o2 = schema.Observation(id=schema.new_id("obs"), source_id="o2",
                            text="개운해서 새로운 식당에 갔다",
                            occurred_at="2026-10-17T20:00:00+09:00")
    for o in (o1, o2):
        store.add_observation(o)
    h1 = schema.new_id("hyp")
    h2 = schema.new_id("hyp")
    store.add_hypothesis(schema.Hypothesis(id=h1, statement="익숙한 집이 편하다",
                                           status="active"))
    store.add_hypothesis(schema.Hypothesis(id=h2, statement="새로운 식당이 좋다",
                                           status="active"))
    store.add_edge(schema.Edge(rel="SUPPORTS", from_kind="Observation",
                               from_id=o1.id, to_kind="Hypothesis",
                               to_id=h1, evidence=o1.id))
    store.add_edge(schema.Edge(rel="SUPPORTS", from_kind="Observation",
                               from_id=o2.id, to_kind="Hypothesis",
                               to_id=h2, evidence=o2.id))
    for a, b in ((h1, h2), (h2, h1)):
        store.add_edge(schema.Edge(rel="ALTERNATIVE_TO",
                                   from_kind="Hypothesis", from_id=a,
                                   to_kind="Hypothesis", to_id=b,
                                   evidence=o1.id))
    return h1, h2


def test_simulate_disagree(store):
    h1, h2 = _seed_competing(store)
    res = simulate_mod.simulate(store, "저녁 주문", ["익숙한 집", "새로운 식당"])
    assert res.predictors_used == 2
    assert res.votes == {"익숙한 집": 1, "새로운 식당": 1}
    assert res.agree is False
    assert res.rounds_used == 1  # 조건 없음 → what-if 없음


def test_simulate_what_if_round(store):
    _seed_competing(store)
    conds = [{"kind": "상태", "value": "피곤함"}]
    res = simulate_mod.simulate(store, "저녁 주문", ["익숙한 집", "새로운 식당"],
                                conditions=conds)
    assert res.rounds_used == 2
    assert len(res.what_if) == 1
    assert res.what_if[0]["conditions"] == []


def test_simulate_caps_and_empty(store):
    res = simulate_mod.simulate(store, "뭐 먹지", ["A", "B"])
    assert res.predictors_used == 0 and res.note == "insufficient_views"
    _seed_competing(store)
    capped = simulate_mod.simulate(store, "저녁", ["익숙한 집", "새로운 식당"],
                                   max_predictors=1)
    assert capped.predictors_used == 1


def test_record_outcomes(store):
    h1, h2 = _seed_competing(store)
    res = simulate_mod.simulate(store, "저녁 주문", ["익숙한 집", "새로운 식당"])
    n = simulate_mod.record_outcomes(store, res, "ep-1", "익숙한 집")
    assert n == 2
    assert store.hit_stats(h1) == {"n": 1, "hits": 1, "rate": 1.0}
    assert store.hit_stats(h2)["hits"] == 0


def test_pick_prefers_alternative_clique(store):
    h1, h2 = _seed_competing(store)
    h3 = schema.new_id("hyp")
    store.add_hypothesis(schema.Hypothesis(id=h3, statement="무관한 가설",
                                           status="active"))
    picked = simulate_mod.pick_hypotheses(
        store, "저녁 주문", ["익숙한 집", "새로운 식당"],
        schema.utcnow_iso(), max_n=2)
    assert {h.id for h in picked} == {h1, h2}
