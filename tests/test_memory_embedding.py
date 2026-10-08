"""P0-3 step 1: the pluggable embedding seam.

Two properties must hold and are pinned here:

* ``DeterministicEmbeddingProvider`` is honest and deterministic -- same input,
  same fixed-dimension vector; it declares ``is_neural=False`` and its docstring
  says outright that it is *not* a neural embedding.
* ``kairos.memory.semantic.rank_by_similarity`` works **through** the seam:
  with nothing configured it keeps the historical deterministic TF-IDF
  behaviour, and when a *real* (neural) provider is configured/registered it
  actually uses ``provider.embed()``. No silent downgrade either way.

No model, no network, no new dependency.
"""
from __future__ import annotations

import logging

import pytest

from kairos.memory import embedding as emb
from kairos.memory.embedding import (
    DETERMINISTIC_DIM,
    DeterministicEmbeddingProvider,
    EmbeddingProvider,
    from_settings,
    register_provider,
    registered_providers,
    tokenize,
)
from kairos.memory.semantic import (
    embedding_backend,
    rank_by_embedding,
    rank_by_similarity,
)


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class _ConstantNeural(EmbeddingProvider):
    """A 'neural' provider that maps every text to the same direction.

    Crude on purpose: it makes the seam *observable* -- cosine is 1.0 for every
    doc, so the neural path returns every index while the TF-IDF path returns
    only the lexically matching one.
    """

    name = "constant-neural"
    dim = 2
    is_neural = True

    def __init__(self) -> None:
        self.calls: list = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]


class _ExplodingNeural(EmbeddingProvider):
    name = "exploding-neural"
    dim = 2
    is_neural = True

    def embed(self, texts):
        raise RuntimeError("no model weights available")


@pytest.fixture(autouse=True)
def _no_provider_env(monkeypatch):
    """Every test starts with no embedding provider configured."""
    monkeypatch.delenv(emb.EMBEDDING_PROVIDER_ENV, raising=False)


# ---------------------------------------------------------------------------
# DeterministicEmbeddingProvider: deterministic, offline, honest
# ---------------------------------------------------------------------------

def test_docstring_is_honest_that_it_is_not_neural():
    doc = (DeterministicEmbeddingProvider.__doc__ or "").upper()
    assert "NOT A NEURAL EMBEDDING" in doc
    # and it does not claim to be semantic either
    assert "NOT A SEMANTIC EMBEDDING" in doc


def test_deterministic_provider_is_flagged_not_neural():
    p = DeterministicEmbeddingProvider()
    assert p.is_neural is False
    assert isinstance(p.name, str) and p.name
    assert isinstance(p.dim, int) and p.dim == DETERMINISTIC_DIM


def test_same_input_twice_is_identical():
    p = DeterministicEmbeddingProvider()
    a = p.embed(["fix the database timeout", "更新项目说明文档"])
    b = p.embed(["fix the database timeout", "更新项目说明文档"])
    assert a == b


def test_different_input_gives_a_different_vector():
    p = DeterministicEmbeddingProvider()
    v1, v2 = p.embed(["database connection timeout", "the sky is blue"])
    assert v1 != v2


def test_dimension_is_stable_across_calls_and_texts():
    p = DeterministicEmbeddingProvider()
    rows = p.embed(["a short one", "a much longer piece of text with 汉字 混排"])
    assert [len(r) for r in rows] == [p.dim, p.dim]
    # and repeated calls keep the same dimension
    assert len(p.embed(["anything at all"])[0]) == p.dim


def test_vectors_are_l2_normalized():
    p = DeterministicEmbeddingProvider()
    (vec,) = p.embed(["database connection timeout"])
    norm = sum(v * v for v in vec) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-9)


def test_empty_batch_is_empty():
    assert DeterministicEmbeddingProvider().embed([]) == []


def test_tokenize_handles_english_and_cjk_bigrams():
    assert "database" in tokenize("Database connection")
    # CJK: character bigrams + single char run
    assert "数据" in tokenize("数据库")
    assert tokenize("") == []


# ---------------------------------------------------------------------------
# from_settings: returns the real one if configured, else the honest fallback
# ---------------------------------------------------------------------------

def test_from_settings_defaults_to_the_deterministic_fallback():
    p = from_settings()
    assert isinstance(p, DeterministicEmbeddingProvider)
    assert p.is_neural is False


def test_from_settings_falls_back_loudly_on_an_unknown_name(monkeypatch, caplog):
    monkeypatch.setenv(emb.EMBEDDING_PROVIDER_ENV, "does-not-exist")
    with caplog.at_level(logging.WARNING):
        p = from_settings()
    assert isinstance(p, DeterministicEmbeddingProvider) and p.is_neural is False
    assert any("not a registered provider" in r.message for r in caplog.records)


