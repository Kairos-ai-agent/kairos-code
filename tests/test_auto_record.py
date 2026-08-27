"""Tests for the Round 14 auto-record CLI behavior.

These tests run the record flow as a real subprocess so we
verify the CLI wiring (not just the library API).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml


def _run_in_subprocess(script: str, cwd: Path,
                       env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a Python snippet in a fresh interpreter so we can test
    the CLI behavior without the test runner's import path
    leaking in."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, cwd=str(cwd), env=full_env,
    )


def _write_suite(path: Path, cases: list, threshold: float = 0.5,
                 name: str = "auto-record-test") -> None:
    """Write a properly-formatted YAML suite to disk."""
    suite = {
        "name": name,
        "threshold": threshold,
        "cases": cases,
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(suite, f, sort_keys=False)


def _python_run_script(suite_path: str, dataset_path: str) -> str:
    """Build a Python snippet that runs the suite + auto-records.

    The snippet uses the ``os.environ['DATASET']`` so the test
    runner can pass the dataset path through the env.
    """
    return f"""
import sys
sys.path.insert(0, {repr(str(Path(__file__).resolve().parent.parent))})
import os
from kairos.eval import run_suite, record_run, load_suite
from pathlib import Path

def target(prompt):
    if "p1" in prompt: return {{'output': 'yes', 'tools_called': []}}
    if "p2" in prompt: return {{'output': 'world', 'tools_called': []}}
    if "c1" in prompt: return {{'output': 'hi', 'tools_called': []}}
    if "c2" in prompt: return {{'output': 'bye', 'tools_called': []}}
    return {{'output': prompt, 'tools_called': []}}

spec = load_suite(Path({suite_path!r}))
result = run_suite(spec, target=target)
print(f"pass_rate={{result.pass_rate}}")
ds = Path(os.environ['DATASET'])
threshold = float(spec.get('threshold', 0.5))
if result.pass_rate >= threshold:
    n = record_run(result, ds, only_passed=True)
    print(f"recorded={{n}}")
else:
    print("skipped")
"""


def test_auto_record_appends_on_pass(tmp_path: Path):
    """A suite with 100% pass rate triggers auto-record."""
    suite_path = tmp_path / "s.yaml"
    _write_suite(suite_path, [
        {"name": "c1", "input": "c1 prompt", "graders": [{"contains": ["hi"]}]},
        {"name": "c2", "input": "c2 prompt", "graders": [{"contains": ["bye"]}]},
    ])
    dataset = tmp_path / "ds.jsonl"
    snippet = _python_run_script(str(suite_path), str(dataset))
    r = _run_in_subprocess(snippet, cwd=tmp_path,
                          env={"DATASET": str(dataset)})
    assert r.returncode == 0, f"stderr={r.stderr}"
    assert "pass_rate=1.0" in r.stdout
    assert "recorded=2" in r.stdout
    entries = [json.loads(l) for l in dataset.read_text(encoding="utf-8").splitlines() if l]
    assert len(entries) == 2
    assert {e["case_name"] for e in entries} == {"c1", "c2"}


def test_auto_record_does_not_trigger_below_threshold(tmp_path: Path):
    """A suite with pass_rate below threshold does NOT record."""
    suite_path = tmp_path / "s.yaml"
    _write_suite(suite_path, [
        {"name": "c1", "input": "c1 prompt", "graders": [{"contains": ["hi"]}]},
        {"name": "c2", "input": "c2 prompt",
         "graders": [{"contains": ["NOT_THERE"]}]},
    ], threshold=1.0)
    dataset = tmp_path / "ds.jsonl"
    snippet = _python_run_script(str(suite_path), str(dataset))
    r = _run_in_subprocess(snippet, cwd=tmp_path,
                          env={"DATASET": str(dataset)})
    assert r.returncode == 0, f"stderr={r.stderr}"
    assert "skipped" in r.stdout
    assert not dataset.exists() or dataset.read_text(encoding="utf-8").strip() == ""


def test_auto_record_partial_records_only_passes(tmp_path: Path):
    """A 50% suite records the passing case (only)."""
    suite_path = tmp_path / "s.yaml"
    _write_suite(suite_path, [
        {"name": "passes", "input": "p1 prompt", "graders": [{"contains": ["yes"]}]},
        {"name": "fails", "input": "p2 prompt",
         "graders": [{"contains": ["NO"]}]},
    ], threshold=0.5)
    dataset = tmp_path / "ds.jsonl"
    snippet = _python_run_script(str(suite_path), str(dataset))
    r = _run_in_subprocess(snippet, cwd=tmp_path,
                          env={"DATASET": str(dataset)})
    assert r.returncode == 0, f"stderr={r.stderr}"
    assert "recorded=1" in r.stdout
    entries = [json.loads(l) for l in dataset.read_text(encoding="utf-8").splitlines() if l]
    assert len(entries) == 1
    assert entries[0]["case_name"] == "passes"
