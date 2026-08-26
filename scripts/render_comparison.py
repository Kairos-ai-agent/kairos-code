"""Render a one-page comparison of Kairos Code vs Codex Harness vs Claude Code.

Outputs ``docs/kairos-vs-codex-vs-claude.png`` — a single image
that summarizes the three products across 16 feature dimensions.
Color coding:
  - green   = real, working capability
  - yellow  = partial / limited
  - red     = absent
  - blue    = unique selling point
"""
from __future__ import annotations

import os
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch

# ---------------------------------------------------------------------------
# Comparison table
# ---------------------------------------------------------------------------
# Each row: (feature, kairos, codex, claude_code, weight)
# weight: "high" | "med" | "low"
# values:
#   "yes"        — real, working
#   "partial"    — limited
#   "no"         — absent
#   "unique"     — unique to kairos
#   "best"       — kairos is best
ROWS = [
    # category header (rendered as a band)
    ("__HDR__", "Multi-agent",   "",   "",   ""),
    ("Coder/Reviewer loop",     "yes",  "yes",  "no (subagent only)", "high"),
    ("Plan mode",               "yes",  "no",   "best",                "high"),
    ("Best-of-N",               "best", "no",   "no",                  "med"),
    ("Reviewer panel",          "yes",  "no",   "no",                  "med"),
    ("Self-reflection",         "yes",  "no",   "no",                  "med"),

    ("__HDR__", "Sandbox & Security",  "",   "",   ""),
    ("Linux Landlock syscall",  "yes (ctypes)",   "best",   "no (perms only)",     "high"),
    ("Windows Job Object",      "yes (ctypes)",   "yes",     "no",                  "high"),
    ("Permissions (allow/ask/deny)", "yes",       "yes",     "best",                "high"),
    ("Output guardrail",        "unique",         "no",      "no",                  "med"),
    ("Jailbreak/cheat features","refused",        "refused", "refused",             "med"),

    ("__HDR__", "Memory & Context",  "",   "",   ""),
    ("AGENTS.md / skills",      "yes",            "yes",     "own (CLAUDE.md)",     "high"),
    ("Manifest",                "yes",            "yes",     "no",                  "med"),
    ("Auto-learning",           "yes",            "no",      "no",                  "med"),
    ("Memory hierarchy",        "med",            "med",     "best",                "med"),

    ("__HDR__", "Tooling & Extensibility",  "",   "",   ""),
    ("MCP client",              "yes",            "yes",     "yes",                 "high"),
    ("Built-in MCP servers",    "unique (fs)",    "no",      "no",                  "med"),
    ("Hooks (Pre/Post/Stop)",   "yes (3 types)",  "no",      "yes (4 types)",       "high"),
    ("Slash commands",          "yes (10 built-in)", "no",  "yes",                 "high"),
    ("Plugins",                 "unique",         "no",      "no",                  "low"),
    ("Sub-modes (RO/sandbox)",  "unique",         "no",      "no",                  "med"),

    ("__HDR__", "Observability & Cost",  "",   "",   ""),
    ("Prometheus /metrics",     "unique",         "no",      "no",                  "high"),
    ("Per-project cost (USD)",  "unique",         "no",      "no",                  "high"),
    ("Tracing",                 "yes (JSONL)",    "yes",     "weak",                "med"),

    ("__HDR__", "UI & Distribution",  "",   "",   ""),
    ("TUI",                     "yes (Textual)",  "yes",     "yes",                 "med"),
    ("Web SPA",                 "unique",         "no",      "no",                  "high"),
    ("Voice (STT/TTS)",         "yes (edge-tts)", "no",      "weak",                "med"),
    ("Computer use (mouse/kb)", "unique (Win ctypes)", "no", "no",              "low"),
    ("Cloud S3 storage",        "unique (boto3+SigV4)", "no", "no",          "med"),
    ("Open source",             "MIT",            "Apache 2.0", "no",                "high"),
    ("Install",                 "git+pip+npm",    "curl",     "npm i -g",            "med"),
    ("Perf / scale",            "Python single",  "Rust (best)", "TS",             "high"),
    ("Public benchmark",        "HumanEval/MBPP", "ARC-AGI-3 (38.3%)", "n/a",      "high"),
]

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------

KAIROS, CODEX, CLAUDE = "Kairos Code", "Codex Harness", "Claude Code"
COL_LABEL_BG = {
    KAIROS: "#0f1115",
    CODEX:  "#5b6068",
    CLAUDE: "#5b6068",
}
COL_LABEL_FG = {
    KAIROS: "#fd971f",  # kairos brand (orange)
    CODEX:  "#c5c8c6",
    CLAUDE: "#c5c8c6",
}
ROW_BG = "#0f0f10"
ALT_BG = "#171719"

# cell color map
CELL = {
    "yes":     ("#0e3a25", "#10a37f", "●"),
    "partial": ("#3a2e0e", "#d97706", "◐"),
    "no":      ("#3a0e0e", "#dc2626", "○"),
    "best":    ("#0e2a3a", "#2563eb", "★"),
    "unique":  ("#2a0e3a", "#7c3aed", "◆"),
    "refused": ("#1f1f21", "#797979", "—"),
}


