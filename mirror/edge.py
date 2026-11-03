"""Edge server: the front door for outside AI services.

The main Mirror app owns the database. This process holds no data. It calls the
main app over HTTP and offers two ways in, both behind one bearer token:

  /mcp                   remote MCP (streamable HTTP) for clients that support
                         custom connectors
  /v1/chat/completions   OpenAI-compatible gateway for any tool that lets you set
                         a base URL. It adds what Mirror knows to the request and
                         can remember the user's message afterwards.

Run:
  MIRROR_TOKEN=... MIRROR_BACKEND=http://127.0.0.1:8000 \
  MIRROR_UPSTREAM_BASE=https://api.openai.com/v1 MIRROR_UPSTREAM_KEY=... \
  python -m mirror.edge --host 127.0.0.1 --port 8100
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
from dataclasses import dataclass, field

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

INSTRUCTIONS = (
    "Mirror keeps what it has learned about the user from past conversations. "
    "At the start of a task that depends on the user's preferences or habits, call "
    "mirror_context with a short description of the task and use what comes back "
    "quietly. The reply also names the situations the request matches (for example code "
    "review or deadline pressure); mirror_scenario can run one of them in full. Never reveal it unprompted and never make claims about the user's "
    "personality. When the user states a decision, a preference or a correction, call "
    "mirror_remember with their own words. If the user says a remembered point is "
    "wrong, call mirror_feedback."
)

MIN_CAPTURE_CHARS = 8
MAX_CAPTURE_CHARS = 2000


@dataclass
class Config:
    token: str
    backend: str = "http://127.0.0.1:8000"
    upstream_base: str = "https://api.openai.com/v1"
    upstream_key: str = ""
    inject: bool = True
    capture: bool = False          # off by default: the user's messages leave the gateway
    context_budget: int = 6
    pending: list = field(default_factory=list)   # background capture tasks


def config_from_env() -> Config:
    return Config(
        token=os.environ.get("MIRROR_TOKEN", ""),
        backend=os.environ.get("MIRROR_BACKEND", "http://127.0.0.1:8000"),
        upstream_base=os.environ.get("MIRROR_UPSTREAM_BASE", "https://api.openai.com/v1"),
        upstream_key=os.environ.get("MIRROR_UPSTREAM_KEY", ""),
        inject=os.environ.get("MIRROR_INJECT", "1") != "0",
        capture=os.environ.get("MIRROR_CAPTURE", "0") == "1")


# -- shared helpers ------------------------------------------------------------

def last_user_text(messages: list) -> str:
    """The most recent user message as plain text (string or content parts)."""
    for m in reversed(messages or []):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [p.get("text", "") for p in content
                     if isinstance(p, dict) and p.get("type") == "text"]
            return "\n".join(x for x in parts if x).strip()
    return ""


def context_message(items: list[dict], scenarios: list[dict] | None = None) -> str:
    lines = [
        "Background about the user, from Mirror's memory of their past conversations. "
        "Use it only when it is relevant, do not mention it unprompted, and never "
        "make claims about the user's personality or type."]
    if scenarios:
        lines.append("Situation this looks like: "
                     + ", ".join(s["name"] for s in scenarios) + ".")
    for it in items:
        lines.append(f"- {it['statement']} ({it.get('evidence', 0)} evidence, "
                     f"{it.get('status', 'candidate')})")
    return "\n".join(lines)


def with_context(messages: list, items: list[dict],
                 scenarios: list[dict] | None = None) -> list:
    return [{"role": "system", "content": context_message(items, scenarios)}] + list(messages)


class BearerAuth:
    """ASGI middleware. Fails closed: no configured token means every call is refused."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        got = dict(scope["headers"]).get(b"authorization", b"").decode()
        ok = bool(self.token) and hmac.compare_digest(got, f"Bearer {self.token}")
        if not ok:
            resp = JSONResponse({"error": "unauthorized"}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
            await resp(scope, receive, send)
            return
        await self.app(scope, receive, send)


# -- app -----------------------------------------------------------------------

def create_app(cfg: Config, backend: httpx.AsyncClient | None = None,
               upstream: httpx.AsyncClient | None = None):
    backend = backend or httpx.AsyncClient(base_url=cfg.backend, timeout=120)
    upstream = upstream or httpx.AsyncClient(timeout=None)

    async def fetch_context(text: str) -> tuple[list[dict], list[dict]]:
        try:
            r = await backend.post("/api/retrieve", json={
                "request": text, "budget": cfg.context_budget})
            if r.status_code != 200:
                return [], []
            data = r.json()
            return data.get("items", []), data.get("scenarios", [])
        except Exception:
            return [], []     # Mirror being down must never break the user's request

    async def remember(text: str) -> dict:
        r = await backend.post("/api/memories", json={"text": text})
        return r.json()

    async def capture(text: str) -> None:
        if MIN_CAPTURE_CHARS <= len(text) <= MAX_CAPTURE_CHARS:
            try:
                await remember(text)
            except Exception:
                pass

    mcp = MCPServer("mirror", instructions=INSTRUCTIONS)

    @mcp.tool()
    async def mirror_context(request: str, budget: int = 6) -> dict:
        """What Mirror has learned about the user that is relevant to this task."""
        items, scenarios = await fetch_context(request)
        return {"items": items[:max(1, min(budget, 20))], "scenarios": scenarios}

    @mcp.tool()
    async def mirror_scenario(scenario_id: str, inputs: dict) -> dict:
        """Run one scenario in full (for example code_review or deadline_pressure)."""
        r = await backend.post(f"/api/scenarios/{scenario_id}/run", json={"inputs": inputs})
        return r.json()

    @mcp.tool()
    async def mirror_remember(text: str) -> dict:
        """Remember something the user said, in their own words."""
        return await remember(text)

    @mcp.tool()
    async def mirror_feedback(hypothesis_id: str, verdict: str) -> dict:
        """Tell Mirror a remembered point is right or wrong. verdict: correct, wrong or dont_use."""
        r = await backend.post(f"/api/hypotheses/{hypothesis_id}/feedback",
                               json={"verdict": verdict})
        return r.json()

    app = mcp.streamable_http_app(
        stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False))   # the bearer token is the gate

    async def chat_completions(request):
        if not cfg.upstream_key:
            return JSONResponse({"error": "gateway has no upstream key configured"}, 503)
        body = await request.json()
        skip = request.headers.get("x-mirror-skip") == "1"
        text = last_user_text(body.get("messages", []))
        items = []
        if cfg.inject and not skip and text:
            items, scenarios = await fetch_context(text)
            if items:
                body["messages"] = with_context(body["messages"], items, scenarios)
        headers = {"authorization": f"Bearer {cfg.upstream_key}",
                   "content-type": "application/json"}
        out_headers = {"x-mirror-injected": str(len(items))}
        if cfg.capture and not skip and text:
            cfg.pending.append(asyncio.create_task(capture(text)))
        url = cfg.upstream_base.rstrip("/") + "/chat/completions"
        if body.get("stream"):
            req = upstream.build_request("POST", url, json=body, headers=headers)
            r = await upstream.send(req, stream=True)

            async def body_iter():
                if r.is_stream_consumed:      # a transport that already buffered it
                    yield r.content
                else:
                    async for chunk in r.aiter_raw():
                        yield chunk

            return StreamingResponse(
                body_iter(), status_code=r.status_code, headers=out_headers,
                media_type=r.headers.get("content-type", "text/event-stream"),
                background=BackgroundTask(r.aclose))
        r = await upstream.post(url, json=body, headers=headers)
        return Response(r.content, status_code=r.status_code, headers=out_headers,
                        media_type=r.headers.get("content-type", "application/json"))

    async def models(request):
        r = await upstream.get(cfg.upstream_base.rstrip("/") + "/models",
                               headers={"authorization": f"Bearer {cfg.upstream_key}"})
        return Response(r.content, status_code=r.status_code,
                        media_type=r.headers.get("content-type", "application/json"))

    app.router.routes.append(Route("/v1/chat/completions", chat_completions,
                                   methods=["POST"]))
    app.router.routes.append(Route("/v1/models", models, methods=["GET"]))
    return BearerAuth(app, cfg.token)


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description="Mirror edge server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8100)
    args = ap.parse_args(argv)
    cfg = config_from_env()
    if not cfg.token:
        raise SystemExit("MIRROR_TOKEN is required. The edge server refuses to start "
                         "without a bearer token.")
    uvicorn.run(create_app(cfg), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
