import json
import time

import httpx
import pytest
from starlette.testclient import TestClient

from mirror import edge

TOKEN = "t0k3n"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ITEMS = [{"kind": "Hypothesis", "id": "hyp-1", "statement": "Prefers short answers",
          "status": "active", "use_policy": "apply_silently", "evidence": 3}]


class Rec:
    def __init__(self):
        self.backend, self.upstream = [], []


@pytest.fixture
def make():
    def _make(inject=True, capture=False, key="sk-up", stream=False):
        rec = Rec()

        def backend(req: httpx.Request):
            body = json.loads(req.content or b"{}")
            rec.backend.append((req.url.path, body))
            if req.url.path == "/api/retrieve":
                return httpx.Response(200, json={"items": ITEMS})
            return httpx.Response(200, json={"ok": True})

        def upstream(req: httpx.Request):
            body = json.loads(req.content or b"{}")
            rec.upstream.append((req.url.path, dict(req.headers), body))
            if body.get("stream"):
                return httpx.Response(200, content=b"data: {\"x\":1}\n\ndata: [DONE]\n\n",
                                      headers={"content-type": "text/event-stream"})
            return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

        cfg = edge.Config(token=TOKEN, upstream_key=key, inject=inject, capture=capture)
        app = edge.create_app(
            cfg, backend=httpx.AsyncClient(transport=httpx.MockTransport(backend),
                                           base_url="http://backend"),
            upstream=httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
        return TestClient(app), rec, cfg
    return _make


def chat(client, text="What should I order tonight?", headers=None, **extra):
    return client.post("/v1/chat/completions", headers={**AUTH, **(headers or {})},
                       json={"model": "m", "messages": [{"role": "user", "content": text}],
                             **extra})


def test_everything_requires_the_token(make):
    client, _, _ = make()
    assert client.post("/v1/chat/completions", json={}).status_code == 401
    assert client.post("/v1/chat/completions", json={},
                       headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/mcp", json={}).status_code == 401


def test_no_configured_token_refuses_everything():
    app = edge.create_app(edge.Config(token=""))
    assert TestClient(app).post("/v1/chat/completions", json={},
                                headers={"Authorization": "Bearer "}).status_code == 401


def test_context_is_injected_as_first_system_message(make):
    client, rec, _ = make()
    r = chat(client)
    assert r.status_code == 200 and r.headers["x-mirror-injected"] == "1"
    _, headers, body = rec.upstream[0]
    assert body["messages"][0]["role"] == "system"
    assert "Prefers short answers" in body["messages"][0]["content"]
    assert body["messages"][-1]["content"] == "What should I order tonight?"
    assert headers["authorization"] == "Bearer sk-up"   # the upstream key, not the token


def test_skip_header_and_inject_off_pass_through_untouched(make):
    client, rec, _ = make()
    chat(client, headers={"x-mirror-skip": "1"})
    assert len(rec.upstream[0][2]["messages"]) == 1
    client2, rec2, _ = make(inject=False)
    chat(client2)
    assert len(rec2.upstream[0][2]["messages"]) == 1
    assert not [p for p, _ in rec2.backend if p == "/api/retrieve"]


def test_backend_down_does_not_break_the_request():
    def boom(req):
        raise httpx.ConnectError("down")

    cfg = edge.Config(token=TOKEN, upstream_key="k")
    app = edge.create_app(
        cfg, backend=httpx.AsyncClient(transport=httpx.MockTransport(boom),
                                       base_url="http://backend"),
        upstream=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"choices": []}))))
    r = TestClient(app).post("/v1/chat/completions", headers=AUTH, json={
        "messages": [{"role": "user", "content": "hello there"}]})
    assert r.status_code == 200 and r.headers["x-mirror-injected"] == "0"