def cell_color(value: str):
    if value in CELL:
        return CELL[value]
    return ("#1f1f21", "#797979", "·")


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def render(out_path: Path) -> None:
    fig = plt.figure(figsize=(22, 28), facecolor="#0a0a0b")

    # Title bar
    title_ax = fig.add_axes([0, 0.965, 1, 0.035])
    title_ax.set_facecolor("#fd971f")
    title_ax.set_xticks([]); title_ax.set_yticks([])
    for s in title_ax.spines.values():
        s.set_visible(False)
    title_ax.text(0.5, 0.5,
        "Kairos Code   vs   Codex Harness   vs   Claude Code",
        ha="center", va="center", fontsize=22, fontweight="bold",
        color="#0f1115",
    )

    # Subtitle / legend
    sub_ax = fig.add_axes([0, 0.945, 1, 0.020])
    sub_ax.set_facecolor("#0a0a0b")
    sub_ax.set_xticks([]); sub_ax.set_yticks([])
    for s in sub_ax.spines.values():
        s.set_visible(False)
    legend_items = [
        ("●", "#10a37f", "Yes"),
        ("◐", "#d97706", "Partial"),
        ("○", "#dc2626", "No"),
        ("★", "#2563eb", "Kairos best"),
        ("◆", "#7c3aed", "Kairos unique"),
        ("—", "#797979", "Both refuse"),
    ]
    parts = []
    for sym, col, label in legend_items:
        parts.append(f"{sym}  {label}    ")
    sub_ax.text(0.5, 0.5, "    ".join(p.strip() for p in parts),
        ha="center", va="center", fontsize=11, color="#c5c8c6",
        family="monospace",
    )

    # Column widths
    col_x = [0.04, 0.40, 0.57, 0.74]  # feature / kairos / codex / claude
    col_w = [0.36, 0.17, 0.17, 0.22]
    header_y = 0.920

    # Column headers
    header_ax = fig.add_axes([0, header_y, 1, 0.025])
    header_ax.set_facecolor("#1f1f21")
    header_ax.set_xticks([]); header_ax.set_yticks([])
    for s in header_ax.spines.values():
        s.set_color("#797979")
    for i, (name, _) in enumerate([
        ("Feature", None),
        (KAIROS, COL_LABEL_FG[KAIROS]),
        (CODEX, COL_LABEL_FG[CODEX]),
        (CLAUDE, COL_LABEL_FG[CLAUDE]),
    ]):
        x = col_x[i] + col_w[i] / 2
        header_ax.text(x, 0.5, name, ha="center", va="center",
            fontsize=14, fontweight="bold",
            color=COL_LABEL_FG.get(name, "#c5c8c6"),
        )

    # Row rendering
    n_rows = len(ROWS)
    row_h = 0.018
    y_top = header_y - 0.027
    y = y_top
    n = 0
    for row in ROWS:
        feature, kairos_v, codex_v, claude_v, weight = row
        is_hdr = feature == "__HDR__"
        if is_hdr:
            band_ax = fig.add_axes([0, y - row_h, 1, row_h])
            band_ax.set_facecolor("#0e2a3a")
            band_ax.set_xticks([]); band_ax.set_yticks([])
            for s in band_ax.spines.values():
                s.set_visible(False)
            band_ax.text(0.02, 0.5, kairos_v, ha="left", va="center",
                fontsize=13, fontweight="bold", color="#58d1eb",
                family="monospace",
            )
            y -= row_h
            n += 1
            continue

        # alternating row background
        row_ax = fig.add_axes([0, y - row_h, 1, row_h])
        row_ax.set_facecolor(ROW_BG if n % 2 == 0 else ALT_BG)
        row_ax.set_xticks([]); row_ax.set_yticks([])
        for s in row_ax.spines.values():
            s.set_visible(False)

        # feature name
        weight_marker = {
            "high": "●", "med": "○", "low": "·",
        }.get(weight, "·")
        weight_color = {
            "high": "#fd971f", "med": "#797979", "low": "#3a3a3c",
        }.get(weight, "#797979")
        row_ax.text(col_x[0] + 0.005, 0.5, f"{weight_marker}",
            ha="left", va="center", fontsize=10, color=weight_color,
        )
        row_ax.text(col_x[0] + 0.018, 0.5, feature,
            ha="left", va="center", fontsize=11, color="#e0e0e0",
        )

        # value cells
        for i, val in enumerate([kairos_v, codex_v, claude_v]):
            cell_ax = fig.add_axes([col_x[i+1], y - row_h, col_w[i+1], row_h])
            cell_ax.set_xticks([]); cell_ax.set_yticks([])
            bg, fg, sym = cell_color(val)
            cell_ax.set_facecolor(bg)
            for s in cell_ax.spines.values():
                s.set_color("#3a3a3c")
                s.set_linewidth(0.5)
            # symbol on the left
            cell_ax.text(0.04, 0.5, sym, ha="left", va="center",
                fontsize=14, color=fg, fontweight="bold",
            )
            # text
            cell_ax.text(0.16, 0.5, val,
                ha="left", va="center", fontsize=10, color="#e0e0e0",
            )
        y -= row_h
        n += 1

    # Footer / summary
    foot_ax = fig.add_axes([0, 0.005, 1, 0.020])
    foot_ax.set_facecolor("#0a0a0b")
    foot_ax.set_xticks([]); foot_ax.set_yticks([])
    for s in foot_ax.spines.values():
        s.set_visible(False)
    foot_ax.text(0.5, 0.5,
        "●  high-impact   ○  medium-impact   ·  low-impact   |   snapshot 2026-08-26  |  drawn from docs/REAL_FEATURES_REPORT.md + LANDING_RECOMMENDATIONS_REPORT.md + ROUND_7_FOLLOWUP_REPORT.md",
        ha="center", va="center", fontsize=9, color="#797979",
        family="monospace",
    )

    # Save
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, facecolor=fig.get_facecolor(),
                bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="docs/kairos-vs-codex-vs-claude.png")
    args = parser.parse_args()
    render(Path(args.out))
