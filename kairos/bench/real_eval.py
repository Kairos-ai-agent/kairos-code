"""Real harness evaluation using MiniMax API (R38.6.4).

Runs the 10 SWE-bench Lite tasks against a real LLM (MiniMax-Text-01)
and produces baseline numbers (pass_rate, mean_score, per-task scores).

Usage:
    python real_eval.py            # run all 10 tasks
    python real_eval.py --task off_by_one   # one task
    python real_eval.py --out baseline.json # save report
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

# Make harness_eval importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kairos.bench.harness_eval import (  # noqa: E402
    TASKS,
    HarnessReport,
    HarnessResult,
    print_report,
    run_task,
)

def _load_api_key() -> str:
    """API key from the environment, or from a file you point us at.

    Used to read a hardcoded C:\\Users\\<author>\\Desktop path, which made
    the benchmark unusable for anyone else (and leaked a personal path).
    """
    env = os.environ.get("MINIMAX_API_KEY")
    if env:
        return env.strip()
    key_file = os.environ.get("KAIROS_BENCH_KEY_FILE")
    if key_file:
        try:
            return Path(key_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SystemExit(f"could not read KAIROS_BENCH_KEY_FILE={key_file}: {exc}")
    raise SystemExit(
        "no API key: set MINIMAX_API_KEY, or KAIROS_BENCH_KEY_FILE=<path to a key file>"
    )


API_KEY = _load_api_key()
BASE_URL = os.environ.get(
    "MINIMAX_BASE_URL", "https://api.minimaxi.com/v1/chat/completions"
)
MODEL = os.environ.get("MINIMAX_MODEL", "MiniMax-Text-01")


def build_prompt(task, work_dir: Path) -> list[dict]:
    """Build the chat messages: system + user (task + repo files)."""
    file_listing = []
    for rel_path, content in task.repo_files.items():
        full = work_dir / rel_path
        if full.exists():
            try:
                actual = full.read_text(encoding="utf-8")
            except Exception:
                actual = content
        else:
            actual = content
        file_listing.append(f"### {rel_path}\n```python\n{actual}\n```")
    files_block = "\n\n".join(file_listing)

    system = (
        "You are a code-editing assistant. Read the task and the existing files, "
        "then output a unified diff (git/unified format) that fixes the task. "
        "Output ONLY the diff inside a single ```diff fenced code block. "
        "Do not include explanations, do not include prose. "
        "If the task says 'fix', 'add', 'rename', or 'implement', produce a diff "
        "that, when applied with `git apply`, would make the change. "
        "Use '--- a/<path>' and '+++ b/<path>' headers and unified hunk headers."
    )
    user = (
        f"# Task\n{task.prompt}\n\n"
        f"# Repository files\n{files_block}\n\n"
        f"# Output\n"
        f"Produce the unified diff in a single ```diff block."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# Per-process usage accumulator — read by the API layer to
# attach cost/token stats to leaderboard entries.
USAGE: dict[str, int] = {
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "calls": 0,
}


def reset_usage() -> None:
    USAGE["input_tokens"] = 0
    USAGE["output_tokens"] = 0
    USAGE["total_tokens"] = 0
    USAGE["calls"] = 0


def _record_usage(usage: dict | None) -> None:
    if not usage:
        return
    inp = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
    out = int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
    tot = int(usage.get("total_tokens", inp + out) or (inp + out))
    USAGE["input_tokens"] += inp
    USAGE["output_tokens"] += out
    USAGE["total_tokens"] += tot
    USAGE["calls"] += 1


# Approximate cost ($/1M tokens) per model. Used to estimate cost
# when real pricing data isn't available. Conservative defaults.
_COST_PER_1M = {
    "MiniMax-Text-01": (0.5, 2.0),
    "gpt-4o": (5.0, 15.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "claude-3-5-sonnet-latest": (3.0, 15.0),
    "claude-3-5-haiku-latest": (0.8, 4.0),
    "claude-opus-4-1": (15.0, 75.0),
}


def estimate_cost(model: str) -> float:
    """Estimate the cost (USD) of the current USAGE for ``model``."""
    in_rate, out_rate = _COST_PER_1M.get(model, (1.0, 3.0))
    return (
        USAGE["input_tokens"] * in_rate / 1_000_000
        + USAGE["output_tokens"] * out_rate / 1_000_000
    )


async def call_minimax(messages: list[dict], max_tokens: int = 2048) -> tuple[str, str, dict]:
    """Call MiniMax API. Returns (content, error, usage_dict)."""
    body = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "content-type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            r = await client.post(BASE_URL, json=body, headers=headers)
        if r.status_code != 200:
            return "", f"HTTP {r.status_code}: {r.text[:300]}", {}
        data = r.json()
        usage = data.get("usage", {})
        _record_usage(usage)
        if "choices" in data and data["choices"]:
            return data["choices"][0]["message"].get("content", ""), "", usage
        return "", f"no choices: {json.dumps(data)[:300]}", usage
    except Exception as e:
        return "", f"{type(e).__name__}: {e}", {}


def extract_diff(text: str) -> str:
    """Extract diff content from a ```diff fenced block, or return text as-is."""
    if "```diff" in text:
        start = text.index("```diff") + len("```diff")
        end = text.find("```", start)
        if end != -1:
            return text[start:end].strip()
    if "```" in text:
        # any fenced block
        start = text.index("```") + 3
        # skip optional language
        nl = text.find("\n", start)
        if nl != -1 and nl - start < 20:
            start = nl + 1
        end = text.find("```", start)
        if end != -1:
            return text[start:end].strip()
    return text.strip()


