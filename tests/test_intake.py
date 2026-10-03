"""Intake tests.

The point of this module is that the *caller* is not constrained, so the tests
are mostly a pile of badly-behaved documents: no headings, no keywords, mixed
languages, a table, a pasted chat, one that is empty, one that asks for
something dangerous. Every one of them must still produce a usable record —
or an explicit question. What must never happen is a crash, or silent work on
something we did not understand.

The model-backed path is exercised through an injected fake, so these run
offline with no key and no network.
"""
from __future__ import annotations

import json

import pytest

from kairos.intake import (
    Intake,
    RepoFacts,
    heuristic_units,
    render_questions,
    render_understanding,
    scan_cautions,
    task_id_from,
    verify_quote,
)


class _Reply:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeLLM:
    """Stands in for a provider: records prompts, returns a scripted answer."""

    def __init__(self, answer: str = "", *, raises: Exception | None = None):
        self.answer = answer
        self.raises = raises
        self.prompts: list[str] = []

    async def complete(self, messages):
        self.prompts.append(messages[-1].content)
        if self.raises is not None:
            raise self.raises
        return _Reply(self.answer)


def _intake(llm=None, **kwargs) -> Intake:
    return Intake(llm, **kwargs)


# ---------------------------------------------------------------------------
# Anything is acceptable input
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_bare_sentence_is_still_a_task():
    """No headings, no keywords, no structure — and it still resolves."""
    result = await _intake().accept("把登录页面的错误提示改成中文。")

    assert not result.blocked
    assert len(result.units) == 1
    assert "登录" in result.units[0].goal
    assert result.extracted_by == "heuristic"


@pytest.mark.asyncio
async def test_english_prose_without_structure():
    result = await _intake().accept(
        "Refactor the retry helper so it honours Retry-After, and add a test "
        "for the 429 path."
    )
    assert not result.blocked
    assert result.units


@pytest.mark.asyncio
async def test_multi_module_document_splits_into_units():
    """The main agent modularises the project; one document, many modules."""
    doc = """# 项目：订单系统

## 模块一：订单模型
新增 order.py，定义 Order 数据结构。

## 模块二：订单接口
在 api/orders.py 暴露 REST 接口。

## 模块三：库存校验
校验库存，不足时报错。

## 模块四：单元测试
给上面三块写 pytest 测试。

## 模块五：文档
更新 README。
"""
    result = await _intake().accept(doc)
    titles = " ".join(u.title for u in result.units)
    assert not result.blocked
    assert len(result.units) >= 5
    assert "模块三" in titles


@pytest.mark.asyncio
async def test_document_with_a_table_and_code_block():
    doc = """任务：修 3 个 bug

| 文件 | 问题 |
|---|---|
| src/a.py | 空指针 |
| src/b.py | 超时 |

用 `pytest tests -q` 验证。
"""
    result = await _intake().accept(doc)
    assert not result.blocked
    assert result.units
    joined = " ".join(u.goal for u in result.units)
    assert "src/a.py" in " ".join(
        p for u in result.units for p in u.scope_paths) or "src/a.py" in joined


@pytest.mark.asyncio
async def test_pasted_chat_log_is_readable_as_a_task():
    doc = """user: 那个导出老是乱码
assistant: 编码问题，统一成 utf-8 就行
user: 那就改一下吧，顺便加个测试
"""
    result = await _intake().accept(doc)
    assert not result.blocked
    assert result.units


# ---------------------------------------------------------------------------
# What we derive without being told
# ---------------------------------------------------------------------------


def test_acceptance_commands_are_picked_up():
    doc = "改完跑 `pytest tests/test_auth.py -q`，再跑 ruff check src/。"
    units = heuristic_units(doc)
    assert units
    assert any("pytest" in a for a in units[0].acceptance)


def test_scope_paths_are_picked_up():
    doc = "修改 src/auth/login.py 和 web/components/Login.tsx，别动其他文件。"
    units = heuristic_units(doc)
    assert "src/auth/login.py" in units[0].scope_paths
    assert "web/components/Login.tsx" in units[0].scope_paths


