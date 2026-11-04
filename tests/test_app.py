import pytest
from starlette.testclient import TestClient

from mirror import app as app_mod


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRROR_DB", str(tmp_path / "app.kuzu"))
    monkeypatch.setenv("MIRROR_EXTRACTOR", "mock")
    monkeypatch.setenv("MIRROR_NO_INDEX", "1")
    monkeypatch.setenv("MIRROR_ROUTER", "mock")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app_mod.reset_state()
    test_client = TestClient(app_mod.app)
    for text, occurred in [
        ("피곤한 저녁에 익숙한 집에 배달을 주문했다.",
         "2026-10-16T20:00:00+09:00"),
        ("지친 밤에 단골집에 또 갔다.", "2026-10-17T20:00:00+09:00"),
    ]:
        res = test_client.post("/api/memories",
                               json={"text": text, "occurred_at": occurred})
        assert res.status_code == 200 and res.json()["ok"]
    yield test_client
    app_mod.reset_state()


def test_homepage_and_status(client):
    assert client.get("/").status_code == 200
    assert "Mirror" in client.get("/").text
    status = client.get("/api/status").json()
    assert status["counts"]["Observation"] == 2
    assert status["index_mode"] == "off"


def test_chat_local_answer_with_citations(client):
    res = client.post("/api/chat", json={"message": "피곤한 저녁에 뭐 먹지"})
    assert res.status_code == 200
    data = res.json()
    assert data["mode"] == "local"
    assert data["text"] and data["citations"]
    assert data["decision"]["intent"]


def test_chat_with_options_proposal(client):
    res = client.post("/api/chat", json={
        "message": "저녁 주문", "options": ["익숙한 집", "새 식당"]})
    assert res.status_code == 200
    assert res.json()["proposal"]["intervention"]["level"] in (0, 1)


def test_memories_search_add_delete(client):
    found = client.get("/api/memories", params={"q": "단골집"}).json()
    assert len(found["items"]) == 1
    added = client.post("/api/memories",
                        json={"text": "개운한 아침에 산책했다"}).json()
    assert added["ok"]
    obs_id = added["obs_id"]
    gone = client.delete(f"/api/memories/{obs_id}")
    assert gone.status_code == 200 and gone.json()["ok"]
    assert client.get("/api/memories", params={"q": "산책"}).json()["items"] == []


def test_hypotheses_feedback(client):
    hyps = client.get("/api/hypotheses").json()["hypotheses"]
    hyp_id = next(h["id"] for h in hyps if "수고" in h["statement"])
    res = client.post(f"/api/hypotheses/{hyp_id}/feedback",
                      json={"verdict": "아님"})
    assert res.status_code == 200 and res.json()["ok"]
    again = client.get("/api/hypotheses").json()["hypotheses"]
    assert next(h for h in again if h["id"] == hyp_id)["status"] == "suspended"


def test_graph_report_relations_decide_maintain(client):
    graph = client.get("/api/graph").json()
    assert graph["nodes"] and graph["edges"]
    report = client.get("/api/report").json()
    assert "New hypotheses" in report["markdown"]
    relations = client.get("/api/relations").json()
    assert isinstance(relations["vocabulary"], list)
    decided = client.post("/api/decide", json={
        "request": "저녁 주문", "options": ["익숙한 집", "새 식당"]}).json()
    assert decided["intervention"]["level"] in (0, 1)
    promoted = client.post("/api/maintain", json={"job": "promote"}).json()
    assert len(promoted["promoted"]) >= 1
    assert client.post("/api/maintain", json={"job": "nope"}).status_code == 400


def test_chat_generate_keeps_backend_after_transient_error(monkeypatch):
    from mirror import app as app_mod
    from mirror import llm as llm_mod

    calls = []

    def flaky(prompt, timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            raise llm_mod.LlmError("muse exec timed out after 60s")
        return "ok"

    monkeypatch.setattr(app_mod, "_BACKENDS", (("spark", flaky),))
    monkeypatch.setattr(app_mod, "_llm_unavailable", set())
    monkeypatch.setattr(app_mod, "_llm_backend", None)
    assert app_mod.chat_generate("p", timeout=5)[0] == "local"
    assert app_mod.chat_generate("p", timeout=5) == ("spark", "ok")
    assert calls == [5, 5]


def test_product_default_extractor_is_llm_not_rules(monkeypatch):
    from mirror import app as app_mod

    monkeypatch.delenv("MIRROR_EXTRACTOR", raising=False)
    monkeypatch.setattr(app_mod, "_extractor", None)
    assert app_mod.get_extractor().name == "spark"


def test_static_serves_only_the_avatar_files(client, tmp_path, monkeypatch):
    from mirror import app as app_mod

    (tmp_path / "avatar.json").write_text('{"image": "avatar.png"}')
    (tmp_path / "secret.txt").write_text("nope")
    monkeypatch.setattr(app_mod, "STATIC_DIR", tmp_path)
    assert client.get("/static/avatar.json").json()["image"] == "avatar.png"
    assert client.get("/static/secret.txt").status_code == 404
    assert client.get("/static/avatar.png").status_code == 404      # allowed name, no file
    assert client.get("/static/..%2Fapp.py").status_code == 404


def test_gate_by_relevance_uses_a_lower_bar_when_a_situation_matched():
    from mirror import app as app_mod

    items = [{"id": "a", "score": 0.66}, {"id": "b", "score": 0.60}, {"id": "c", "score": 0.50}]
    plain = app_mod.gate_by_relevance(items, matched=False, hybrid=True)
    assert [i["id"] for i in plain] == ["a"]
    matched = app_mod.gate_by_relevance(items, matched=True, hybrid=True)
    assert [i["id"] for i in matched] == ["a", "b"]
    assert [i["id"] for i in app_mod.gate_by_relevance(items, False, True, min_score=0.4)] == ["a", "b"]


def test_gate_by_relevance_does_not_drop_anything_without_embeddings():
    from mirror import app as app_mod

    items = [{"id": "a", "score": 0.1}]
    assert app_mod.gate_by_relevance(items, matched=False, hybrid=False) == items


def test_gate_drops_items_far_behind_the_best_and_caps_the_count():
    from mirror import app as app_mod

    items = [{"id": str(i), "score": 0.90 - i * 0.01} for i in range(9)] + [{"id": "far", "score": 0.70}]
    kept = app_mod.gate_by_relevance(items, matched=False, hybrid=True)
    assert len(kept) == app_mod.MAX_ITEMS and "far" not in [i["id"] for i in kept]
    assert [i["id"] for i in kept] == ["0", "1", "2", "3", "4"]   # best first


def test_retrieve_for_outside_callers_keeps_conditions(client):
    from mirror import schema
    store = app_mod.get_store()
    store.add_hypothesis(schema.Hypothesis(
        id="hyp-cond", statement="피곤한 저녁에는 익숙한 배달을 고른다",
        conditions=["피곤한 저녁"], status=schema.STATUS_ACTIVE, domain="food"))
    store.bump_revision()
    items = client.post("/api/retrieve", json={"request": "피곤한 저녁에 뭐 먹지"}).json()["items"]
    item = next(i for i in items if i["id"] == "hyp-cond")
    assert item["conditions"] == ["피곤한 저녁"] and item["domain"] == "food"
    assert item["status"] == "active" and "counter_examples" in item
