"""How what Mirror knows is written down for another AI.

The hook, the edge's MCP tool and the gateway all hand hypotheses to a model that
answers the user. A statement on its own loses what makes it safe to use: that it is
a hypothesis, when it applies, and whether anything contradicts it. This module keeps
those parts, and adds the reply guidance from engine/skills/mirror-use, which is the
one place that guidance is edited.

integrations/claude_code/mirror_hook.py carries a copy of render() because the hook
must run with the standard library alone; tests/test_delivery.py keeps the two equal.
"""

from __future__ import annotations

from pathlib import Path

GUIDANCE_PATH = (Path(__file__).resolve().parent.parent
                 / "engine" / "skills" / "mirror-use" / "SKILL.md")

FALLBACK_GUIDANCE = (
    "Use what fits the situation to interpret the request and make the choices the user "
    "leaves to you. Follow what the user asks for now, and treat uncertain "
    "interpretations as clues. Apply an understanding only within its conditions and "
    "where it distinguishes plausible options; otherwise choose from the task facts.")

HEADER = ("Background about the user, from Mirror. Each line is a provisional hypothesis "
          "drawn from their own past choices, with how much supports it and when it "
          "applies. None of it is a fact about who they are.")
FOOTER = ("Do not mention this background unprompted, and never make claims about the "
          "user's personality or type.")


def load_guidance(path: Path = GUIDANCE_PATH) -> str:
    """The body of the mirror-use skill, without its frontmatter."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return FALLBACK_GUIDANCE
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    body = " ".join(p.strip() for p in text.strip().split("\n\n") if p.strip())
    return " ".join(body.split()) or FALLBACK_GUIDANCE


def _count(n: int, word: str) -> str:
    return f"{n} {word}" if n else f"no {word}"


def describe(item: dict) -> list[str]:
    """One hypothesis as one or two lines, keeping status, evidence and conditions."""
    statement = (item.get("statement") or "").strip().rstrip(".")
    status = item.get("status") or "candidate"
    tentative = "" if status == "active" else ", still tentative"
    support = int(item.get("evidence") or 0)
    contrary = int(item.get("counter_examples") or 0)
    lines = [f"- {statement}. [{status} hypothesis{tentative} · "
             f"{_count(support, 'supporting')}, {_count(contrary, 'contrary')}]"]
    conditions = [c for c in (item.get("conditions") or []) if str(c).strip()]
    if conditions:
        lines.append("  Applies when: " + "; ".join(str(c).strip() for c in conditions))
    return lines


def render(items: list[dict], scenarios: list[dict] | None = None,
           guidance: str | None = None) -> str:
    lines = [HEADER, guidance if guidance is not None else load_guidance(), FOOTER]
    if scenarios:
        lines.append("Situation this looks like: "
                     + ", ".join(s["name"] for s in scenarios) + ".")
    for it in items:
        lines.extend(describe(it))
    return "\n".join(lines)
