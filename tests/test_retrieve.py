import pytest

from mirror import observe as observe_mod
from mirror import retrieve as retrieve_mod


@pytest.fixture
def seeded(store, mock):
    observe_mod.observe(
        store, mock, "s1",
        {"text": "피곤한 저녁에 익숙한 집에 배달을 주문했다.",
         "occurred_at": "2026-10-16T20:00:00+09:00"}, 0)
    observe_mod.observe(
        store, mock, "s2",
        {"text": "주말에 처음으로 방탈출에 갔다.",
         "occurred_at": "2026-10-25T14:00:00+09:00"}, 1)
    for row in store._rows("MATCH (h:Hypothesis) RETURN h.id"):
        store.set_hypothesis_status(row[0], "active")
    return store


def test_retrieve_returns_evidence_and_policy(seeded):
    res = retrieve_mod.retrieve(seeded, "피곤한 저녁 주문 수고를")
    assert res["items"], "expected related items"
    obs_items = [i for i in res["items"] if i["kind"] == "Observation"]
    assert obs_items and "text" in obs_items[0]
    hyp_items = [i for i in res["items"] if i["kind"] == "Hypothesis"]
    assert hyp_items
    hyp = hyp_items[0]
    assert hyp["supports"] and "use_policy" in hyp and "contradicts" in hyp


def test_retrieve_explicit_conditions_first(seeded):
    res = retrieve_mod.retrieve(
        seeded, "저녁 주문",
        explicit_conditions=[{"kind": "상태", "value": "피곤함"}])
    assert res["conditions"][0] == {"kind": "상태", "value": "피곤함"}


def test_retrieve_budget_and_as_of(seeded):
    res = retrieve_mod.retrieve(seeded, "저녁", budget=1)
    assert len(res["items"]) <= 1
    past = retrieve_mod.retrieve(seeded, "저녁",
                                 as_of="2000-01-01T00:00:00+00:00")
    assert past["items"] == []


def test_retrieve_never_raises(store, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(store, "search_observations", _boom)
    res = retrieve_mod.retrieve(store, "저녁")
    assert res["items"] == [] and "error" in res
