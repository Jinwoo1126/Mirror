import pytest

from mirror import bm25 as bm25_mod
from mirror import schema
from mirror import search as search_mod
from mirror import vstore as vstore_mod


class FakeEmbedder:
    """결정적 가짜 임베딩 (모델 없이 의미 경로 테스트). 토큰 해시 버킷."""

    available = True

    def embed(self, texts):
        vecs = []
        for text in texts:
            vec = [0.0] * 64
            for tok in bm25_mod.tokenize_bm25(text):
                vec[sum(ord(c) for c in tok) % 64] += 1.0
            vecs.append(vec)
        return vecs


def _obs(store, source_id, text):
    obs = schema.Observation(id=schema.new_id("obs"), source_id=source_id,
                             text=text,
                             occurred_at="2026-10-16T20:00:00+09:00")
    store.add_observation(obs)
    return obs


def test_bm25_ranks_relevant_first():
    idx = bm25_mod.BM25()
    idx.add("d1", "피곤한 저녁에 익숙한 집에 주문했다")
    idx.add("d2", "개운한 아침에 새로운 메뉴를 시도했다")
    scores = idx.score("피곤한 저녁 주문")
    assert scores["d1"] > scores.get("d2", 0.0)
    idx.remove("d1")
    assert "d1" not in idx.score("저녁")


def test_bm25_save_load(tmp_path):
    idx = bm25_mod.BM25()
    idx.add("d1", "피곤한 저녁")
    path = str(tmp_path / "bm25.json")
    idx.save(path)
    assert bm25_mod.BM25.load(path).score("피곤")["d1"] > 0


def test_vstore_search(tmp_path):
    vs = vstore_mod.VectorStore(str(tmp_path / "v.json"))
    vs.upsert("a", [1.0, 0.0])
    vs.upsert("b", [0.0, 1.0])
    assert vs.search([1.0, 0.0], top_k=1)[0][0] == "a"
    with pytest.raises(ValueError):
        vs.upsert("c", [1.0])
    vs.save()
    assert len(vstore_mod.VectorStore(str(tmp_path / "v.json"))) == 2


def test_hybrid_search_prefers_semantic_match(store, tmp_path):
    _obs(store, "s1", "피곤한 저녁에 익숙한 집에 배달을 주문했다")
    _obs(store, "s2", "블로그에 DJ 선곡 과정을 정리했다")
    idx = search_mod.HybridIndex.build(
        store, vector_path=str(tmp_path / "v.json"),
        bm25_path=str(tmp_path / "b.json"), embedder=FakeEmbedder())
    assert idx.mode == "hybrid"
    hits = idx.search_obs("지친 밤 단골집 주문", store, top_k=2)
    assert hits and hits[0]["obs"].source_id == "s1"
    assert hits[0]["score"] > 0


def test_hybrid_bm25_only_without_embedder(store):
    _obs(store, "s1", "피곤한 저녁에 익숙한 집에 배달을 주문했다")

    class Dead:
        available = False

        def embed(self, texts):
            return None

    idx = search_mod.HybridIndex.build(store, embedder=Dead())
    assert idx.mode == "bm25"
    hits = idx.search_obs("피곤한 저녁", store)
    assert [h["obs"].source_id for h in hits] == ["s1"]
    assert idx.score("피곤 저녁", "피곤한 저녁에 주문") > 0.0


def test_refresh_tracks_add_and_delete(store, tmp_path):
    o1 = _obs(store, "s1", "피곤한 저녁")
    idx = search_mod.HybridIndex.build(
        store, vector_path=str(tmp_path / "v.json"),
        bm25_path=str(tmp_path / "b.json"), embedder=FakeEmbedder())
    assert idx.refresh(store) == 0
    _obs(store, "s2", "개운한 아침")
    assert idx.refresh(store) == 1
    store.delete_node("Observation", o1.id)
    idx.refresh(store)
    leftovers = [h for h in idx.search_obs("피곤", store, top_k=10)
                 if h["obs"].id == o1.id]
    assert leftovers == []
    kept = [h for h in idx.search_obs("개운한", store, top_k=10)]
    assert [h["obs"].source_id for h in kept] == ["s2"]
