"""Pluggable text-embedding interface for the memory subsystem (P0-3, step 1).

Why this exists
---------------
``kairos/memory/semantic.py`` used to compute similarity *inline* with a
TF-IDF cosine ranker (see its own docstring: "NOT a neural embedding"). A
real, learned embedding could only be swapped in by editing that file. This
module is the seam the evaluation report asked for: a small, dependency-free
``EmbeddingProvider`` contract that both the existing offline ranker and a
future neural model can implement, so ``semantic.py`` can work *through* an
interface instead of hard-coding one vectorizer.

Honest boundary (read this before trusting a vector)
----------------------------------------------------
* There is **no neural model here and this module never loads one**. Nothing
  in this repository's dependencies can produce a learned embedding, and the
  project deliberately does not download weights or make network calls in
  this layer.
* The only provider shipped here is :class:`DeterministicEmbeddingProvider`,
  which is **not** a neural embedding and **not** a semantic embedding. It is
  a reproducible token-hashing vectorizer. It will not match a paraphrase, a
  translation, or a synonym the way a learned embedding would.
* ``from_settings()`` is the factory the rest of the code calls. It returns a
  *real* provider only when one has actually been registered/configured; with
  nothing configured it returns the deterministic fallback **and says so in
  the log**. It never pretends the fallback is a neural model.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from abc import ABC, abstractmethod
from typing import Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

#: Environment variable naming a registered provider to use. Absent / empty /
#: unknown -> the deterministic offline fallback (see :func:`from_settings`).
EMBEDDING_PROVIDER_ENV = "KAIROS_EMBEDDING_PROVIDER"

#: Fixed dimension of :class:`DeterministicEmbeddingProvider`'s vectors. A
#: power of two keeps the modulo cheap; 256 collisions are rare enough for the
#: short texts this layer sees while staying tiny in memory.
DETERMINISTIC_DIM = 256

_WORD_RE = re.compile(r"[a-z0-9_+#.\-]{2,}")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(text: str) -> List[str]:
    """Mixed English/CJK tokenizer shared by every offline provider.

    English: lowercase word-ish tokens (len >= 2).
    CJK: character bigrams per contiguous run (single char if the run is
    length 1). This is the exact tokenization ``semantic.py`` has always
    used, kept here so there is a single definition.
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


class EmbeddingProvider(ABC):
    """The contract a text embedder must satisfy.

    Attributes (implemented by each provider, typically as class attributes):

    * ``name`` -- a short, stable identifier used in logs and diagnostics so
      it is always visible *which* backend produced a vector.
    * ``dim`` -- the vector length every :meth:`embed` row has. Stable for a
      given provider.
    * ``is_neural`` -- ``True`` only for a genuinely learned/neural embedding.
      The offline fallback is ``False``; callers may branch on this and must
      not treat a ``False`` provider as semantic.

    ``embed`` maps a batch of texts to one vector each. It takes the whole
    batch (not one text) so a provider is free to use batch context; the
    result is ``list[list[float]]`` in the same order as the input, and the
    empty input yields ``[]``.
    """

    @property
    @abstractmethod
    def name(self) -> str:  # pragma: no cover - trivial
        raise NotImplementedError

    @property
    @abstractmethod
    def dim(self) -> int:  # pragma: no cover - trivial
        raise NotImplementedError

    @property
    @abstractmethod
    def is_neural(self) -> bool:  # pragma: no cover - trivial
        raise NotImplementedError

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        """Return one vector per text, in input order."""
        raise NotImplementedError

    @staticmethod
    def _hash_token(token: str) -> int:
        """Stable, process-independent hash of a token.

        Uses BLAKE2b (not Python's built-in ``hash``, whose seed changes every
        process) so the same text yields the same vector across runs and
        machines -- determinism is the whole point of the fallback.
        """
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big")


