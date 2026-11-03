import importlib.util

import pytest

from mirror import extract as ex
from mirror import schema
from tests.conftest import DINNER_TEXT


def _obs_in(text=DINNER_TEXT):
    return ex.ObservationInput(source_id="s", text=text,
                               occurred_at="2026-10-16T20:00:00+09:00")


def test_mock_is_deterministic(mock):
    assert mock.extract(_obs_in()) == mock.extract(_obs_in())


def test_mock_tired_familiar_hypothesis(mock):
    result = mock.extract(_obs_in())
    assert ex.validate_extraction(result) == []
    statements = [n.attrs.get("statement") for n in result.nodes
                  if n.kind == schema.HYPOTHESIS]
    assert "피곤할 때는 고르는 수고를 회피한다" in statements
    rels = {(e.rel, e.from_ref, e.to_ref) for e in result.edges}
    assert ("SUPPORTS", "obs", "hyp-0") in rels
    assert ("CONDITIONED_ON", "hyp-0", "ctx-0") in rels
    assert all(e.evidence_ref == "obs" for e in result.edges)


def test_mock_competing_hypotheses_link_alternatives(mock):
    text = "피곤한 저녁에 익숙한 곳 대신 새로운 메뉴를 처음 시도했다."
    result = mock.extract(_obs_in(text))
    assert ex.validate_extraction(result) == []
    refs = {n.ref: n.attrs["statement"] for n in result.nodes
            if n.kind == "Hypothesis"}
    pairs = {(refs[e.from_ref], refs[e.to_ref]) for e in result.edges
             if e.rel == "ALTERNATIVE_TO"}
    fatigue = "피곤할 때는 고르는 수고를 회피한다"
    novel = "새로운 경험을 탐색하려 한다"
    assert (fatigue, novel) in pairs and (novel, fatigue) in pairs


def test_validate_catches_missing_evidence():
    result = ex.ExtractionResult(
        nodes=[ex.NodeSpec(schema.HYPOTHESIS, "hyp-0",
                           {"statement": "s"})],
        edges=[ex.EdgeSpec("SUPPORTS", "obs", "hyp-0", "")])
    errors = ex.validate_extraction(result)
    assert any("evidence" in e for e in errors)


def test_validate_catches_ghost_ref_and_status():
    result = ex.ExtractionResult(
        nodes=[ex.NodeSpec(schema.HYPOTHESIS, "hyp-0",
                           {"statement": "s", "status": "active"})],
        edges=[ex.EdgeSpec("SUPPORTS", "obs", "hyp-ghost", "obs")])
    errors = ex.validate_extraction(result)
    assert any("hyp-ghost" in e for e in errors)
    assert any("candidate" in e for e in errors)


def test_validate_catches_sensitive_policy():
    result = ex.ExtractionResult(
        nodes=[ex.NodeSpec(schema.HYPOTHESIS, "hyp-0",
                           {"statement": "s", "domain": "관계·지원",
                            "use_policy": "apply_silently"})],
        edges=[])
    assert ex.validate_extraction(result) != []


def test_parse_bad_shape():
    with pytest.raises(ex.ExtractionError):
        ex.parse_extraction_json({"nodes": "nope", "edges": []})
    with pytest.raises(ex.ExtractionError):
        ex.parse_extraction_json({"nodes": [], "edges": [{"rel": "SUPPORTS"}]})


def test_get_extractor_unknown():
    with pytest.raises(ValueError):
        ex.get_extractor("nope")


def test_spark_prompt_wiring():
    ext = ex.SparkExtractor()
    prompt = ext._prompt_text(_obs_in("SCENE-MARKER"))
    assert "evidence_ref" in prompt and "SCENE-MARKER" in prompt


def test_optional_extractors_need_packages():
    if importlib.util.find_spec("anthropic") is None:
        with pytest.raises(ex.NotConfigured):
            ex.ClaudeExtractor().extract(_obs_in())
    if importlib.util.find_spec("ollama") is None:
        with pytest.raises(ex.NotConfigured):
            ex.OllamaExtractor().extract(_obs_in())


def _mock(text):
    from mirror import extract as ex
    return ex.MockExtractor().extract(
        ex.ObservationInput(source_id="t", text=text, occurred_at="unknown"))


def _stmts(result):
    return {n.attrs["statement"] for n in result.nodes
            if n.kind == "Hypothesis"}


def test_mock_aversion_to_novelty_is_not_exploration():
    r = _mock("야근으로 피곤해서 새로운 식당을 찾아보기 귀찮았다. "
              "결국 항상 시키는 익숙한 집에 배달을 주문했다.")
    assert "새로운 경험을 탐색하려 한다" not in _stmts(r)
    assert "피곤할 때는 고르는 수고를 회피한다" in _stmts(r)


def test_mock_novelty_still_detected_without_aversion():
    r = _mock("주말에 처음으로 방탈출에 갔다.")
    assert "새로운 경험을 탐색하려 한다" in _stmts(r)


def test_mock_other_person_liking_is_not_my_preference():
    r = _mock("DJ 선곡 기록. 반복이 강한 곡 위주로 골랐고 친구 J가 좋아했다.")
    assert not [n for n in r.nodes if n.kind == "Preference"]
    assert any(n.attrs.get("name") == "DJ 선곡"
               for n in r.nodes if n.kind == "Entity")


