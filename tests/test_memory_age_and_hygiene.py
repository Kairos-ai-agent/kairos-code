"""Memory carries its age, and the block says out loud that it is not live state.

A note written three months ago is not evidence about today's code, but the
prompt used to render it exactly like one written a minute ago — so the model
asserted both with the same confidence. Ages come from timestamps that were
already in the database; nothing new is stored.
"""
from __future__ import annotations

import time

from kairos.core.persistence import Persistence
from kairos.learning.reflect import _build_reflection_prompt
from kairos.memory.retrieval import (
    MEMORY_DRIFT_WARNING,
    _age_phrase,
    _age_suffix,
    _render_global_insights,
    _render_notes,
    _render_working_fixes,
    assemble_coder_memory,
)

DAY = 86400.0


# ============================================================ the age helper

def test_age_phrase_scales_with_distance():
    now = time.time()
    assert _age_phrase(now) == "just now"
    assert _age_phrase(now - 3600) == "today"
    assert _age_phrase(now - DAY) == "yesterday"
    assert _age_phrase(now - 3 * DAY) == "3 days ago"
    assert _age_phrase(now - 90 * DAY) == "3 months ago"
    assert _age_phrase(now - 800 * DAY).endswith("years ago")


def test_age_phrase_treats_a_missing_stamp_as_unknown():
    """"Never recorded" (0.0) must not render as "56 years ago"."""
    for nothing in (None, 0, 0.0, "", "not-a-date"):
        assert _age_phrase(nothing) == ""
        assert _age_suffix(nothing) == ""


def test_age_suffix_prefers_the_first_usable_stamp():
    now = time.time()
    assert _age_suffix(now - 2 * DAY, now) == ", 2 days ago"
    assert _age_suffix(None, now - 2 * DAY) == ", 2 days ago"
    assert _age_suffix(0, None) == ""


# ============================================================ the renderers

def test_notes_show_their_age():
    now = time.time()
    out = _render_notes([{
        "id": 1, "kind": "convention", "title": "T", "body": "snake_case",
        "source": "user", "use_count": 0,
        "created_at": now - 5 * DAY, "updated_at": now - 5 * DAY,
    }])
    assert "(user, 5 days ago)" in out
    assert "snake_case" in out
    assert "((" not in out  # ages must not nest inside the source parens


def test_notes_without_a_stamp_render_without_an_invented_age():
    out = _render_notes([{
        "id": 1, "kind": "fact", "title": "T", "body": "payload",
        "source": "agent", "use_count": 0,
    }])
    assert "(agent)" in out
    assert "payload" in out
    assert "days ago" not in out


def test_working_fixes_and_global_insights_show_their_age():
    now = time.time()
    fixes = _render_working_fixes([{
        "id": 1, "from_signature": "sig", "fix_body": "the patch",
        "success_count": 3,
        "created_at": now - 10 * DAY, "updated_at": now - 10 * DAY,
    }])
    assert "used 3x, 10 days ago" in fixes
    assert "the patch" in fixes

    global_block = _render_global_insights([{
        "id": 1, "category": "pitfall", "body": "the lesson", "use_count": 2,
        "created_at": now - 200 * DAY,
    }])
    assert "6 months ago" in global_block
    assert "the lesson" in global_block


# ============================================================ the assembled block

def test_assembled_block_announces_memory_is_not_live_state(tmp_path):
    db = Persistence(tmp_path / "mem.db")
    db.add_project_note("p1", "convention", "Snake case",
                        "Use snake_case.", source="user")

    block = assemble_coder_memory(db, "p1", "do the thing")

    assert block.startswith(MEMORY_DRIFT_WARNING)
    assert "Use snake_case." in block


def test_the_warning_is_not_emitted_when_there_is_no_memory():
    """An empty block stays empty — the warning is not noise."""
    assert assemble_coder_memory(None, "p1", "req") == ""


def test_the_warning_names_the_two_things_that_matter():
    lowered = MEMORY_DRIFT_WARNING.lower()
    assert "at the time" in lowered   # not "now"
    assert "check" in lowered and "code" in lowered


# ============================================================ reflection prompt

def test_reflection_prompt_lists_what_never_to_save():
    prompt = _build_reflection_prompt("digest body")
    assert "Never save:" in prompt
    for forbidden in ("credentials", "tokens", "API keys", "connection strings",
                      "personal data", "identifiers"):
        assert forbidden in prompt, forbidden
    assert "as if it were published" in prompt


def test_reflection_prompt_asks_for_an_observation_not_a_policy():
    prompt = _build_reflection_prompt("digest body")
    assert "what was true then" in prompt
    assert "no longer trust it blindly" in prompt


def test_reflection_prompt_still_demands_the_json_shape():
    """The contract the parser depends on is untouched."""
    prompt = _build_reflection_prompt("digest body")
    assert '"notes"' in prompt and '"skills"' in prompt
    assert "digest body" in prompt
