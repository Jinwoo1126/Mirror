"""Hosted mode: token checks, per-user isolation through the real HTTP app, account
erasure, and the HTTP transport for the LLM."""

import time

import httpx
import jwt
import pytest
from starlette.testclient import TestClient

from mirror import app as app_mod
from mirror import auth as auth_mod
from mirror import llm as llm_mod
from tests.conftest import PG_DSN, drop_pg_user, new_pg_user

SECRET = "test-secret-test-secret-test-secret-0123"


def make_token(sub, *, secret=SECRET, aud="authenticated", exp=3600, alg="HS256"):
    claims = {"sub": sub, "aud": aud, "exp": int(time.time()) + exp}
    return jwt.encode(claims, secret, algorithm=alg)


@pytest.fixture
def hosted_env(monkeypatch):
    monkeypatch.setenv("MIRROR_HOSTED", "1")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setenv("MIRROR_NO_INDEX", "1")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    if PG_DSN:
        monkeypatch.setenv("MIRROR_PG_DSN", PG_DSN)


# -- token verification (no database needed) ------------------------------------

UID = "11111111-1111-1111-1111-111111111111"


def test_valid_token_gives_user_id(hosted_env):
    assert auth_mod.verify_token(make_token(UID)) == UID


@pytest.mark.parametrize("bad", [
    lambda: "",
    lambda: "not-a-jwt",
    lambda: make_token(UID, secret="x" * 40),            # forged signature
    lambda: make_token(UID, exp=-10),                     # expired
    lambda: make_token(UID, aud="anon"),                  # wrong audience
    lambda: make_token("not-a-uuid"),                     # subject is not a user id
    lambda: jwt.encode({"sub": UID, "aud": "authenticated",
                        "exp": int(time.time()) + 60}, None, algorithm="none"),
])
def test_bad_tokens_are_rejected(hosted_env, bad):
    with pytest.raises(auth_mod.AuthError):
        auth_mod.verify_token(bad())


def test_asymmetric_token_is_refused_without_a_jwks_url(hosted_env):
    # alg confusion guard: an RS256 header must not be checked against the HS256 secret
    token = jwt.encode({"sub": UID, "aud": "authenticated", "exp": int(time.time()) + 60},
                       SECRET, algorithm="HS256")
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "HS256"
    forged = token.replace(token.split(".")[0], "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9")
    with pytest.raises(auth_mod.AuthError):
        auth_mod.verify_token(forged)


def test_bearer_parsing():
    assert auth_mod.bearer("Bearer abc") == "abc"
    assert auth_mod.bearer("bearer abc") == "abc"
    assert auth_mod.bearer("Basic abc") == ""
    assert auth_mod.bearer(None) == ""


def test_api_requires_a_token_but_config_is_public(hosted_env):
    c = TestClient(app_mod.app)
    assert c.get("/api/config").json()["hosted"] is True
    assert c.get("/healthz").status_code == 200
    for path in ("/api/status", "/api/profile", "/api/graph", "/api/memories"):
        r = c.get(path)
        assert r.status_code == 401, path
    r = c.get("/api/profile", headers={"Authorization": "Bearer " + make_token(UID, exp=-5)})
    assert r.status_code == 401


def test_a_user_id_in_the_request_cannot_pick_the_store(hosted_env):
    c = TestClient(app_mod.app)
    r = c.get(f"/api/profile?user_id={UID}", headers={"X-User-Id": UID})
    assert r.status_code == 401


def test_config_reports_not_hosted_locally(monkeypatch):
    monkeypatch.delenv("MIRROR_HOSTED", raising=False)
    assert TestClient(app_mod.app).get("/api/config").json() == {"hosted": False}


# -- isolation through the HTTP app (needs Postgres) ----------------------------

needs_pg = pytest.mark.skipif(not PG_DSN, reason="set MIRROR_TEST_PG_DSN")


