"""Mirror 로컬 웹앱 (personal.ai식: 채팅 우선 + 기억 스택 관리).

실행: python -m mirror.app [--port 8000]
열기: http://127.0.0.1:8000

채팅 생성 답변은 spark → Claude → ollama 순으로 자동 감지하고,
없으면 검색 기반 로컬 답변(extractive)으로 동작한다.
"""

from __future__ import annotations

import argparse
import contextlib
import contextvars
import json
import os
import re
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.routing import Route

from . import assistant as assistant_mod
from . import auth as auth_mod
from . import constellation as const_mod
from . import control as control_mod
from . import extract as ex
from . import hypotheses as hyp_mod
from . import llm as llm_mod
from . import observe as observe_mod
from . import policy as policy_mod
from . import profile as profile_mod
from . import report as report_mod
from . import retrieve as retrieve_mod
from . import router as router_mod
from . import scenarios as scen_mod
from . import schema
from . import tokens as tokens_mod
from . import ui_export as ui_mod
from .store import MirrorStore

STATIC_DIR = Path(__file__).resolve().parent / "static"



def hosted() -> bool:
    """Hosted mode: Supabase auth, one Postgres-backed store per request."""
    return os.environ.get("MIRROR_HOSTED") == "1"


class _Lock:
    """One global lock for the single-user local app. Hosted requests each have their
    own store, so nothing is shared and a slow extraction must not block other users."""

    def __init__(self):
        self._lock = threading.Lock()

    def __enter__(self):
        if not hosted():
            self._lock.acquire()

    def __exit__(self, *exc):
        if not hosted():
            self._lock.release()


_lock = _Lock()
_store: MirrorStore | None = None
_request_store: contextvars.ContextVar = contextvars.ContextVar("mirror_store", default=None)
_request_anonymous: contextvars.ContextVar = contextvars.ContextVar("mirror_anonymous", default=False)
_extractor = None
_index = None
_router = None
_llm_backend: str | None = None
_llm_unavailable: set[str] = set()


def get_store() -> MirrorStore:
    global _store
    if hosted():
        st = _request_store.get()
        if st is None:  # a route that forgot to sit behind HostedAuth
            raise RuntimeError("no user store for this request")
        return st
    if _store is None:
        _store = MirrorStore(os.environ.get("MIRROR_DB", "data/local/mirror.kuzu"))
    return _store


def get_extractor():
    global _extractor
    if _extractor is None:
        _extractor = ex.get_extractor(os.environ.get("MIRROR_EXTRACTOR", "spark"))
    return _extractor


def get_index():
    global _index
    if hosted():
        return None  # search index is per user; not built in hosted mode yet
    if _index is None and os.environ.get("MIRROR_NO_INDEX") != "1":
        from . import search as search_mod

        db = os.environ.get("MIRROR_DB", "data/local/mirror.kuzu")
        base = os.path.splitext(db)[0]
        _index = search_mod.HybridIndex.build(
            get_store(), vector_path=base + ".vectors.json",
            bm25_path=base + ".bm25.json")
    return _index


def get_router():
    global _router
    if _router is None:
        _router = router_mod.get_router(os.environ.get("MIRROR_ROUTER", "mock"))
    return _router


def reset_state() -> None:
    """테스트용 싱글턴 초기화."""
    global _store, _extractor, _index, _router, _llm_backend
    if _store is not None:
        _store.close()
    _store = None
    _extractor = None
    _index = None
    _router = None
    _llm_backend = None
    _llm_unavailable.clear()


# -- 채팅 LLM 자동 감지 ------------------------------------------------------

def _try_spark(prompt: str, timeout: int | None = None) -> str:
    muse_bin = os.environ.get("MIRROR_MUSE_BIN", "muse")
    if shutil.which(muse_bin) is None and not llm_mod.use_http(muse_bin):
        raise llm_mod.LlmError("muse CLI not found")
    return llm_mod.run_muse_exec(
        prompt, model=os.environ.get("MIRROR_SPARK_MODEL", "muse-spark-1.3"),
        effort=os.environ.get("MIRROR_SPARK_EFFORT", "low"),
        timeout=timeout or int(os.environ.get("MIRROR_LLM_TIMEOUT", "60")),
        muse_bin=os.environ.get("MIRROR_MUSE_BIN", "muse"))


