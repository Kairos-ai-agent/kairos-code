"""A deterministic, no-network generator for the skeleton CLI and its tests.

A :class:`~kairos.skeleton.adapters.PromptWorker` needs *a* ``generate``
callable. In production that is a real model provider; for ``kairos skeleton
run --generator offline`` (and for hermetic tests) it is
:class:`OfflineGenerator`, which reads the ``INPUT: <ref>`` headers the worker
puts in the prompt and cites every one back, plus a self-check line.
"""
from __future__ import annotations

import re


class OfflineGenerator:
    """Stand-in for an LLM: cite every input back, then self-check.

    No provider, no API key, no network -- the same input always yields the
    same deliverable, which is exactly what a CLI end-to-end test needs.
    """

    def __call__(self, prompt: str) -> str:
        names = re.findall(r"INPUT: (\S+)", prompt or "")
        rows = [f"- 结论 {i + 1}，依据 [[{name}]]" for i, name in enumerate(names)]
        return "\n".join([
            "# 对比报告",
            "",
            "## 对比",
            *rows,
            "",
            "## 自检引用",
            f"SELF_CHECK: citations={len(names)}/{len(names)} ok",
        ])
