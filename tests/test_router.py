from mirror import router as router_mod
from mirror import schema


def test_mock_intents(store):
    r = router_mod.MockRouter()
    assert r.decide("둘 중 뭐가 좋을까 추천해줘", store).intent == "결정 도움"
    assert r.decide("요즘 너무 힘들고 걱정돼", store).intent == "감정 대화"
    assert r.decide("내일 식당 예약해줘", store).intent == "실행 요청"
    assert r.decide("이번 주 기록을 정리해줘", store).intent == "정보 정리"


def test_mock_style_and_scope(store):
    store.add_style(schema.Style(id="sty-1", pattern="정보성 블로그형",
                                 example_refs=["obs-x"], intent="정보 정리"))
    store.upsert_entity("장소", "A카페")
    d = router_mod.MockRouter().decide("A카페 근처 맛집을 정리해줘", store)
    assert d.intent == "정보 정리"
    assert d.style_id == "sty-1"
    assert d.style_examples == ["obs-x"]
    assert "취향·경험" in d.domains
    assert "A카페" in d.entities


def test_llm_router_falls_back(store, monkeypatch):
    router = router_mod.LlmRouter(backend="spark", muse_bin="definitely-missing-bin")
    d = router.decide("둘 중 뭐가 좋을까", store)
    assert d.routed_by.startswith("llm+fallback")
    assert d.fallback_reason
    assert d.intent == "결정 도움"


def test_llm_router_coerces_valid_output(store, monkeypatch):
    store.add_style(schema.Style(id="sty-9", pattern="건조한 업무형",
                                 intent="정보 정리"))
    router = router_mod.LlmRouter(backend="spark")
    monkeypatch.setattr(router, "_call", lambda system, user: {
        "intent": "정보 정리", "style_id": "sty-9",
        "domains": ["취향·경험", "날조 영역"], "entities": ["A카페"],
        "confidence": 0.8})
    d = router.decide("아무거나", store)
    assert d.routed_by == "llm:spark"
    assert d.style_id == "sty-9"
    assert d.domains == ["취향·경험"]


def test_llm_router_rejects_bad_intent(store, monkeypatch):
    router = router_mod.LlmRouter(backend="spark")
    monkeypatch.setattr(router, "_call",
                        lambda system, user: {"intent": "독심술"})
    d = router.decide("둘 중 뭐가 좋을까", store)
    assert d.routed_by.startswith("llm+fallback")
    assert d.intent == "결정 도움"