def _try_claude(prompt: str, timeout: int | None = None) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise llm_mod.LlmError("no ANTHROPIC_API_KEY")
    return llm_mod.call_claude_text(
        prompt, model=os.environ.get("MIRROR_CLAUDE_MODEL", "claude-haiku-4-5"),
        api_key=key,
        timeout=timeout or int(os.environ.get("MIRROR_LLM_TIMEOUT", "60")))


def _try_ollama(prompt: str, timeout: int | None = None) -> str:
    return llm_mod.call_ollama_text(
        prompt, model=os.environ.get("MIRROR_OLLAMA_MODEL", "llama3.1"),
        timeout=timeout or int(os.environ.get("MIRROR_LLM_TIMEOUT", "60")))


_PERMANENT_HINTS = ("not found", "credentials", "no ANTHROPIC", "not configured",
                    "keychain", "connection refused")


def _is_permanent(err: Exception) -> bool:
    """설치·인증 문제는 계속 실패하지만, 시간 초과 같은 일시 오류는 다시 시도한다."""
    msg = str(err).lower()
    return any(h in msg for h in _PERMANENT_HINTS)


_BACKENDS = (("spark", _try_spark), ("Muse", _try_claude),
             ("ollama", _try_ollama))


def chat_generate(prompt: str, timeout: int | None = None) -> tuple[str, str]:
    """사용 가능한 백엔드로 생성. (mode, text) 반환, 전부 실패 시 local."""
    global _llm_backend
    order = ([_llm_backend] if _llm_backend else []) + [
        name for name, _ in _BACKENDS if name != _llm_backend]
    funcs = dict(_BACKENDS)
    for name in order:
        if name in _llm_unavailable:
            continue
        try:
            text = funcs[name](prompt, timeout)
        except Exception as e:
            if _is_permanent(e):
                _llm_unavailable.add(name)
            continue
        if text.strip():
            _llm_backend = name
            return name, text.strip()
        _llm_unavailable.add(name)
    return "local", ""


def _personas() -> list:
    """Other people on this machine, from MIRROR_PERSONAS=[{"name": ..., "url": ...}]."""
    try:
        data = json.loads(os.environ.get("MIRROR_PERSONAS") or "[]")
    except ValueError:
        return []
    return [{"name": str(p["name"]), "url": str(p["url"])} for p in data
            if isinstance(p, dict) and p.get("name") and p.get("url")]


def _json(data, status: int = 200) -> JSONResponse:
    return JSONResponse(schema.to_jsonable(data), status_code=status)


# -- 라우트 --------------------------------------------------------------------

async def privacy_page(request) -> HTMLResponse:
    from . import privacy

    return HTMLResponse(privacy.render())


async def homepage(request) -> HTMLResponse:
    html = (STATIC_DIR / "app.html").read_text(encoding="utf-8")
    # The page is one file that changes with every deploy. Make browsers re-check it each
    # time, or people keep running an old version after an update.
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


async def api_status(request):
    with _lock:
        store = get_store()
        counts = {}
        for kind in ("Observation", "Hypothesis", "Entity", "Context",
                     "Preference", "Style"):
            counts[kind] = store.count_nodes(kind)
        index = get_index()
        return _json({
            "revision": store.get_revision(), "counts": counts,
            "index_mode": index.mode if index is not None else "off",
            "extractor": get_extractor().name,
            "persona": os.environ.get("MIRROR_PERSONA_NAME", ""),
            "personas": _personas(),
            "llm_backend": _llm_backend,
            "llm_candidates": [n for n, _ in _BACKENDS
                               if n not in _llm_unavailable],
        })


async def api_chat(request):
    body = await request.json()
    message = (body.get("message") or "").strip()
    options = body.get("options") or None
    if not message:
        return _json({"error": "empty message"}, 400)
    with _lock:
        store = get_store()
        out = assistant_mod.answer(
            store, get_router(), None, message, budget=8,
            index=get_index(), options=options)
        mode, text = chat_generate(out["prompt"])
        if mode == "local":
            text = assistant_mod.extractive_answer(out["retrieval"],
                                                   out.get("proposal"))
        return _json({
            "text": text, "mode": mode, "citations": out["citations"],
            "retrieval": out["retrieval"], "proposal": out.get("proposal"),
            "decision": out["decision"],
            "pending": {"text": message},
        })


