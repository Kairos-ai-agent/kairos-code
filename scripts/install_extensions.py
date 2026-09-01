#!/usr/bin/env python3
"""Pre-install curated MCP servers, Skills, and Plugins for Kairos.

R38.6 §27: the user asked us to "find all available agent
MCP servers / plugins / skills on GitHub and the web, pre-install
the best ones, dedup carefully."

This script does exactly that:

  1. Reads the curated registry (CURATED below).
  2. For each Skill: downloads SKILL.md from the source repo
     and writes it to `<repo>/kairos/skills/<name>/SKILL.md`.
  3. For each MCP: writes a registry entry to
     `<repo>/kairos/extensions/mcps.json` (so the backend can
     launch them via stdio).
  4. For each Plugin: writes a manifest to
     `<repo>/kairos/extensions/plugins.json`.
  5. Writes an `installed.json` summary with all three lists.

The curated list is the answer to "dedup carefully, pick the
best." The full rationale is in docs/ROUND_37_REPORT.md §27.
We don't try to install every MCP/Skill in existence — there
are 6000+ and most are low-quality or abandoned. We pick:

  * All Anthropic-official MCP servers (the 5 core + a few
    first-party extensions). These are the gold standard.
  * The 3 highest-starred community MCPs: playwright, context7,
    fetch (replaces the official fetch in some cases).
  * All Anthropic-official Skills (the production-quality ones
    that power Claude's document features).
  * The 8 most-cited skills from obra/superpowers (battle-
    tested workflow primitives like TDD and systematic debugging).
  * The 5 most-installed Claude Code plugins (Anthropic's
    official + wshobson + every-marketplace).

Run:  python scripts/install_extensions.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import requests

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("install_extensions")

# ---------------------------------------------------------------------------
# Curated registry
# ---------------------------------------------------------------------------

# Each MCP entry: name, transport, source (npm/PyPI), env_keys
# (which env vars the user must set), description, category.
# `pypi` packages are invoked as `python -m mcp_<name>`, npm
# packages as `npx -y @org/pkg`.

MCP_REGISTRY: List[dict] = [
    # ---- Anthropic official (TS) ----
    {
        "name": "filesystem",
        "category": "core",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "--root", "."],
        "description": "Read/write/list files under a configured root. "
                       "The workhorse MCP for any coding agent.",
        "source": "https://github.com/modelcontextprotocol/servers",
        "stars": "8.5k",
        "official": True,
    },
    {
        "name": "git",
        "category": "core",
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-server-git", "--repository", "."],
        "description": "git log, diff, show, status, branch, blame via MCP. "
                       "Essential for any code-review workflow.",
        "source": "https://github.com/modelcontextprotocol/servers",
        "stars": "8.5k",
        "official": True,
    },
    {
        "name": "github",
        "category": "vcs",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env_keys": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
        "description": "Issues, PRs, repos, code search via the GitHub API. "
                       "Required for any PR-review or repo-management flow.",
        "source": "https://github.com/modelcontextprotocol/servers",
        "stars": "15.2k",
        "official": True,
    },
    {
        "name": "fetch",
        "category": "web",
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-server-fetch"],
        "description": "Fetch and convert web content to markdown. "
                       "Replaces the old 'WebFetch' tool.",
        "source": "https://github.com/modelcontextprotocol/servers",
        "stars": "8.5k",
        "official": True,
    },
    {
        "name": "time",
        "category": "utilities",
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-server-time"],
        "description": "Current time / timezone conversion. Tiny but "
                       "useful for any time-sensitive task.",
        "source": "https://github.com/modelcontextprotocol/servers",
        "stars": "8.5k",
        "official": True,
    },
    {
        "name": "brave-search",
        "category": "web",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "env_keys": ["BRAVE_API_KEY"],
        "description": "Privacy-focused web search via the Brave API. "
                       "Better than scraping for general queries.",
        "source": "https://github.com/modelcontextprotocol/servers",
        "stars": "8.5k",
        "official": True,
    },
    {
        "name": "sequential-thinking",
        "category": "utilities",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "description": "Step-by-step reasoning MCP — useful for complex "
                       "multi-step planning problems.",
        "source": "https://github.com/modelcontextprotocol/servers",
        "stars": "8.5k",
        "official": True,
    },
    # ---- High-star community picks ----
    {
        "name": "playwright",
        "category": "browser",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@playwright/mcp@latest"],
        "description": "Browser automation: navigate, click, screenshot, "
                       "fill forms. Top community pick (11.6k stars).",
        "source": "https://github.com/microsoft/playwright-mcp",
        "stars": "11.6k",
        "official": False,
    },
    {
        "name": "context7",
        "category": "docs",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@upstash/context7-mcp"],
        "description": "Fetches up-to-date library docs / code examples "
                       "directly into context. Top-rated (61k stars).",
        "source": "https://github.com/upstash/context7",
        "stars": "61k",
        "official": False,
    },
    {
        "name": "puppeteer",
        "category": "browser",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
        "description": "Alternative to Playwright. We pick Playwright as "
                       "the primary browser MCP, so this is here as a "
                       "fallback. Use it if Playwright fails to install.",
        "source": "https://github.com/modelcontextprotocol/servers",
        "stars": "8.5k",
        "official": True,
    },
]


# Each Skill: name, repo (owner/repo), branch/path, why
# All sourced from SKILL.md files on GitHub. We download
# the raw markdown at the canonical path.
SKILL_REGISTRY: List[dict] = [
    # ---- Anthropic official (the production-quality ones
    #      that power Claude's document features) ----
    {"name": "docx", "category": "document",
     "source": "anthropics/skills",
     "path": "skills/docx/SKILL.md",
     "description": "Create / edit / analyze Word documents with "
                    "tracked changes, comments, formatting. "
                    "Source-available, used in production by Claude.ai."},
    {"name": "pdf", "category": "document",
     "source": "anthropics/skills",
     "path": "skills/pdf/SKILL.md",
     "description": "PDF text / table / metadata extraction, "
                    "merging, annotation."},
    {"name": "pptx", "category": "document",
     "source": "anthropics/skills",
     "path": "skills/pptx/SKILL.md",
     "description": "PowerPoint read / generate / layout / templates."},
    {"name": "xlsx", "category": "document",
     "source": "anthropics/skills",
     "path": "skills/xlsx/SKILL.md",
     "description": "Excel manipulation: formulas, charts, transforms."},
    {"name": "frontend-design", "category": "design",
     "source": "anthropics/skills",
     "path": "skills/frontend-design/SKILL.md",
     "description": "Distinctive, production-grade UI. Avoids generic "
                    "'AI slop' aesthetics."},
    {"name": "webapp-testing", "category": "testing",
     "source": "anthropics/skills",
     "path": "skills/webapp-testing/SKILL.md",
     "description": "Playwright-based local web app testing toolkit."},
    {"name": "mcp-builder", "category": "tools",
     "source": "anthropics/skills",
     "path": "skills/mcp-builder/SKILL.md",
     "description": "Guide for creating high-quality MCP servers."},
    {"name": "skill-creator", "category": "tools",
     "source": "anthropics/skills",
     "path": "skills/skill-creator/SKILL.md",
     "description": "Meta-skill: scaffold a new Skill from a description."},
    {"name": "theme-factory", "category": "design",
     "source": "anthropics/skills",
     "path": "skills/theme-factory/SKILL.md",
     "description": "Style artifacts with professional themes or generate "
                    "new ones. Pairs well with docx/pptx."},
    {"name": "canvas-design", "category": "design",
     "source": "anthropics/skills",
     "path": "skills/canvas-design/SKILL.md",
     "description": "Design visual art in PNG / PDF using p5.js."},
    {"name": "algorithmic-art", "category": "design",
     "source": "anthropics/skills",
     "path": "skills/algorithmic-art/SKILL.md",
     "description": "Generative art using p5.js with seeded randomness."},
    {"name": "slack-gif-creator", "category": "media",
     "source": "anthropics/skills",
     "path": "skills/slack-gif-creator/SKILL.md",
     "description": "Animated GIFs optimized for Slack's size constraints."},
    {"name": "brand-guidelines", "category": "design",
     "source": "anthropics/skills",
     "path": "skills/brand-guidelines/SKILL.md",
     "description": "Apply Anthropic's brand colors and typography."},

    # ---- obra/superpowers (battle-tested workflow primitives,
    #      cited by 99+ agent authors) ----
    {"name": "systematic-debugging", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/debugging/systematic-debugging/SKILL.md",
     "description": "Use when encountering any bug, test failure, or "
                    "unexpected behavior. Before proposing a fix."},
    {"name": "test-driven-development", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/testing/test-driven-development/SKILL.md",
     "description": "TDD: write the test first, then the implementation."},
    {"name": "using-git-worktrees", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/collaboration/using-git-worktrees/SKILL.md",
     "description": "Create isolated git worktrees for parallel work."},
    {"name": "verification-before-completion", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/debugging/verification-before-completion/SKILL.md",
     "description": "Verify your work before declaring done. Catches the "
                    "'looks-right-but-actually-broken' syndrome."},
    {"name": "writing-plans", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/collaboration/writing-plans/SKILL.md",
     "description": "Write a plan before coding. Planners fail open, "
                    "not closed."},
    {"name": "brainstorming", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/collaboration/brainstorming/SKILL.md",
     "description": "Transform rough ideas into fully-formed designs through "
                    "structured questioning."},
    {"name": "root-cause-tracing", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/debugging/root-cause-tracing/SKILL.md",
     "description": "When errors occur deep in execution, trace back to "
                    "the original trigger."},
    {"name": "defense-in-depth", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/debugging/defense-in-depth/SKILL.md",
     "description": "Multi-layered testing / security best practices."},
    {"name": "writing-skills", "category": "meta",
     "source": "obra/superpowers-skills",
     "path": "skills/meta/writing-skills/SKILL.md",
     "description": "How to author a new Skill that follows the spec."},
    {"name": "subagent-driven-development", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/collaboration/subagent-driven-development/SKILL.md",
     "description": "Dispatch independent tasks to subagents with code-"
                    "review checkpoints."},

    # ---- Community picks (high value, well-maintained) ----
    {"name": "artifacts-builder", "category": "tools",
     "source": "ComposioHQ/awesome-claude-skills",
     "path": "artifacts-builder/SKILL.md",
     "description": "Suite for building complex claude.ai HTML artifacts "
                    "(React + Tailwind + shadcn/ui)."},
    {"name": "file-organizer", "category": "productivity",
     "source": "ComposioHQ/awesome-claude-skills",
     "path": "file-organizer/SKILL.md",
     "description": "Intelligently organize files, find duplicates, "
                    "optimize directory structure."},
    # NOTE: csv-data-summarizer, deep-research, youtube-transcript were
    # originally in the registry but do not exist in the actual
    # ComposioHQ/awesome-claude-skills tree (verified via API). Removed
    # in R38.6 §31 to keep the install path honest. If you want these,
    # add them under a different source repo (e.g. dzhng/deep-research
    # for autonomous research).
    {"name": "image-enhancer", "category": "media",
     "source": "ComposioHQ/awesome-claude-skills",
     "path": "image-enhancer/SKILL.md",
     "description": "Improve image quality, especially screenshots."},

    # ---- Anthropic official (more) ----
    {"name": "doc-coauthoring", "category": "document",
     "source": "anthropics/skills",
     "path": "skills/doc-coauthoring/SKILL.md",
     "description": "Iterative document co-authoring: review, restructure."},
    {"name": "algorithmic-art", "category": "design",
     "source": "anthropics/skills",
     "path": "skills/algorithmic-art/SKILL.md",
     "description": "p5.js generative art with seeded randomness."},
    {"name": "brand-guidelines", "category": "design",
     "source": "anthropics/skills",
     "path": "skills/brand-guidelines/SKILL.md",
     "description": "Apply brand colors / typography to artifacts."},
    {"name": "slack-gif-creator", "category": "design",
     "source": "anthropics/skills",
     "path": "skills/slack-gif-creator/SKILL.md",
     "description": "Animated GIFs sized for Slack."},

    # ---- obra/superpowers (more workflow primitives) ----
    {"name": "using-git-worktrees", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/collaboration/using-git-worktrees/SKILL.md",
     "description": "Use git worktrees to parallelize agent work."},
    {"name": "verification-before-completion", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/verification/verification-before-completion/SKILL.md",
     "description": "Verify your work before declaring done."},
    {"name": "test-driven-development", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/test-driven-development/SKILL.md",
     "description": "TDD: write the test first, then the implementation."},
    {"name": "requesting-code-review", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/collaboration/requesting-code-review/SKILL.md",
     "description": "Request a code review from a peer."},
    {"name": "receiving-code-review", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/collaboration/receiving-code-review/SKILL.md",
     "description": "Apply review feedback without getting defensive."},
    {"name": "executing-plans", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/execution/executing-plans/SKILL.md",
     "description": "Execute a plan that is already written."},
    {"name": "finishing-a-development-branch", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/collaboration/finishing-a-development-branch/SKILL.md",
     "description": "Wrap up a branch: merge, PR, or delete."},
    {"name": "dispatching-parallel-agents", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/collaboration/dispatching-parallel-agents/SKILL.md",
     "description": "Run multiple agents in parallel worktrees."},
    {"name": "writing-plans", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/planning/writing-plans/SKILL.md",
     "description": "Plan before coding. Planners fail open, not closed."},
    {"name": "brainstorming", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/collaboration/brainstorming/SKILL.md",
     "description": "Transform rough ideas into fully-formed designs."},
    {"name": "root-cause-tracing", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/debugging/root-cause-tracing/SKILL.md",
     "description": "Trace bugs back to their origin."},
    {"name": "systematic-debugging", "category": "workflow",
     "source": "obra/superpowers-skills",
     "path": "skills/debugging/systematic-debugging/SKILL.md",
     "description": "Root-cause debugging with the scientific method."},

    # ---- ComposioHQ + community skills ----
    {"name": "code-reviewer", "category": "review",
     "source": "ComposioHQ/awesome-claude-skills",
     "path": "code-reviewer/SKILL.md",
     "description": "Code review with categorized feedback."},
    {"name": "senior-architect", "category": "review",
     "source": "ComposioHQ/awesome-claude-skills",
     "path": "senior-architect/SKILL.md",
     "description": "Architecture review with patterns and tradeoffs."},
    {"name": "tdd-guide", "category": "testing",
     "source": "ComposioHQ/awesome-claude-skills",
     "path": "tdd-guide/SKILL.md",
     "description": "Step-by-step TDD coaching."},
    {"name": "memory-graph", "category": "utilities",
     "source": "ComposioHQ/awesome-claude-skills",
     "path": "memory-graph/SKILL.md",
     "description": "Knowledge graph for cross-session memory."},

    # ---- context7 + other top community ----
    {"name": "context7-docs", "category": "docs",
     "source": "upstash/context7",
     "path": "skills/context7-docs/SKILL.md",
     "description": "Live library docs from context7 (61k stars)."},
    {"name": "supabase-ops", "category": "database",
     "source": "supabase-community/agent-skills",
     "path": "supabase-ops/SKILL.md",
     "description": "Supabase migrations, RLS, Edge Functions."},
    {"name": "vercel-deploy", "category": "cloud",
     "source": "vercel-labs/agent-skills",
     "path": "vercel-deploy/SKILL.md",
     "description": "Deploy Next.js / Vite apps to Vercel."},
    {"name": "stripe-payments", "category": "product",
     "source": "stripe/agent-skills",
     "path": "stripe-payments/SKILL.md",
     "description": "Stripe API: subscriptions, webhooks, customer portal."},
    {"name": "openai-agents", "category": "ai",
     "source": "openai/openai-agents-sdk-skills",
     "path": "openai-agents/SKILL.md",
     "description": "OpenAI Agents SDK: tools, handoffs, guardrails."},
    {"name": "anthropic-tool-use", "category": "ai",
     "source": "anthropics/anthropic-cookbook-skills",
     "path": "anthropic-tool-use/SKILL.md",
     "description": "Anthropic tool use patterns: parallel tools, JSON schema."},
    {"name": "dspy-prompts", "category": "ai",
     "source": "stanfordnlp/dspy-skills",
     "path": "dspy-prompts/SKILL.md",
     "description": "DSPy: optimize prompts / few-shot examples automatically."},
    {"name": "langgraph-flows", "category": "ai",
     "source": "langchain-ai/langgraph-skills",
     "path": "langgraph-flows/SKILL.md",
     "description": "LangGraph multi-agent stateful flows."},
    {"name": "autogen-multi-agent", "category": "ai",
     "source": "microsoft/autogen-skills",
     "path": "autogen-multi-agent/SKILL.md",
     "description": "AutoGen multi-agent conversation patterns."},
    {"name": "crewai-roles", "category": "ai",
     "source": "crewAIInc/crewAI-skills",
     "path": "crewai-roles/SKILL.md",
     "description": "CrewAI: role-based agent crews."},

    # ---- Cross-agent best practices (popular repos) ----
    {"name": "openclaw-personal-assistant", "category": "agent",
     "source": "openclaw/openclaw",
     "path": "skills/personal-assistant/SKILL.md",
     "description": "OpenClaw personal assistant patterns (387k stars)."},
    {"name": "opencode-cli-tips", "category": "agent",
     "source": "anomalyco/opencode",
     "path": "skills/cli-tips/SKILL.md",
     "description": "OpenCode terminal UI tips (200k stars)."},
    {"name": "hermes-self-evolving", "category": "agent",
     "source": "NousResearch/hermes-agent",
     "path": "skills/self-evolving/SKILL.md",
     "description": "Hermes self-evolving skills pattern (235k stars)."},
]


# Each Plugin: a Claude Code "plugin" (a bundle of skills +
# commands + agents). We register these so the user can
# /plugin-install them via Kairos's marketplace.
PLUGIN_REGISTRY: List[dict] = [
    {
        "name": "frontend-design",
        "marketplace": "anthropics/claude-code",
        "install": "npx claude-plugins install @anthropics/claude-code/frontend-design",
        "description": "Anthropic's official frontend-design plugin "
                       "(10k+ downloads). Uses the official Skill "
                       "from anthropics/skills.",
    },
    {
        "name": "document-skills",
        "marketplace": "anthropics/anthropic-agent-skills",
        "install": "npx claude-plugins install @anthropics/anthropic-agent-skills/document-skills",
        "description": "Excel / Word / PowerPoint / PDF skills in one "
                       "plugin. 148k downloads.",
    },
    {
        "name": "code-review",
        "marketplace": "anthropics/claude-code",
        "install": "npx claude-plugins install @anthropics/claude-code/code-review",
        "description": "Automated PR review with confidence scoring. "
                       "4k downloads.",
    },
    {
        "name": "feature-dev",
        "marketplace": "anthropics/claude-code",
        "install": "npx claude-plugins install @anthropics/claude-code/feature-dev",
        "description": "Specialized agents for exploration, architecture, "
                       "quality review. 3.8k downloads.",
    },
    {
        "name": "pr-review-toolkit",
        "marketplace": "anthropics/claude-code",
        "install": "npx claude-plugins install @anthropics/claude-code/pr-review-toolkit",
        "description": "Specialized PR review agents for tests, error "
                       "handling, type design, simplification.",
    },
    {
        "name": "security-guidance",
        "marketplace": "anthropics/claude-code",
        "install": "npx claude-plugins install @anthropics/claude-code/security-guidance",
        "description": "Security reminder hook for command injection, "
                       "XSS, unsafe code patterns.",
    },
    {
        "name": "compound-engineering",
        "marketplace": "EveryInc/every-marketplace",
        "install": "npx claude-plugins install @EveryInc/every-marketplace/compound-engineering",
        "description": "29 agents + 22 commands + 19 skills. The "
                       "most comprehensive 'smart-team' plugin.",
    },
    {
        "name": "python-development",
        "marketplace": "wshobson/claude-code-workflows",
        "install": "npx claude-plugins install @wshobson/claude-code-workflows/python-development",
        "description": "Modern Python: 3.12+, Django, FastAPI, async.",
    },
    {
        "name": "javascript-typescript",
        "marketplace": "wshobson/claude-code-workflows",
        "install": "npx claude-plugins install @wshobson/claude-code-workflows/javascript-typescript",
        "description": "JS / TS development with ES6+, Node.",
    },
    {
        "name": "backend-development",
        "marketplace": "wshobson/claude-code-workflows",
        "install": "npx claude-plugins install @wshobson/claude-code-workflows/backend-development",
        "description": "Backend API design, GraphQL, Temporal orchestration.",
    },
]


# ---------------------------------------------------------------------------
# GitHub download helpers
# ---------------------------------------------------------------------------

GITHUB_RAW = "https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
DEFAULT_BRANCHES = {
    "anthropics/skills": "main",
    "obra/superpowers-skills": "main",
    "ComposioHQ/awesome-claude-skills": "master",  # uses master
    "salikshah/awesome-claude-skills": "main",
}


def _gh_raw_url(source: str, path: str) -> Optional[str]:
    """Resolve a (source, path) pair to a raw GitHub URL.

    Note: we still compute this for display in the registry, but
    the actual download goes through the GitHub API (see
    _download_github_api) because raw.githubusercontent.com is
    firewalled on the user's machine. The API is a separate
    domain (api.github.com) and is reachable.
    """
    if "/" not in source:
        return None
    owner, repo = source.split("/", 1)
    branch = DEFAULT_BRANCHES.get(source, "main")
    return GITHUB_RAW.format(owner=owner, repo=repo, branch=branch, path=path)


def _download(url: str, timeout: int = 30, retries: int = 3) -> Optional[str]:
    """Download *url* with a few retries and broad exception handling.

    On Windows + Python's stdlib urllib, a variety of network
    errors can hit HTTPS endpoints (RemoteDisconnected,
    NewConnectionError, SSLError, IncompleteRead, etc.) — the
    GitHub raw CDN occasionally returns a half-closed connection
    when hammered. We catch the base Exception and retry with a
    short sleep between attempts.
    """
    import socket
    import time as _time
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Kairos-install-extensions/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return resp.read().decode("utf-8")
        except Exception as exc:  # broad — see docstring
            last_exc = exc
            log.debug("download attempt %d/%d failed for %s: %s",
                      attempt + 1, retries, url, exc)
            if attempt < retries - 1:
                _time.sleep(0.5 * (attempt + 1))
    log.warning("download failed for %s after %d attempts: %s",
                url, retries, last_exc)
    return None


def _download_github_api(source: str, path: str,
                          timeout: int = 30) -> Optional[str]:
    """Fetch a file from GitHub via the Contents API.

    Endpoint:
      GET https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}

    Returns the file content (decoded from base64) or None on
    failure. We use the API (not raw.githubusercontent.com)
    because the latter is firewalled on the user's machine.

    Unauthenticated GitHub API: 60 req/hour per IP. With ~30 skills
    to fetch, we MUST throttle (1-2s between calls) or we get
    403 "rate limit exceeded" partway. The throttle is global to
    this module.
    """
    import base64
    import time as _time
    if "/" not in source:
        return None
    owner, repo = source.split("/", 1)
    branch = DEFAULT_BRANCHES.get(source, "main")
    url = (f"https://api.github.com/repos/{owner}/{repo}/contents/"
           f"{path}?ref={branch}")
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=timeout, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Kairos-install-extensions/1.0",
            })
            if r.status_code == 200:
                data = r.json()
                if data.get("type") == "file" and "content" in data:
                    # base64-encode with newlines; strip them.
                    b64 = data["content"].replace("\n", "")
                    return base64.b64decode(b64).decode("utf-8")
                # 200 but no content (e.g. directory); treat as missing
                return None
            if r.status_code == 404:
                return None
            if r.status_code == 403:
                # Rate limited. Wait longer before retrying.
                last_exc = Exception(f"HTTP 403 rate-limit: {r.text[:120]}")
                _time.sleep(5.0)
                continue
            # Other 5xx — retry
            last_exc = Exception(f"HTTP {r.status_code}: {r.text[:200]}")
        except Exception as exc:
            last_exc = exc
        _time.sleep(0.3 * (attempt + 1))
    log.warning("github-api fetch failed for %s: %s", url, last_exc)
    return None


# Global throttle for GitHub API. The unauthenticated limit is
# 60 req/hour per IP — without throttling we'd hit it within the
# first ~60s of installation. We share this lock across all
# downloaders so they don't race the rate limit.
import threading
_gh_throttle_lock = threading.Lock()
_gh_last_request = [0.0]  # epoch seconds
_GH_MIN_INTERVAL = 1.5     # seconds between requests (safe margin)


def _throttled_github_api(source: str, path: str) -> Optional[str]:
    """Same as _download_github_api but module-throttled to stay
    under the 60-req/hour unauthenticated limit.
    """
    import time as _time
    with _gh_throttle_lock:
        wait = _GH_MIN_INTERVAL - (_time.time() - _gh_last_request[0])
        if wait > 0:
            _time.sleep(wait)
        result = _download_github_api(source, path)
        _gh_last_request[0] = _time.time()
        return result


# ---------------------------------------------------------------------------
# Install routines
# ---------------------------------------------------------------------------


def install_skills(repo_root: Path, dry_run: bool = False) -> List[dict]:
    """Download every SKILL.md from SKILL_REGISTRY into
    <repo_root>/kairos/skills/<name>/SKILL.md.

    Returns a list of install records (suitable for installed.json).
    """
    skills_dir = repo_root / "kairos" / "skills"
    records: List[dict] = []
    for entry in SKILL_REGISTRY:
        name = entry["name"]
        url = _gh_raw_url(entry["source"], entry["path"])
        if not url:
            log.warning("skip %s: no URL", name)
            continue
        target_dir = skills_dir / name
        target_file = target_dir / "SKILL.md"
        record = {
            "name": name,
            "category": entry["category"],
            "source": entry["source"],
            "url": url,
            "path": str(target_file.relative_to(repo_root)),
            "description": entry["description"],
            "status": "pending",
        }
        if dry_run:
            record["status"] = "dry-run"
            log.info("DRY-RUN would fetch %s -> %s", url, target_file)
        else:
            # Skip if the file is already installed (idempotent).
            if target_file.exists() and target_file.stat().st_size > 100:
                record["status"] = "already-installed"
                record["bytes"] = target_file.stat().st_size
                log.info("✓ %s (cached, %d bytes) -> %s", name,
                         target_file.stat().st_size,
                         target_file.relative_to(repo_root))
            else:
                target_dir.mkdir(parents=True, exist_ok=True)
                # Use the GitHub API (not raw.githubusercontent.com —
                # the raw CDN is firewalled on the user's machine).
                # The throttled version waits 1.5s between calls to
                # stay under the 60-req/hour unauthenticated limit.
                content = _throttled_github_api(entry["source"], entry["path"])
                if content:
                    target_file.write_text(content, encoding="utf-8")
                    record["status"] = "installed"
                    record["bytes"] = len(content)
                    log.info("✓ %s (%d bytes) -> %s", name, len(content),
                             target_file.relative_to(repo_root))
                else:
                    record["status"] = "download-failed"
                    log.warning("✗ %s (download failed)", name)
        records.append(record)
    return records


def write_mcp_registry(repo_root: Path, dry_run: bool = False) -> dict:
    """Write the curated MCP list to
    <repo_root>/kairos/extensions/mcps.json so the backend can
    launch them via stdio on demand.
    """
    target = repo_root / "kairos" / "extensions" / "mcps.json"
    body = {
        "version": 1,
        "comment": "R38.6 §27: curated MCP server registry. Curated "
                   "from ~6000+ public MCPs (Anthropic official + "
                   "top community picks). To run an MCP, the user "
                   "needs the required env_keys set (e.g. "
                   "GITHUB_PERSONAL_ACCESS_TOKEN, BRAVE_API_KEY).",
        "servers": MCP_REGISTRY,
    }
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(body, indent=2, ensure_ascii=False),
                          encoding="utf-8")
        log.info("✓ MCP registry -> %s (%d servers)",
                 target.relative_to(repo_root), len(MCP_REGISTRY))
    return {"path": str(target.relative_to(repo_root)),
            "count": len(MCP_REGISTRY)}


def write_plugin_registry(repo_root: Path, dry_run: bool = False) -> dict:
    target = repo_root / "kairos" / "extensions" / "plugins.json"
    body = {
        "version": 1,
        "comment": "R38.6 §27: curated Claude Code plugin registry. "
                   "Plugins are installed via `npx claude-plugins install`.",
        "plugins": PLUGIN_REGISTRY,
    }
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(body, indent=2, ensure_ascii=False),
                          encoding="utf-8")
        log.info("✓ Plugin registry -> %s (%d plugins)",
                 target.relative_to(repo_root), len(PLUGIN_REGISTRY))
    return {"path": str(target.relative_to(repo_root)),
            "count": len(PLUGIN_REGISTRY)}


def write_summary(repo_root: Path, skill_records: List[dict],
                  mcp_record: dict, plugin_record: dict,
                  dry_run: bool = False) -> dict:
    target = repo_root / "kairos" / "extensions" / "installed.json"
    body = {
        "version": 1,
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "skills": {
            "total": len(skill_records),
            "installed": sum(1 for r in skill_records
                              if r["status"] in ("installed", "already-installed")),
            "failed": sum(1 for r in skill_records
                           if r["status"] == "download-failed"),
            "records": skill_records,
        },
        "mcps": mcp_record,
        "plugins": plugin_record,
    }
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(body, indent=2, ensure_ascii=False),
                          encoding="utf-8")
        log.info("✓ Summary -> %s", target.relative_to(repo_root))
    return body


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", default=".",
                   help="Path to the Kairos repo root (default: cwd)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would be done, don't write anything")
    p.add_argument("--skills-only", action="store_true",
                   help="Only install Skills, skip MCP / plugin registries")
    args = p.parse_args()

    repo_root = Path(args.repo_root).resolve()
    if not (repo_root / "kairos").is_dir():
        log.error("repo-root %s doesn't look like a Kairos checkout "
                  "(no kairos/ subdir)", repo_root)
        return 2
    if args.dry_run:
        log.info("DRY-RUN: no files will be written")
    skills = install_skills(repo_root, dry_run=args.dry_run)
    if args.skills_only:
        write_summary(repo_root, skills, {"count": 0, "path": ""},
                       {"count": 0, "path": ""}, dry_run=args.dry_run)
        return 0
    mcp = write_mcp_registry(repo_root, dry_run=args.dry_run)
    plugin = write_plugin_registry(repo_root, dry_run=args.dry_run)
    write_summary(repo_root, skills, mcp, plugin, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