def test_capture_is_off_by_default_and_remembers_only_the_user_message(make):
    client, rec, _ = make()
    chat(client)
    time.sleep(0.2)
    assert not [p for p, _ in rec.backend if p == "/api/memories"]
    client2, rec2, _ = make(capture=True)
    chat(client2, text="I always pick the quiet table")
    time.sleep(0.3)
    saved = [b for p, b in rec2.backend if p == "/api/memories"]
    assert saved == [{"text": "I always pick the quiet table"}]


def test_capture_skips_tiny_and_skipped_requests(make):
    client, rec, _ = make(capture=True)
    chat(client, text="hi")
    chat(client, text="a long enough message", headers={"x-mirror-skip": "1"})
    time.sleep(0.3)
    assert not [p for p, _ in rec.backend if p == "/api/memories"]


def test_streaming_is_passed_through(make):
    client, rec, _ = make()
    r = chat(client, stream=True)
    assert r.status_code == 200 and b"[DONE]" in r.content
    assert rec.upstream[0][2]["stream"] is True
    assert rec.upstream[0][2]["messages"][0]["role"] == "system"


def test_gateway_without_upstream_key_is_503(make):
    client, _, _ = make(key="")
    assert chat(client).status_code == 503


def test_content_parts_user_message_is_read():
    msgs = [{"role": "system", "content": "s"},
            {"role": "user", "content": [{"type": "text", "text": "part one"},
                                         {"type": "image_url", "image_url": {}},
                                         {"type": "text", "text": "part two"}]}]
    assert edge.last_user_text(msgs) == "part one\npart two"
    assert edge.last_user_text([]) == ""


def test_mcp_endpoint_answers_initialize_with_the_token(make):
    client, _, _ = make()
    with client:
        r = client.post("/mcp", headers={**AUTH, "Accept": "application/json, text/event-stream"},
                        json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                         "clientInfo": {"name": "t", "version": "0"}}})
    assert r.status_code == 200
    assert r.json()["result"]["serverInfo"]["name"] == "mirror"


# -- multi-user (hosted) mode ----------------------------------------------------

GOOD = "mir_" + "g" * 43
OTHER = "mir_" + "o" * 43


@pytest.fixture
def multi():
    """Edge in multi-user mode against a fake main app that knows two tokens."""
    seen = []

    def backend(req: httpx.Request):
        tok = req.headers.get("authorization", "").removeprefix("Bearer ")
        seen.append((req.url.path, tok))
        if tok not in (GOOD, OTHER):
            return httpx.Response(401, json={"error": "invalid"})
        if req.url.path == "/api/retrieve":
            return httpx.Response(200, json={"items": ITEMS if tok == GOOD else []})
        return httpx.Response(200, json={"ok": True})

    up = []

    def upstream(req: httpx.Request):
        up.append(dict(req.headers))
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    cfg = edge.Config(token="", upstream_key="OPERATOR-KEY", multiuser=True)
    app = edge.create_app(
        cfg, backend=httpx.AsyncClient(transport=httpx.MockTransport(backend),
                                       base_url="http://backend"),
        upstream=httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    return TestClient(app), seen, up


def test_multiuser_rejects_missing_wrong_and_non_token_credentials(multi):
    client, seen, _ = multi
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic x"},
                    {"Authorization": "Bearer mir_" + "z" * 43}):
        assert client.post("/mcp", json={}, headers=headers).status_code == 401
    assert client.post("/v1/chat/completions", json={}).status_code == 401


def test_multiuser_passes_each_callers_own_token_to_the_backend(multi):
    client, seen, up = multi
    hdr = lambda t: {"Authorization": f"Bearer {t}", "X-Upstream-Key": "user-key"}
    body = {"model": "m", "messages": [{"role": "user", "content": "What should I order?"}]}
    r1 = client.post("/v1/chat/completions", headers=hdr(GOOD), json=body)
    r2 = client.post("/v1/chat/completions", headers=hdr(OTHER), json=body)
    assert r1.status_code == 200 and r1.headers["x-mirror-injected"] == "1"
    assert r2.status_code == 200 and r2.headers["x-mirror-injected"] == "0"
    retrieves = [tok for path, tok in seen if path == "/api/retrieve"]
    assert retrieves == [GOOD, OTHER]            # each call carried its own caller's token


