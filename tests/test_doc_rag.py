"""P0-4 document RAG: FTS5 content index + retrieval API + product wiring.

What is pinned here (and why):

* the file-content index is **FTS5** on this build and is created automatically
  -- the ``project_files`` table used to say "We don't index `content`" and this
  proves it now does;
* ``Persistence.search_files_content`` returns ranked hits that carry the
  source file, a matched fragment and a score, and an empty result is *always*
  explained (``status`` + ``note``), never a silent ``[]``;
* the **vector path is off unless a genuinely neural embedder is configured** --
  the deterministic offline fallback leaves it off and the result says so; a
  registered neural provider turns it on and is actually consulted
  (``rank_by_embedding`` / ``embedding_backend`` end up with real call sites);
* the two product paths that used to dump a whole workspace / upload order into
  the prompt now hand over *relevant excerpts* for a large input and the
  historical render for a small one.

No new dependency, no network, no model. All DBs live under ``tmp_path``.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from kairos.core.persistence import Persistence
from kairos.memory import doc_search as ds
from kairos.memory import embedding as emb
from kairos.memory.embedding import DeterministicEmbeddingProvider, EmbeddingProvider
from kairos.skeleton import DocSetWorkspace, OfflineGenerator, PromptWorker, Task


@pytest.fixture
def db(tmp_path):
    return Persistence(tmp_path / "rag.db")


@pytest.fixture(autouse=True)
def _no_provider_env(monkeypatch):
    monkeypatch.delenv(emb.EMBEDDING_PROVIDER_ENV, raising=False)


class _RecordingNeural(EmbeddingProvider):
    """A fake *neural* provider that records every batch it is asked to embed."""

    name = "recording-neural"
    dim = 4
    is_neural = True

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        # Distinct direction per token so cosine ordering is deterministic.
        out = []
        for t in texts:
            vec = [0.0, 0.0, 0.0, 0.0]
            for ch in (t or ""):
                vec[ord(ch) % self.dim] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / norm for v in vec])
        return out


# ---------------------------------------------------------------------------
# (1) FTS5 availability + index creation
# ---------------------------------------------------------------------------

def test_fts5_is_available_and_trigram_capable_on_this_build():
    assert ds.fts5_available() is True


def test_index_is_created_as_fts5_and_reports_its_kind(tmp_path):
    conn = __import__("sqlite3").connect(tmp_path / "x.db")
    kind = ds.ensure_index(conn)
    assert kind in ("fts5-trigram", "fts5-unicode61")
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = ?", (ds.FTS_TABLE,)
    ).fetchone()
    assert row is not None and "fts5" in row[0].lower()
    conn.close()


# ---------------------------------------------------------------------------
# (2) retrieval API: hits with source/fragment/score + explicit semantics
# ---------------------------------------------------------------------------

def test_search_returns_ranked_hits_with_source_and_fragment(db):
    db.add_file("f1", "p1", "设计.md", "text/markdown", 64,
                "数据库连接超时需要修复，延迟约 40ms")
    db.add_file("f2", "p1", "spec.txt", "text/plain", 32,
                "fix database connection timeout")

    res = db.search_files_content("数据库超时", 5, project_id="p1")
    assert res["status"] == "ok"
    assert res["index"].startswith("fts5")
    assert res["count"] == 1
    hit = res["hits"][0]
    assert hit["file"] == "设计.md"
    assert hit["file_id"] == "f1"
    assert hit["project_id"] == "p1"
    assert "数据库" in hit["snippet"]
    assert isinstance(hit["score"], float)


def test_empty_query_has_an_explicit_status(db):
    db.add_file("f1", "p1", "a.md", "text/markdown", 5, "hello world")
    res = db.search_files_content("   ", 5, project_id="p1")
    assert res["status"] == "empty-query"
    assert res["hits"] == []
    assert "empty" in res["note"].lower()


def test_empty_index_has_an_explicit_status(db):
    res = db.search_files_content("anything", 5, project_id="p1")
    assert res["status"] == "empty-index"
    assert res["hits"] == []
    assert "index" in res["note"].lower()


def test_no_match_is_explained_not_silent(db):
    db.add_file("f1", "p1", "a.md", "text/markdown", 5, "the sky is blue")
    res = db.search_files_content("quantum flux capacitor", 5, project_id="p1")
    assert res["status"] == "no-match"
    assert res["hits"] == []
    assert "no indexed document matched" in res["note"]


def test_two_char_cjk_term_is_not_dropped(db):
    """2-char Chinese terms cannot be a trigram; the scan fallback finds them."""
    db.add_file("f1", "p1", "alpha.md", "text/markdown", 24, "延迟：约 40ms")
    db.add_file("f2", "p1", "beta.md", "text/markdown", 40, "成本：每百万 token 0.3 元")
    res = db.search_files_content("延迟", 5, project_id="p1")
    assert res["status"] == "ok"
    assert [h["file"] for h in res["hits"]] == ["alpha.md"]


def test_search_scopes_to_one_project(db):
    db.add_file("f1", "p1", "a.md", "text/markdown", 20, "serverless deployment")
    db.add_file("f2", "p2", "b.md", "text/markdown", 20, "serverless deployment")
    scoped = db.search_files_content("serverless", 5, project_id="p1")
    assert [h["file_id"] for h in scoped["hits"]] == ["f1"]
    everything = db.search_files_content("serverless", 5)
    assert {h["file_id"] for h in everything["hits"]} == {"f1", "f2"}


# ---------------------------------------------------------------------------
# (3) incremental indexing, delete, rebuild
# ---------------------------------------------------------------------------

def test_add_file_indexes_incrementally(db):
    db.add_file("f1", "p1", "a.md", "text/markdown", 12, "alpha keyword here")
    assert db.search_files_content("keyword", 5, project_id="p1")["count"] == 1


def test_delete_file_unindexes(db):
    db.add_file("f1", "p1", "a.md", "text/markdown", 12, "alpha keyword here")
    assert db.delete_file("f1") is True
    res = db.search_files_content("keyword", 5, project_id="p1")
    assert res["hits"] == []
    assert res["status"] == "empty-index"   # the only document was un-indexed


def test_rebuild_reindexes_files_that_predate_the_index(db):
    """Simulate an old DB: rows exist, the index does not. First search heals it."""
    db.add_file("f1", "p1", "a.md", "text/markdown", 12, "legacy row content xyz")
    # Wipe the index the way a pre-RAG database would not have it.
    import sqlite3
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {ds.FTS_TABLE}")
        conn.execute(f"DROP TABLE IF EXISTS {ds.PLAIN_TABLE}")

    res = db.search_files_content("legacy", 5, project_id="p1")
    assert res["status"] == "ok"
    assert res["count"] == 1
    assert res.get("auto_indexed") == 1


def test_rebuild_file_index_returns_the_indexed_count(db):
    db.add_file("f1", "p1", "a.md", "text/markdown", 5, "one")
    db.add_file("f2", "p1", "b.md", "text/markdown", 5, "two")
    db.add_file("f3", "p2", "c.md", "text/markdown", 5, "three")
    assert db.rebuild_file_index() == 3
    assert db.rebuild_file_index(project_id="p1") == 2
    assert db.search_files_content("three", 5, project_id="p2")["count"] == 1


def test_delete_project_drops_its_index_rows(db):
    db.add_file("f1", "p1", "a.md", "text/markdown", 12, "unique marker zzz")
    db.delete_project("p1")
    res = db.search_files_content("zzz", 5)
    assert res["status"] == "empty-index"


# ---------------------------------------------------------------------------
# (4) vector path: off by default, on (and consulted) with a neural provider
# ---------------------------------------------------------------------------

def test_vector_path_is_off_and_explicit_with_the_offline_fallback(db):
    assert ds.vector_backend_status()["enabled"] is False
    db.add_file("f1", "p1", "a.md", "text/markdown", 12, "alpha keyword here")
    res = db.search_files_content("keyword", 5, project_id="p1")
    assert res["vector_enabled"] is False
    assert res["vector_rerank"] is False
    assert "not enabled" in res["note"]
    assert res["vector_backend"]["is_neural"] is False


def test_vector_rerank_is_used_when_a_neural_provider_is_configured(db, monkeypatch):
    fake = _RecordingNeural()
    monkeypatch.setitem(emb._PROVIDER_FACTORIES, fake.name, lambda: fake)
    monkeypatch.setenv(emb.EMBEDDING_PROVIDER_ENV, fake.name)

    assert ds.vector_backend_status()["enabled"] is True
    db.add_file("f1", "p1", "a.md", "text/markdown", 24, "database timeout error")
    db.add_file("f2", "p1", "b.md", "text/markdown", 24, "database timeout again")

    res = db.search_files_content("database timeout", 5, project_id="p1")
    assert res["vector_enabled"] is True
    assert res["vector_rerank"] is True
    assert res["vector_backend"]["name"] == fake.name
    # rank_by_embedding really drove provider.embed(): [query] + one doc/text.
    assert fake.calls, "the neural provider was never consulted"


# ---------------------------------------------------------------------------
# (5) product wiring: large workspace -> relevant excerpts, small unchanged
# ---------------------------------------------------------------------------

def _make_docs(root: Path) -> DocSetWorkspace:
    docs = root / "docs"
    docs.mkdir()
    (docs / "alpha.md").write_text("- 延迟：约 40ms\n", encoding="utf-8")
    (docs / "beta.md").write_text("- 成本：每百万 token 0.3 元\n", encoding="utf-8")
    return DocSetWorkspace(root)


def test_small_workspace_prompt_context_is_unchanged(tmp_path):
    ws = _make_docs(tmp_path)
    ctx = ws.as_prompt_context(query="延迟")
    # small workspace -> the historical "INPUT: <name>" inline, every body present
    assert "INPUT: alpha.md" in ctx
    assert "INPUT: beta.md" in ctx
    assert "40ms" in ctx
    assert "0.3 元" in ctx
    assert "RELEVANT EXCERPTS" not in ctx


def test_large_workspace_prompt_context_prefers_relevant_excerpts(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "alpha.md").write_text("噪声。" * 400 + "\n延迟约 40ms", encoding="utf-8")
    (docs / "beta.md").write_text("无关填充。" * 400, encoding="utf-8")
    (docs / "gamma.md").write_text("部署说明：无需服务器。" * 400, encoding="utf-8")
    ws = DocSetWorkspace(tmp_path)

    ctx = ws.as_prompt_context(max_chars=300, query="部署是否需要服务器")
    assert "RELEVANT EXCERPTS" in ctx
    assert "### gamma.md" in ctx            # the source file is named
    assert "服务器" in ctx                  # and a matching fragment is shown
    # The whole (1200+ char) workspace was NOT dumped; the ceiling was respected.
    assert len(ctx) <= 300 + len("RELEVANT EXCERPTS") + 400
    assert "INPUT: beta.md" not in ctx


async def test_prompt_worker_passes_the_task_as_the_retrieval_query(tmp_path):
    """The product worker steers a large workspace by the task text."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "big.md").write_text("x" * 5000 + "关键指标：成本 0.3 元", encoding="utf-8")
    (docs / "noise.md").write_text("y" * 5000, encoding="utf-8")
    ws = DocSetWorkspace(tmp_path)

    prompts = []
    worker = PromptWorker(generate=lambda p: prompts.append(p) or "ok",
                          max_context_chars=600)
    await worker.run(ws, Task(instruction="成本是多少", output_name="report.md"))

    prompt = prompts[0]
    assert "RELEVANT EXCERPTS" in prompt
    assert "### big.md" in prompt
    assert "0.3 元" in prompt


