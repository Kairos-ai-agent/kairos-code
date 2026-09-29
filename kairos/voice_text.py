"""Turn a written answer into something worth hearing.

Agent replies are Markdown: fenced code, tables, bullet lists, links, bold
spans. Read aloud, all of that is noise — the reader hears punctuation and
file paths. This module reduces a reply to the sentences a person would
actually say, and caps the result so a long answer does not become a
monologue.

Two things live here:

* :func:`distill_for_speech` — the filter. It runs on every utterance, so it
  is pure, synchronous and stdlib-only.
* :data:`VOICE_REPLY_DIRECTIVE` — appended to the chat system prompt while
  voice mode is on, so the model writes short, speakable answers in the first
  place instead of leaving the filter to cut a wall of text down to size.

The directive asks the model to put anything long after a ``Details:`` marker;
:func:`distill_for_speech` cuts at that marker, so the screen keeps the full
answer and the ear gets the short one.
"""
from __future__ import annotations

import re

#: Sentences end here. Covers Latin and CJK, because the reply's language is
#: whatever the user is speaking.
SENTENCE_ENDINGS = "。！？!?…."
#: Softer stops, used only when no real sentence ending fits the budget.
CLAUSE_ENDINGS = "；;，,、:："

#: The spoken form is cut at one of these markers (the directive asks the model
#: to put long detail after it). Both English and Chinese, since the model
#: answers in the user's language.
DETAIL_MARKERS = ("details:", "detail:", "详情：", "细节：", "详细：")

DEFAULT_MAX_CHARS = 420

_CODE_FENCE = re.compile(r"```.*?```", re.S)
_CODE_FENCE_UNCLOSED = re.compile(r"```.*\Z", re.S)
_TABLE_BLOCK = re.compile(r"(?:^[ \t]*\|.*\|[ \t]*$\n?){2,}", re.M)
_TABLE_ROW = re.compile(r"^[ \t]*\|.*\|[ \t]*$", re.M)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_AUTOLINK = re.compile(r"<(?:https?|mailto):[^>\s]+>")
_HTML_TAG = re.compile(r"</?[A-Za-z][^>\n]{0,80}>")
_INLINE_CODE = re.compile(r"`([^`\n]*)`")
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.M)
_BULLET = re.compile(r"^[ \t]{0,3}(?:[-*+\u2022\u00b7]|[0-9]{1,3}[.)])[ \t]+", re.M)
_QUOTE = re.compile(r"^[ \t]{0,3}>[ \t]?", re.M)
_RULE = re.compile(r"^[ \t]{0,3}(?:[-*_][ \t]*){3,}$", re.M)
_EMPHASIS = re.compile(r"(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1", re.S)
_URL = re.compile(r"https?://\S+")
_SPACES = re.compile(r"[ \t\u00a0\u3000]+")
_NEWLINES = re.compile(r"\n{2,}")
#: A CJK character — including its punctuation, which is what actually ends up
#: at a boundary ("第一步完成。" + "第二步完成。"). Decides whether two pieces
#: join with a space. Latin text needs one; CJK must not get one.
_CJK = re.compile(
    r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uf900-\ufaff\uff00-\uffef\uac00-\ud7af]"
)


def _strip_markup(text: str) -> str:
    """Reduce Markdown to plain prose. Order matters: fences first."""
    text = _CODE_FENCE.sub(" ", text)
    text = _CODE_FENCE_UNCLOSED.sub(" ", text)
    text = _TABLE_BLOCK.sub("\n", text)
    text = _TABLE_ROW.sub(" ", text)
    text = _IMAGE.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _AUTOLINK.sub(" ", text)
    text = _HTML_TAG.sub(" ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _RULE.sub("\n", text)
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    text = _QUOTE.sub("", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = _URL.sub(" ", text)
    return text


def _cut_at_detail_marker(text: str) -> str:
    lowered = text.lower()
    positions = [lowered.find(m) for m in DETAIL_MARKERS]
    positions = [p for p in positions if p >= 0]
    return text[: min(positions)] if positions else text


def _normalise(text: str) -> str:
    """Collapse runs of whitespace and re-join wrapped lines.

    A line break the model inserted mid-sentence is a space; a line that
    stands on its own (a bullet, a paragraph) is a pause. CJK lines join
    with no separator, Latin ones with a space — decided per boundary, not
    for the text as a whole.
    """
    text = _SPACES.sub(" ", text)
    lines = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if (lines and not _CJK.search(lines[-1][-1])
                and not _CJK.search(line[0]) and not lines[-1].endswith(
                    tuple(SENTENCE_ENDINGS))):
            # Latin text the model wrapped: the newline was a space.
            lines[-1] = f"{lines[-1]} {line}"
        else:
            lines.append(line)
    out = ""
    for line in lines:
        if not out:
            out = line
            continue
        joiner = "" if (_CJK.search(out[-1]) and _CJK.search(line[0])) else " "
        out += joiner + line
    return _NEWLINES.sub("\n", out).strip()


def _cap(text: str, max_chars: int) -> str:
    """Trim to ``max_chars``, preferring a sentence boundary."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    window = text[:max_chars]
    for endings in (SENTENCE_ENDINGS, CLAUSE_ENDINGS):
        for index in range(len(window) - 1, -1, -1):
            if window[index] in endings:
                return window[: index + 1].strip()
    cut = window.rstrip()
    if not _CJK.search(cut) and " " in cut:
        cut = cut.rsplit(" ", 1)[0].rstrip()
    return cut + "…"



def distill_for_speech(text: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Return the spoken form of ``text``.

    Pure and total: never raises, never returns ``None``. An answer that is
    nothing but code or tables comes back as an empty string, which the caller
    should treat as "nothing to say out loud" rather than as an error.
    """
    if not text:
        return ""
    body = _cut_at_detail_marker(text)
    body = _strip_markup(body)
    body = _normalise(body)
    return _cap(body, max_chars)


#: Appended to the chat system prompt while voice mode is on. Kept short: it is
#: prepended to every spoken turn, and the model's own instructions already
#: cover tone and language.
VOICE_REPLY_DIRECTIVE = """
## Voice mode is on

Your reply is read aloud, so write for the ear, not the eye:

- Answer in the first sentence. No preamble, no restating the question.
- Two or three sentences total, unless the user asks for more.
- No Markdown: no headings, bullet lists, tables, code fences or bare URLs.
  Read values out in words.
- If the detail still matters, end with a line starting with "Details:" and
  put the long version there. The interface shows it but does not speak it.
"""
