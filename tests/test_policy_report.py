import pytest

from mirror import observe as observe_mod
from mirror import policy as policy_mod
from mirror import report as report_mod
from mirror import schema
from mirror import simulate as simulate_mod


def _two_day_hyp(store, mock):
    observe_mod.observe(store, mock, "s1", {
        "text": "피곤한 저녁에 익숙한 곳에서 먹었다.",
        "occurred_at": "2026-10-16T20:00:00+09:00"}, 0)
    observe_mod.observe(store, mock, "s2", {
        "text": "지친 밤에 단골집에 또 갔다.",
        "occurred_at": "2026-10-17T20:00:00+09:00"}, 1)
    hyp_id = store.find_hypothesis_by_statement("피곤할 때는 고르는 수고를 회피한다")
    store.set_hypothesis_status(hyp_id, "active")
    return hyp_id


def test_level0_default_silent(store):
    out = policy_mod.decide(store, "뭐 먹지")
    assert out.level == 0 and out.options_presented == []


def test_level1_on_disagreement(store, mock):
    _two_day_hyp(store, mock)
    sim = simulate_mod.simulate(store, "저녁 주문", ["익숙한 집", "새로운 식당"])
    out = policy_mod.decide(store, "저녁 주문", sim)
    assert out.level in (0, 1)
    if not sim.agree and sim.votes:
        assert out.level == 1
        assert sorted(out.options_presented) == ["새로운 식당", "익숙한 집"]
        assert out.evidence_ids


def test_level2_and_frequency_cap(store, mock):
    hyp_id = _two_day_hyp(store, mock)
    store._conn.execute(
        "MATCH (h:Hypothesis {id: $id}) SET h.use_policy = 'presentable'",
        {"id": hyp_id})
    ctx = {"topic": "저녁", "hyp_id": hyp_id}
    first = policy_mod.decide(store, "저녁", None, ctx, {"l2_enabled": True})
    assert first.level == 2
    assert "해석 없이" in first.body
    second = policy_mod.decide(store, "저녁", None, ctx, {"l2_enabled": True})
    assert second.level == 1 and second.downgraded_from == 2


def test_level2_requires_two_days_and_opt_in(store, mock):
    observe_mod.observe(store, mock, "s1", {
        "text": "피곤한 저녁에 익숙한 곳에서 먹었다.",
        "occurred_at": "2026-10-16T20:00:00+09:00"}, 0)
    hyp_id = store.find_hypothesis_by_statement("피곤할 때는 고르는 수고를 회피한다")
    store._conn.execute(
        "MATCH (h:Hypothesis {id: $id}) SET h.use_policy = 'presentable'",
        {"id": hyp_id})
    ctx = {"topic": "저녁", "hyp_id": hyp_id}
    out = policy_mod.decide(store, "저녁", None, ctx, {"l2_enabled": True})
    assert out.level == 0  # 근거 1일뿐
    out2 = policy_mod.decide(store, "저녁", None,
                             {"topic": "저녁", "hyp_id": hyp_id}, {})
    assert out2.level == 0  # 옵트인 없음


def test_level3_before_irreversible(store):
    ctx = {"topic": "메일", "omission_evidence": 2,
           "versions": ["A안", "B안"]}
    out = policy_mod.decide(store, "발송", None, ctx,
                            {"irreversible_action": True})
    assert out.level == 3
    assert "A안" in out.body and "B안" in out.body


def test_forbidden_copy_rejected():
    with pytest.raises(policy_mod.PolicyError):
        policy_mod.check_copy("당신은 회피형 타입입니다")
    with pytest.raises(policy_mod.PolicyError):
        policy_mod.check_copy("우울증 진단이 의심됩니다")
    policy_mod.check_copy("두 날 모두 먼저 해줄 일을 정하셨어요")


def test_weekly_report_and_feedback(store, mock):
    hyp_id = _two_day_hyp(store, mock)
    report = report_mod.weekly_report(store, "2000-01-01T00:00:00+00:00")
    assert any(h["id"] == hyp_id for h in report["new_hypotheses"])
    assert "새로 생긴 가설" in report["markdown"]
    keep = report_mod.apply_feedback(store, hyp_id, "맞음")
    assert keep["ok"] and keep["action"] == "keep"
    wrong = report_mod.apply_feedback(store, hyp_id, "아님")
    assert wrong["ok"]
    assert store.get_hypothesis(hyp_id).status == "suspended"
    off = report_mod.apply_feedback(store, hyp_id, "쓰지 마")
    assert off["ok"]
    assert store.get_hypothesis(hyp_id).status == "suppressed"
    bad = report_mod.apply_feedback(store, hyp_id, "모름")
    assert not bad["ok"]
