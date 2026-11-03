"""의존성 없는 BM25 (한국어 토큰 + 문자 바이그램)."""

from __future__ import annotations

import json
import math
import os
from collections import Counter

from . import retrieve as retrieve_mod


def tokenize_bm25(text: str) -> list[str]:
    tokens = retrieve_mod.tokenize(text or "")
    grams = []
    for tok in tokens:
        if len(tok) >= 2:
            grams += [tok[i: i + 2] for i in range(len(tok) - 1)]
    return tokens + grams


class BM25:
    def __init__(self, k1: float = 1.2, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs: dict[str, Counter] = {}
        self.lengths: dict[str, int] = {}
        self.df: Counter = Counter()

    def __len__(self) -> int:
        return len(self.docs)

    def add(self, doc_id: str, text: str) -> None:
        self.remove(doc_id)
        terms = Counter(tokenize_bm25(text))
        self.docs[doc_id] = terms
        self.lengths[doc_id] = sum(terms.values())
        for term in terms:
            self.df[term] += 1

    def remove(self, doc_id: str) -> None:
        terms = self.docs.pop(doc_id, None)
        if terms is None:
            return
        self.lengths.pop(doc_id, None)
        for term in terms:
            self.df[term] -= 1
            if self.df[term] <= 0:
                del self.df[term]

    @property
    def avgdl(self) -> float:
        if not self.lengths:
            return 0.0
        return sum(self.lengths.values()) / len(self.lengths)

    def score(self, query: str) -> dict[str, float]:
        qterms = tokenize_bm25(query)
        n = len(self.docs)
        if n == 0 or not qterms:
            return {}
        avgdl = self.avgdl or 1.0
        out: dict[str, float] = {}
        for term in set(qterms):
            df = self.df.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for doc_id, terms in self.docs.items():
                tf = terms.get(term, 0)
                if tf == 0:
                    continue
                denom = tf + self.k1 * (
                    1 - self.b + self.b * self.lengths[doc_id] / avgdl)
                out[doc_id] = out.get(doc_id, 0.0) + idf * tf * (
                    self.k1 + 1) / denom
        return out

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"docs": {k: dict(v) for k, v in self.docs.items()},
                       "lengths": self.lengths, "df": dict(self.df)}, f)

    @classmethod
    def load(cls, path: str) -> "BM25":
        inst = cls()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            inst.docs = {k: Counter(v)
                         for k, v in data.get("docs", {}).items()}
            inst.lengths = data.get("lengths", {})
            inst.df = Counter(data.get("df", {}))
        return inst
