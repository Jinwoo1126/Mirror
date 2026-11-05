# engine/skills: the one place the understanding guidance is edited

These five files came from the team handoff (`Mirror-team-handoff`, 2026-11-04). They say how
a host should pick the experiences that matter, form a provisional explanation, check and
revise it, and use it in a reply. Edit them here; nothing else in the repository keeps its
own copy of the reply guidance.

| File | What reads it here |
|---|---|
| `mirror-use/SKILL.md` | `mirror/delivery.py` puts its body into every context Mirror hands out: the hook, the edge gateway's system message and the MCP server instructions. `mirror_hook.py --install --target muse` installs it beside the Muse skill as `.agents/skills/mirror-use`. |
| `mirror/form.md`, `mirror/check-revise.md` | The extraction prompt in `mirror/extract.py` follows their rules for existing hypotheses: link new evidence to them by id, and narrow or hold one back when a later experience or a correction limits it. |
| `mirror/SKILL.md`, `mirror/select.md` | Reference for hosts that run the procedure themselves. Not loaded by the server. |

The skills name operations of the handoff's local SQLite reference. This repository has no
such CLI; the same work goes through these routes:

| Skill operation | Here |
|---|---|
| `index`, `context`, `deliver` | `POST /api/retrieve` (edge: `mirror_context`, `/v1/context`). Items keep `status`, `conditions`, `evidence` and `counter_examples`; `internal_only` is withheld. |
| `append` | `POST /api/memories` with optional `episode` and `prior_answer` (the assistant's reply the user is reacting to). |
| `patch` (support, contradict, narrow) | The extractor's edges to an existing hypothesis id and its `revisions`, applied in `mirror/observe.py`. |
| `withdraw` | `control("correct", kind="interpretation")`, or the feedback verdict `wrong` |
| `suspend` | `control("suppress")`, or the verdict `dont_use` |
| `delete` | `control("delete")`, which also blocks the understanding from coming back |
| `prepare`, `inspect`, `events` | No counterpart yet. |

The handoff's SQLite store (`mirror/store.py` in the ZIP) is a separate package that is also
called `mirror`. It is not copied here.