@pytest.fixture
def two_clients(hosted_env):
    a, b = new_pg_user(PG_DSN), new_pg_user(PG_DSN)
    c = TestClient(app_mod.app)
    ha = {"Authorization": "Bearer " + make_token(a)}
    hb = {"Authorization": "Bearer " + make_token(b)}
    yield c, ha, hb
    drop_pg_user(PG_DSN, a)
    drop_pg_user(PG_DSN, b)


@needs_pg
def test_users_see_only_their_own_data_over_http(two_clients):
    c, ha, hb = two_clients
    r = c.post("/api/declare", headers=ha, json={"statement": "I plan the night before."})
    assert r.status_code == 200 and r.json()["ok"]
    mine = c.get("/api/hypotheses", headers=ha).json()
    theirs = c.get("/api/hypotheses", headers=hb).json()
    assert [h["statement"] for h in mine["hypotheses"]] == ["I plan the night before."]
    assert theirs["hypotheses"] == []
    assert c.get("/api/memories", headers=hb).json()["items"] == []
    assert c.get("/api/status", headers=ha).json()["counts"]["Hypothesis"] == 1
    assert c.get("/api/status", headers=hb).json()["counts"]["Hypothesis"] == 0


@needs_pg
def test_deleting_the_account_erases_the_data(two_clients):
    c, ha, hb = two_clients
    c.post("/api/declare", headers=ha, json={"statement": "I prefer short meetings."})
    c.post("/api/declare", headers=hb, json={"statement": "I like long walks."})
    r = c.delete("/api/account", headers=ha)
    assert r.status_code == 200 and r.json()["data_deleted"] is True
    assert c.get("/api/hypotheses", headers=ha).json()["hypotheses"] == []
    assert len(c.get("/api/hypotheses", headers=hb).json()["hypotheses"]) == 1


def test_account_delete_is_not_available_locally(monkeypatch):
    monkeypatch.delenv("MIRROR_HOSTED", raising=False)
    assert TestClient(app_mod.app).delete("/api/account").status_code == 404


# -- LLM over HTTP ---------------------------------------------------------------

def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_meta_http_returns_the_message_text(monkeypatch):
    monkeypatch.setenv("META_API_KEY", "k")
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = req.read().decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    assert llm_mod.run_meta_http("hello", client=_client(handler)) == "hi"
    assert seen["auth"] == "Bearer k" and "hello" in seen["body"]


@pytest.mark.parametrize("resp", [
    httpx.Response(401, text="no"),
    httpx.Response(200, json={"choices": []}),
    httpx.Response(200, text="<html>"),
])
def test_meta_http_errors_become_llm_errors(monkeypatch, resp):
    monkeypatch.setenv("META_API_KEY", "k")
    with pytest.raises(llm_mod.LlmError):
        llm_mod.run_meta_http("x", client=_client(lambda req: resp))


def test_meta_http_needs_a_key(monkeypatch):
    monkeypatch.delenv("META_API_KEY", raising=False)
    with pytest.raises(llm_mod.LlmError):
        llm_mod.run_meta_http("x")


def test_transport_choice(monkeypatch):
    monkeypatch.delenv("META_API_KEY", raising=False)
    assert llm_mod.use_http("definitely-not-installed") is False
    monkeypatch.setenv("META_API_KEY", "k")
    assert llm_mod.use_http("definitely-not-installed") is True   # no CLI, has key
    monkeypatch.setenv("MIRROR_LLM_TRANSPORT", "http")
    assert llm_mod.use_http("sh") is True                          # forced


# -- rate limiting ----------------------------------------------------------------

def test_limiter_blocks_after_the_limit_and_is_per_user():
    lim = app_mod.RateLimiter()
    assert all(lim.allow("a", "x", 3) for _ in range(3))
    assert lim.allow("a", "x", 3) is False
    assert lim.allow("b", "x", 3) is True          # another user is unaffected
    assert lim.allow("a", "other-bucket", 3) is True


def test_costly_routes_are_recognised():
    assert app_mod._is_costly("POST", "/api/chat")
    assert app_mod._is_costly("POST", "/api/scenarios/doc_draft/run")
    assert not app_mod._is_costly("GET", "/api/profile")
    assert not app_mod._is_costly("GET", "/api/chat")