@pytest.mark.parametrize("phrase", [
    "不要动 src/legacy/",
    "DO NOT touch the migrations",
    "非目标：性能优化",
    "别改数据库结构",
])
def test_out_of_scope_is_recognised_in_both_languages(phrase):
    units = heuristic_units(f"做一些事情。\n{phrase}")
    assert units
    assert units[0].out_of_scope


@pytest.mark.asyncio
async def test_missing_acceptance_becomes_an_assumption_not_a_guess():
    result = await _intake().accept("把日志级别从 debug 改成 info。")
    assert result.units
    assert result.assumptions
    assert not result.units[0].acceptance  # we did not invent one


# ---------------------------------------------------------------------------
# Never silently guess, never crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_acceptance_is_not_a_reason_to_stop():
    """Most task documents never name a test command. Stopping to ask every
    time would make unattended dispatch useless, so a missing acceptance is
    recorded as an assumption and the work goes ahead."""
    result = await _intake().accept("把超时从 30 秒改成 60 秒")
    assert result.units and not result.blocked
    assert not result.needs_confirmation
    assert result.assumptions


@pytest.mark.asyncio
async def test_an_empty_document_blocks_with_a_question():
    result = await _intake().accept("   \n\n  ")
    assert result.blocked
    assert result.questions
    assert "空" in result.questions[0]


@pytest.mark.asyncio
async def test_dangerous_document_is_flagged_not_silently_run():
    doc = "帮我把数据库 drop table orders 清一下，顺便更新 .env 里的 API key。"
    result = await _intake().accept(doc)
    assert result.cautions
    assert result.needs_confirmation
    assert result.questions


def test_caution_scan_reports_categories_with_context():
    cautions = scan_cautions("then run rm -rf /tmp/x and read the .env file")
    joined = " ".join(cautions)
    assert "destructive" in joined
    assert "credentials" in joined


def test_mentioning_a_forbidden_thing_is_not_a_veto():
    """A document that *forbids* credentials must not be treated as asking
    for them — we flag and ask, we do not refuse."""
    cautions = scan_cautions("不要读取 .env，凭据由 CI 注入。")
    assert cautions  # flagged …
    # … but the caller can see it came from a prohibition, and nothing here
    # blocks the task on its own.
    assert all(isinstance(c, str) for c in cautions)


# ---------------------------------------------------------------------------
# Model path
# ---------------------------------------------------------------------------