def test_render_relevant_excerpts_names_what_the_cap_dropped():
    docs = [
        {"file": "a.md", "body": "alpha topic here"},
        {"file": "b.md", "body": "beta topic here"},
        {"file": "c.md", "body": "gamma topic here"},
    ]
    text, meta = ds.render_relevant_excerpts("alpha beta gamma", docs, cap=60)
    assert text is not None
    assert "[TRUNCATED]" in text
    assert meta["hits"] == 3


def test_render_relevant_excerpts_returns_none_when_nothing_matches():
    docs = [{"file": "a.md", "body": "the sky is blue"}]
    text, meta = ds.render_relevant_excerpts("quantum flux capacitor", docs, cap=100)
    assert text is None            # caller keeps its own render
    assert meta["hits"] == 0


# ---------------------------------------------------------------------------
# (6) product wiring: reference digest ranks by relevance for many files
# ---------------------------------------------------------------------------

def _orch_with_db(db):
    from kairos.core.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch._db = db
    orch._projects = {}
    return orch


def test_reference_digest_unchanged_without_a_query(db):
    """No query -> the historical newest-first inline order, verbatim."""
    orch = _orch_with_db(db)
    db.add_file("f1", "p1", "small.md", "text/markdown", 30, "Tiny but complete body.")
    digest = orch.build_reference_digest("p1")
    assert "## 参考资料" in digest
    assert "Tiny but complete body." in digest
    assert "ranked by relevance" not in digest


