"""로컬 임베딩 + 벡터 저장소 (의미 검색).

모델: 다국어 MiniLM ONNX (fastembed, 384차원). 모델이 없으면 임베더는
사용 불가로 보고 BM25 전용으로 동작한다 (search.py).
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def default_model_dir() -> str:
    if os.environ.get("MIRROR_MODEL_DIR"):
        return os.environ["MIRROR_MODEL_DIR"]
    return str(Path(__file__).resolve().parent.parent / "data" / "models")


class Embedder:
    """게으른 임베더. fastembed·모델이 없으면 available=False."""

    def __init__(self, model: str = MODEL_NAME, model_dir: str | None = None):
        self.model = model
        self.model_dir = model_dir or default_model_dir()
        self._backend = None
        self._failed = False

    @property
    def available(self) -> bool:
        if self._backend is not None:
            return True
        if self._failed:
            return False
        try:
            from fastembed import TextEmbedding

            self._backend = TextEmbedding(self.model,
                                         cache_dir=self.model_dir)
            return True
        except Exception:
            self._failed = True
            return False

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        if not self.available or not texts:
            return None
        assert self._backend is not None
        out = []
        for vec in self._backend.embed(texts):
            out.append([float(x) for x in vec])
        return out


_embedder: Embedder | None = None


def get_embedder(model_dir: str | None = None) -> Embedder:
    global _embedder
    if _embedder is None or model_dir is not None:
        _embedder = Embedder(model_dir=model_dir or default_model_dir())
    return _embedder


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class VectorStore:
    """1인용 소규모 JSON 벡터 저장소."""

    def __init__(self, path: str):
        self.path = path
        self.dim = 0
        self.items: dict[str, dict] = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.dim = data.get("dim", 0)
            self.items = data.get("items", {})

    def __len__(self) -> int:
        return len(self.items)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"dim": self.dim, "items": self.items}, f)

    def upsert(self, doc_id: str, vector: list[float],
               meta: dict | None = None) -> None:
        if self.dim == 0:
            self.dim = len(vector)
        if len(vector) != self.dim:
            raise ValueError(f"dim mismatch: {len(vector)} != {self.dim}")
        self.items[doc_id] = {"vec": vector, "meta": meta or {}}

    def remove(self, doc_id: str) -> None:
        self.items.pop(doc_id, None)

    def search(self, vector: list[float], top_k: int = 10,
               allow: set[str] | None = None) -> list[tuple[str, float]]:
        scored = []
        for doc_id, item in self.items.items():
            if allow is not None and doc_id not in allow:
                continue
            scored.append((doc_id, cosine(vector, item["vec"])))
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:top_k]
