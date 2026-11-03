import pytest

from mirror import control as control_mod
from mirror import observe as observe_mod
from mirror import schema
from tests.conftest import DINNER_TEXT


H1_STATEMENT = "피곤할 때는 고르는 수고를 회피한다"


@pytest.fixture
def seeded(store, mock):
    res = observe_mod.observe(
        store, mock, "s1",
        {"text": DINNER_TEXT,
         "occurred_at": "2026-10-16T20:00:00+09:00"}, 0)
    assert res.ok
    hyp_id = store._rows("MATCH (h:Hypothesis {statement: $s}) RETURN h.id",
                         {"s": H1_STATEMENT})[0][0]
    all_hyps = [r[0] for r in store._rows("MATCH (h:Hypothesis) RETURN h.id")]
    store.set_hypothesis_status(hyp_id, "active")
    return {"obs_id": res.obs_id, "hyp_id": hyp_id, "all_hyps": all_hyps,
            "revision": res.revision}


def test_query(store, seeded):
    res = control_mod.control(store, "query",
                              {"kind": "Hypothesis", "id": seeded["hyp_id"]})
    assert res.ok and res.detail["node"]["id"] == seeded["hyp_id"]
    missing = control_mod.control(store, "query",
                                  {"kind": "Hypothesis", "id": "nope"})
    assert not missing.ok


def test_correct_fact_demotes_dependents(store, seeded):
    res = control_mod.control(
        store, "correct", {"kind": "Observation", "id": seeded["obs_id"]},
        {"kind": "fact", "text": "그때 고른 건 다른 집이었어",
         "corrected_text": "피곤해서 다른 집에 주문했다"})
    assert res.ok
    assert set(res.detail["demoted_to_candidate"]) == set(seeded["all_hyps"])
    assert store.get_hypothesis(seeded["hyp_id"]).status == "candidate"
    assert "다른 집" in store.get_observation(seeded["obs_id"]).text


def test_correct_interpretation_suspends(store, seeded):
    res = control_mod.control(
        store, "correct", {"kind": "Hypothesis", "id": seeded["hyp_id"]},
        {"kind": "interpretation", "text": "그런 이유는 아니야"})
    assert res.ok
    assert store.get_hypothesis(seeded["hyp_id"]).status == "suspended"
    assert "alternatives_boosted_at_retrieval" in res.detail


def test_correct_change_retires_and_spawns_candidate(store, seeded):
    res = control_mod.control(
        store, "correct", {"kind": "Hypothesis", "id": seeded["hyp_id"]},
        {"kind": "change", "text": "요즘은 달라졌어",
         "new_statement": "요즘은 새로운 곳을 먼저 찾는다"})
    assert res.ok
    old = store.get_hypothesis(seeded["hyp_id"])
    assert old.status == "retired" and old.valid_to is not None
    new_id = res.detail["new_candidate"]
    assert store.get_hypothesis(new_id).status == "candidate"


def test_correct_request_leaves_hypotheses_alone(store, seeded):
    res = control_mod.control(
        store, "correct", {"kind": "Observation", "id": seeded["obs_id"]},
        {"kind": "request", "text": "오늘은 익숙한 걸 원해",
         "context_kind": "당일 요청", "context_value": "익숙한 것"})
    assert res.ok and "ephemeral_context" in res.detail
    assert store.get_hypothesis(seeded["hyp_id"]).status == "active"


def test_correct_rejects_bad_kind(store, seeded):
    res = control_mod.control(
        store, "correct", {"kind": "Hypothesis", "id": seeded["hyp_id"]},
        {"kind": "mind-reading", "text": "x"})
    assert not res.ok


def test_suppress(store, seeded):
    res = control_mod.control(store, "suppress",
                              {"kind": "Hypothesis", "id": seeded["hyp_id"]})
    assert res.ok
    assert store.get_hypothesis(seeded["hyp_id"]).status == "suppressed"
    ent = store.upsert_entity("장소", "집")
    bad = control_mod.control(store, "suppress",
                              {"kind": "Entity", "id": ent.id})
    assert not bad.ok


def test_delete_blocks_and_reallow_lifts(store, mock, seeded):
    res = control_mod.control(store, "delete",
                              {"kind": "Hypothesis", "id": seeded["hyp_id"]})
    assert res.ok and res.detail["blocked"]
    assert store.get_node("Hypothesis", seeded["hyp_id"]) is None
    back = control_mod.control(store, "reallow",
                               {"kind": "Hypothesis", "id": seeded["hyp_id"]})
    assert back.ok and back.detail["unblocked"]
    # 다시 수집하면 가설이 되살아난다 (차단 해제 확인)
    res2 = observe_mod.observe(
        store, mock, "s2",
        {"text": "피곤한 저녁에 익숙한 곳에서 먹었다."}, back.revision)
    assert res2.ok and res2.skipped_blocked == []


def test_reallow_nothing(store, seeded):
    res = control_mod.control(store, "reallow",
                              {"kind": "Hypothesis", "id": seeded["hyp_id"]})
    assert not res.ok


def test_unknown_action(store):
    assert not control_mod.control(store, "hypnotize").ok