def test_reference_digest_ranks_many_files_by_relevance(db):
    orch = _orch_with_db(db)
    # 10 files, only one is about "数据库"; without ranking it could be cut.
    for i in range(9):
        db.add_file(f"n{i}", "p1", f"noise{i}.md", "text/markdown", 40,
                    f"unrelated filler number {i}")
    db.add_file("target", "p1", "db.md", "text/markdown", 40,
                "数据库连接超时需要修复")
    digest = orch.build_reference_digest("p1", query="数据库超时")
    assert "ranked by relevance" in digest
    assert "db.md" in digest
    # db.md (the relevant one) is the first file listed.
    headings = [ln for ln in digest.splitlines() if ln.startswith("### ")]
    assert headings[0] == "### db.md"


def test_reference_digest_survives_a_failing_search(db):
    """A retrieval failure must degrade to the old order, never break the loop."""
    orch = _orch_with_db(db)

    def _boom(*_a, **_k):
        raise RuntimeError("index unavailable")

    orch._db = db
    db.search_files_content = _boom  # type: ignore[assignment]
    for i in range(9):
        db.add_file(f"n{i}", "p1", f"noise{i}.md", "text/markdown", 40, f"filler {i}")
    digest = orch.build_reference_digest("p1", query="anything")
    assert "## 参考资料" in digest
    assert "noise0.md" in digest or "noise8.md" in digest


# ---------------------------------------------------------------------------
# (7) CLI rebuild entry point
# ---------------------------------------------------------------------------

def test_cli_index_rebuilds_and_reports(tmp_path, monkeypatch):
    from kairos.cli import EXIT_OK, main
    from kairos.config.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    db = Persistence(tmp_path / "kairos.db")
    db.add_file("f1", "p1", "a.md", "text/markdown", 12, "hello keyword world")

    code = main(["index", "--json"])
    assert code == EXIT_OK
    # Re-open and confirm the content is searchable.
    db2 = Persistence(tmp_path / "kairos.db")
    assert db2.search_files_content("keyword", 5, project_id="p1")["count"] == 1


def test_cli_index_parses():
    from kairos.cli import build_parser
    args = build_parser().parse_args(["index", "--project", "abc", "--json"])
    assert args.command == "index"
    assert args.project == "abc"
    assert args.json_output is True
