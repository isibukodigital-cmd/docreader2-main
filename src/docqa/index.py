"""Offline hybrid (keyword + embedding) search with permission filtering.

This in-memory implementation supports local evaluation and the CLI. The HTTP
service uses SupabaseVectorStore for persisted, tenant-scoped retrieval.
"""
from __future__ import annotations

import json
import re
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from .ingest import Chunk

_TOKEN = re.compile(r"[a-z0-9]+")
_NUM_COMMA = re.compile(r"(?<=\d),(?=\d)")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(_NUM_COMMA.sub("", text.lower()))


# ---------------------------------------------------------------- embedders
class SentenceTransformerEmbedder:
    """Local embedding model (runs on CPU or GPU). Downloads weights on first use."""

    QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", device: str | None = None):
        from sentence_transformers import SentenceTransformer

        self.name = model_name
        self.model = SentenceTransformer(model_name, device=device)

    def encode_docs(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.model.encode(texts, normalize_embeddings=True,
                                            batch_size=32, show_progress_bar=False))

    def encode_query(self, query: str) -> np.ndarray:
        return np.asarray(self.model.encode(self.QUERY_PREFIX + query, normalize_embeddings=True))


class HashEmbedder:
    """Dependency-free bag-of-words hashing embedder. For tests and offline smoke runs only."""

    def __init__(self, dim: int = 512):
        self.name = f"hash-{dim}"
        self.dim = dim

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for tok in tokenize(text):
            v[zlib.crc32(tok.encode()) % self.dim] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def encode_docs(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._vec(t) for t in texts])

    def encode_query(self, query: str) -> np.ndarray:
        return self._vec(query)


def read_meta(directory: str | Path) -> dict:
    """The meta.json written by HybridIndex.save (empty dict if there is none)."""
    path = Path(directory) / "meta.json"
    return json.loads(path.read_text()) if path.exists() else {}


def index_embedder_name(directory: str | Path) -> str | None:
    """Name of the embedder a saved index was built with (None = keyword-only index)."""
    return read_meta(directory).get("embedder")


# -------------------------------------------------------------------- index
@dataclass
class Hit:
    chunk: Chunk
    score: float
    bm25: float
    dense: float | None


class HybridIndex:
    def __init__(self, embedder=None, rrf_k: int = 60):
        self.embedder = embedder
        self.rrf_k = rrf_k
        self.chunks: list[Chunk] = []
        self.emb: np.ndarray | None = None
        self.bm25: BM25Okapi | None = None

    # -- building -------------------------------------------------------
    def build(self, chunks: list[Chunk]) -> "HybridIndex":
        self.chunks = list(chunks)
        texts = [c.index_text for c in self.chunks]
        self.bm25 = BM25Okapi([tokenize(t) for t in texts])
        self.emb = self.embedder.encode_docs(texts) if self.embedder else None
        return self

    # -- permissions ----------------------------------------------------
    def _visible(self, user_groups: list[str] | None) -> list[int]:
        """Return chunks visible to an explicitly resolved principal.

        An unauthenticated or unresolved caller has no private groups. Documents
        explicitly marked with the reserved "all" ACL remain public.
        """
        allowed = set(user_groups or [])
        return [
            i for i, c in enumerate(self.chunks)
            if "all" in c.acl or allowed & set(c.acl)
        ]

    # -- searching ------------------------------------------------------
    def search(self, query: str, k: int = 4, user_groups: list[str] | None = None,
               pool: int = 30, tenant_id: str | None = None) -> list[Hit]:
        """Return the top-k chunks the given user is allowed to see.

        Permission filtering happens BEFORE ranking, so restricted text can
        never reach the model or leak through scores.
        """
        del tenant_id
        if k < 1 or pool < 1:
            raise ValueError("k and pool must be positive")
        visible = self._visible(user_groups)
        if not visible:
            return []
        vis = np.asarray(visible)

        bm25_all = np.asarray(self.bm25.get_scores(tokenize(query)))
        bm25_vis = bm25_all[vis]
        dense_vis = None
        if self.embedder is not None and self.emb is not None:
            q = self.embedder.encode_query(query)
            if q.shape[-1] != self.emb.shape[1]:
                raise ValueError(
                    f"query embedding has {q.shape[-1]} dimensions but the index has "
                    f"{self.emb.shape[1]}: the index was built with a different embedder "
                    f"({getattr(self.embedder, 'name', '?')!r} is not the one it was built with)")
            dense_vis = self.emb[vis] @ q

        fused: dict[int, float] = {}
        order_bm25 = np.argsort(-bm25_vis)[:pool]
        for rank, j in enumerate(order_bm25):
            if bm25_vis[j] > 0:
                fused[j] = fused.get(j, 0.0) + 1.0 / (self.rrf_k + rank + 1)
        if dense_vis is not None:
            dense_order = [j for j in np.argsort(-dense_vis)[:pool] if dense_vis[j] > 0]
            for rank, j in enumerate(dense_order):
                fused[j] = fused.get(j, 0.0) + 1.0 / (self.rrf_k + rank + 1)

        top = sorted(fused.items(), key=lambda kv: -kv[1])[:k]
        return [
            Hit(chunk=self.chunks[visible[j]], score=float(s), bm25=float(bm25_vis[j]),
                dense=float(dense_vis[j]) if dense_vis is not None else None)
            for j, s in top
        ]

    # -- persistence ----------------------------------------------------
    def save(self, directory: str | Path) -> None:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        (d / "chunks.json").write_text(json.dumps([c.to_dict() for c in self.chunks]))
        if self.emb is not None:
            np.save(d / "embeddings.npy", self.emb)
        (d / "meta.json").write_text(json.dumps({
            "embedder": getattr(self.embedder, "name", None) if self.emb is not None else None,
            "dim": int(self.emb.shape[1]) if self.emb is not None else None,
            "rrf_k": self.rrf_k}))

    @classmethod
    def load(cls, directory: str | Path, embedder=None, bm25_only: bool = False) -> "HybridIndex":
        """Load a saved index.

        An index that holds embeddings must be loaded with the embedder it was built
        with; a different (or missing) one is an error instead of a crash at query
        time or a silent fall-back to keyword search. Pass bm25_only=True to
        deliberately ignore the embeddings.
        """
        d = Path(directory)
        meta = read_meta(d)
        emb_path = d / "embeddings.npy"
        has_emb = emb_path.exists() and not bm25_only
        stored = meta.get("embedder")
        if has_emb:
            if embedder is None:
                raise ValueError(f"this index was built with embedder {stored!r}: pass the same "
                                 f"embedder, or bm25_only=True for keyword-only search")
            given = getattr(embedder, "name", None)
            if stored is not None and given != stored:
                raise ValueError(f"this index was built with embedder {stored!r} but {given!r} "
                                 f"was given; rebuild the index or use the matching embedder")
        idx = cls(embedder=embedder if has_emb else None, rrf_k=meta.get("rrf_k", 60))
        idx.chunks = [Chunk(**c) for c in json.loads((d / "chunks.json").read_text())]
        idx.bm25 = BM25Okapi([tokenize(c.index_text) for c in idx.chunks])
        idx.emb = np.load(emb_path) if has_emb else None
        if idx.emb is not None and len(idx.emb) != len(idx.chunks):
            raise ValueError(f"index is inconsistent: {len(idx.chunks)} chunks but "
                             f"{len(idx.emb)} embeddings")
        return idx
