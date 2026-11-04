"""What reaches the answering AI keeps a hypothesis's meaning: status, evidence, conditions."""

import importlib.util
from pathlib import Path

import pytest

from mirror import delivery

HOOK = Path(__file__).resolve().parent.parent / "integrations" / "claude_code" / "mirror_hook.py"


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("mirror_hook", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ACTIVE = {"statement": "When tired, keeps to-do list short", "status": "active",
          "evidence": 3, "counter_examples": 0, "conditions": ["wiped out"]}
CANDIDATE = {"statement": "Picks the familiar option.", "status": "candidate",
             "evidence": 10, "counter_examples": 1, "conditions": []}


def test_active_hypothesis_keeps_its_conditions_and_counts():
    lines = delivery.describe(ACTIVE)
    assert lines[0] == ("- When tired, keeps to-do list short. "
                        "[active hypothesis · 3 supporting, no contrary]")
    assert lines[1] == "  Applies when: wiped out"


def test_candidate_is_marked_tentative_and_shows_contrary_evidence():
    (line,) = delivery.describe(CANDIDATE)
    assert "candidate hypothesis, still tentative" in line
    assert "10 supporting, 1 contrary" in line
    assert "option.." not in line


def test_rendered_context_carries_the_reply_guidance_from_the_skill():
    text = delivery.render([ACTIVE], [{"name": "Fit my energy"}])
    skill = delivery.GUIDANCE_PATH.read_text(encoding="utf-8")
    assert "apply an understanding only within its supported scope" in skill
    assert "apply an understanding only within its supported scope" in text
    assert "name: mirror-use" not in text            # frontmatter is stripped
    assert text.index(delivery.HEADER) < text.index("Situation this looks like")
    assert "never make claims about the user's personality" in text


def test_missing_skill_file_falls_back_to_short_guidance(tmp_path):
    assert delivery.load_guidance(tmp_path / "nope.md") == delivery.FALLBACK_GUIDANCE


def test_hook_copy_writes_exactly_what_the_reference_writes(hook):
    items = [ACTIVE, CANDIDATE, {"statement": "No fields at all"}]
    scen = [{"name": "Fit my energy"}]
    assert hook.build_context(items, scen) == delivery.render(items, scen)
    assert hook.load_guidance() == delivery.load_guidance()
    assert hook.HEADER == delivery.HEADER and hook.FOOTER == delivery.FOOTER
    assert hook.FALLBACK_GUIDANCE == delivery.FALLBACK_GUIDANCE


def test_edge_gateway_and_mcp_instructions_use_the_same_rendering():
    from mirror import edge
    assert edge.context_message([ACTIVE]) == delivery.render([ACTIVE])
    assert delivery.load_guidance() in edge.INSTRUCTIONS
