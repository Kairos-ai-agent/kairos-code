"""The same note written twice is one note.

Reflections re-read a digest that overlaps the previous one, so the same lesson
arrives again on the next pass; users re-add a note they forgot writing. Before
this, each arrival was a new row, and the prompt filled with near-identical
entries — while "most-used first" stopped meaning anything, because a repeated
lesson and a confirmed one looked the same.

Dedupe happens in code, not in the schema: a UNIQUE constraint would have to be
migrated onto a table that already contains duplicates.
"""
from __future__ import annotations

from kairos.core.persistence import Persistence


def _db(tmp_path) -> Persistence:
    return Persistence(tmp_path / "notes.db")


def test_writing_the_same_note_twice_keeps_one_row(tmp_path):
    db = _db(tmp_path)

    first = db.add_project_note("p1", "pitfall", "Careful", "Body text here.")
    second = db.add_project_note("p1", "pitfall", "Careful", "Body text here.")

    assert first == second
    rows = db.list_project_notes("p1")
    assert len(rows) == 1
    # The repeat is evidence, not noise: it counts as one more use.
    assert rows[0]["use_count"] == 1


def test_a_different_body_is_a_different_note(tmp_path):
    db = _db(tmp_path)
    db.add_project_note("p1", "pitfall", "Careful", "First version.")
    db.add_project_note("p1", "pitfall", "Careful", "Second version.")

    assert len(db.list_project_notes("p1")) == 2


def test_kind_and_title_still_separate_notes(tmp_path):
    db = _db(tmp_path)
    db.add_project_note("p1", "pitfall", "Careful", "Same body.")
    db.add_project_note("p1", "convention", "Careful", "Same body.")
    db.add_project_note("p1", "pitfall", "Different", "Same body.")

    assert len(db.list_project_notes("p1")) == 3


def test_the_same_note_in_another_project_stays_separate(tmp_path):
    db = _db(tmp_path)
    db.add_project_note("p1", "fact", "T", "Body.")
    db.add_project_note("p2", "fact", "T", "Body.")

    assert len(db.list_project_notes("p1")) == 1
    assert len(db.list_project_notes("p2")) == 1


def test_repeats_float_to_the_top_of_the_list(tmp_path):
    """The ordering the prompt relies on: a confirmed note outranks a fresh one."""
    db = _db(tmp_path)
    db.add_project_note("p1", "convention", "Fresh", "Seen once.")
    to_confirm = db.add_project_note("p1", "convention", "Confirmed", "Seen twice.")
    db.add_project_note("p1", "convention", "Confirmed", "Seen twice.")

    rows = db.list_project_notes("p1")
    assert rows[0]["title"] == "Confirmed"
    assert rows[0]["id"] == to_confirm
    assert rows[0]["use_count"] == 1


def test_truncation_happens_before_dedupe(tmp_path):
    """Two bodies that differ only past the 2000-char cut are the same note."""
    db = _db(tmp_path)
    long_a = "x" * 2000 + " tail A"
    long_b = "x" * 2000 + " tail B"

    db.add_project_note("p1", "fact", "Long", long_a)
    db.add_project_note("p1", "fact", "Long", long_b)

    assert len(db.list_project_notes("p1")) == 1