async def llm_harness(task, work_dir: Path) -> str:
    """harness_run callable: take task + work_dir, return predicted diff string."""
    messages = build_prompt(task, work_dir)
    content, err, _usage = await call_minimax(messages)
    if err:
        print(f"  [err {task.name}] {err}", file=sys.stderr)
        return ""
    diff = extract_diff(content)
    return diff


async def call_openai(messages: list[dict], model: str = "gpt-4o-mini",
                      base_url: str = "https://api.openai.com/v1",
                      api_key: str = "", max_tokens: int = 2048) -> tuple[str, str, dict]:
    """Call OpenAI chat completions (uses the openai SDK if available)."""
    import os as _os
    api_key = api_key or _os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return "", "OPENAI_API_KEY not set (set env or pass api_key)", {}
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        resp = await client.chat.completions.create(
            model=model, messages=messages,
            max_tokens=max_tokens, temperature=0.0,
        )
        usage = {
            "input_tokens": resp.usage.prompt_tokens if resp.usage else 0,
            "output_tokens": resp.usage.completion_tokens if resp.usage else 0,
            "total_tokens": resp.usage.total_tokens if resp.usage else 0,
        } if resp.usage else {}
        _record_usage(usage)
        return resp.choices[0].message.content or "", "", usage
    except Exception as e:
        return "", f"openai: {type(e).__name__}: {e}", {}


async def call_anthropic(messages: list[dict], model: str = "claude-3-5-sonnet-latest",
                         base_url: str = "https://api.anthropic.com",
                         api_key: str = "", max_tokens: int = 2048) -> tuple[str, str, dict]:
    """Call Anthropic messages API (uses the anthropic SDK if available)."""
    import os as _os
    api_key = api_key or _os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "", "ANTHROPIC_API_KEY not set (set env or pass api_key)", {}
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    user_msgs = [m for m in messages if m["role"] != "system"]
    try:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=api_key, base_url=base_url)
        resp = await client.messages.create(
            model=model, system=system, messages=user_msgs,
            max_tokens=max_tokens, temperature=0.0,
        )
        usage = {
            "input_tokens": resp.usage.input_tokens if resp.usage else 0,
            "output_tokens": resp.usage.output_tokens if resp.usage else 0,
        } if resp.usage else {}
        _record_usage(usage)
        text = "".join(b.text for b in resp.content if hasattr(b, "text"))
        return text, "", usage
    except Exception as e:
        return "", f"anthropic: {type(e).__name__}: {e}", {}


def llm_harness_for(provider: str, model: str = "", api_key: str = "",
                     base_url: str = ""):
    """Return an async llm_harness(task, work_dir) closure bound to a provider.

    Synchronous: returns a closure that is itself an async function, so
    callers can just ``await harness(task, work_dir)`` without needing
    to spin up a new event loop.
    """
    p = provider.lower().strip()

    async def _fn(task, work_dir: Path) -> str:
        messages = build_prompt(task, work_dir)
        if p in ("minimax", "minimaxi"):
            content, err, _u = await call_minimax(messages)
        elif p == "openai":
            content, err, _u = await call_openai(
                messages,
                model=model or "gpt-4o-mini",
                base_url=base_url or "https://api.openai.com/v1",
                api_key=api_key,
            )
        elif p == "anthropic":
            content, err, _u = await call_anthropic(
                messages,
                model=model or "claude-3-5-sonnet-latest",
                base_url=base_url or "https://api.anthropic.com",
                api_key=api_key,
            )
        else:
            raise ValueError(f"unknown provider: {provider}")
        if err:
            print(f"  [err {task.name}] {err}", file=sys.stderr)
            return ""
        return extract_diff(content)

    return _fn


async def run_eval(task_filter: str | None, out_path: str | None):
    selected = TASKS
    if task_filter:
        selected = [t for t in TASKS if t.name == task_filter]
        if not selected:
            raise SystemExit(f"unknown task: {task_filter}")

    print(f"Running real eval on {len(selected)} task(s) via {BASE_URL} ({MODEL})")
    started = time.time()
    results: list[HarnessResult] = []
    for task in selected:
        print(f"  -> {task.name} ...", end="", flush=True)
        r = await run_task(task, llm_harness)
        results.append(r)
        marker = "PASS" if r.score >= 0.7 else "FAIL"
        print(f" [{marker}] {r.score:.2f} ({r.duration_s:.1f}s)")

    report = HarnessReport(results=results, total_s=time.time() - started)
    print_report(report)

    if out_path:
        Path(out_path).write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"wrote {out_path}")
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--task", default="", help="run a single task by name")
    p.add_argument("--out", default="", help="output JSON path")
    args = p.parse_args()
    asyncio.run(run_eval(args.task or None, args.out or None))


if __name__ == "__main__":
    main()
