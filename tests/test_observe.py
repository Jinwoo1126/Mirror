from mirror import control as control_mod
from mirror import extract as ex
from mirror import observe as observe_mod
from mirror import schema
from tests.conftest import DINNER_TEXT


def _event(text=DINNER_TEXT):
    return {"text": text, "occurred_at": "2026-10-16T20:00:00+09:00"}


class _FailingExtractor:
    name = "failing"

    def extract(self, obs):
        raise ex.ExtractionError("boom")


class _InvalidExtractor:
    name = "invalid"

    def extract(self, obs):
        return ex.ExtractionResult(
            nodes=[ex.NodeSpec(schema.HYPOTHESIS, "hyp-0", {"statement": "s"})],
            edges=[ex.EdgeSpec("SUPPORTS", "obs", "hyp-0", "")])


def test_observe_success(store, mock):
    res = observe_mod.observe(store, mock, "s1", _event(), 0)
    assert res.ok and not res.duplicate
    assert res.revision == 1
    assert res.nodes_added >= 2 and res.edges_added >= 1
    assert store.get_observation_by_source("s1").id == res.obs_id


def test_observe_duplicate_no_bump(store, mock):
    observe_mod.observe(store, mock, "s1", _event(), 0)
    res = observe_mod.observe(store, mock, "s1", _event(), 1)
    assert res.ok and res.duplicate
    assert res.revision == 1


def test_observe_stale_revision_writes_nothing(store, mock):
    res = observe_mod.observe(store, mock, "s1", _event(), 99)
    assert not res.ok and "stale" in res.error
    assert store.get_observation_by_source("s1") is None
    assert store.get_revision() == 0


def test_observe_empty_text_writes_nothing(store, mock):
    res = observe_mod.observe(store, mock, "s1", {"text": "  "}, 0)
    assert not res.ok
    assert store.get_revision() == 0


def test_observe_extract_failure_writes_nothing(store):
    res = observe_mod.observe(store, _FailingExtractor(), "s1", _event(), 0)
    assert not res.ok and "extract" in res.error
    assert store.get_observation_by_source("s1") is None


def test_observe_invalid_extraction_writes_nothing(store):
    res = observe_mod.observe(store, _InvalidExtractor(), "s1", _event(), 0)
    assert not res.ok and "validate" in res.error
    assert store.get_observation_by_source("s1") is None


def test_observe_skips_blocked_understanding(store, mock):
    first = observe_mod.observe(store, mock, "s1", _event(), 0)
    assert first.ok
    statement = "피곤할 때는 고르는 수고를 회피한다"
    hyp_id = store.find_hypothesis_by_statement(statement)
    assert hyp_id
    deleted = control_mod.control(store, "delete",
                                  {"kind": "Hypothesis", "id": hyp_id})
    assert deleted.ok
    # 같은 진술을 재생산하는 새 관찰 → 관찰은 남고 가설은 차단
    res = observe_mod.observe(
        store, mock, "s2",
        _event("피곤한 저녁에 익숙한 곳에서 먹었다."),
        deleted.revision)
    assert res.ok
    assert any("hyp-0" in s for s in res.skipped_blocked)
    assert store.find_hypothesis_by_statement(statement) is None
