"""하이브리드 검색: BM25 + 벡터 코사인 + RRF 융합 (MiroFish 로컬식).

임베딩이 없으면 BM25 전용으로 동작한다. 관찰 텍스트가 원천이며,
인덱스는 저장소에서 재구축할 수 있는 파생물이다.
"""

from __future__ import annotations

from . import bm25 as bm25_mod
from . import schema
from . import vstore as vstore_mod
from .store import MirrorStore


def _rrf(ranks: list[list[str]], k: int = 60) -> dict[str, float]:
    fused: dict[str, float] = {}
    for ranking in ranks:
        for rank, doc_id in enumerate(ranking, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank)
    return fused


class HybridIndex:
    def __init__(self, bm25: bm25_mod.BM25 | None = None,
                 vstore: vstore_mod.VectorStore | None = None,
                 embedder: vstore_mod.Embedder | None = None,
                 vector_path: str = "", bm25_path: str = ""):
        self.bm25 = bm25 or bm25_mod.BM25()
        self.vstore = vstore
        self.embedder = embedder
        self.vector_path = vector_path
        self.bm25_path = bm25_path

    @property
    def mode(self) -> str:
        if (self.embedder is not None and self.embedder.available
                and self.vstore is not None):
            return "hybrid"
        return "bm25"

    @classmethod
    def build(cls, store: MirrorStore, vector_path: str = "",
              bm25_path: str = "", model_dir: str | None = None,
              embedder=None) -> "HybridIndex":
        idx = cls(bm25=bm25_mod.BM25.load(bm25_path) if bm25_path else None,
                  vstore=vstore_mod.VectorStore(vector_path)
                  if vector_path else None,
                  embedder=embedder if embedder is not None
                  else vstore_mod.get_embedder(model_dir),
                  vector_path=vector_path, bm25_path=bm25_path)
        idx.refresh(store)
        return idx

    def _all_obs(self, store: MirrorStore) -> list:
        rows = store._rows("MATCH (o:Observation) RETURN o.id")
        out = []
        for (oid,) in rows:
            obs = store.get_observation(oid)
            if obs is not None:
                out.append(obs)
        return out

    def refresh(self, store: MirrorStore, batch: int = 32) -> int:
        """새 관찰을 인덱스에 반영. 추가 건수 반환."""
        current = {o.id for o in self._all_obs(store)}
        known = set(self.bm25.docs)
        # 삭제된 관찰 정리
        for gone in known - current:
            self.bm25.remove(gone)
            if self.vstore is not None:
                self.vstore.remove(gone)
        fresh = [o for o in self._all_obs(store) if o.id not in known]
        for obs in fresh:
            self.bm25.add(obs.id, obs.text or "")
        if (self.vstore is not None and self.embedder is not None
                and self.embedder.available and fresh):
            for i in range(0, len(fresh), batch):
                chunk = fresh[i: i + batch]
                vecs = self.embedder.embed([o.text or "" for o in chunk])
                if vecs is None:
                    break
                for obs, vec in zip(chunk, vecs):
                    self.vstore.upsert(obs.id, vec,
                                       {"source_id": obs.source_id})
        if self.bm25_path:
            self.bm25.save(self.bm25_path)
        if self.vector_path and self.vstore is not None:
            self.vstore.save()
        return len(fresh)

    def search_obs(self, query: str, store: MirrorStore,
                   as_of: str | None = None, top_k: int = 10,
                   rrf_k: int = 60, min_vec: float = 0.35) -> list[dict]:
        """관찰 하이브리드 검색. [{obs, score, bm25, vec}] 반환.

        벡터 후보는 코사인 하한(min_vec) 미만이면 융합 전에 버린다.
        """
        as_of = as_of or schema.utcnow_iso()
        bm25_scores = self.bm25.score(query)
        bm25_rank = sorted(bm25_scores, key=lambda d: bm25_scores[d],
                           reverse=True)
        vec_scores: dict[str, float] = {}
        if self.mode == "hybrid":
            assert self.embedder is not None and self.vstore is not None
            vecs = self.embedder.embed([query])
            if vecs is not None:
                for doc_id, s in self.vstore.search(vecs[0],
                                                    top_k=max(top_k * 3, 20)):
                    if s >= min_vec:
                        vec_scores[doc_id] = s
        vec_rank = sorted(vec_scores, key=lambda d: vec_scores[d],
                          reverse=True)
        ranks = [r for r in (bm25_rank, vec_rank) if r]
        if not ranks:
            return []
        fused = _rrf(ranks, rrf_k)
        out = []
        for doc_id in sorted(fused, key=lambda d: fused[d], reverse=True):
            obs = store.get_observation(doc_id)
            if obs is None or obs.recorded_at > as_of:
                continue
            out.append({"obs": obs, "score": round(fused[doc_id], 5),
                        "bm25": round(bm25_scores.get(doc_id, 0.0), 4),
                        "vec": round(vec_scores.get(doc_id, 0.0), 4)})
            if len(out) >= top_k:
                break
        return out

    def score(self, query: str, corpus: str) -> float:
        """질의-본문 관련도. 임베딩 있으면 코사인, 없으면 토큰 겹침."""
        if self.embedder is not None and self.embedder.available:
            vecs = self.embedder.embed([query, corpus or " "])
            if vecs is not None:
                return (vstore_mod.cosine(vecs[0], vecs[1]) + 1.0) / 2.0
        qtokens = set(bm25_mod.tokenize_bm25(query))
        if not qtokens:
            return 0.0
        ctokens = set(bm25_mod.tokenize_bm25(corpus))
        return len(qtokens & ctokens) / len(qtokens)
