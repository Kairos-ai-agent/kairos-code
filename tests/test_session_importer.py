"""Tests for scripts.import_session_to_eval."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Make the scripts/ dir importable
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from import_session_to_eval import (  # noqa: E402
    _extract_first_def,
    _extract_needles,
    cases_from_session_json,
    write_yaml,
    _yaml_str,
    _dump_grader,
)


# ---------------------------------------------------------------------------
# _extract_needles
# ---------------------------------------------------------------------------


def test_extract_needles_returns_substrings():
    out = "def hello(): return 42"
    needles = _extract_needles(out, max_n=3)
    assert "def" not in needles  # too short
    assert "hello" in needles
    assert "return" in needles


def test_extract_needles_dedups():
    # Use 4+ char tokens so they pass the length filter
    out = "alpha beta alpha gamma alpha"
    needles = _extract_needles(out, max_n=10)
    # Each unique word at most once
    assert needles.count("alpha") == 1
    assert needles.count("beta") == 1
    assert needles.count("gamma") == 1


def test_extract_needles_skips_stopwords():
    out = "the and with for this"
    needles = _extract_needles(out)
    assert needles == []


def test_extract_needles_respects_max_n():
    out = "alpha beta gamma delta epsilon"
    needles = _extract_needles(out, max_n=3)
    assert len(needles) == 3


# ---------------------------------------------------------------------------
# _extract_def
# ---------------------------------------------------------------------------


def test_extract_def_function():
    pat = _extract_first_def("def my_function(x: int) -> int:\n    return x")
    assert pat  # non-empty
    # The pattern matches the function name as a word boundary
    import re
    assert re.search(pat, "def my_function(): pass")
    assert not re.search(pat, "def other_function(): pass")


def test_extract_def_class():
    pat = _extract_first_def("class FooBar:\n    pass")
    assert pat
    import re
    assert re.search(pat, "class FooBar:")


def test_extract_def_no_def_returns_empty():
    """The text "def or class here" actually has a "def or" that
    matches the pattern (a one-letter function name "or"). The
    function only refuses clearly non-code text."""
    # Truly non-code text — no def or class keyword at all
    assert _extract_first_def("hello world this is just prose") == ""


# ---------------------------------------------------------------------------
# cases_from_session_json
# ---------------------------------------------------------------------------


def test_cases_from_eval_suite_result_replay():
    data = {
        "suite_name": "demo",
        "cases": [
            {"name": "c1", "input": "p1", "output": "def hello(): return 42"},
            {"name": "c2", "input": "p2", "output": "x" * 20},
        ],
    }
    cases = cases_from_session_json(data, mode="replay")
    assert len(cases) == 2
    assert cases[0]["name"] == "c1"
    assert cases[0]["input"] == "p1"
    # A grader was created from the output
    assert cases[0]["graders"]


def test_cases_from_eval_suite_result_scaffold():
    data = {
        "suite_name": "demo",
        "cases": [
            {"name": "c1", "input": "p1", "output": "anything"},
        ],
    }
    cases = cases_from_session_json(data, mode="scaffold")
    assert cases[0]["graders"] == []


def test_cases_from_loop_session_history():
    data = {
        "session_id": "abc",
        "history": [
            {"round": 1, "coder": "def foo(): return 1"},
            {"round": 2, "coder": "def bar(): return 2"},
        ],
    }
    cases = cases_from_session_json(data, mode="replay")
    assert len(cases) == 2
    assert cases[0]["name"] == "round-1"
    assert cases[1]["name"] == "round-2"
    # Both have at least one grader (the def regex)
    for c in cases:
        assert c["graders"]


def test_cases_from_one_shot():
    data = {
        "requirement": "Write a function",
        "output": "def my_func(): return 1",
    }
    cases = cases_from_session_json(data, mode="replay")
    assert len(cases) == 1
    assert cases[0]["input"] == "Write a function"


def test_cases_from_unknown_format_raises():
    with pytest.raises(ValueError):
        cases_from_session_json({"foo": "bar"}, mode="replay")


# ---------------------------------------------------------------------------
# YAML serialization
# ---------------------------------------------------------------------------


def test_write_yaml_emits_valid_structure(tmp_path: Path):
    cases = [
        {"name": "c1", "input": "p1",
         "graders": [{"contains": ["hello"]}, {"regex": "x"}]},
    ]
    out = tmp_path / "x.yaml"
    write_yaml(cases, out, name="my-suite")
    text = out.read_text(encoding="utf-8")
    assert "name: my-suite" in text
    assert "name: c1" in text
    assert "input: p1" in text
    assert "contains: [hello]" in text
    assert "regex: x" in text


def test_yaml_str_quotes_strings_with_colons():
    assert _yaml_str("hello: world") == '"hello: world"'


def test_yaml_str_doesnt_quote_simple_strings():
    assert _yaml_str("hello") == "hello"


def test_dump_grader_list():
    out = _dump_grader({"contains": ["a", "b"]})
    assert "contains" in out
    assert "a" in out and "b" in out


# ---------------------------------------------------------------------------
# End-to-end CLI smoke
# ---------------------------------------------------------------------------


def test_cli_scaffold_mode(tmp_path: Path):
    session = tmp_path / "session.json"
    session.write_text(json.dumps({
        "requirement": "Write fizzbuzz",
        "output": "def fizzbuzz(n):\n    for i in range(1, n+1):\n        print(i)",
    }))
    out = tmp_path / "eval.yaml"
    r = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "import_session_to_eval.py"),
         "--session", str(session), "--out", str(out), "--mode", "scaffold"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "Write fizzbuzz" in text
    # scaffold mode → empty graders
    assert "graders: []" in text


def test_cli_replay_mode_includes_def_regex(tmp_path: Path):
    session = tmp_path / "session.json"
    session.write_text(json.dumps({
        "requirement": "Write a function",
        "output": "def my_special_function():\n    return 42",
    }))
    out = tmp_path / "eval.yaml"
    r = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "import_session_to_eval.py"),
         "--session", str(session), "--out", str(out), "--mode", "replay"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    text = out.read_text(encoding="utf-8")
    # The function name is pinned in a regex
    assert "my_special_function" in text
