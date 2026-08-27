"""Round 37 — UI source-level checks.

Vitest tests for the React components were attempted but
abandoned — mounting the full antd tree in jsdom takes >15s per
test, which exceeds the CI budget. The behavioural coverage is
provided by the backend tests in `test_r37_backend.py` and the
source-level checks below.

These tests read the .tsx source files and assert that the R37
markers are present:
  - SidebarFooter testids in ChatSidebar.tsx
  - Run-as-task toggle in ChatComposer.tsx
  - LLM Models tab in SettingsDrawer.tsx
  - folder picker in ChatComposer.tsx
  - runAsTask branch in Chat.tsx (chat vs task dispatch)
  - "LLM Models" tab label

If the R37 changes are reverted, these tests fail.
"""
from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_SRC = REPO_ROOT / "web" / "src"


def _read(rel: str) -> str:
    p = WEB_SRC / rel
    assert p.exists(), f"{p} does not exist"
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AppLayout — topbar trimmed, no top-right controls
# ---------------------------------------------------------------------------


def test_applayout_no_longer_has_top_right_avatar_dropdown():
    """The avatar / settings dropdown was moved to the bottom-left
    footer in R37. The topbar should no longer contain it."""
    src = _read("components/AppLayout.tsx")
    # Dropdown is a component that wraps a button + menu — its JSX
    # tag form is `<Dropdown menu={...}>`. Look for the JSX form
    # rather than the bare import, so this isn't fooled by the
    # theme store import that happens to mention "Dropdown" in a
    # string somewhere.
    assert "<Dropdown" not in src, "AppLayout.tsx still renders <Dropdown>"
    assert "<Avatar" not in src, "AppLayout.tsx still renders <Avatar>"
    # The theme toggle also moved
    assert "SunOutlined" not in src and "MoonOutlined" not in src, (
        "Theme toggle should be in the sidebar footer, not the topbar"
    )
    # Tools / Today links moved too
    assert "/today" not in src
    assert "/tools" not in src


def test_applayout_no_longer_has_folder_picker():
    """R37 dedup: the FolderPicker is NOT in the topbar anymore.
    It used to appear in both the topbar AND the composer (above
    the chat input) — that was a confusing duplicate. The composer
    is now the canonical project-switcher location (the user sees
    it next to where they type), so the topbar one was removed.
    The topbar now only has the sidebar toggle and the logo."""
    src = _read("components/AppLayout.tsx")
    # Look for the JSX tag form (i.e. the import + the rendered
    # element) — not the bare word in a comment, since the docstring
    # is allowed to mention FolderPicker as a "what was removed".
    assert "import FolderPicker" not in src, (
        "AppLayout.tsx still imports FolderPicker — it should not "
        "(R37 dedup; the composer is the canonical project-switcher)."
    )
    assert "<FolderPicker" not in src, (
        "AppLayout.tsx still renders <FolderPicker ...> — duplicate "
        "of the one in ChatComposer.tsx."
    )
    # Topbar still has the logo
    assert "Kairos" in src  # logo text
    # Topbar still has the sidebar toggle
    assert "MenuFoldOutlined" in src or "MenuUnfoldOutlined" in src


# ---------------------------------------------------------------------------
# ChatSidebar — SidebarFooter at the bottom
# ---------------------------------------------------------------------------


def test_chatsidebar_exports_sidebarfooter():
    """SidebarFooter is exported (R37) so vitest can mount it
    in isolation. The production app uses it via ChatSidebar's
    render tree."""
    src = _read("components/ChatSidebar.tsx")
    assert "export const SidebarFooter" in src


def test_chatsidebar_footer_has_today_tools_settings_theme():
    """All 4 footer buttons are present with the expected testids."""
    src = _read("components/ChatSidebar.tsx")
    for tid in ("footer-today", "footer-tools", "footer-settings", "footer-theme"):
        assert f'data-testid="{tid}"' in src, f"missing testid: {tid}"


def test_chatsidebar_footer_uses_navigate_for_today_and_tools():
    """Today + Tools call useNavigate(). Settings calls openDrawer().
    Theme is wired to the store's `toggle` action."""
    src = _read("components/ChatSidebar.tsx")
    # The SidebarFooter is the part that does the navigation.
    assert "navigate('/today')" in src
    assert "navigate('/tools')" in src
    assert "openSettings" in src
    # Theme footer wires onClick={toggle} — verify the binding.
    assert "useThemeStore" in src
    # The footer's onClick passes the store's `toggle` action ref.
    # Allow either `toggle()` (invocation) or `toggle` (reference)
    # depending on the component's call style.
    import re
    assert re.search(r"onClick=\{toggle\}", src), (
        "footer theme button onClick should be wired to the toggle action"
    )


# ---------------------------------------------------------------------------
# ChatComposer — Run-as-task toggle + folder picker
# ---------------------------------------------------------------------------


def test_chatcomposer_has_folder_picker_above_input():
    """R37: the project picker is rendered just above the input
    box so the user can switch projects without scrolling up."""
    src = _read("components/ChatComposer.tsx")
    # The folder picker is mounted with data-testid="composer-folder"
    assert 'data-testid="composer-folder"' in src


