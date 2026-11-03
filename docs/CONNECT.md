# Connecting Mirror to other AI services

Mirror keeps what it has learned about you. To make other AI services use it, there are
three paths. They differ in how automatic they are and in which services support them.

| Path | Automatic? | Works with |
|---|---|---|
| **Gateway** (`/v1/chat/completions`) | Yes. Context is added to every request. | Any tool that lets you set an OpenAI-compatible base URL |
| **Remote MCP** (`/mcp`) | No. The AI decides when to call the tools. | Clients that support custom MCP connectors |
| **Client hooks** | Yes | Clients with hooks (for example Claude Code). Not built yet. |

Which services support custom base URLs, remote MCP or hooks changes often. Check each
service's own documentation before you rely on one.

## Architecture

The main Mirror app (UI and API) owns the database and stays on your private network.
The edge server holds no data. It calls the main app over HTTP and is the only thing you
expose.

```
AI service ──► edge server ──► main Mirror app ──► database
  (token)        :8100            :8000
```

## Start the edge server

```sh
export MIRROR_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export MIRROR_BACKEND=http://127.0.0.1:8000          # the main Mirror app
export MIRROR_UPSTREAM_BASE=https://api.openai.com/v1  # gateway only
export MIRROR_UPSTREAM_KEY=...                         # gateway only
python -m mirror.edge --host 127.0.0.1 --port 8100
```

The edge server refuses to start without `MIRROR_TOKEN`, and every route requires
`Authorization: Bearer <token>`.

| Variable | Default | Meaning |
|---|---|---|
| `MIRROR_TOKEN` | required | Bearer token for every route |
| `MIRROR_BACKEND` | `http://127.0.0.1:8000` | Main Mirror app |
| `MIRROR_UPSTREAM_BASE` | `https://api.openai.com/v1` | Where the gateway forwards requests |
| `MIRROR_UPSTREAM_KEY` | empty | Key for the upstream provider. Without it the gateway returns 503. |
| `MIRROR_INJECT` | `1` | Add what Mirror knows to each request. `0` to turn off. |
| `MIRROR_CAPTURE` | `0` | Remember the user's message after each request. Off by default. |

## Gateway

In the tool's settings, set the base URL to `http://<edge-host>:8100/v1` and use the
Mirror token as the API key. Your real provider key stays in `MIRROR_UPSTREAM_KEY` on the
edge server.

- Mirror adds one system message at the top with the hypotheses relevant to the user's
  latest message. Hypotheses marked `internal_only` are never sent, and raw memories are
  not sent.
- With `MIRROR_CAPTURE=1`, the user's latest message (8 to 2000 characters) is saved as a
  memory. The assistant's reply is not saved. This sends the message to Mirror's
  extractor, which uses an LLM.
- Send the header `x-mirror-skip: 1` to skip both for one request.
- If Mirror is down the request goes through unchanged.
- Streaming responses are passed through.

## Remote MCP

Add `http://<edge-host>:8100/mcp` as a streamable-HTTP MCP server with the header
`Authorization: Bearer <token>`. Tools:

- `mirror_context(request, budget)`: what Mirror knows that is relevant to a task
- `mirror_remember(text)`: remember something the user said
- `mirror_feedback(hypothesis_id, verdict)`: `correct`, `wrong` or `dont_use`

The server sends instructions telling the AI to call `mirror_context` at the start of a
task and `mirror_remember` when the user states a decision or correction. Whether the AI
actually calls them is up to the AI, so this path is not guaranteed.

## Reaching Mirror from outside your network

Services that run in the cloud need a public HTTPS address. A static bearer token may not
be accepted by every connector, and OAuth is not supported yet. If you expose the edge
server (for example with Tailscale Funnel), expose only the edge port, never the main app,
and treat the token like a password.

## What leaves your machine

- Gateway: your messages go to the upstream provider you configured, as they would
  without Mirror, plus the injected context.
- Capture and `mirror_remember`: the text goes to Mirror's LLM extractor.
- Nothing is sent to a third party that you did not configure.