class DeterministicEmbeddingProvider(EmbeddingProvider):
    """An offline, zero-dependency, deterministic token-hashing vectorizer.

    THIS IS NOT A NEURAL EMBEDDING. It is not a semantic embedding either. It
    performs no learning, has no parameters, needs no model file, and makes no
    network call. It only hashes the raw tokens of the text into fixed-size
    buckets and L2-normalizes the result, so:

    * the SAME text always produces the SAME vector (reproducible);
    * DIFFERENT text generally produces a different vector;
    * it captures only *surface* token overlap -- it cannot match a paraphrase
      ("database timeout" vs "connection dropped") or a translation the way a
      learned embedding would.

    Its purpose is to be the *honest* stand-in that ``from_settings()``
    falls back to when no real embedding is configured, and to prove the
    provider seam works without shipping a model. A real neural provider is a
    future addition; it would set ``is_neural = True`` and this class would
    stay exactly as it is.
    """

    name = "deterministic-hash-blake2b"
    dim = DETERMINISTIC_DIM
    is_neural = False

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        out: List[List[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for token in tokenize(text or ""):
                h = self._hash_token(token)
                idx = h % self.dim
                # Signed hashing: collisions whose sign differs cancel instead
                # of adding up into a false-positive overlap.
                vec[idx] += 1.0 if (h >> 1) & 1 else -1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


# ---------------------------------------------------------------------------
# registry + factory
# ---------------------------------------------------------------------------

#: name -> factory returning an :class:`EmbeddingProvider`. Empty in this
#: repository (no neural embedding is bundled); a future round registers one
#: here (or the orchestrator does at startup) and ``from_settings`` picks it up.
_PROVIDER_FACTORIES: Dict[str, Callable[[], EmbeddingProvider]] = {}


def register_provider(
    name: str, factory: Callable[[], EmbeddingProvider]
) -> None:
    """Register a provider factory under ``name`` for :func:`from_settings`.

    Used by a future neural backend (and by tests) to make a real embedder
    discoverable without importing its heavy dependencies into this module.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("provider name must be a non-empty string")
    if not callable(factory):
        raise TypeError("factory must be callable")
    _PROVIDER_FACTORIES[name.strip().lower()] = factory


def registered_providers() -> List[str]:
    """Names currently registered for :func:`from_settings` (sorted)."""
    return sorted(_PROVIDER_FACTORIES)


def from_settings() -> EmbeddingProvider:
    """Return the embedding provider the settings/environment ask for.

    Resolution, in order:

    1. ``KAIROS_EMBEDDING_PROVIDER`` names a registered provider -> build it.
       Only a provider that reports ``is_neural=True`` is accepted as a "real"
       embedding; a registered factory that yields a non-neural provider is
       refused with a warning and the deterministic fallback is returned.
    2. No name, or an unknown/unbuildable name -> the deterministic offline
       fallback.

    In every branch the chosen backend is written to the log, so a caller (or
    an operator reading logs) is never misled about whether a *neural* model
    actually produced the vectors. This function never claims silently.
    """
    requested = (os.environ.get(EMBEDDING_PROVIDER_ENV) or "").strip().lower()
    if requested:
        factory = _PROVIDER_FACTORIES.get(requested)
        if factory is None:
            logger.warning(
                "embedding: %s=%r is not a registered provider (known: %s); "
                "using the deterministic offline fallback %r (is_neural=False)",
                EMBEDDING_PROVIDER_ENV, requested,
                registered_providers() or "none",
                DeterministicEmbeddingProvider.name,
            )
            return DeterministicEmbeddingProvider()
        try:
            provider = factory()
        except Exception:
            logger.warning(
                "embedding: provider %r failed to initialise; using the "
                "deterministic offline fallback %r (is_neural=False)",
                requested, DeterministicEmbeddingProvider.name, exc_info=True,
            )
            return DeterministicEmbeddingProvider()
        if provider is None or not getattr(provider, "is_neural", False):
            logger.warning(
                "embedding: provider %r is not neural (is_neural=False); "
                "using the deterministic offline fallback %r instead",
                requested, DeterministicEmbeddingProvider.name,
            )
            return DeterministicEmbeddingProvider()
        logger.info(
            "embedding: using configured provider %r (is_neural=True, dim=%s)",
            provider.name, getattr(provider, "dim", "?"),
        )
        return provider
    logger.info(
        "embedding: no provider configured (%s unset); using the deterministic "
        "offline fallback %r -- this is NOT a neural embedding",
        EMBEDDING_PROVIDER_ENV, DeterministicEmbeddingProvider.name,
    )
    return DeterministicEmbeddingProvider()


__all__ = [
    "EmbeddingProvider",
    "DeterministicEmbeddingProvider",
    "EMBEDDING_PROVIDER_ENV",
    "DETERMINISTIC_DIM",
    "tokenize",
    "register_provider",
    "registered_providers",
    "from_settings",
]
