"""
vector_store.py
===============
Simple VectorDB — lưu knowledge base cho RAG.

Storage: JSON file (documents) + numpy array (embeddings)
Search: cosine similarity với numpy

Documents lưu:
  - Trade logs (win/loss với context)
  - Market regime summaries
  - Error incidents
  - Strategy insights
  - Pattern observations
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Optional

import numpy as np

import config


@dataclass
class Document:
    doc_id:    str
    content:   str
    doc_type:  str   # "trade_log" | "regime" | "error" | "insight" | "pattern"
    metadata:  dict  = field(default_factory=dict)
    created_at:str   = field(default_factory=lambda: datetime.now().isoformat())
    embedding: list  = field(default_factory=list)  # stored as list for JSON


class VectorStore:
    """
    Lightweight vector store using numpy cosine similarity.

    Usage:
        store = VectorStore()
        store.add("Market showed strong uptrend at F618 fib level", doc_type="pattern")
        results = store.search("fibonacci golden zone uptrend", top_k=5)
    """

    STORE_FILE = config.VECTOR_STORE_FILE
    EMB_DIM    = 128  # Simple TF-IDF style embedding dimension

    def __init__(self) -> None:
        self._docs: list[Document] = []
        self._embeddings: Optional[np.ndarray] = None
        self._vocab: dict[str, int] = {}
        self.load()

    # ── Simple embedding (TF-IDF like, no external deps) ─────────

    def _build_vocab(self) -> None:
        """Build vocabulary from all documents."""
        words: set[str] = set()
        for doc in self._docs:
            words.update(self._tokenize(doc.content))
        self._vocab = {w: i for i, w in enumerate(sorted(words))}

    def _tokenize(self, text: str) -> list[str]:
        """Simple whitespace + lowercase tokenization."""
        import re
        tokens = re.findall(r'\b\w+\b', text.lower())
        return [t for t in tokens if len(t) > 2]  # filter short tokens

    def _embed(self, text: str) -> np.ndarray:
        """
        Create a simple bag-of-words embedding.
        For production, replace with a real embedding model.
        """
        tokens = self._tokenize(text)
        if not self._vocab or not tokens:
            return np.zeros(self.EMB_DIM, dtype=np.float32)

        # Bag of words → hash into fixed-dim vector
        vec = np.zeros(self.EMB_DIM, dtype=np.float32)
        for token in tokens:
            # Use hash to map to fixed dims (robust to new tokens)
            idx = hash(token) % self.EMB_DIM
            vec[idx] += 1.0

        # L2 normalize
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        a_n = np.linalg.norm(a)
        b_n = np.linalg.norm(b)
        if a_n == 0 or b_n == 0:
            return 0.0
        return float(np.dot(a, b) / (a_n * b_n))

    # ── CRUD ──────────────────────────────────────────────────────

    def add(
        self,
        content:  str,
        doc_type: str   = "insight",
        metadata: dict  = None,
        doc_id:   str   = None,
    ) -> str:
        """Add a document to the store. Returns doc_id."""
        if not content.strip():
            return ""

        did = doc_id or f"{doc_type}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        emb = self._embed(content)

        doc = Document(
            doc_id    = did,
            content   = content,
            doc_type  = doc_type,
            metadata  = metadata or {},
            embedding = emb.tolist(),
        )
        self._docs.append(doc)
        self._rebuild_embedding_matrix()
        self.save()
        return did

    def add_trade_log(self, record: dict) -> str:
        """Add a trade record as searchable knowledge."""
        won   = record.get("won", False)
        sym   = record.get("symbol", "")
        dir_  = record.get("direction", "")
        score = record.get("signal_score", 0)
        pnl   = record.get("pnl", 0)
        rsi   = record.get("rsi", 50)
        content = (
            f"Trade {'WIN' if won else 'LOSS'}: {sym} {dir_} "
            f"score={score:.0f} pnl={pnl:+.2f} rsi={rsi:.0f}"
        )
        return self.add(content, doc_type="trade_log", metadata=record)

    def search(
        self,
        query:   str,
        top_k:   int  = None,
        doc_type: str = None,
    ) -> list[tuple[Document, float]]:
        """
        Search for relevant documents.
        Returns list of (document, similarity_score) sorted by relevance.
        """
        top_k = top_k or config.LLM_RAG_TOP_K
        if not self._docs or self._embeddings is None:
            return []

        query_emb = self._embed(query)
        sims      = []
        for i, doc in enumerate(self._docs):
            if doc_type and doc.doc_type != doc_type:
                continue
            doc_emb = self._embeddings[i]
            sim     = self._cosine_similarity(query_emb, doc_emb)
            sims.append((doc, sim))

        sims.sort(key=lambda x: x[1], reverse=True)
        return sims[:top_k]

    def _rebuild_embedding_matrix(self) -> None:
        if not self._docs:
            self._embeddings = None
            return
        self._embeddings = np.array(
            [doc.embedding for doc in self._docs], dtype=np.float32
        )

    # ── Persistence ───────────────────────────────────────────────

    def save(self) -> None:
        # Only keep last 10000 documents to avoid unbounded growth
        if len(self._docs) > 10000:
            self._docs = self._docs[-10000:]
            self._rebuild_embedding_matrix()
        data = {"docs": [asdict(d) for d in self._docs]}
        with open(self.STORE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    def load(self) -> None:
        if not os.path.exists(self.STORE_FILE):
            return
        try:
            with open(self.STORE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            self._docs = []
            for d in data.get("docs", []):
                doc = Document(**{k: v for k, v in d.items() if k in Document.__dataclass_fields__})
                self._docs.append(doc)
            self._rebuild_embedding_matrix()
            print(f"[VectorStore] Loaded {len(self._docs)} documents")
        except Exception as exc:
            print(f"[VectorStore] Load failed: {exc}")

    def stats(self) -> dict:
        type_counts: dict[str, int] = {}
        for doc in self._docs:
            type_counts[doc.doc_type] = type_counts.get(doc.doc_type, 0) + 1
        return {"total": len(self._docs), "by_type": type_counts}


if __name__ == "__main__":
    store = VectorStore()
    store.add("Strong uptrend at F618 fibonacci zone with high momentum RSI=32 oversold bounce CALL", "pattern")
    store.add("Consecutive losses at R_50 PUT during evening session — avoid", "insight")
    store.add("Trade WIN R_100 CALL score=85 pnl=+8.5 rsi=28", "trade_log")

    results = store.search("fibonacci golden zone uptrend signal")
    for doc, score in results:
        print(f"  [{score:.3f}] {doc.content[:80]}")
    print(f"\nStats: {store.stats()}")