def test_mock_own_liking_is_preference():
    r = _mock("A카페가 좋아졌다.")
    assert [n for n in r.nodes if n.kind == "Preference"]


def test_mock_single_char_context_needs_word_boundary():
    ctx = lambda t: {n.attrs["value"] for n in _mock(t).nodes
                     if n.kind == "Context"}
    assert "비" not in ctx("비빔밥을 먹었다.")
    assert "비" in ctx("비 오는 날 집에 있었다.")
    assert "비" in ctx("비가 와서 늦었다.")
    assert "밤" in ctx("밤에 일찍 잤다.")


def test_mock_habit_cues_and_night_context():
    r = _mock("밤에 늘 가던 편의점에 들렀다.")
    assert "익숙한 선택에서 안정감을 느낀다" in _stmts(r)
    assert any(n.attrs.get("value") == "밤" for n in r.nodes
               if n.kind == "Context")


def test_mock_outcome_state_is_not_a_condition():
    outcome = _mock("처음으로 헬스장에 갔는데 개운했다.")
    cond = _mock("개운해서 헬스장에서 새로운 운동을 해 봤다.")
    conds = lambda r: [e for e in r.edges if e.rel == "CONDITIONED_ON"]
    assert not conds(outcome)
    assert conds(cond)


def test_mock_single_char_entity_needs_word_boundary():
    names = lambda t: [n.attrs["name"] for n in _mock(t).nodes
                       if n.kind == "Entity"]
    assert "집" not in names("집중이 잘 됐다.")
    assert "집" in names("집으로 돌아왔다.")
    assert "집" in names("집에서 쉬었다.")


def test_mock_topic_marked_other_person_is_not_my_preference():
    r = _mock("친구 J는 맛있다고 좋아했다.")
    assert not [n for n in r.nodes if n.kind == "Preference"]


def test_mock_preference_subject_is_nearest_before_cue():
    r = _mock("친구 J가 추천해서 가 본 방탈출 카페가 정말 좋았다.")
    prefs = [n.attrs["target"] for n in r.nodes if n.kind == "Preference"]
    assert prefs == ["방탈출 카페"]


def test_mock_always_cue_is_a_word_not_part_of_today():
    assert "익숙한 선택에서 안정감을 느낀다" not in _stmts(
        _mock("오늘 처음 가는 식당에서 점심을 먹었다."))
    assert "익숙한 선택에서 안정감을 느낀다" in _stmts(
        _mock("늘 마시던 커피를 시켰다."))


def test_drop_bad_edges_keeps_valid_rest():
    result = ex.ExtractionResult(
        nodes=[ex.NodeSpec(schema.STYLE, "sty-0",
                           {"pattern": "정보성", "intent": "정보 정리"})],
        edges=[ex.EdgeSpec("DERIVED_FROM", "obs", "sty-0", "obs")])
    errors = ex.validate_extraction(result)
    assert errors and errors[0].startswith("edge[0]")
    kept, left = ex._drop_bad_edges(result, errors)
    assert left == [] and kept.edges == [] and len(kept.nodes) == 1


def test_drop_bad_edges_leaves_node_errors_alone():
    result = ex.ExtractionResult(
        nodes=[ex.NodeSpec(schema.ENTITY, "ent-0", {"kind": "장소"})], edges=[])
    errors = ex.validate_extraction(result)
    kept, left = ex._drop_bad_edges(result, errors)
    assert left == errors


def test_user_prompt_lists_known_hypotheses_for_reuse():
    obs = ex.ObservationInput(source_id="t", text="x", occurred_at="unknown")
    plain = ex.build_user_prompt(obs)
    assert "Existing hypotheses" not in plain
    withk = ex.build_user_prompt(obs, ["피곤할 때는 익숙한 선택을 한다"])
    assert "reuse its statement EXACTLY" in withk
    assert "- 피곤할 때는 익숙한 선택을 한다" in withk


def test_extraction_prompt_has_no_korean_examples_and_asks_for_scene_language():
    import re
    sample_part = ex.EXTRACTION_SYSTEM_PROMPT.split("Node kinds and required attrs:")[0]
    examples = re.findall(r'"(?:name|value|phrase)": "([^"]*)"', ex.EXTRACTION_SYSTEM_PROMPT)
    assert not any(re.search("[가-힣]", e) for e in examples)
    assert "same language as the scene" in ex.EXTRACTION_SYSTEM_PROMPT
    user = ex.build_user_prompt(ex.ObservationInput(source_id="t", text="x", occurred_at="u"))
    assert not re.search("[가-힣]", user)
    assert "same language as the scene" in user


def test_sensitive_domain_is_forced_to_internal_only_instead_of_rejected():
    data = {"nodes": [{"kind": "Hypothesis", "ref": "hyp-0", "attrs": {
        "statement": "Wants short answers", "domain": "자기이해·반복 주제",
        "use_policy": "apply_silently", "status": "candidate"}}], "edges": []}
    r = ex.parse_extraction_json(data)
    assert r.nodes[0].attrs["use_policy"] == "internal_only"
    assert ex.validate_extraction(r) == []
    plain = ex.parse_extraction_json({"nodes": [{"kind": "Hypothesis", "ref": "h", "attrs": {
        "statement": "x", "domain": "취향·경험", "use_policy": "apply_silently"}}], "edges": []})
    assert plain.nodes[0].attrs["use_policy"] == "apply_silently"
