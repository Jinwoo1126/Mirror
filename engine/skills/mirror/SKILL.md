---
name: mirror
description: Form, retrieve, and revise a user's provisional understanding from their choices, experiences, and feedback using the local Mirror store.
---

# Mirror

Use the host's existing reasoning to connect personal experiences, not just summarize instructions. The store checks provenance and revisions; it does not determine whether an interpretation is true. This skill orchestrates three linked procedures in the **same host**, not three models, services or mandatory calls per turn. Load a procedure when its work is needed; reuse it once loaded.

## Use during ordinary work

For ordinary personalized decisions and corrections to an ongoing answer, load [the reply guidance](../mirror-use/SKILL.md) and apply it to the current deliverable. Reuse supplied or already retrieved relevant understanding; this guidance requires no additional model call, formation or store write.

1. [Select relevant understanding and source experience](select.md) when personal understanding could affect current help, or new experience could change it. For reply delivery use the active index and pass only the selected card statements and necessary excerpts. For formation, inspect full cards and source events and append actual dialogue with stable IDs, roles and chronology. Current requests take priority.
2. [Form a provisional understanding](form.md) when selected evidence warrants one. Connect choices, experiences, possible function and conditions; preserve ordinary useful observations when no deeper interpretation is warranted. A plain factual lookup, formatting edit or repetition need not form or patch anything.
3. [Check/revise and deliver](check-revise.md) before persisting a material candidate and on relevant feedback. Check source support, contrary evidence, alternatives and scope using observed dialogue only; preserve a small decision trace, then keep/narrow/revise/withdraw/leave unresolved. Persist through `Store.request`/CLI `patch`, reread relevant `context`, and use the revised active understanding in the present/next answer.

Immediate feedback updates the affected understanding locally before present help. At a meaningful task boundary/transition use `prepare`; `noop` means skip reflection. Review only evidence actually processed. No background automation, fixed interval, per-turn classifier, compulsory question or compulsory synthesis call is installed. The same source evidence cannot count as both formation and later validation.

The experiment permits ordinary task clarification but forbids interviewing for hidden behavioral bases or psychological self-analysis. Internal why-chains are not questions to the user. Respect corrections and stop/delete requests immediately; never infer defense from disagreement or reconstruct suppressed content.

## Form an understanding

The full source-grounded formation procedure is [form](form.md). It preserves verbatim correction boundaries, applies established criteria without re-asking, distinguishes observation/self-report/hypothesis, and keeps explanations provisional. Current useful criteria do not require invented depth.

## Resolve material evidence gaps iteratively

Use [select's evidence loop](select.md#resolve-material-evidence-gaps-iteratively): provisional candidate → material evidence question → accessible support and contrasts/conditions → [formation](form.md) → [check/revise](check-revise.md). Repeat only within the agreed budget when new evidence could change the interpretation; otherwise retain uncertainty and help with the task. This is an executable host procedure, not demonstrated quality or semantic archive search.

## Local interface

From the project root, send one JSON request on stdin to:

```sh
python3 -m mirror --db .mirror-private/mirror.sqlite
```

Python callers use `Store.request({"op":"context","scope":"product"})`, not a `Store.context` method. The field is `scope`; `domain` is not accepted.

Operations: `append`, `index`, `deliver`, `context`, `events`, `prepare`, `patch`, `delete`, `inspect`. See [the architecture contract](../../docs/ARCHITECTURE.md) for fields and examples. One SQLite file belongs to one user. No runtime/schema change, automatic host hook or model call is installed by these procedure files. Follow [check/revise's persistence instructions](check-revise.md#persist-and-deliver) for revisions, withdrawal versus user suppression/deletion, review cursors and delivery to the next answer.

Record actual calls, elapsed time and tokens if available. Unknown cost is unknown, not zero. Procedure adoption alone does not establish understanding quality, better help or independent review.