async def api_memories_list(request):
    query = request.query_params.get("q", "").strip()
    limit = min(int(request.query_params.get("limit", "30")), 100)
    with _lock:
        store = get_store()
        index = get_index()
        if query and index is not None:
            hits = index.search_obs(query, store, top_k=limit)
            items = [{"id": h["obs"].id, "text": h["obs"].text,
                      "occurred_at": h["obs"].occurred_at,
                      "source_id": h["obs"].source_id,
                      "score": h["score"]} for h in hits]
        else:
            rows = store.recent_observations(limit)
            items = [{"id": r[0], "text": r[1], "occurred_at": r[2],
                      "source_id": r[3]} for r in rows]
            if query:
                items = [i for i in items if query in (i["text"] or "")]
        return _json({"items": items})


async def api_memories_add(request):
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return _json({"error": "empty text"}, 400)
    import uuid as _uuid

    source_id = body.get("source_id") or (
        "user-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        + "-" + _uuid.uuid4().hex[:4])
    if hosted():
        # Learning takes 10-40 s: queue it and answer now; the worker does the rest.
        store = get_store()
        if store.get_observation_by_source(source_id) is not None:
            return _json({"ok": True, "duplicate": True, "status": "done"})
        try:
            job_id = store.enqueue_job("learn", {
                "text": text, "source_id": source_id,
                "occurred_at": body.get("occurred_at") or schema.utcnow_iso()})
        except ValueError as e:
            return _json({"error": str(e)}, 429)
        return _json({"ok": True, "queued": True, "job_id": job_id, "status": "queued"}, 202)
    with _lock:
        store = get_store()
        res = observe_mod.observe(
            store, get_extractor(), source_id,
            {"text": text,
             "occurred_at": body.get("occurred_at") or schema.utcnow_iso()},
            store.get_revision())
        if res.ok and not res.duplicate and get_index() is not None:
            get_index().refresh(store)
        return _json(res, 200 if res.ok else 422)


async def api_job(request):
    if not hosted():
        return _json({"error": "only available in hosted mode"}, 404)
    job = get_store().get_job(request.path_params["job_id"])
    return _json(job, 200) if job else _json({"error": "no such job"}, 404)


async def api_memories_delete(request):
    obs_id = request.path_params["obs_id"]
    with _lock:
        store = get_store()
        res = control_mod.control(store, "delete",
                                  {"kind": "Observation", "id": obs_id})
        if res.ok and get_index() is not None:
            get_index().refresh(store)
        return _json(res, 200 if res.ok else 404)


async def api_hypotheses(request):
    with _lock:
        data = ui_mod.export_graph(get_store())
        return _json({"hypotheses": data["hypotheses"]})


async def api_feedback(request):
    hyp_id = request.path_params["hyp_id"]
    body = await request.json()
    with _lock:
        out = report_mod.apply_feedback(
            get_store(), hyp_id, body.get("verdict", ""),
            body.get("text", ""))
        return _json(out, 200 if out["ok"] else 422)


async def api_graph(request):
    with _lock:
        return _json(ui_mod.export_graph(get_store()))


async def api_report(request):
    with _lock:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        return _json(report_mod.weekly_report(get_store(), since))


async def api_profile(request):
    with _lock:
        return _json(profile_mod.build_profile(get_store()))


async def api_scenarios(request):
    return _json({"scenarios": scen_mod.list_scenarios()})


async def api_scenario_run(request):
    sid = request.path_params["scenario_id"]
    body = await request.json()
    with _lock:
        try:
            out = scen_mod.run_scenario(
                get_store(), get_router(), get_index(), sid,
                body.get("inputs") or {},
                lambda p: chat_generate(p, timeout=int(
                    os.environ.get("MIRROR_SCENARIO_TIMEOUT", "240"))))
        except scen_mod.ScenarioError as e:
            return _json({"error": str(e)}, 400)
        return _json(out)


_STATIC_OK = re.compile(r"^avatar\.(png|webp|jpg|jpeg|json)$")


def avatar_dir() -> Path:
    """Each Mirror instance can have its own character (MIRROR_AVATAR_DIR)."""
    return Path(os.environ.get("MIRROR_AVATAR_DIR") or STATIC_DIR)


async def static_file(request):
    """Only the optional avatar image and its anchor file are served."""
    name = request.path_params["name"]
    path = avatar_dir() / name
    if not _STATIC_OK.match(name) or not path.is_file():
        return _json({"error": "not found"}, 404)
    return FileResponse(path)


async def api_map(request):
    with _lock:
        return _json(const_mod.build_map(get_store()))


async def api_declare(request):
    body = await request.json()
    with _lock:
        res = const_mod.declare(get_store(), body.get("statement", ""),
                                bool(body.get("private")))
        if res.get("ok") and not res.get("duplicate") and get_index() is not None:
            get_index().refresh(get_store())
    return _json(res, 200 if res.get("ok") else 400)


async def api_scenarios_match(request):
    body = await request.json()
    return _json({"matches": scen_mod.match_scenarios(body.get("text", ""))})


# Embedding similarity is weak on its own, so the bar depends on whether the request already
# names a situation (a scenario trigger matched). Tuned on a small set of prompts; the
# caller can override it with "min_score".
MIN_SCORE_PLAIN = 0.66
MIN_SCORE_MATCHED = 0.58
MARGIN = 0.08      # how far behind the best match an item may be
MAX_ITEMS = 5


def gate_by_relevance(items: list[dict], matched: bool, hybrid: bool,
                      min_score: float | None = None) -> list[dict]:
    """Keep only hypotheses that are actually related to the request.

    Without embeddings the scores are on another scale, so nothing is dropped there.
    """
    if not hybrid:
        return items
    floor = min_score if min_score is not None else (
        MIN_SCORE_MATCHED if matched else MIN_SCORE_PLAIN)
    kept = sorted((i for i in items if (i.get("score") or 0) >= floor),
                  key=lambda i: -(i.get("score") or 0))
    if kept:   # drop what is far behind the best match, and keep the context short
        top = kept[0].get("score") or 0
        kept = [i for i in kept if (i.get("score") or 0) >= top - MARGIN][:MAX_ITEMS]
    return kept


async def api_retrieve(request):
    """Related understanding for outside callers (the edge server).

    Only hypotheses come back by default, and internal_only ones are withheld.
    Raw memories are included only when include_memories is true.
    """
    body = await request.json()
    q = (body.get("request") or "").strip()
    if not q:
        return _json({"error": "empty request"}, 400)
    budget = max(1, min(int(body.get("budget", 6)), 20))
    matched = scen_mod.match_scenarios(q)
    with _lock:
        store = get_store()
        out = retrieve_mod.retrieve(store, q, [], None, budget, index=get_index())
        raw = list(out.get("items", []))
        seen = {it["id"] for it in raw}
        for m in matched[:2]:   # also look up what matters for the situation the text triggers
            extra = retrieve_mod.retrieve(store, f"{m['topic']} {q}", [], None, 4,
                                          index=get_index())
            for it in extra.get("items", []):
                if it["id"] not in seen:
                    seen.add(it["id"])
                    raw.append(it)
    items = []
    for it in raw:
        if it["kind"] == schema.HYPOTHESIS:
            if it.get("use_policy") == "internal_only":
                continue
            items.append({"kind": it["kind"], "id": it["id"],
                          "statement": it["statement"], "status": it["status"],
                          "score": it.get("score"),
                          "use_policy": it["use_policy"],
                          "evidence": len(it.get("supports", [])),
                          "counter_examples": len(it.get("contradicts", [])),
                          "evidence_ids": it.get("supports", [])})
        elif body.get("include_memories"):
            items.append(it)
    index = get_index()
    items = gate_by_relevance(items, bool(matched), index is not None and index.mode == "hybrid",
                              body.get("min_score"))
    return _json({"items": items, "scenarios": [{"id": m["id"], "name": m["name"]} for m in matched],
                  "revision": out.get("revision")})


async def api_relations(request):
    with _lock:
        return _json({"vocabulary": get_store().relation_vocabulary()})


async def api_decide(request):
    body = await request.json()
    options = body.get("options") or []
    if len(options) < 2:
        return _json({"error": "need 2+ options"}, 400)
    with _lock:
        return _json(policy_mod.propose(
            get_store(), body.get("request", ""), options,
            body.get("conditions") or [], body.get("context") or {},
            body.get("settings") or {}))


async def api_maintain(request):
    body = await request.json()
    job = body.get("job", "")
    with _lock:
        store = get_store()
        if job == "nightly":
            return _json(hyp_mod.nightly_maintenance(store))
        if job == "weekly":
            return _json(hyp_mod.weekly_review(store))
        if job == "promote":
            return _json(hyp_mod.promote_candidates(store))
        return _json({"error": f"unknown job: {job}"}, 400)


async def healthz(request):
    return _json({"ok": True})


async def readyz(request):
    """Can this server reach its database and does the schema exist? Public and
    harmless: it says only yes or no, never the connection string or the error text."""
    if not hosted():
        return _json({"ok": True, "hosted": False})
    dsn = os.environ.get("MIRROR_PG_DSN", "")
    if not dsn:
        return _json({"ok": False, "db": False, "reason": "no database configured"}, 503)

    def check() -> str:
        import psycopg

        try:
            with psycopg.connect(dsn, connect_timeout=5) as c:
                c.execute("select 1").fetchone()
                c.execute("select count(*) from public.jobs").fetchone()
                c.execute("select count(*) from public.api_tokens").fetchone()
                c.execute("select count(*) from public.observations").fetchone()
        except Exception as e:
            return type(e).__name__
        return ""

    failure = await run_in_threadpool(check)
    if failure:
        return _json({"ok": False, "db": False, "reason": failure}, 503)
    return _json({"ok": True, "db": True})


async def api_config(request):
    """What the browser needs to sign in. Public by design: the anon key is meant to
    be shipped to browsers, and row level security is what protects the data."""
    if not hosted():
        return _json({"hosted": False})
    return _json({"hosted": True,
                  "supabase_url": os.environ.get("SUPABASE_URL", ""),
                  "supabase_anon_key": os.environ.get("SUPABASE_ANON_KEY", "")})


async def api_export(request):
    """Download everything Mirror stores about the signed-in user."""
    if not hosted():
        return _json({"error": "only available in hosted mode"}, 404)
    data = await run_in_threadpool(get_store().export_all)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return JSONResponse(data, headers={
        "Content-Disposition": f'attachment; filename="mirror-export-{day}.json"',
        "Cache-Control": "no-store"})


async def api_account_delete(request):
    """Delete everything stored for the signed-in user, and the account itself when the
    server holds the admin key."""
    if not hosted():
        return _json({"error": "only available in hosted mode"}, 404)
    store = get_store()
    uid = str(store.uid)
    await run_in_threadpool(store.delete_all_data)
    removed_account = False
    admin_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if admin_key and base:
        import httpx

        def _drop() -> bool:
            r = httpx.delete(f"{base}/auth/v1/admin/users/{uid}", timeout=15,
                             headers={"Authorization": f"Bearer {admin_key}",
                                      "apikey": admin_key})
            return r.status_code in (200, 204)
        removed_account = await run_in_threadpool(_drop)
    return _json({"ok": True, "data_deleted": True, "account_deleted": removed_account})


async def api_sample(request):
    """Queue a few example memories from a made-up person, once per account, so a new
    account has something to look at. Each one is a real LLM job, so for anonymous
    accounts every one is counted against their shared hourly budget."""
    if not hosted():
        return _json({"error": "only available in hosted mode"}, 404)
    from .samples import SAMPLES

    store = get_store()
    if store.count_nodes(schema.OBSERVATION) > 0:
        return _json({"error": "Samples are only for an empty account."}, 409)
    todo = [x for x in SAMPLES if store.get_observation_by_source(x[0]) is None]
    if not todo:
        return _json({"ok": True, "job_ids": [], "status": "done"})
    if _request_anonymous.get():
        cap = int(os.environ.get("MIRROR_ANON_COSTLY_PER_HOUR", "100"))
        for _ in todo:
            if not _limiter.allow("*anonymous*", "costly", cap, window=3600):
                return _json({"error": "The trial is busy right now. Try again later."}, 429)
    try:
        ids = [store.enqueue_job("learn", {"text": text, "source_id": sid, "occurred_at": at})
               for sid, at, text in todo]
    except ValueError as e:
        return _json({"error": str(e)}, 429)
    return _json({"ok": True, "queued": True, "job_ids": ids, "status": "queued"}, 202)


async def api_tokens_list(request):
    if not hosted():
        return _json({"error": "only available in hosted mode"}, 404)
    return _json({"tokens": get_store().list_tokens()})


async def api_tokens_create(request):
    if not hosted():
        return _json({"error": "only available in hosted mode"}, 404)
    body = await request.json()
    try:
        return _json(get_store().create_token(body.get("name", "")), 201)
    except ValueError as e:
        return _json({"error": str(e)}, 400)


async def api_tokens_revoke(request):
    if not hosted():
        return _json({"error": "only available in hosted mode"}, 404)
    ok = get_store().revoke_token(request.path_params["token_id"])
    return _json({"ok": ok}, 200 if ok else 404)


routes = [
    Route("/", homepage),
    Route("/api/jobs/{job_id}", api_job),
    Route("/api/sample", api_sample, methods=["POST"]),
    Route("/api/tokens", api_tokens_list),
    Route("/api/tokens", api_tokens_create, methods=["POST"]),
    Route("/api/tokens/{token_id}", api_tokens_revoke, methods=["DELETE"]),
    Route("/healthz", healthz),
    Route("/readyz", readyz),
    Route("/api/config", api_config),
    Route("/api/account", api_account_delete, methods=["DELETE"]),
    Route("/api/export", api_export),
    Route("/privacy", privacy_page),
    Route("/api/status", api_status),
    Route("/api/chat", api_chat, methods=["POST"]),
    Route("/api/memories", api_memories_list),
    Route("/api/memories", api_memories_add, methods=["POST"]),
    Route("/api/memories/{obs_id}", api_memories_delete, methods=["DELETE"]),
    Route("/api/hypotheses", api_hypotheses),
    Route("/api/hypotheses/{hyp_id}/feedback", api_feedback,
          methods=["POST"]),
    Route("/api/graph", api_graph),
    Route("/api/report", api_report),
    Route("/api/relations", api_relations),
    Route("/api/profile", api_profile),
    Route("/api/retrieve", api_retrieve, methods=["POST"]),
    Route("/api/map", api_map),
    Route("/static/{name}", static_file),
    Route("/api/declare", api_declare, methods=["POST"]),
    Route("/api/scenarios/match", api_scenarios_match, methods=["POST"]),
    Route("/api/scenarios", api_scenarios),
    Route("/api/scenarios/{scenario_id}/run", api_scenario_run, methods=["POST"]),
    Route("/api/decide", api_decide, methods=["POST"]),
    Route("/api/maintain", api_maintain, methods=["POST"]),
]

PUBLIC_API = {"/api/config"}

# What a personal API token may call. Everything else (issuing tokens, deleting the
# account, bulk maintenance, deleting memories) needs a real signed-in session.
TOKEN_ROUTES = (
    ("GET", "/api/status"), ("GET", "/api/profile"), ("GET", "/api/map"),
    ("GET", "/api/memories"), ("POST", "/api/memories"), ("POST", "/api/retrieve"),
    ("POST", "/api/chat"), ("POST", "/api/declare"), ("POST", "/api/scenarios/match"),
    ("GET", "/api/scenarios"),
)


def token_allowed(method: str, path: str) -> bool:
    if any(method == m and path == p for m, p in TOKEN_ROUTES):
        return True
    if method == "POST" and path.startswith("/api/scenarios/") and path.endswith("/run"):
        return True
    if method == "GET" and path.startswith("/api/jobs/"):
        return True
    return (method == "POST" and path.startswith("/api/hypotheses/")
            and path.endswith("/feedback"))

# Calls that spend LLM budget get a tighter limit than ordinary reads.
COSTLY = (("POST", "/api/sample"), ("GET", "/api/export"), ("POST", "/api/chat"), ("POST", "/api/memories"), ("POST", "/api/maintain"),
          ("POST", "/api/decide"))


def _is_costly(method: str, path: str) -> bool:
    return any(method == m and path == p for m, p in COSTLY) or (
        method == "POST" and path.startswith("/api/scenarios/") and path.endswith("/run"))


class RateLimiter:
    """Sliding window per user, kept in this worker's memory. With N workers the real
    ceiling is up to N times the setting; move it to the database or Redis if that
    matters."""

    def __init__(self):
        self.hits: dict[tuple[str, str], list[float]] = {}

    def allow(self, uid: str, bucket: str, limit: int, window: float = 60.0) -> bool:
        import time

        now = time.monotonic()
        key = (uid, bucket)
        recent = [t for t in self.hits.get(key, []) if now - t < window]
        if len(recent) >= limit:
            self.hits[key] = recent
            return False
        recent.append(now)
        self.hits[key] = recent
        if len(self.hits) > 10000:  # drop idle users so the table cannot grow forever
            self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < window}
        return True


