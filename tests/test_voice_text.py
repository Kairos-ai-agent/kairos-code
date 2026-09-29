"""Voice-mode text handling: the spoken form of a written answer.

The point of these tests is that what reaches the speaker is *sayable*. A
reply full of fenced code, tables and bare URLs is normal on screen and
unusable in the ear, so every rule here has a matching case.
"""
from __future__ import annotations

import pytest

from kairos.voice_text import (
    DEFAULT_MAX_CHARS,
    VOICE_REPLY_DIRECTIVE,
    distill_for_speech,
)


# ---------------------------------------------------------------------------
# Things that must not be read aloud
# ---------------------------------------------------------------------------


def test_code_fences_are_dropped():
    text = "Run this.\n\n```python\nprint('hi')\n```\n\nDone."
    out = distill_for_speech(text)
    assert "print" not in out
    assert "```" not in out
    assert out.startswith("Run this.")
    assert "Done." in out


def test_unclosed_code_fence_is_dropped_too():
    out = distill_for_speech("Here goes:\n\n```python\nfor i in range(3):\n")
    assert "range" not in out


def test_tables_are_dropped():
    text = (
        "Three files changed.\n\n"
        "| file | lines |\n"
        "| --- | --- |\n"
        "| a.py | 12 |\n"
        "| b.py | 3 |\n\n"
        "All tests pass."
    )
    out = distill_for_speech(text)
    assert "a.py" not in out
    assert "|" not in out
    assert "Three files changed." in out
    assert "All tests pass." in out


def test_links_keep_their_label_and_lose_their_address():
    out = distill_for_speech(
        "See [the docs](https://example.com/docs?a=1) and https://example.com/x."
    )
    assert "the docs" in out
    assert "example.com" not in out


def test_images_and_autolinks_are_dropped():
    out = distill_for_speech("![chart](chart.png) and <https://example.com> and text")
    assert "chart" not in out
    assert "example.com" not in out
    assert "text" in out


# ---------------------------------------------------------------------------
# Markdown noise that should be unwrapped, not deleted
# ---------------------------------------------------------------------------


def test_headings_bullets_and_quotes_lose_their_markers():
    text = "## Result\n\n- first thing\n- second thing\n\n> a quote"
    out = distill_for_speech(text)
    assert "#" not in out
    assert "- " not in out
    assert ">" not in out
    assert "first thing" in out and "second thing" in out and "a quote" in out


def test_emphasis_markers_are_unwrapped():
    out = distill_for_speech("This is **very** important and _also_ fine.")
    assert "**" not in out and "_" not in out
    assert "very" in out and "also" in out


def test_inline_code_keeps_its_contents():
    out = distill_for_speech("Set `KAIROS_SENTINEL=off` first.")
    assert "KAIROS_SENTINEL=off" in out
    assert "`" not in out


def test_horizontal_rules_are_dropped():
    out = distill_for_speech("Above.\n\n---\n\nBelow.")
    assert "---" not in out


# ---------------------------------------------------------------------------
# The Details: hand-off between screen and ear
# ---------------------------------------------------------------------------


def test_detail_marker_cuts_the_spoken_form():
    text = (
        "Fixed it by pinning the dependency.\n\n"
        "Details: the resolver picked 4.2.0 because the lockfile was stale, "
        "and 4.2.0 dropped support for Python 3.9, so I regenerated it."
    )
    out = distill_for_speech(text)
    assert out == "Fixed it by pinning the dependency."
    assert "lockfile" not in out


@pytest.mark.parametrize("marker", ["Details:", "details:", "DETAILS:", "详情：", "细节："])
def test_detail_marker_is_case_and_language_insensitive(marker):
    out = distill_for_speech(f"Short answer. {marker} and then a long tail that "
                            "should never be spoken out loud, not ever.")
    assert out == "Short answer."


def test_no_marker_leaves_the_text_alone():
    out = distill_for_speech("Just one line.")
    assert out == "Just one line."


def test_directive_and_filter_agree_on_the_marker():
    """The model is told to write a marker the filter actually cuts on."""
    assert "Details:" in VOICE_REPLY_DIRECTIVE
    assert distill_for_speech("Answer. Details: tail.") == "Answer."


# ---------------------------------------------------------------------------
# Length: short by default, never cut mid-word
# ---------------------------------------------------------------------------


def test_cap_prefers_a_sentence_boundary():
    text = "First sentence is here. " * 40
    out = distill_for_speech(text, max_chars=60)
    assert len(out) <= 60
    assert out.endswith(".")


def test_cap_works_on_cjk_without_spaces():
    text = "这是第一句话。这是第二句话。这是第三句话。" * 10
    out = distill_for_speech(text, max_chars=30)
    assert len(out) <= 30
    assert out.endswith("。")


def test_cap_without_any_boundary_cuts_on_a_word():
    out = distill_for_speech("supercalifragilistic " * 20, max_chars=40)
    assert len(out) <= 41
    assert out.endswith("…")
    assert not out.endswith(" …")
    assert "supercalifragilistic" in out


def test_default_cap_is_generous_enough_for_two_sentences():
    text = "The build passes. All 2928 tests are green."
    assert distill_for_speech(text) == text
    assert DEFAULT_MAX_CHARS >= len(text)


# ---------------------------------------------------------------------------
# Totality: this runs on every utterance, so it must never raise
# ---------------------------------------------------------------------------


def test_empty_and_whitespace_input():
    assert distill_for_speech("") == ""
    assert distill_for_speech("   \n\n  ") == ""


def test_pure_code_answer_is_empty_not_an_error():
    assert distill_for_speech("```python\nprint('only code')\n```") == ""
    assert distill_for_speech("| a | b |\n| --- | --- |") == ""


@pytest.mark.parametrize("junk", [
    "`" * 200,
    "**" * 100,
    "[" * 80 + "(" * 80,
    "```",
    "\n\n\n",
    "<div",
    ">",
    "- ",
])
def test_hostile_markup_does_not_raise(junk):
    assert isinstance(distill_for_speech(junk), str)


def test_cjk_lines_join_without_a_space():
    out = distill_for_speech("第一步完成。\n第二步完成。")
    assert " " not in out
    assert out == "第一步完成。第二步完成。"


def test_latin_wrapped_lines_join_with_a_space():
    out = distill_for_speech("This line was wrapped by\nthe model mid sentence.")
    assert out == "This line was wrapped by the model mid sentence."
