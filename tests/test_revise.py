"""A new experience links to existing hypotheses by id, and narrows or holds them back."""

import json

import pytest

from mirror import extract as ex
from mirror import observe as observe_mod
from mirror import schema


class Scripted:
    """An extractor that returns a fixed result and records what it was shown."""

    name = "scripted"

    def __init__(self, result: ex.ExtractionResult):
        self.result = result
        self.known_hypotheses = None
        self.seen: list[ex.ObservationInput] = []

    def extract(self, obs):
        self.seen.append(obs)
        return self.result


def _active(store, hid="hyp-fam", statement="Picks the familiar option", conditions=()):
    store.add_hypothesis(schema.Hypothesis(id=hid, statement=statement,
                                           conditions=list(conditions),
                                           status=schema.STATUS_ACTIVE))
    store.bump_revision()
    return hid


def _observe(store, extractor, text="On weekends I want to try somewhere new.", **event):
    return observe_mod.observe(store, extractor, f"src-{len(extractor.seen)}",
                               {"text": text, "occurred_at": "2026-11-01T12:00:00+09:00",
                                **event}, store.get_revision())


def test_extractor_sees_existing_hypotheses_with_ids_status_and_conditions(store):
    _active(store, conditions=["tired"])
    x = Scripted(ex.ExtractionResult())
    assert _observe(store, x).ok
    assert x.known_hypotheses == [{"id": "hyp-fam", "statement": "Picks the familiar option",
                                   "status": "active", "conditions": ["tired"]}]
    prompt = ex.build_user_prompt(x.seen[0], x.known_hypotheses)
    assert "- hyp:hyp-fam [active; when: tired] Picks the familiar option" in prompt


def test_support_links_to_an_existing_hypothesis_without_matching_words(store):
    hid = _active(store)
    x = Scripted(ex.ExtractionResult(edges=[
        ex.EdgeSpec("SUPPORTS", "obs", "hyp:hyp-fam", "obs")]))
    res = _observe(store, x, text="Ordered the same thing as always.")
    assert res.ok and res.revised == []
    assert res.obs_id in store.supporting_observations(hid)
    assert store.get_hypothesis(hid).status == schema.STATUS_ACTIVE


def test_unknown_existing_ref_is_rejected_and_nothing_is_written(store):
    _active(store)
    before = store.get_revision()
    x = Scripted(ex.ExtractionResult(edges=[
        ex.EdgeSpec("SUPPORTS", "obs", "hyp:hyp-invented", "obs")]))
    res = _observe(store, x)
    assert not res.ok and "unknown to_ref" in res.error
    assert store.get_revision() == before


def test_contrary_experience_holds_an_active_hypothesis_back(store):
    hid = _active(store)
    x = Scripted(ex.ExtractionResult(edges=[
        ex.EdgeSpec("CONTRADICTS", "obs", "hyp:hyp-fam", "obs")]))
    res = _observe(store, x)
    assert res.ok and res.revised == [f"held back {hid}: contrary experience"]
    assert store.get_hypothesis(hid).status == schema.STATUS_CANDIDATE
    assert res.obs_id in store.contradicting_observations(hid)


def test_narrowing_adds_a_condition_with_evidence_and_keeps_it_active(store):
    hid = _active(store)
    x = Scripted(ex.ExtractionResult(
        edges=[ex.EdgeSpec("CONTRADICTS", "obs", "hyp:hyp-fam", "obs")],
        revisions=[ex.RevisionSpec("hyp:hyp-fam", "narrow", "  on tired   weekdays ")]))
    res = _observe(store, x)
    assert res.ok and res.revised == [f"narrowed {hid}: on tired weekdays"]
    hyp = store.get_hypothesis(hid)
    assert hyp.status == schema.STATUS_ACTIVE and hyp.conditions == ["on tired weekdays"]
    ctx = store.find_context("condition", "on tired weekdays")
    assert ctx is not None
    # the same boundary again does not duplicate the condition
    again = Scripted(ex.ExtractionResult(revisions=[
        ex.RevisionSpec("hyp:hyp-fam", "narrow", "on tired weekdays")]))
    assert _observe(store, again, text="Weekday, tired, usual place again.").ok
    assert store.get_hypothesis(hid).conditions == ["on tired weekdays"]


@pytest.mark.parametrize("rev, message", [
    (ex.RevisionSpec("hyp:hyp-fam", "rewrite", "x"), "unknown action"),
    (ex.RevisionSpec("hyp:hyp-nope", "narrow", "x"), "not an existing hypothesis"),
    (ex.RevisionSpec("hyp:hyp-fam", "narrow", " "), "missing condition"),
])
def test_bad_revisions_are_rejected(store, rev, message):
    _active(store)
    res = _observe(store, Scripted(ex.ExtractionResult(revisions=[rev])))
    assert not res.ok and message in res.error


def test_suppressed_hypotheses_are_not_offered_or_linkable(store):
    hid = _active(store)
    store.set_hypothesis_status(hid, schema.STATUS_SUPPRESSED)
    x = Scripted(ex.ExtractionResult(edges=[
        ex.EdgeSpec("SUPPORTS", "obs", "hyp:hyp-fam", "obs")]))
    res = _observe(store, x)
    assert x.known_hypotheses == [] and not res.ok


def test_prior_answer_is_context_for_the_extractor_and_kept_with_the_episode(store):
    x = Scripted(ex.ExtractionResult())
    res = _observe(store, x, text="No, not the usual place on weekends.",
                   prior_answer="Order your usual fried chicken.", episode="s-1")
    assert res.ok
    seen = x.seen[0]
    assert seen.prior_answer == "Order your usual fried chicken."
    prompt = ex.build_user_prompt(seen)
    assert "assistant's previous reply (context only" in prompt
    assert "scene (the person's own words): No, not the usual place" in prompt
    obs = store.get_observation_by_source("src-0")
    assert obs.text == "No, not the usual place on weekends."     # only the person's words
    assert json.loads(obs.raw_ref) == {"episode": "s-1",
                                       "prior_answer": "Order your usual fried chicken."}


def test_revisions_parse_from_model_json():
    r = ex.parse_extraction_json({"nodes": [], "edges": [], "revisions": [
        {"hypothesis": "hyp:hyp-1", "action": "narrow", "condition": "weekdays"}]})
    assert r.revisions == [ex.RevisionSpec("hyp:hyp-1", "narrow", "weekdays")]
    with pytest.raises(ex.ExtractionError):
        ex.parse_extraction_json({"revisions": [{"action": "narrow"}]})
