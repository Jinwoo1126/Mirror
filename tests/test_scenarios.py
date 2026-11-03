import pytest

from mirror import observe as observe_mod
from mirror import router as router_mod
from mirror import scenarios as sc


def _local(prompt):
    return "local", ""


@pytest.fixture
def seeded(store, mock):
    r = observe_mod.observe(store, mock, "s1", {
        "text": "피곤한 저녁에 익숙한 곳에서 먹었다.",
        "occurred_at": "2026-10-16T20:00:00+09:00"}, 0)
    observe_mod.observe(store, mock, "s2", {
        "text": "지친 밤에 단골집에 또 갔다.",
        "occurred_at": "2026-10-17T20:00:00+09:00"}, r.revision)
    return store


def _run(store, sid, inputs, gen=_local):
    return sc.run_scenario(store, router_mod.MockRouter(), None, sid, inputs, gen)


def test_list_has_four_scenarios():
    ids = {s["id"] for s in sc.list_scenarios()}
    assert ids == {"doc_draft", "music_recommend", "day_plan", "choice_help"}


def test_doc_draft_local_gives_outline_not_prose(seeded):
    out = _run(seeded, "doc_draft", {"topic": "Dinner", "kind": "Journal entry"})
    assert out["mode"] == "local"
    assert "Outline" in out["text"] and "What I did today" in out["text"]
    assert "LLM" in out["note"]


def test_music_ranks_only_given_candidates(seeded):
    out = _run(seeded, "music_recommend",
               {"situation": "피곤한 저녁", "candidates": "곡 A\n곡 B"})
    assert "1." in out["text"] and "2." in out["text"]
    assert "곡 C" not in out["text"]


def test_music_without_candidates_does_not_invent_tracks(seeded):
    out = _run(seeded, "music_recommend", {"situation": "출근길"})
    assert "No candidate tracks" in out["text"]


def test_day_plan_tired_caps_at_three(seeded):
    out = _run(seeded, "day_plan",
               {"state": "Tired", "tasks": "a\nb\nc\nd\ne"})
    assert "at most 3" in out["text"] and "Push to tomorrow" in out["text"]
    assert "- d" in out["text"]


def test_day_plan_normal_keeps_all(seeded):
    out = _run(seeded, "day_plan", {"state": "Normal", "tasks": "a\nb\nc\nd"})
    assert "4. d" in out["text"]


def test_choice_needs_two_options(seeded):
    with pytest.raises(sc.ScenarioError):
        _run(seeded, "choice_help", {"request": "저녁", "options": "하나"})


def test_missing_required_and_unknown(seeded):
    with pytest.raises(sc.ScenarioError):
        _run(seeded, "doc_draft", {"topic": " "})
    with pytest.raises(sc.ScenarioError):
        _run(seeded, "nope", {})


def test_llm_output_is_used_when_available(seeded):
    out = _run(seeded, "doc_draft", {"topic": "x"}, lambda p: ("llm", "생성된 본문"))
    assert out["mode"] == "llm" and out["text"] == "생성된 본문" and out["note"] == ""
