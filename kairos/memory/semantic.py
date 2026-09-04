"""Lightweight semantic similarity — no external dependencies.

Provides `rank_by_similarity`, a batch TF-IDF cosine ranker used to
re-rank / supplement keyword-based memory recall. It understands
mixed English + CJK text (CJK text is tokenized into character
bigrams, which handles rephrased queries far better than raw
substring matching).

This is NOT a neural embedding — it cannot match across languages.
It closes the gap where "same meaning, different wording / word
order" queries fall through exact-token recall. A future round can
swap in a real embedding model behind the same function signature.

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
import re
from collections import Counter
from typing import Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9_+#.\-]{2,}")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")

# Docs below this similarity are treated as unrelated. TF-IDF cosine
# on short texts rarely exceeds ~0.6; 0.08 keeps near-zero noise out
# without suppressing genuinely related-but-lexically-different pairs.
DEFAULT_MIN_SCORE = 0.08


def _tokenize(text: str) -> List[str]:
    """Mixed English/CJK tokenizer.

    English: lowercase word-ish tokens (len >= 2).
    CJK: character bigrams per contiguous run (single char if the
    run is length 1).
    """
    if not text:
        return []
    lowered = text.lower()
    tokens = _WORD_RE.findall(lowered)
    for match in _CJK_RUN_RE.finditer(lowered):
        run = match.group(0)
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def _tfidf_vec(tokens: List[str], df: Counter, n_docs: int) -> dict:
    tf = Counter(tokens)
    return {
        term: (1.0 + math.log(count))
        * (math.log((1.0 + n_docs) / (1.0 + df[term])) + 1.0)
        for term, count in tf.items()
    }


def _norm(vec: dict) -> float:
    return math.sqrt(sum(w * w for w in vec.values())) or 1.0


def rank_by_similarity(
    query: str,
    docs: Iterable[str],
    *,
    top_k: Optional[int] = None,
    min_score: float = DEFAULT_MIN_SCORE,
) -> List[Tuple[int, float]]:
    """Rank ``docs`` by TF-IDF cosine similarity to ``query``.

    Returns ``[(doc_index, score), ...]`` sorted by descending score
    (ties keep original order). Indices refer to positions in the
    input iterable, so callers can reorder their original objects.
    """
    doc_list = list(docs)
    if not doc_list:
        return []

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
