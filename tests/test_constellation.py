import pytest

from mirror import constellation as cn
from mirror import observe as observe_mod
from mirror import scenarios as sc


def test_classify_routes_by_keyword_in_both_languages():
    assert cn.classify("I always pick the same restaurant for lunch") == "mouth"
    assert cn.classify("반복이 강한 곡 위주로 고른다") == "ears"
    assert cn.classify("Writes short emails and memos") == "hands"
    assert cn.classify("Feels tired after late meetings") == "heart"
    assert cn.classify("Walks in the park when stuck") == "feet"
    assert cn.classify("Prefers to decide quickly") == "brain"
    assert cn.classify("") == "brain"


def test_ascii_keywords_match_whole_words_only():
    assert cn.classify("a runtime error") != "feet"      # "run" inside "runtime"
    assert cn.classify("a quick run in the morning") == "feet"


def test_declare_creates_an_active_backed_hypothesis(store):
    res = cn.declare(store, "I decide fast and revisit later")
    assert res["ok"] and not res.get("duplicate")
    hyp = store.get_hypothesis(res["id"])
    assert hyp.status == "active" and hyp.use_policy == "apply_silently"
    assert store.supporting_observations(res["id"]) == [res["observation"]]
    again = cn.declare(store, "I decide fast and revisit later")
    assert again["duplicate"] and again["id"] == res["id"]


def test_private_declaration_is_internal_only(store):
    res = cn.declare(store, "I find big meetings draining", private=True)
    assert store.get_hypothesis(res["id"]).use_policy == "internal_only"


def test_declare_rejects_empty(store):
    assert not cn.declare(store, "   ")["ok"]


def test_map_puts_declared_statements_in_a_region_and_in_person(store):
    cn.declare(store, "I eat the same lunch most days")
    cn.declare(store, "I usually choose the quiet option")
    m = cn.build_map(store)
    regions = {r["id"]: r for r in m["regions"]}
    assert [s["label"] for s in regions["mouth"]["stars"]] == ["I eat the same lunch most days"]
    assert all(s["declared"] for s in m["person"]["declared"])
    assert len(m["person"]["declared"]) == 2
    assert any("quiet option" in s["label"] for s in m["person"]["chooses"])
    assert [r["id"] for r in m["regions"]] == ["brain", "ears", "eyes", "mouth", "hands", "heart", "feet"]


def test_map_includes_extracted_things_and_states(store, mock):
    observe_mod.observe(store, mock, "s1", {
        "text": "피곤한 저녁에 익숙한 식당에서 먹었다.",
        "occurred_at": "2026-10-16T20:00:00+09:00"}, 0)
    m = cn.build_map(store)
    labels = {s["label"] for r in m["regions"] for s in r["stars"]}
    assert "식당" in labels and "피곤함" in labels


def test_scenarios_are_english_and_hookable():
    assert len(sc.SCENARIOS) == 14
    for s in sc.SCENARIOS.values():
        assert not any("가" <= ch <= "힣" for ch in s.name + s.summary + s.needs_llm), s.id
        assert s.triggers, s.id
        for f in s.fields:
            assert not any("가" <= ch <= "힣" for ch in f.label + f.placeholder), (s.id, f.key)


@pytest.mark.parametrize("text,expected", [
    ("Please review this pull request", "code_review"),
    ("I'm behind schedule on this deadline", "deadline_pressure"),
    ("Can you explain how transformers work", "learning_style"),
    ("Draft an email reply to my manager", "writing_tone"),
    ("planning a trip to Lisbon", "travel_plan"),
])
def test_match_scenarios(text, expected):
    assert expected in [m["id"] for m in sc.match_scenarios(text)]


def test_match_nothing_for_unrelated_text():
    assert sc.match_scenarios("What is the capital of France?") == []


def test_lens_scenario_local_lists_patterns_or_a_hint(store, mock):
    import mirror.router as router_mod
    out = sc.run_scenario(store, router_mod.MockRouter(), None, "code_review",
                          {"subject": "A refactor of billing"}, lambda p: ("local", ""))
    assert out["mode"] == "local" and "no patterns relevant" in out["text"]
    assert "pull requests" in out["text"] or "review habits" in out["text"]
    cn.declare(store, "I block pull requests that have no tests")
    out2 = sc.run_scenario(store, router_mod.MockRouter(), None, "code_review",
                           {"subject": "pull request about billing tests"},
                           lambda p: ("local", ""))
    assert "no tests" in out2["text"]


def test_statement_decides_before_evidence():
    assert cn.classify_pattern("I eat late", ["listened to a track", "a song", "music"]) == "mouth"


def test_general_pattern_is_not_pulled_by_a_few_memories():
    mixed = ["ate lunch", "a new track", "wrote a memo", "went to the park", "had coffee"]
    assert cn.classify_pattern("Repeats familiar choices", mixed) == "brain"


def test_clear_majority_of_evidence_sets_the_region():
    food = ["ate lunch", "had coffee", "ordered dinner", "a snack", "wrote a memo"]
    assert cn.classify_pattern("Repeats familiar choices", food) == "mouth"


def test_a_sql_request_does_not_trigger_the_document_scenario():
    assert "doc_draft" not in [m["id"] for m in sc.match_scenarios("Write a SQL query to find duplicates")]
    assert "doc_draft" in [m["id"] for m in sc.match_scenarios("Write a blog post about my week")]
