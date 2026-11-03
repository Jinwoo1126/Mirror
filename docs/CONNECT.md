# Connecting Mirror to other AI services

Mirror keeps what it has learned about you. To make other AI services use it, there are
three paths. They differ in how automatic they are and in which services support them.

| Path | Automatic? | Works with |
|---|---|---|
| **Gateway** (`/v1/chat/completions`) | Yes. Context is added to every request. | Any tool that lets you set an OpenAI-compatible base URL |
| **Remote MCP** (`/mcp`) | No. The AI decides when to call the tools. | Clients that support custom MCP connectors |
| **Client hooks** | Yes | Clients with hooks. A Claude Code hook is included (see below). |

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
- `mirror_scenario(scenario_id, inputs)`: run one scenario in full

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

## Scenarios a hook can fire

Every scenario has trigger patterns. When a request matches, `mirror_context` and the
gateway also look up what matters for that situation and name it in the context
("Situation this looks like: Review code my way"). `POST /api/scenarios/match` with
`{"text": "..."}` returns the matches, which is what a client hook calls.

| Scenario id | Fires on requests about |
|---|---|
| `code_review` | code review, pull requests, diffs, refactors |
| `writing_tone` | emails, replies, drafts, rewrites, tone |
| `decision_recap` | "should I", deciding, picking, "vs" |
| `deadline_pressure` | deadlines, crunch, tight schedules |
| `learning_style` | "explain", "teach me", tutorials |
| `ai_instructions` | system prompts, custom instructions |
| `collaboration` | managers, teammates, stakeholders, 1:1s |
| `shopping` | buying, budget, price |
| `energy_routine` | tired, energy, sleep, workouts |
| `travel_plan` | trips, itineraries, flights, hotels |
| `doc_draft`, `music_recommend`, `day_plan`, `choice_help` | writing, music, planning, choosing |

## The body map

`GET /api/map` returns every pattern, thing and style sorted into a body region (brain,
ears, eyes, mouth, hands, heart, feet). Regions are chosen by keyword rules in English
and Korean. A pattern's own wording decides first; only when it has no clue does a clear
majority of its evidence decide; otherwise it goes to the brain. `POST /api/declare`
adds something the person says about themselves. Add `"private": true` to keep it out of
anything sent to other AI services.

## Claude Code hook

`integrations/claude_code/mirror_hook.py` is a `UserPromptSubmit` hook. Before Claude sees a
prompt it asks Mirror what is related and adds it as hidden context (`additionalContext`).
It uses only the Python standard library and fails open: if Mirror is down, slow or
confused, the prompt goes through untouched.

```sh
# from the project you want it in; writes .claude/settings.local.json (git-ignored)
python3 integrations/claude_code/mirror_hook.py --install --url http://HOST:8000
python3 integrations/claude_code/mirror_hook.py --uninstall
```

- Claude Code watches its settings files, so a new hook applies from the next prompt.
- Only related hypotheses are added. Relevance is the embedding score of each hypothesis
  against the prompt, with a higher bar (0.66) for plain prompts and a lower one (0.58) when
  the prompt names a known situation (a scenario trigger matched). Items far behind the best
  match are dropped and at most 5 are added. These numbers were tuned on a small set of
  prompts, and on prompts that were not used for tuning about 6 of 8 behaved as expected:
  an unrelated prompt can still get one or two items. Override per request with `min_score`.
- `--capture-prompts` (off by default) also remembers what you type, in the background. Prompts
  that look like they contain a secret, are shorter than 8 characters or longer than 2000 are
  never sent. Slash commands and automatic prompts are skipped.
- `MIRROR_HOOK=off` turns it off for a session. `MIRROR_HOOK_DEBUG=1` prints errors to stderr.
- Point it at a Mirror instance that holds your own data. A demo database would put someone
  else's habits into your prompts.

## Muse Code

Muse Code (the `muse` CLI) has three places Mirror can plug in. All three were run against a
real `muse` (1.4.14) in a scratch workspace.

| Connection point | What it does | Where it lives |
|---|---|---|
| **Hook** (`UserPromptSubmit`) | Adds related understanding before the model sees the prompt | `<project>/.muse/hooks.json` |
| **MCP server** | Lets Muse call `mirror_context`, `mirror_remember`, `mirror_feedback`, `mirror_scenario` | `~/.config/muse/settings.json` |
| **Skill** | Tells Muse when to call those tools, so it does so without being asked | `<project>/.agents/skills/mirror/SKILL.md` |

The hook is the same script as the Claude Code one. Muse Code sends the typed prompt in a
`prompt` field (Claude Code documents `prompt_text`); the script reads either. Muse Code runs
hooks with a cleared environment, so the hook finds Mirror through `.muse/mirror-hook.json`,
and the installed command uses an absolute Python path.

```sh
# hook + skill, into one project
python3 integrations/claude_code/mirror_hook.py --install --target muse --url http://HOST:8000 --project DIR
python3 integrations/claude_code/mirror_hook.py --uninstall --target muse --project DIR

# MCP: print the entry, then add it to ~/.config/muse/settings.json yourself
python3 integrations/claude_code/mirror_hook.py --print-mcp http://HOST:8100/mcp --token-file data/local/edge.token
```

`mcp_servers` in the settings file takes `"transport": "streamable_http"`, a `url` and `headers`.
Project-level MCP is not available in Muse Code, so this one entry goes in the user settings;
the installer does not edit that file.

What was observed, same model and same question ("I am tired tonight, what should I order for
dinner?"): with no Mirror the answer was generic; with the hook pointed at the first demo person
it recommended the usual familiar order and rest; pointed at the second person (the opposite
profile) it recommended trying a new place. With the MCP server and the skill but no instruction
to use any tool, Muse called `mirror_context` on its own and used what came back; without the
skill it did not call it.

Muse Code hook events are `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`,
`PostToolUse`, `PostToolUseFailure`, `PostToolBatch`, `PreLLMCall`, `PostLLMCall`, `PreCompact`,
`PostCompact`, `SubagentStart`, `SubagentStop`, `Notification`, `Stop`, `StopFailure` and
`SessionEnd`. Only `UserPromptSubmit` is used here. The exact hook input and output are not in
Meta's public docs; they were confirmed by running the hook and by the strings in the binary
(`hookSpecificOutput.hookEventName`, `additionalContext`, `decision: block`).
