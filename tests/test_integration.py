"""통합테스트: 수집→승격→조회→시뮬레이션→개입→정정→리포트→삭제→차단 전주."""

from mirror import assistant as assistant_mod
from mirror import control as control_mod
from mirror import hypotheses as hyp_mod
from mirror import ingest as ingest_mod
from mirror import observe as observe_mod
from mirror import policy as policy_mod
from mirror import report as report_mod
from mirror import retrieve as retrieve_mod
from mirror import router as router_mod
from mirror import schema
from mirror import simulate as simulate_mod
from mirror import ui_export as ui_mod

DAY1 = {"text": "피곤한 저녁에 익숙한 곳에서 먹었다.",
        "occurred_at": "2026-10-16T20:00:00+09:00"}
DAY2 = {"text": "지친 밤에 단골집에 또 갔다.",
        "occurred_at": "2026-10-17T20:00:00+09:00"}
H1 = "피곤할 때는 고르는 수고를 회피한다"


def test_full_lifecycle(store, mock):
    # 1. 수집 2건 → 같은 가설에 근거 누적
    r1 = observe_mod.observe(store, mock, "s1", DAY1, 0)
    r2 = observe_mod.observe(store, mock, "s2", DAY2, r1.revision)
    assert r2.reused_nodes >= 1
    hyp_id = store.find_hypothesis_by_statement(H1)
    assert len(store.supporting_observations(hyp_id)) == 2

    # 2. 승격 (서로 다른 날 2건, 반례 없음)
    assert hyp_id in hyp_mod.promote_candidates(store)["promoted"]

    # 3. 라우터→어시스턴트 답변 (근거 인용 포함)
    out = assistant_mod.answer(store, router_mod.MockRouter(),
                               lambda p: "답변", "피곤한 저녁 주문 수고를")
    assert out["text"] == "답변" and out["citations"]

    # 4. decision 모드 조회 → proposal 첨부
    found = retrieve_mod.retrieve(store, "저녁 주문", budget=5,
                                  options=["익숙한 집", "새 식당"])
    assert "proposal" in found
    assert found["proposal"]["intervention"]["level"] in (0, 1)

    # 5. 시뮬레이션 → 개입 결정
    sim = simulate_mod.simulate(store, "저녁 주문",
                                ["익숙한 집", "새 식당"])
    assert sim.predictors_used >= 1
    record = simulate_mod.record_outcomes(store, sim, "ep-x", "익숙한 집")
    assert record == sim.predictors_used - sim.abstains
    intervention = policy_mod.decide(store, "저녁 주문", sim)
    assert intervention.level in (0, 1)

    # 6. 해석 이견 → 보류, 주간 리포트에 반영
    corr = control_mod.control(
        store, "correct", {"kind": "Hypothesis", "id": hyp_id},
        {"kind": "interpretation", "text": "그런 이유는 아니야"})
    assert corr.ok
    weekly = report_mod.weekly_report(store, "2000-01-01T00:00:00+00:00")
    assert any(h["id"] == hyp_id for h in weekly["new_hypotheses"])
    assert any(h["id"] == hyp_id for h in weekly["demoted"])
    assert weekly["corrections"]

    # 7. 삭제 → 재수집 차단, UI 내보내기 정합
    assert control_mod.control(store, "delete",
                               {"kind": "Hypothesis", "id": hyp_id}).ok
    r3 = observe_mod.observe(store, mock, "s3", DAY1, store.get_revision())
    assert any("hyp-" in s for s in r3.skipped_blocked)
    data = ui_mod.export_graph(store)
    assert all(h["id"] != hyp_id for h in data["hypotheses"])
    assert data["nodes"] and data["edges"]


def test_ingest_then_retrieve_flow(store, mock, tmp_path):
    path = tmp_path / "in.jsonl"
    path.write_text(
        '{"source_id": "a", "text": "피곤한 저녁에 익숙한 곳에서 먹었다.", '
        '"occurred_at": "2026-10-16T20:00:00+09:00"}\n'
        '{"source_id": "b", "text": "지친 밤에 단골집에 또 갔다.", '
        '"occurred_at": "2026-10-17T20:00:00+09:00"}\n',
        encoding="utf-8")
    stats = ingest_mod.ingest_file(str(path), store, mock)
    assert stats["ok"] == 2 and stats["failed"] == 0
    assert hyp_mod.promote_candidates(store)["promoted"]
    found = retrieve_mod.retrieve(store, "피곤한 저녁 주문 수고를")
    kinds = {i["kind"] for i in found["items"]}
    assert {"Observation", "Hypothesis"} <= kinds