_limiter = RateLimiter()


class HostedAuth:
    """In hosted mode every /api request needs a valid Supabase token. The token's
    subject, never anything the client sends, picks the user whose store is opened."""

    def __init__(self, inner):
        self.inner = inner

    async def _reject(self, send, status: int, message: str):
        body = json.dumps({"error": message}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (scope["type"] != "http" or not hosted() or not path.startswith("/api/")
                or path in PUBLIC_API):
            return await self.inner(scope, receive, send)
        headers = dict(scope["headers"])
        token = auth_mod.bearer(headers.get(b"authorization", b"").decode("latin-1"))
        dsn = os.environ.get("MIRROR_PG_DSN", "")
        method = scope.get("method", "GET")
        anonymous = False
        if tokens_mod.looks_like_token(token):
            if not dsn:
                return await self._reject(send, 500, "server has no database configured")
            uid = await run_in_threadpool(tokens_mod.resolve, dsn, token)
            if uid is None:
                return await self._reject(send, 401, "invalid or revoked API token")
            if not token_allowed(method, path):
                return await self._reject(
                    send, 403, "API tokens cannot use this endpoint; sign in instead")
        else:
            try:
                claims = await run_in_threadpool(auth_mod.verify_claims, token)
            except auth_mod.AuthError as e:
                return await self._reject(send, 401, str(e))
            uid = claims["sub"]
            anonymous = claims.get("is_anonymous") is True
        costly = _is_costly(method, path)
        # Anyone can mint anonymous accounts, so per-user limits alone do not protect the LLM
        # budget. Cap what all anonymous accounts together may spend per hour.
        if anonymous and costly and not _limiter.allow(
                "*anonymous*", "costly", int(os.environ.get("MIRROR_ANON_COSTLY_PER_HOUR", "100")),
                window=3600):
            return await self._reject(
                send, 429, "The trial is busy right now. Try again later, or sign in.")
        limit = int(os.environ.get("MIRROR_RATE_COSTLY" if costly else "MIRROR_RATE_ALL",
                                   "10" if costly else "120"))
        if not _limiter.allow(uid, "costly" if costly else "all", limit):
            return await self._reject(send, 429, "Too many requests. Try again in a minute.")
        if not dsn:
            return await self._reject(send, 500, "server has no database configured")
        from .pg_store import PostgresStore

        store = await run_in_threadpool(PostgresStore, dsn, uid)
        marker = _request_store.set(store)
        anon_marker = _request_anonymous.set(anonymous)
        try:
            await self.inner(scope, receive, send)
        finally:
            _request_anonymous.reset(anon_marker)
            _request_store.reset(marker)
            await run_in_threadpool(store.close)


@contextlib.asynccontextmanager
async def lifespan(_app):
    """Hosted: run a background worker thread next to the web server. Several app
    processes can each run one; jobs are claimed so none runs twice."""
    worker = None
    if hosted() and os.environ.get("MIRROR_WORKER", "1") != "0" \
            and os.environ.get("MIRROR_PG_DSN"):
        from . import worker as worker_mod

        worker = worker_mod.start_thread(os.environ["MIRROR_PG_DSN"])
    try:
        yield
    finally:
        if worker:
            worker[1].set()


app = HostedAuth(Starlette(routes=routes, lifespan=lifespan))


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Mirror web app")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--db", default="")
    parser.add_argument("--avatar-dir", default="")
    parser.add_argument("--name", default="", help="shown in the person switcher")
    args = parser.parse_args(argv)
    if args.db:
        os.environ["MIRROR_DB"] = args.db
    if args.avatar_dir:
        os.environ["MIRROR_AVATAR_DIR"] = args.avatar_dir
    if args.name:
        os.environ["MIRROR_PERSONA_NAME"] = args.name
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