@needs_pg
def test_http_returns_429_when_a_user_floods(two_clients, monkeypatch):
    c, ha, hb = two_clients
    monkeypatch.setenv("MIRROR_RATE_ALL", "3")
    app_mod._limiter.hits.clear()
    codes = [c.get("/api/status", headers=ha).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]
    assert c.get("/api/status", headers=hb).status_code == 200
    app_mod._limiter.hits.clear()


# -- personal API tokens ------------------------------------------------------------

def test_token_helpers():
    from mirror import tokens
    t = tokens.generate()
    assert tokens.looks_like_token(t) and t.startswith("mir_")
    assert tokens.digest(t) != t and len(tokens.digest(t)) == 64
    assert tokens.prefix(t) == t[:8]
    assert tokens.generate() != t
    assert not tokens.looks_like_token("mir_") and not tokens.looks_like_token("abc" * 20)


def test_token_route_scope():
    assert app_mod.token_allowed("POST", "/api/retrieve")
    assert app_mod.token_allowed("POST", "/api/memories")
    assert app_mod.token_allowed("POST", "/api/scenarios/doc_draft/run")
    assert app_mod.token_allowed("POST", "/api/hypotheses/h1/feedback")
    for method, path in [("DELETE", "/api/account"), ("POST", "/api/tokens"),
                         ("GET", "/api/tokens"), ("DELETE", "/api/tokens/t1"),
                         ("DELETE", "/api/memories/obs-1"), ("POST", "/api/maintain"),
                         ("GET", "/api/graph")]:
        assert not app_mod.token_allowed(method, path), (method, path)


@needs_pg
def test_token_lifecycle_over_http(two_clients):
    c, ha, hb = two_clients
    made = c.post("/api/tokens", headers=ha, json={"name": "Claude Code on laptop"})
    assert made.status_code == 201
    raw = made.json()["token"]
    tok = {"Authorization": "Bearer " + raw}

    listed = c.get("/api/tokens", headers=ha).json()["tokens"]
    assert [t["name"] for t in listed] == ["Claude Code on laptop"]
    assert "token" not in listed[0] and "token_hash" not in listed[0]
    assert c.get("/api/tokens", headers=hb).json()["tokens"] == []   # not B's

    # the token reaches its user's data
    assert c.post("/api/declare", headers=tok, json={"statement": "I like quiet mornings."}).status_code == 200
    assert len(c.get("/api/hypotheses", headers=ha).json()["hypotheses"]) == 1
    assert c.get("/api/status", headers=tok).json()["counts"]["Hypothesis"] == 1
    assert c.get("/api/tokens", headers=ha).json()["tokens"][0]["last_used_at"]

    # ...but not the dangerous endpoints
    assert c.post("/api/tokens", headers=tok, json={"name": "x"}).status_code == 403
    assert c.delete("/api/account", headers=tok).status_code == 403
    assert c.post("/api/maintain", headers=tok, json={"job": "nightly"}).status_code == 403
    assert c.get("/api/graph", headers=tok).status_code == 403

    # a revoked token stops working at once
    tid = listed[0]["id"]
    assert c.delete(f"/api/tokens/{tid}", headers=hb).status_code == 404   # not B's to revoke
    assert c.delete(f"/api/tokens/{tid}", headers=ha).status_code == 200
    assert c.get("/api/status", headers=tok).status_code == 401


@needs_pg
def test_unknown_or_garbled_tokens_are_rejected(two_clients):
    c, ha, hb = two_clients
    from mirror import tokens
    for bad in (tokens.generate(), "mir_" + "x" * 40, "mir_short"):
        assert c.get("/api/status", headers={"Authorization": "Bearer " + bad}).status_code == 401


