import json

from mirror import eval_precision as ev
from mirror import ingest as ingest_mod


def _write(path, lines):
    path.write_text("\n".join(json.dumps(l, ensure_ascii=False)
                              for l in lines) + "\n", encoding="utf-8")
    return str(path)


def test_ingest_file_ok_and_duplicate(store, mock, tmp_path):
    path = _write(tmp_path / "in.jsonl", [
        {"source_id": "a", "text": "피곤한 저녁에 익숙한 곳에서 먹었다."},
        {"source_id": "b", "text": "주말에 처음으로 방탈출에 갔다."},
    ])
    stats = ingest_mod.ingest_file(path, store, mock)
    assert stats["ok"] == 2 and stats["failed"] == 0
    assert stats["revision"] == 2
    again = ingest_mod.ingest_file(path, store, mock)
    assert again["duplicate"] == 2 and again["ok"] == 0


def test_ingest_file_failures(store, mock, tmp_path):
    raw = tmp_path / "bad.jsonl"
    raw.write_text('{"source_id": "a", "text": "피곤한 저녁에 익숙한 곳에서 먹었다."}\n'
                   'not json\n'
                   '{"source_id": "b", "text": "  "}\n', encoding="utf-8")
    stats = ingest_mod.ingest_file(str(raw), store, mock)
    assert stats["ok"] == 1 and stats["failed"] == 2
    assert len(stats["failures"]) == 2


def test_ingest_dry_run_writes_nothing(store, mock, tmp_path):
    path = _write(tmp_path / "in.jsonl", [
        {"source_id": "a", "text": "피곤한 저녁에 익숙한 곳에서 먹었다."},
    ])
    stats = ingest_mod.ingest_file(path, store, mock, dry_run=True)
    assert stats["ok"] == 1
    assert store.get_revision() == 0
    assert store.get_observation_by_source("a") is None


def test_score_scene_accounting():
    t1 = ("SUPPORTS", "Observation", "obs", "Hypothesis", "hyp:A")
    t2 = ("SUPPORTS", "Observation", "obs", "Hypothesis", "hyp:B")
    t3 = ("SUPPORTS", "Observation", "obs", "Hypothesis", "hyp:C")
    t2_wrong_rel = ("CONTRADICTS", "Observation", "obs", "Hypothesis", "hyp:B")
    t_ghost = ("SUPPORTS", "Observation", "obs", "Hypothesis", "hyp:Z")
    scene = ev.score_scene([t1, t2, t3], [t1, t2_wrong_rel, t_ghost])
    assert scene["exact"] == 1
    assert scene["partial"] == 1
    assert scene["missing"] == 1
    assert scene["spurious"] == 1
    assert scene["exact"] + scene["partial"] + scene["missing"] == scene["gold"]
    assert (scene["exact"] + scene["partial"] + scene["spurious"]
            == scene["predicted"])
    assert sum(scene["error_types"].values()) == scene["spurious"]


def test_score_file_totals_consistent(mock, tmp_path):
    path = _write(tmp_path / "gold.jsonl", [
        {"source_id": "g1", "text": "피곤한 저녁에 익숙한 곳에서 먹었다.",
         "triples": [
             {"rel": "OBSERVED_IN", "from_kind": "Context",
              "from_key": "ctx:피곤함", "to_kind": "Observation",
              "to_key": "obs"},
             {"rel": "SUPPORTS", "from_kind": "Observation",
              "from_key": "obs", "to_kind": "Hypothesis",
              "to_key": "hyp:피곤할 때는 고르는 수고를 회피한다"},
         ]},
        {"source_id": "g2", "text": "주말에 처음으로 방탈출에 갔다.",
         "triples": [
             {"rel": "OBSERVED_IN", "from_kind": "Entity",
              "from_key": "ent:방탈출", "to_kind": "Observation",
              "to_key": "obs"},
         ]},
    ])
    report = ev.score_file(path, mock)
    totals = report["totals"]
    assert totals["scenes"] == 2 and totals["extract_failed"] == 0
    assert totals["gold"] == 3
    assert totals["exact"] + totals["partial"] + totals["missing"] == 3


def test_scope_drops_unannotated_relations_and_merges_hypothesis_classes():
    classes = ev.load_classes()
    triples = [
        ("RELATED_TO", "Entity", "ent:a", "Context", "ctx:저녁"),
        ("DERIVED_FROM", "Criterion", "crit:x", "Hypothesis", "hyp:y"),
        ("SUPPORTS", "Observation", "obs", "Hypothesis",
         "hyp:익숙한 선택에서 안정감을 느낀다"),
    ]
    out = ev.to_scope(triples, classes)
    assert out == [("SUPPORTS", "Observation", "obs", "Hypothesis",
                    "hyp:#routine")]
    gold = ev.to_scope([("SUPPORTS", "Observation", "obs", "Hypothesis",
                         "hyp:익숙한 선택을 반복한다")], classes)
    assert gold == out


def test_mock_merges_adjacent_same_kind_entities():
    from mirror import extract as ex
    r = ex.MockExtractor().extract(ex.ObservationInput(
        source_id="t", text="방탈출 카페에 갔다.", occurred_at="unknown"))
    names = [n.attrs["name"] for n in r.nodes if n.kind == "Entity"]
    assert names == ["방탈출 카페"]