def test_multiuser_never_spends_the_operators_upstream_key(multi):
    client, seen, up = multi
    body = {"model": "m", "messages": [{"role": "user", "content": "hello there"}]}
    r = client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {GOOD}"},
                    json=body)
    assert r.status_code == 503 and not up      # no key of their own: nothing was sent
    r = client.post("/v1/chat/completions", json=body,
                    headers={"Authorization": f"Bearer {GOOD}", "X-Upstream-Key": "theirs"})
    assert r.status_code == 200
    assert up[-1]["authorization"] == "Bearer theirs"
    assert "OPERATOR-KEY" not in json.dumps(up)


def test_multiuser_checks_a_token_once_then_caches(multi):
    client, seen, _ = multi
    h = {"Authorization": f"Bearer {GOOD}", "X-Upstream-Key": "k"}
    body = {"model": "m", "messages": [{"role": "user", "content": "hello there"}]}
    for _ in range(3):
        client.post("/v1/chat/completions", headers=h, json=body)
    assert [p for p, _ in seen].count("/api/status") == 1


def test_multiuser_starts_without_a_shared_token():
    assert edge.create_app(edge.Config(token="", multiuser=True)) is not None


# -- plain HTTP endpoints for agents that cannot speak MCP ---------------------------

def test_context_endpoint_needs_the_token_and_returns_what_mirror_knows(make):
    client, rec, _ = make()
    assert client.post("/v1/context", json={"request": "dinner"}).status_code == 401
    r = client.post("/v1/context", headers=AUTH, json={"request": "What should I order?"})
    assert r.status_code == 200 and r.json()["items"] == ITEMS
    assert rec.backend[-1][0] == "/api/retrieve"


def test_context_endpoint_validates_input_and_caps_the_budget(make):
    client, rec, _ = make()
    assert client.post("/v1/context", headers=AUTH, json={}).status_code == 400
    assert client.post("/v1/context", headers=AUTH, json={"request": "  "}).status_code == 400
    assert client.post("/v1/context", headers=AUTH, content=b"not json").status_code == 400
    ok = client.post("/v1/context", headers=AUTH, json={"request": "dinner", "budget": 9999})
    assert ok.status_code == 200
    assert client.post("/v1/context", headers=AUTH, json={"request": "x", "budget": "many"}).status_code == 200


def test_remember_endpoint_stores_text_through_the_backend(make):
    client, rec, _ = make()
    assert client.post("/v1/remember", json={"text": "I prefer short meetings."}).status_code == 401
    r = client.post("/v1/remember", headers=AUTH, json={"text": "I prefer short meetings."})
    assert r.status_code == 200
    assert rec.backend[-1] == ("/api/memories", {"text": "I prefer short meetings."})
    assert client.post("/v1/remember", headers=AUTH, json={"text": "no"}).status_code == 400
    assert client.post("/v1/remember", headers=AUTH, json={"text": "x" * 3000}).status_code == 400
    client.post("/v1/remember", headers=AUTH, json={
        "text": "No, not that one on weekends.", "prior_answer": "Get the usual.", "episode": "e1"})
    assert rec.backend[-1] == ("/api/memories", {"text": "No, not that one on weekends.",
                                                 "prior_answer": "Get the usual.",
                                                 "episode": "e1"})


def test_multiuser_context_endpoint_forwards_the_callers_own_token(multi):
    client, seen, _ = multi
    r = client.post("/v1/context", headers={"Authorization": f"Bearer {GOOD}"},
                    json={"request": "What should I order?"})
    assert r.status_code == 200 and r.json()["items"] == ITEMS
    assert [tok for path, tok in seen if path == "/api/retrieve"] == [GOOD]