@needs_pg
def test_token_limit_and_names(two_clients):
    c, ha, hb = two_clients
    assert c.post("/api/tokens", headers=ha, json={"name": "  "}).status_code == 400
    for i in range(10):
        assert c.post("/api/tokens", headers=ha, json={"name": f"t{i}"}).status_code == 201
    assert c.post("/api/tokens", headers=ha, json={"name": "one too many"}).status_code == 400
    assert c.post("/api/tokens", headers=hb, json={"name": "mine"}).status_code == 201


@needs_pg
def test_deleting_the_account_removes_its_tokens(two_clients):
    c, ha, hb = two_clients
    raw = c.post("/api/tokens", headers=ha, json={"name": "k"}).json()["token"]
    assert c.delete("/api/account", headers=ha).status_code == 200
    # erasing the data also revokes every token, so nothing outside can keep using it
    assert c.get("/api/status", headers={"Authorization": "Bearer " + raw}).status_code == 401


# -- privacy notice and data export -------------------------------------------------

def test_privacy_notice_is_public_and_states_what_the_code_does(hosted_env, monkeypatch):
    monkeypatch.setenv("MIRROR_OPERATOR_NAME", "Example <Co>")
    monkeypatch.setenv("MIRROR_CONTACT_EMAIL", "privacy@example.com")
    r = TestClient(app_mod.app).get("/privacy")          # no token needed
    assert r.status_code == 200
    body = r.text
    assert "Example &lt;Co&gt;" in body and "privacy@example.com" in body   # escaped
    for must in ("Meta (Model API)", "Supabase", "Download my data", "Delete my data",
                 "Until you delete it"):
        assert must in body
    assert "train" not in body.lower()      # we cannot promise what a provider does


def test_privacy_notice_does_not_invent_an_operator(hosted_env, monkeypatch):
    monkeypatch.delenv("MIRROR_OPERATOR_NAME", raising=False)
    body = TestClient(app_mod.app).get("/privacy").text
    assert "has not published their name" in body


def test_gate_text_does_not_promise_more_than_the_code_does():
    html = (app_mod.STATIC_DIR / "app.html").read_text(encoding="utf-8")
    assert "not used to train" not in html and "shared with others" not in html
    assert "sent to an AI provider" in html and "/privacy" in html


def test_export_needs_a_real_session_not_a_token(hosted_env):
    c = TestClient(app_mod.app)
    assert c.get("/api/export").status_code == 401
    assert not app_mod.token_allowed("GET", "/api/export")


@needs_pg
def test_export_contains_the_users_data_and_nobody_elses(two_clients):
    c, ha, hb = two_clients
    c.post("/api/declare", headers=ha, json={"statement": "I plan the night before."})
    c.post("/api/declare", headers=hb, json={"statement": "I like long walks."})
    raw = c.post("/api/tokens", headers=ha, json={"name": "hook"}).json()["token"]
    r = c.get("/api/export", headers=ha)
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"] and r.headers["cache-control"] == "no-store"
    data = r.json()
    assert [h["statement"] for h in data["hypotheses"]] == ["I plan the night before."]
    assert len(data["observations"]) == 1 and len(data["edges"]) == 1
    flat = r.text
    assert "I like long walks." not in flat                 # B's data
    assert raw not in flat and "token_hash" not in flat     # no secrets
    assert "user_id" not in data["hypotheses"][0] and "seq" not in data["hypotheses"][0]
    assert c.get("/api/export", headers={"Authorization": "Bearer " + raw}).status_code == 403


def test_privacy_notice_names_every_outside_host_the_page_loads_from():
    """If the page starts loading from a new host, the notice has to say so."""
    import re
    from mirror import privacy
    html = (app_mod.STATIC_DIR / "app.html").read_text(encoding="utf-8")
    hosts = set(re.findall(r"https?://([a-zA-Z0-9.-]+)", html))
    notice = privacy.render().lower()
    named = {"cdn.jsdelivr.net": "jsdelivr", "unpkg.com": "unpkg"}
    assert hosts <= set(named), f"new outside host in the page: {hosts - set(named)}"
    assert all(word in notice for word in named.values())
