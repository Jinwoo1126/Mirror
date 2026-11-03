from mirror import assistant as assistant_mod
from mirror import observe as observe_mod
from mirror import router as router_mod
from mirror import schema


def _seed(store, mock):
    res = observe_mod.observe(
        store, mock, "s1",
        {"text": "피곤한 저녁에 익숙한 집에 배달을 주문했다.",
         "occurred_at": "2026-10-16T20:00:00+09:00"}, 0)
    assert res.ok
    return res


def test_answer_wires_router_retrieve_llm(store, mock):
    _seed(store, mock)
    seen = {}

    def fake_llm(prompt):
        seen["prompt"] = prompt
        return "답변: 익숙한 집"

    out = assistant_mod.answer(store, router_mod.MockRouter(), fake_llm,
                               "피곤한 저녁 주문 수고를")
    assert out["text"] == "답변: 익숙한 집"
    assert out["citations"], "expected evidence citations"
    assert "피곤한 저녁" in seen["prompt"]
    assert out["decision"]["intent"]


def test_answer_explicit_style_overrides(store, mock):
    _seed(store, mock)
    store.add_style(schema.Style(id="sty-a", pattern="정보성 블로그형",
                                 intent="정보 정리"))
    store.add_style(schema.Style(id="sty-b", pattern="건조한 업무형",
                                 intent="실행 요청"))
    out = assistant_mod.answer(store, router_mod.MockRouter(),
                               lambda p: "ok", "이번 주 정리해줘",
                               explicit_style="건조한 업무형")
    assert out["style_id"] == "sty-b"
    assert "overrides" in out["style_note"]


def test_answer_llm_failure_keeps_retrieval(store, mock):
    _seed(store, mock)

    def boom(prompt):
        raise RuntimeError("down")

    out = assistant_mod.answer(store, router_mod.MockRouter(), boom,
                               "피곤한 저녁 주문")
    assert out["text"] == "" and "error" in out
    assert out["retrieval"], "retrieval still returned for host fallback"
