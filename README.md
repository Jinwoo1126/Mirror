<div align="center">

# Mirror

**An agent that learns how you think and choose, and lends that to the AI tools you already use.**

It keeps what it learns as a time-aware knowledge graph, shows it back to you as a map of your own
body, and gives the relevant part to another AI when you ask it to.

![Python 3.11](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)
![Docker Compose](https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white)
![Tests](https://img.shields.io/badge/tests-362%20passing-2ea44f)
![Graph](https://img.shields.io/badge/graph-Kuzu-7B61FF)
![MCP](https://img.shields.io/badge/MCP-supported-0F766E)
![Built with Muse](https://img.shields.io/badge/built%20with-Muse-0866FF)

[Quick start](#quick-start) ·
[Connect it to other AI](#connect-it-to-other-ai-services) ·
[How it works](#how-it-works) ·
[Run without containers](#run-it-without-containers) ·
[Hosted version](#a-hosted-multi-user-version-shelved)

<img src="docs/media/body-map.gif" alt="Tapping Brain, Hands and Heart flies into each region of the body map" width="860">

</div>

---

Mirror is not a diary. You tell it what happened, in your own words. It works out what repeats
(what you reach for when you are tired, how you like a document written, what you cut under a
deadline), keeps the evidence next to each pattern, and lets you say "that is wrong". Nothing it
shows is a verdict about your personality: every claim comes with the memories behind it, and you
can correct, suppress or delete it.

## What you see

<table>
<tr>
<td width="50%"><img src="docs/media/body-map-user-a.jpg" alt="Body map of user A"></td>
<td width="50%"><img src="docs/media/body-map-user-b.jpg" alt="Body map of user B"></td>
</tr>
<tr>
<td valign="top"><b>The body map.</b> Everything Mirror knows is sorted by the part of the body it belongs to: the brain thinks and chooses, the ears listen, the mouth eats, the hands write and make. Tap a part to fly in.</td>
<td valign="top"><b>Every person gets their own.</b> This is a different user, who likes the opposite things. The character is drawn from that person's own profile, and sensitive areas are left out.</td>
</tr>
<tr>
<td><img src="docs/media/hypotheses.jpg" alt="Hypotheses with evidence and counter-examples"></td>
<td><img src="docs/media/scenarios.jpg" alt="Fourteen everyday scenarios"></td>
</tr>
<tr>
<td valign="top"><b>Hypotheses.</b> Each pattern lists the memories that support it and the ones that contradict it. Mark it Correct, Wrong or Don't use.</td>
<td valign="top"><b>Scenarios.</b> Fourteen everyday tasks (write a document, pick music, plan a trip, review code, work with someone) helped by what Mirror knows. A hook can start one for you.</td>
</tr>
</table>

<p align="center"><img src="docs/media/graph.jpg" alt="Knowledge graph of memories, things, situations and hypotheses" width="720"></p>

The two users in the screenshots are made up. Their memories are in `data/demo/` and `data/demo2/`.

## A case: coding with Muse Code

The same file and the same three requests go to Muse Code twice, each time connected to a different user's
Mirror over MCP. The commit messages come back in each user's own style, and a decision stated in the
conversation is saved into that user's Mirror and attached to a hypothesis as evidence.

<p align="center"><img src="docs/media/code-case-compare.jpg" alt="The same task for two users: reviews, commit messages and what was saved" width="820"></p>

Details, the exact requests and how to run it yourself are in [`docs/cases/code`](docs/cases/code/README.md).
It is one run per user, with the habits told to Mirror by hand; the page says what it does and does not show.

## Quick start

One container per user. Each mounts only its own data folder, so users never see each other's
memories and neither knows the other exists.

```sh
echo 'META_API_KEY=...' > .env        # to learn from new memories and write answers. Git ignores .env
docker compose up -d --build

# User A   http://127.0.0.1:8011     data/demo
# User B   http://127.0.0.1:8012     data/demo2
docker compose ps                      # open it once the status is healthy
docker compose down                    # stop. Your data stays in data/
```

- You can **read everything that is already there without a key**: memories, profile, body map,
  hypotheses. Only learning from a new memory and writing chat answers need `META_API_KEY`.
- **There is no sign-in.** The ports are published on this machine only (`127.0.0.1`). To reach them
  from your other devices, for example over tailscale, set `MIRROR_BIND` and `MIRROR_HOST` in `.env`
  to this machine's private address, and assume everyone on that network can read the data.
- **Only one process may open a database file** (Kuzu allows one). Do not run
  `python -m mirror.app` on the same `data/demo` while its container is up.
- **Ports 8011 and 8012** avoid 8001 and 8002, which other containers on a developer machine often hold.
- **Starting from a fresh clone** is fine. The demo databases are rebuilt from the observations
  (needs `META_API_KEY`, takes a few minutes):
  ```sh
  docker compose run --rm --entrypoint python user-a -m mirror.ingest /data/persona_observations.jsonl --db /data/mirror.kuzu
  docker compose run --rm --entrypoint python user-b -m mirror.ingest /data/persona_observations.jsonl --db /data/mirror.kuzu
  ```
- **To add a user**, copy one service in `docker-compose.yml` and change the port and the `data/<folder>`.
- Kuzu's file format is tied to its version. It must match the version that wrote the databases
  (`KUZU_VERSION` in `Dockerfile.local`, currently 0.11.3).
- The embedding model is shared from `data/models` as `/models`. If it is missing it is downloaded on
  first run (about 470 MB).

## Connect it to other AI services

| How | What you need | Notes |
|---|---|---|
| **Hooks** for Claude Code and Muse Code | Nothing | The simplest, and the one checked end to end. Every prompt gets the relevant memories added, and unrelated prompts get nothing. |
| **MCP** in Claude Code and Muse Code | An edge container and a token | Checked with both: a fresh session called `mirror_context` and got that user's memories back. See `docs/CONNECT.md`. |
| **MCP** (`/mcp`), **plain HTTP** (`/v1/context`, `/v1/remember`), an **OpenAI-compatible gateway** | An edge container and a token | `echo "MIRROR_TOKEN_A=$(openssl rand -hex 24)" >> .env`, then `docker compose --profile edge up -d`. Edges listen on 8111 (user A) and 8112 (user B). |
| **The Muse agent** (muse.ai) | A public HTTPS address and an edge token | It is a cloud service, so it cannot reach a local address. Needs a tunnel. Not verified. See `docs/CONNECT.md`. |

The app ports (8011, 8012) have no sign-in. Only the edge should ever be exposed.

**Hooks.** No token is needed. From the project you want to use it in:

```sh
python integrations/claude_code/mirror_hook.py --install --target muse --url http://127.0.0.1:8011
# Claude Code: --target claude (the default). For the other user, use port 8012.
```

Full details, the MCP and gateway setup, and the cloud-agent notes are in [`docs/CONNECT.md`](docs/CONNECT.md).

## Principles

- **No flat statements about you.** An interpretation is only ever a hypothesis node that carries
  conditions, counter-examples and alternatives.
- **No edge without evidence.** Every edge except `USED_IN` names the observation that supports it.
- **Two kinds of time.** `valid_*` is when something was true in the world; `recorded_at` is when Mirror
  learned it. Queries can be made `as_of` a moment, which blocks information from the future.
- **A failure changes nothing.** `observe`, `control` and `retrieve` leave the graph as it was and say why.
- **Delete and suppress take effect at once**, and a block list stops a deleted understanding from coming back.
- **Some hypotheses never leave Mirror.** Relationship and self-understanding patterns are marked
  `internal_only` and are left out of anything shown to other tools or used to draw the character.
- **Extraction is done by a language model only.** The rule-based `MockExtractor` exists for tests.
  If no model is available Mirror says so instead of guessing with rules.

## How it works

```
 what you say ──► observe ──► extract (LLM) ──► validate ──► graph (Kuzu, two kinds of time)
                                                               │
 other AI tools ◄── hook / MCP / HTTP / gateway ◄── retrieve ◄─┘   hybrid search: BM25 + embeddings (RRF)
                         ▲                              ▲
                         └──── edge (token) ────────────┘          hypotheses are promoted after
                                                                    2 distinct days with no contradiction
```

<details>
<summary><b>Where things live</b></summary>

```
mirror/
  schema.py store.py          ontology and the Kuzu two-time store
  llm.py extract.py           model calls and the extractors (spark, Claude, ollama)
  observe.py control.py       taking in observations; query, correct, suppress, delete, re-allow
  retrieve.py                 relevant understanding for a request
  hypotheses.py               promotion, weekly review, nightly consolidation
  router.py assistant.py      intent and style routing
  simulate.py                 competing hypotheses for a decision
  agents.py replay.py         A / M1 / M2 agents and the replay harness
  policy.py report.py         intervention levels 0-3 and the weekly change report
  ingest.py eval_precision.py bulk intake and extraction scoring
  vstore.py bm25.py search.py local embeddings, BM25 and hybrid search
  constellation.py            the body map
  scenarios.py                the fourteen scenarios
  avatar_gen.py               the character, generated from a profile
  edge.py                     MCP, plain HTTP and the OpenAI-compatible gateway
  app.py static/app.html      the web app
  pg_store.py auth.py tokens.py worker.py privacy.py    the hosted version (see below)
integrations/claude_code/     the hook for Claude Code and Muse Code, and a Muse Code skill
supabase/                     migrations for the hosted version
Dockerfile.local docker-compose.yml     the containers above
data/demo/ data/demo2/        two made-up users: observations, a ready database, their characters
docs/                         CONNECT.md, AVATAR.md, DEPLOY.md, media/
eval/                         scoring guide and recorded results
tests/                        unit and integration tests
```

Personal sample data and generated reports are not in the repository. Bring your own JSONL in the
same format as `data/demo/persona_observations.jsonl`. The scoring format is in `eval/GUIDELINE.md` and
the recorded measurements are in `eval/RESULTS.md`.

</details>

## Run it without containers

```sh
uv venv --python 3.11
uv pip install -e '.[test]'
source .venv/bin/activate
```

Add `.[Muse]` or `.[ollama]` for those extractors and `.[hosted]` for the Postgres version. The `spark`
extractor uses a signed-in `muse` CLI (`muse-spark-1.3` by default) or `META_API_KEY`.

```sh
# take in observations (add --dry-run to see what would be extracted without saving)
python -m mirror.ingest data/demo/persona_observations.jsonl --db data/local/mirror.kuzu

# score extraction against a gold set; counts are reported as they are
python -m mirror.eval_precision eval/gold_heldout3.jsonl --extractor spark

# the web app, at http://127.0.0.1:8000
python -m mirror.app --db data/local/mirror.kuzu --port 8000

# an MCP server that runs next to a client such as Claude Code or Muse Code
MIRROR_DB=data/local/mirror.kuzu python -m mirror.server

# the scheduled jobs
python -c "from mirror.store import MirrorStore; from mirror import hypotheses as H
st = MirrorStore('data/local/mirror.kuzu'); print(H.nightly_maintenance(st)); print(H.weekly_review(st))"

# tests. The Postgres ones run only when a database is given, and are skipped otherwise
python -m pytest
MIRROR_TEST_PG_DSN=postgresql://postgres:mirror-test-only@127.0.0.1:54329/mirror python -m pytest   # see supabase/README.md
```

Search is a hybrid of BM25 and a multilingual MiniLM embedding (384 dimensions, local ONNX) fused
with reciprocal rank fusion. The model is downloaded on first use to `data/models/` (about 470 MB;
change it with `MIRROR_MODEL_DIR`). Without it Mirror falls back to BM25 only.

Chat answers use the first model it finds: `spark`, then Claude, then ollama. With none of them, it
answers by quoting the matching memories.

Useful variables: `MIRROR_DB`, `MIRROR_EXTRACTOR` (`spark`, `Muse`, `ollama`; default `spark`),
`MIRROR_ROUTER` (`mock`, `llm`), `MIRROR_NO_INDEX=1` (plain search), `MIRROR_MODEL_DIR`,
`META_API_KEY`, `ANTHROPIC_API_KEY`.

An MCP client entry that runs the server directly:

```json
{
  "mcpServers": {
    "mirror": {
      "command": "/path/to/mirror/.venv/bin/python",
      "args": ["-m", "mirror.server"],
      "cwd": "/path/to/mirror",
      "env": {"MIRROR_DB": "data/local/mirror.kuzu", "MIRROR_EXTRACTOR": "spark", "META_API_KEY": "..."}
    }
  }
}
```

## A hosted, multi-user version (shelved)

The same app can run as a web service for many people. The code is finished and tested, but **it is not
deployed.** The server was taken down (zero machines; `fly deploy` brings it back) and the Supabase
project is empty. Two things must be solved before turning it on again: a payment card on the Fly
account (without one the machine is stopped every five minutes), and putting the database and the
server in the same region (with the server in Tokyo and the database in Ireland, a single request
spent over ten seconds waiting on the database).

<details>
<summary><b>What it does, and what is verified</b></summary>

With `MIRROR_HOSTED=1`, Mirror stores each person's data in Postgres (Supabase) instead of Kuzu and
every `/api/*` call needs a Supabase sign-in token.

- **Isolation.** Every table has a `user_id` and row level security. The app's own queries run in a
  session that has the user's rights, so a query that forgot its filter still cannot see another
  person's rows. Deleting an account deletes its data.
- **Personal API tokens.** Issued and revoked on the Connect tab so hooks and other services can
  work without signing in. Only a hash is stored, and a token can read and add memories but nothing else.
- **Background learning.** Saving a memory answers at once; a worker learns from it, with retries and
  one job per person at a time.
- **Your rights.** "Download my data", "Delete my data", and a `/privacy` notice generated from what the code does.
- **Models.** The server calls the Meta Model API over HTTP with `META_API_KEY`, so it needs no `muse` CLI.

Status: the code and its 362 tests are done. It was deployed once; the server, the database
connection, the sign-in screen, sending the email link and the Meta API call were all checked, and then it
was taken down. Not verified: finishing a sign-in through the email link (the email limit was hit),
Google and Meta sign-in, and a hook against a deployed address. Steps and what is left are in
[`docs/DEPLOY.md`](docs/DEPLOY.md); running the Postgres tests locally is in `supabase/README.md`.

| Variable | Use |
|---|---|
| `MIRROR_HOSTED=1` | Turn the hosted mode on |
| `MIRROR_PG_DSN` | Postgres connection string |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | Browser sign-in settings (safe to be public) |
| `SUPABASE_JWT_SECRET` | Only for a project that still uses a shared secret |
| `SUPABASE_SERVICE_ROLE_KEY` | A server secret. Lets "Delete my data" remove the account too (optional) |
| `META_API_KEY` | Model calls |
| `MIRROR_OPERATOR_NAME`, `MIRROR_CONTACT_EMAIL` | Who the privacy notice names |
| `MIRROR_RATE_ALL`, `MIRROR_RATE_COSTLY` | Per-person limits per minute (default 120 and 10) |
| `MIRROR_WORKER=0` | Turn off the worker thread in web processes (when running `python -m mirror.worker` separately) |

</details>

## Built with

- **Muse Code** wrote the initial implementation of Mirror: the knowledge graph, the extraction pipeline
  and the first version of the app. It is also a supported client, with a hook, an MCP entry and a skill included.
- **Muse Spark**, through the Meta Model API, reads memories and extracts what repeats.
- **Muse Image** draws the characters. Each one is designed from the person's own profile, with
  sensitive areas left out.
- Claude Code is a supported client too, through the same hook.
- Kuzu for the graph, Starlette for the app, fastembed for local embeddings, Supabase for the hosted version.
- The ideas of a dynamic ontology and a chat-first memory app come from MiroFish and personal.ai.
