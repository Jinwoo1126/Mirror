"""Mirror 로컬 웹앱 (personal.ai식: 채팅 우선 + 기억 스택 관리).

실행: python -m mirror.app [--port 8000]
열기: http://127.0.0.1:8000

채팅 생성 답변은 spark → Claude → ollama 순으로 자동 감지하고,
없으면 검색 기반 로컬 답변(extractive)으로 동작한다.
"""

from __future__ import annotations

import argparse
import os
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from . import assistant as assistant_mod
from . import control as control_mod
from . import extract as ex
from . import hypotheses as hyp_mod
from . import llm as llm_mod
from . import observe as observe_mod
from . import policy as policy_mod
from . import profile as profile_mod
from . import report as report_mod
from . import router as router_mod
from . import scenarios as scen_mod
from . import schema
from . import ui_export as ui_mod
from .store import MirrorStore

STATIC_DIR = Path(__file__).resolve().parent / "static"

_lock = threading.Lock()
_store: MirrorStore | None = None
_extractor = None
_index = None
_router = None
_llm_backend: str | None = None
_llm_unavailable: set[str] = set()


def get_store() -> MirrorStore:
    global _store
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
    if shutil.which(os.environ.get("MIRROR_MUSE_BIN", "muse")) is None:
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


def _json(data, status: int = 200) -> JSONResponse:
    return JSONResponse(schema.to_jsonable(data), status_code=status)


# -- 라우트 --------------------------------------------------------------------

async def homepage(request) -> HTMLResponse:
    html = (STATIC_DIR / "app.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


async def api_status(request):
    with _lock:
        store = get_store()
        counts = {}
        for kind in ("Observation", "Hypothesis", "Entity", "Context",
                     "Preference", "Style"):
            row = store._one(f"MATCH (n:{kind}) RETURN count(*)")
            counts[kind] = row[0] if row else 0
        index = get_index()
        return _json({
            "revision": store.get_revision(), "counts": counts,
            "index_mode": index.mode if index is not None else "off",
            "extractor": get_extractor().name,
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
            rows = store._rows(
                "MATCH (o:Observation) RETURN o.id, o.text, o.occurred_at, "
                "o.source_id ORDER BY o.recorded_at DESC LIMIT $n",
                {"n": limit})
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
    with _lock:
        store = get_store()
        import uuid as _uuid

        source_id = body.get("source_id") or (
            "user-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            + "-" + _uuid.uuid4().hex[:4])
        res = observe_mod.observe(
            store, get_extractor(), source_id,
            {"text": text,
             "occurred_at": body.get("occurred_at") or schema.utcnow_iso()},
            store.get_revision())
        if res.ok and not res.duplicate and get_index() is not None:
            get_index().refresh(store)
        return _json(res, 200 if res.ok else 422)


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


routes = [
    Route("/", homepage),
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
    Route("/api/scenarios", api_scenarios),
    Route("/api/scenarios/{scenario_id}/run", api_scenario_run, methods=["POST"]),
    Route("/api/decide", api_decide, methods=["POST"]),
    Route("/api/maintain", api_maintain, methods=["POST"]),
]

app = Starlette(routes=routes)


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Mirror web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="")
    args = parser.parse_args(argv)
    if args.db:
        os.environ["MIRROR_DB"] = args.db
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
