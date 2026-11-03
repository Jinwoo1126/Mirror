import json

import pytest

from mirror import replay as replay_mod


def _write(path, episodes):
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False)
                              for e in episodes) + "\n", encoding="utf-8")
    return str(path)


EPISODES = [
    {"episode_id": "e1", "occurred_at": "2026-10-16T20:00:00+09:00",
     "text": "피곤한 저녁. 익숙한 집과 새 식당 중 선택.",
     "options": ["익숙한 집", "새 식당"], "actual": "익숙한 집",
     "conditions": [{"kind": "상태", "value": "피곤함"}]},
    {"episode_id": "e2", "occurred_at": "2026-10-17T20:00:00+09:00",
     "text": "지친 밤. 단골집과 새 뷔페 중 선택.",
     "options": ["단골집", "새 뷔페"], "actual": "단골집",
     "conditions": [{"kind": "상태", "value": "피곤함"}]},
    {"episode_id": "e3", "occurred_at": "2026-10-18T20:00:00+09:00",
     "text": "개운한 저녁. 단골집과 새 다이닝 중 선택.",
     "options": ["단골집", "새 다이닝"], "actual": None,
     "conditions": [{"kind": "상태", "value": "개운함"}]},
]


def test_replay_counts_add_up(tmp_path):
    path = _write(tmp_path / "eps.jsonl", EPISODES)
    report = replay_mod.run_replay(path)
    assert report["n_episodes"] == 3
    assert report["disclosures"] == 2
    for name, stats in report["agents"].items():
        total = (stats["hits"] + stats["misses"] + stats["abstains"]
                 + stats["unobserved"] + stats["errors"])
        assert total == 3, name
        assert stats["unobserved"] == 1, name
        assert len(stats["episodes"]) == 3, name


def test_replay_deterministic(tmp_path):
    path = _write(tmp_path / "eps.jsonl", EPISODES)
    first = replay_mod.run_replay(path)
    second = replay_mod.run_replay(path)
    for name in first["agents"]:
        a, b = first["agents"][name], second["agents"][name]
        assert [(e.get("choice"), e.get("rationale")) for e in a["episodes"]] == \
               [(e.get("choice"), e.get("rationale")) for e in b["episodes"]]


def test_load_episodes_validates(tmp_path):
    bad = _write(tmp_path / "bad.jsonl", [
        {"episode_id": "x", "occurred_at": "t", "text": "t",
         "options": ["A"], "actual": "B"},
    ])
    with pytest.raises(ValueError):
        replay_mod.load_episodes(bad)