def test_from_settings_uses_a_registered_neural_provider(monkeypatch):
    sentinel = _ConstantNeural()
    monkeypatch.setitem(emb._PROVIDER_FACTORIES, "constant-neural", lambda: sentinel)
    monkeypatch.setenv(emb.EMBEDDING_PROVIDER_ENV, "constant-neural")
    p = from_settings()
    assert p is sentinel
    assert p.is_neural is True


def test_from_settings_refuses_a_registered_non_neural_provider(monkeypatch, caplog):
    monkeypatch.setitem(
        emb._PROVIDER_FACTORIES, "not-neural",
        lambda: DeterministicEmbeddingProvider(),
    )
    monkeypatch.setenv(emb.EMBEDDING_PROVIDER_ENV, "not-neural")
    with caplog.at_level(logging.WARNING):
        p = from_settings()
    assert isinstance(p, DeterministicEmbeddingProvider) and p.is_neural is False
    assert any("is not neural" in r.message for r in caplog.records)


def test_register_provider_validates_input():
    with pytest.raises(ValueError):
        register_provider("", lambda: DeterministicEmbeddingProvider())
    with pytest.raises(TypeError):
        register_provider("bad", "not callable")


# ---------------------------------------------------------------------------
# semantic.rank_by_similarity: unchanged offline behaviour + real seam
# ---------------------------------------------------------------------------

class TestRankBySimilarityFallback:
    """With nothing configured, behaviour == the historical TF-IDF ranker."""

    def test_backend_reports_the_deterministic_offline_ranker(self):
        info = embedding_backend()
        assert info["is_neural"] is False
        assert info["name"] == DeterministicEmbeddingProvider.name
        assert info["dim"] == DETERMINISTIC_DIM

    def test_related_doc_outranks_unrelated(self):
        docs = ["fix the database connection timeout", "update the readme title"]
        ranked = rank_by_similarity("database timeout error", docs)
        assert ranked and ranked[0][0] == 0

    def test_noise_is_filtered_by_min_score(self):
        assert rank_by_similarity("quantum flux capacitor", ["the sky is blue"]) == []

    def test_empty_query_and_docs(self):
        assert rank_by_similarity("x", []) == []
        assert rank_by_similarity("", ["doc"]) == []

    def test_ties_keep_original_order(self):
        ranked = rank_by_similarity("same", ["same text", "same text"])
        assert [i for i, _ in ranked] == [0, 1]

    def test_cjk_bigram_match(self):
        docs = ["数据库连接超时需要修复", "更新项目说明文档"]
        ranked = rank_by_similarity("数据库超时问题", docs)
        assert ranked and ranked[0][0] == 0


def test_rank_by_similarity_uses_the_configured_neural_provider(monkeypatch):
    """A real provider is actually consulted (the seam is not decorative)."""
    fake = _ConstantNeural()
    monkeypatch.setitem(emb._PROVIDER_FACTORIES, fake.name, lambda: fake)
    monkeypatch.setenv(emb.EMBEDDING_PROVIDER_ENV, fake.name)

    assert embedding_backend()["is_neural"] is True

    docs = ["beta doc", "alpha doc"]
    neural = rank_by_similarity("alpha", docs)
    # every doc is a positive-multiple of the constant direction -> all kept
    assert {i for i, _ in neural} == {0, 1}
    # ...and the provider's embed() was genuinely driven with [query] + docs
    assert fake.calls and fake.calls[-1] == ["alpha", "beta doc", "alpha doc"]

    # For contrast, the offline TF-IDF ranker keeps only the lexically close one.
    monkeypatch.delenv(emb.EMBEDDING_PROVIDER_ENV, raising=False)
    offline = rank_by_similarity("alpha", docs)
    assert [i for i, _ in offline] == [1]
    assert [i for i, _ in neural] != [i for i, _ in offline]


def test_rank_by_similarity_degrades_loudly_when_the_neural_backend_fails(monkeypatch, caplog):
    fake = _ExplodingNeural()
    monkeypatch.setitem(emb._PROVIDER_FACTORIES, fake.name, lambda: fake)
    monkeypatch.setenv(emb.EMBEDDING_PROVIDER_ENV, fake.name)

    with caplog.at_level(logging.WARNING):
        ranked = rank_by_similarity("alpha", ["beta doc", "alpha doc"])
    # fell back to the deterministic ranker, not a crash and not a silent blank
    assert [i for i, _ in ranked] == [1]
    assert any("failed; using the deterministic" in r.message for r in caplog.records)


def test_rank_by_embedding_goes_through_the_given_provider():
    fake = _ConstantNeural()
    ranked = rank_by_embedding("anything", ["a", "b"], provider=fake)
    assert {i for i, _ in ranked} == {0, 1}
    assert fake.calls and fake.calls[-1] == ["anything", "a", "b"]
