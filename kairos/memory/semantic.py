"""Lightweight semantic similarity — no external dependencies.

Provides `rank_by_similarity`, a batch ranker used to re-rank / supplement
keyword-based memory recall. It understands mixed English + CJK text (CJK
text is tokenized into character bigrams, which handles rephrased queries
far better than raw substring matching).

Vectorisation now goes **through a pluggable provider** (`kairos.memory.
embedding`): `rank_by_similarity` asks `embedding.from_settings()` for an
`EmbeddingProvider`. When a *real* (neural) embedding is configured it uses
`provider.embed(...)`; with nothing configured it keeps the deterministic
TF-IDF cosine ranker below, so behaviour is byte-for-byte what it has always
been.

This default path is NOT a neural embedding — it cannot match across
languages. It closes the gap where "same meaning, different wording / word
order" queries fall through exact-token recall. When no neural provider is
available, the deterministic offline ranker is used deliberately (no silent
downgrade): `embedding_backend()` reports which backend is active.

Usage:
    from kairos.memory.semantic import rank_by_similarity

    ranked = rank_by_similarity("fix db timeout", [
        "database connection timed out",
        "update the readme",
    ])  # -> [(0, 0.31), (1, 0.0)]
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

from kairos.memory.embedding import (
    DeterministicEmbeddingProvider,
    EmbeddingProvider,
    from_settings,
)

logger = logging.getLogger(__name__)

# Docs below this similarity are treated as unrelated. TF-IDF cosine
# on short texts rarely exceeds ~0.6; 0.08 keeps near-zero noise out
# without suppressing genuinely related-but-lexically-different pairs.
DEFAULT_MIN_SCORE = 0.08


def _tokenize(text: str) -> List[str]:
    """Mixed English/CJK tokenizer (shared definition lives in ``embedding``).

    Kept as a thin local alias so the TF-IDF path and the provider path can
    never drift apart on how text is split.
    """
    from kairos.memory.embedding import tokenize

    return tokenize(text)


def _tfidf_vec(tokens: List[str], df: Counter, n_docs: int) -> dict:
    tf = Counter(tokens)
    return {
        term: (1.0 + math.log(count))
        * (math.log((1.0 + n_docs) / (1.0 + df[term])) + 1.0)
        for term, count in tf.items()
    }


def _norm(vec: dict) -> float:
    return math.sqrt(sum(w * w for w in vec.values())) or 1.0


# ---------------------------------------------------------------------------
# provider seam: which embedder does this module work through right now?
# ---------------------------------------------------------------------------


def _resolve_provider() -> Optional[EmbeddingProvider]:
    """The provider settings say to use, or ``None`` if resolution failed.

    A ``None`` return means "fall back to the deterministic TF-IDF ranker";
    it is not an error the caller has to handle.
    """
    try:
        return from_settings()
    except Exception:
        logger.warning(
            "semantic: embedding provider resolution failed; using the "
            "deterministic TF-IDF ranker (offline, not neural)",
            exc_info=True,
        )
        return None


def embedding_backend() -> Dict[str, Any]:
    """Report which backend ``rank_by_similarity`` is currently using.

    ``is_neural`` is ``False`` for the deterministic offline ranker -- callers
    and diagnostics can trust this instead of assuming a learned model exists.
    """
    provider = _resolve_provider() or DeterministicEmbeddingProvider()
    return {
        "name": provider.name,
        "is_neural": bool(provider.is_neural),
        "dim": provider.dim,
    }


# ---------------------------------------------------------------------------
# the two ranking paths
# ---------------------------------------------------------------------------


def _rank_by_tfidf(
    query: str,
    doc_list: List[str],
    *,
    top_k: Optional[int],
    min_score: float,
) -> List[Tuple[int, float]]:
    """Rank by TF-IDF cosine -- the deterministic offline ranker (unchanged)."""
    q_tokens = _tokenize(query)
    doc_tokens = [_tokenize(d) for d in doc_list]

    # Document frequency over the batch (query included) so rare
    # shared terms weigh more than common ones.
    n_docs = len(doc_tokens) + 1
    df: Counter = Counter()
    if q_tokens:
        df.update(set(q_tokens))
    for toks in doc_tokens:
        if toks:
            df.update(set(toks))

    q_vec = _tfidf_vec(q_tokens, df, n_docs) if q_tokens else {}
    q_norm = _norm(q_vec)

    scored: List[Tuple[int, float]] = []
    for idx, toks in enumerate(doc_tokens):
        d_vec = _tfidf_vec(toks, df, n_docs) if toks else {}
        if not q_vec or not d_vec:
            scored.append((idx, 0.0))
            continue
        dot = sum(w * d_vec.get(term, 0.0) for term, w in q_vec.items())
        score = dot / (q_norm * _norm(d_vec))
        scored.append((idx, score))

    scored.sort(key=lambda item: (-item[1], item[0]))
    if top_k is not None:
        scored = scored[:top_k]
    return [(idx, score) for idx, score in scored if score >= min_score]


def _rank_by_embeddings(
    provider: EmbeddingProvider,
    query: str,
    doc_list: List[str],
    *,
    top_k: Optional[int],
    min_score: float,
) -> List[Tuple[int, float]]:
    """Rank by cosine over ``provider.embed()`` vectors (neural backends)."""
    vectors = provider.embed([query or ""] + list(doc_list))
    if not vectors:
        return []
    q_vec = vectors[0]
    q_norm = math.sqrt(sum(v * v for v in q_vec)) or 1.0

    scored: List[Tuple[int, float]] = []
    for idx, d_vec in enumerate(vectors[1:]):
        d_norm = math.sqrt(sum(v * v for v in d_vec)) or 1.0
        dot = sum(a * b for a, b in zip(q_vec, d_vec))
        scored.append((idx, dot / (q_norm * d_norm)))

    scored.sort(key=lambda item: (-item[1], item[0]))
    if top_k is not None:
        scored = scored[:top_k]
    return [(idx, score) for idx, score in scored if score >= min_score]


def rank_by_embedding(
    query: str,
    docs: Iterable[str],
    *,
    provider: Optional[EmbeddingProvider] = None,
    top_k: Optional[int] = None,
    min_score: float = DEFAULT_MIN_SCORE,
) -> List[Tuple[int, float]]:
    """Rank ``docs`` by cosine over an explicit ``EmbeddingProvider``.

    Always goes through ``provider.embed()`` (default: the one settings ask
    for, i.e. the same object :func:`rank_by_similarity` consults). Same return
    shape as :func:`rank_by_similarity`.
    """
    doc_list = list(docs)
    if not doc_list:
        return []
    prov = provider or _resolve_provider() or DeterministicEmbeddingProvider()
    return _rank_by_embeddings(
        prov, query, doc_list, top_k=top_k, min_score=min_score
    )


def rank_by_similarity(
    query: str,
    docs: Iterable[str],
    *,
    top_k: Optional[int] = None,
    min_score: float = DEFAULT_MIN_SCORE,
) -> List[Tuple[int, float]]:
    """Rank ``docs`` by similarity to ``query``.

    Returns ``[(doc_index, score), ...]`` sorted by descending score
    (ties keep original order). Indices refer to positions in the
    input iterable, so callers can reorder their original objects.

    When a real (neural) embedding is configured, similarity is the cosine
    over that provider's vectors. Otherwise the deterministic offline TF-IDF
    cosine is used -- behaviourally identical to before this seam existed.
    """
    doc_list = list(docs)
    if not doc_list:
        return []

    provider = _resolve_provider()
    if provider is not None and provider.is_neural:
        try:
            return _rank_by_embeddings(
                provider, query, doc_list, top_k=top_k, min_score=min_score
            )
        except Exception:
            # Announced, not silent: a failing neural backend degrades to the
            # deterministic ranker rather than dropping recall.
            logger.warning(
                "semantic: neural embedding backend %r failed; using the "
                "deterministic TF-IDF ranker", provider.name, exc_info=True,
            )
    return _rank_by_tfidf(query, doc_list, top_k=top_k, min_score=min_score)


__all__ = [
    "rank_by_similarity",
    "rank_by_embedding",
    "embedding_backend",
    "DEFAULT_MIN_SCORE",
]