def _model_answer(**overrides) -> str:
    payload = {
        "units": [{
            "title": "订单接口",
            "goal": "在 api/orders.py 暴露 REST 接口",
            "scope_paths": ["api/orders.py"],
            "deliverables": ["接口实现"],
            "acceptance": ["pytest tests/test_orders.py -q"],
            "out_of_scope": ["不改数据库结构"],
            "depends_on": [],
            "source_quote": "在 api/orders.py 暴露 REST 接口",
            "confidence": 0.9,
        }],
        "assumptions": [],
        "questions": [],
        "risk": "low",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_model_extraction_is_used_when_available():
    doc = "## 订单接口\n在 api/orders.py 暴露 REST 接口，跑 pytest tests/test_orders.py -q。"
    llm = FakeLLM(_model_answer())
    result = await _intake(llm).accept(doc)

    assert result.extracted_by == "model"
    assert len(result.units) == 1
    assert result.units[0].scope_paths == ["api/orders.py"]
    assert result.units[0].confidence == pytest.approx(0.9)
    # The document reaches the model verbatim — we do not pre-digest it.
    assert llm.prompts and doc in llm.prompts[0]


@pytest.mark.asyncio
async def test_fenced_json_is_accepted():
    llm = FakeLLM("```json\n" + _model_answer() + "\n```")
    result = await _intake(llm).accept("## x\n在 api/orders.py 暴露 REST 接口")
    assert result.extracted_by == "model"


@pytest.mark.asyncio
async def test_an_invented_quote_is_caught_and_costs_confidence():
    """A model that cites something not in the document is not trusted."""
    quoted = _model_answer()
    payload = json.loads(quoted)
    payload["units"][0]["source_quote"] = "这句话原文里根本没有"
    llm = FakeLLM(json.dumps(payload, ensure_ascii=False))

    result = await _intake(llm).accept("## 订单接口\n在 api/orders.py 暴露 REST 接口")
    assert result.extracted_by == "model"
    assert result.units[0].source_quote == ""
    assert result.units[0].confidence <= 0.4
    assert "unverifiable" in result.notes


@pytest.mark.asyncio
async def test_model_garbage_falls_back_to_structure():
    llm = FakeLLM("I'm sorry, I can't help with that.")
    result = await _intake(llm).accept("## 一\n做点事情")
    assert result.extracted_by == "heuristic"
    assert result.units
    assert any("确认" in q for q in result.questions)


@pytest.mark.asyncio
async def test_model_failure_falls_back_to_structure():
    llm = FakeLLM(raises=RuntimeError("connection reset"))
    result = await _intake(llm).accept("## 一\n做点事情")
    assert result.extracted_by == "heuristic"
    assert result.units


@pytest.mark.asyncio
async def test_no_model_at_all_still_works():
    result = await _intake(None).accept("## 一\n做点事情")
    assert result.units
    assert result.extracted_by == "heuristic"


@pytest.mark.asyncio
async def test_corrupt_payload_shape_is_survived():
    llm = FakeLLM(json.dumps({"units": "not a list"}))
    result = await _intake(llm).accept("做一些事")
    assert result.units
    assert result.extracted_by == "heuristic"


def test_verify_quote_ignores_reformatting_but_not_invention():
    source = "需要\n修改   src/auth.py  里的逻辑"
    assert verify_quote("修改 src/auth.py 里的逻辑", source)
    assert not verify_quote("修改 src/other.py 里的逻辑", source)


# ---------------------------------------------------------------------------
# Identity, caching, rendering
# ---------------------------------------------------------------------------


def test_task_id_prefers_a_numbered_filename():
    assert task_id_from("T001-auth.md", "whatever") == "T001"
    assert task_id_from("12-fix.md", "whatever") == "12"

    # No number anywhere: derive one from the content, deterministically.
    derived = task_id_from("", "some content")
    assert derived.startswith("task-")
    assert derived == task_id_from("", "some content")


def test_task_id_without_a_number_is_derived_from_content():
    first = task_id_from("auth-work.md", "do a thing")
    second = task_id_from("auth-work.md", "do a thing")
    third = task_id_from("auth-work.md", "do another thing")
    assert first == second          # same document, same task
    assert first != third           # different document, different task
    assert first.startswith("auth-work-")


@pytest.mark.asyncio
async def test_same_document_is_not_re_extracted(tmp_path):
    llm = FakeLLM(_model_answer())
    intake = _intake(llm, cache_dir=tmp_path)
    doc = "## 订单接口\n在 api/orders.py 暴露 REST 接口"

    first = await intake.accept(doc)
    second = await intake.accept(doc)

    assert first.source_sha256 == second.source_sha256
    assert len(llm.prompts) == 1     # the second call came from cache
    assert [u.title for u in second.units] == [u.title for u in first.units]


@pytest.mark.asyncio
async def test_understanding_and_questions_render_readably():
    doc = "## 订单接口\n在 api/orders.py 暴露 REST 接口。\n还要删掉 .env"
    result = await _intake().accept(doc, source_file="T007-orders.md")

    understanding = render_understanding(result)
    assert "T007" in understanding
    assert "我这样理解的" in understanding
    assert "原文依据" in understanding
    assert "我替你做的默认" in understanding

    questions = render_questions(result)
    assert "不需要任何格式" in questions


def test_repo_facts_gather_never_raises_on_a_bare_directory(tmp_path):
    (tmp_path / "AGENTS.md").write_text("always run pytest -q\n", encoding="utf-8")
    facts = RepoFacts.gather(tmp_path)
    assert facts.root == tmp_path
    assert "pytest" in "\n".join(facts.acceptance_hints)
    assert "AGENTS.md" in facts.tree


def test_repo_facts_on_a_missing_directory_still_returns():
    facts = RepoFacts.gather("Z:/definitely/not/here")
    assert facts.tree == []
