"""주간 변경 리포트 P4 (계획서 7장).

"이번 주 Mirror가 바꾼 이해": 새로 생긴 가설, 반례로 내려간 가설, 정정 내역을
근거 링크와 함께 보여준다. 해석 통보가 아니라 변경 이력 화면이다.
"""

from __future__ import annotations

from . import control as control_mod
from . import schema
from .store import MirrorStore

VERDICT_KEEP = "맞음"
VERDICT_WRONG = "아님"
VERDICT_SUPPRESS = "쓰지 마"
VERDICTS = (VERDICT_KEEP, VERDICT_WRONG, VERDICT_SUPPRESS)
# English aliases the UI sends; the Korean constants stay as the stored protocol values
VERDICT_ALIASES = {"correct": VERDICT_KEEP, "wrong": VERDICT_WRONG,
                   "dont_use": VERDICT_SUPPRESS, "don't use": VERDICT_SUPPRESS}


def weekly_report(store: MirrorStore, since: str,
                  as_of: str | None = None) -> dict:
    as_of = as_of or schema.utcnow_iso()
    new_hyps = []
    for row in store.hypotheses_recorded_between(since, as_of):
        new_hyps.append({
            "id": row[0], "statement": row[1], "status": row[2],
            "use_policy": row[3], "domain": row[4],
            "supports": store.supporting_observations(row[0]),
            "contradicts": store.contradicting_observations(row[0]),
        })
    demoted, retired = [], []
    for row in store.hypotheses_changed_between(since, as_of):
        entry = {"id": row[0], "statement": row[1], "status": row[2],
                 "supports": store.supporting_observations(row[0])}
        if row[2] == schema.STATUS_SUSPENDED:
            demoted.append(entry)
        elif row[2] == schema.STATUS_RETIRED:
            retired.append(entry)
    corrections = []
    for row in store.corrections_between(since, as_of):
        corrections.append({"id": row[0], "kind": row[1], "target": row[2],
                            "text": row[3]})
    report = {"since": since, "as_of": as_of, "new_hypotheses": new_hyps,
              "demoted": demoted, "retired": retired,
              "corrections": corrections}
    report["markdown"] = render_markdown(report)
    return report


def render_markdown(report: dict) -> str:
    lines = ["# What Mirror changed this week",
             f"{report['since'][:10]} ~ {report['as_of'][:10]}", ""]
    lines.append(f"## New hypotheses ({len(report['new_hypotheses'])})")
    for h in report["new_hypotheses"]:
        lines.append(f"- [{h['status']}] {h['statement']} (evidence "
                     f"{', '.join(h['supports']) or 'none'})")
    lines.append("")
    lines.append(f"## Demoted or retired hypotheses "
                 f"({len(report['demoted']) + len(report['retired'])})")
    for h in report["demoted"] + report["retired"]:
        lines.append(f"- [{h['status']}] {h['statement']}")
    lines.append("")
    lines.append(f"## Corrections ({len(report['corrections'])})")
    for c in report["corrections"]:
        lines.append(f"- [{c['kind']}] {c['target']}: {c['text']}")
    lines.append("")
    lines.append("Mark a hypothesis Correct, Wrong or Don't use to apply it.")
    return "\n".join(lines)


def apply_feedback(store: MirrorStore, hyp_id: str, verdict: str,
                   text: str = "") -> dict:
    """Apply a report verdict through control."""
    verdict = VERDICT_ALIASES.get(verdict.strip().lower(), verdict)
    if verdict not in VERDICTS:
        return {"ok": False, "error": f"unknown_verdict: {verdict}"}
    if verdict == VERDICT_KEEP:
        if store.get_node(schema.HYPOTHESIS, hyp_id) is None:
            return {"ok": False, "error": "not_found"}
        return {"ok": True, "action": "keep", "id": hyp_id}
    if verdict == VERDICT_WRONG:
        res = control_mod.control(
            store, "correct", {"kind": schema.HYPOTHESIS, "id": hyp_id},
            {"kind": schema.CORRECTION_INTERPRETATION,
             "text": text or "Marked wrong from the report"})
        return {"ok": res.ok, "action": "correct", "error": res.error,
                "detail": res.detail, "revision": res.revision}
    res = control_mod.control(store, "suppress",
                              {"kind": schema.HYPOTHESIS, "id": hyp_id})
    return {"ok": res.ok, "action": "suppress", "error": res.error,
            "detail": res.detail, "revision": res.revision}