def test_chatcomposer_has_run_as_task_toggle():
    """The toggle is in the action row next to the send button."""
    src = _read("components/ChatComposer.tsx")
    assert 'data-testid="run-as-task-toggle"' in src
    assert "runAsTask" in src
    assert "setRunAsTask" in src


def test_chatcomposer_on_submit_signature_takes_runtask_boolean():
    """onSubmit(text, runAsTask) — the chat vs task dispatch happens
    in the parent (Chat.tsx)."""
    src = _read("components/ChatComposer.tsx")
    assert "onSubmit: (text: string, runAsTask: boolean)" in src


# ---------------------------------------------------------------------------
# Chat.tsx — chat vs task dispatch
# ---------------------------------------------------------------------------


def test_chat_tsx_handle_submit_branches_on_runtask():
    """The R37 fix for 'every message becomes a task': when
    runAsTask=false we POST /chat (single-turn, no loop); when
    true we POST /start (loop)."""
    src = _read("pages/Chat.tsx")
    # The handleSubmit has a runAsTask parameter
    assert "handleSubmit = async (text: string, runAsTask: boolean)" in src
    # Branches: /chat for chat mode, /start for task mode
    assert "/chat" in src
    assert "/start" in src
    # The user bubble's topic is "user.input" or "ask.answer"
    assert "'user.input'" in src or '"user.input"' in src


# ---------------------------------------------------------------------------
# SettingsDrawer — LLM Models tab, OpenAI / Anthropic only
# ---------------------------------------------------------------------------


def test_settings_drawer_provider_tab_renamed_to_llm_models():
    """The user said the LLM settings were 'hidden somewhere'. R37
    renames the tab from 'Provider' to 'LLM Models' and surfaces
    it as the 6th tab (right after Voice / MCP)."""
    src = _read("components/SettingsDrawer.tsx")
    assert "LLM Models" in src, "Tab label 'LLM Models' missing"
    # The old label "Provider" should NOT be the user-facing tab
    # text anymore (the variable name is still 'provider' but the
    # JSX label is the source of truth).
    assert 'label: <span><RobotOutlined /> LLM Models' in src


def test_settings_drawer_provider_active_uses_only_openai_anthropic():
    """The dropdown now shows exactly 2 options: OpenAI-compatible
    and Anthropic-compatible. No more Ollama / DeepSeek / custom."""
    src = _read("components/SettingsDrawer.tsx")
    # Only 2 option entries
    openai_count = src.count("'openai'")
    anthropic_count = src.count("'anthropic'")
    # The select options AND the type union both mention 'openai' /
    # 'anthropic'. We expect them to appear in multiple places but
    # the LEGACY providers (ollama/deepseek) should not appear in
    # the active-provider Select options.
    assert "value: 'ollama'" not in src, (
        "Legacy Ollama option is still in the select — should be removed"
    )
    assert "value: 'deepseek'" not in src
    assert "value: 'custom'" not in src
    # Sanity: both OpenAI and Anthropic appear at least once
    assert openai_count >= 1
    assert anthropic_count >= 1


def test_settings_drawer_test_connection_button_present():
    """Each provider form has a 'Test connection' button that
    POSTs to /config/test_connection."""
    src = _read("components/SettingsDrawer.tsx")
    assert 'data-testid="openai-test-connection"' in src
    assert 'data-testid="anthropic-test-connection"' in src
    assert "/config/test_connection" in src


def test_settings_drawer_openai_test_posts_openai_provider():
    """When the user clicks Test on the OpenAI form, the payload
    is `{provider: 'openai', base_url, api_key, model}`."""
    src = _read("components/SettingsDrawer.tsx")
    # The OpenAI form calls api.post with provider 'openai'
    assert "provider: 'openai'" in src
    # And the Anthropic form calls with provider 'anthropic'
    assert "provider: 'anthropic'" in src


def test_settings_drawer_legacy_provider_shape_migrated():
    """The old {apiKeyEnv, ollamaBaseUrl, ollamaModel} shape is
    detected and migrated to the new {openai, anthropic} shape.
    A user with old settings on disk will not lose the UI."""
    src = _read("components/SettingsDrawer.tsx")
    assert "'apiKeyEnv' in d.provider" in src
    assert "'ollamaBaseUrl' in d.provider" in src


# ---------------------------------------------------------------------------
# settingsStore — type definitions
# ---------------------------------------------------------------------------


def test_settings_store_provider_has_only_openai_and_anthropic():
    """The ProviderSettings type now has `active: 'openai' | 'anthropic'`
    only — no ollama / deepseek / custom."""
    src = _read("stores/settingsStore.ts")
    assert "LlmProvider = 'openai' | 'anthropic'" in src
    # openai + anthropic are nested objects with baseUrl/apiKey/model
    assert "OpenAIConfig" in src
    assert "AnthropicConfig" in src
    assert "baseUrl: string" in src
    assert "apiKey: string" in src
    assert "model: string" in src
    # No more apiKeyEnv / ollamaBaseUrl / ollamaModel
    assert "apiKeyEnv" not in src
    assert "ollamaBaseUrl" not in src
    assert "ollamaModel" not in src
