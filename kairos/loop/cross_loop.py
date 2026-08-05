"""Cross-loop memory, pattern detection, and Coder temperature policy."""
from __future__ import annotations

import json
import logging
from typing import List

from kairos.loop.gates import APPROVE_SCORE_THRESHOLD

logger = logging.getLogger(__name__)

MAX_HISTORY_ROUNDS = 5
CODER_TEMPERATURE_START = 0.7
CODER_TEMPERATURE_END = 0.15
CODER_TEMPERATURE_DECAY_ROUNDS = 15


def coder_temperature_for_round(round_no: int) -> float:
    """Linearly decay exploration temperature, clamped at both ends."""
    if round_no <= 1:
        return CODER_TEMPERATURE_START
    if round_no >= CODER_TEMPERATURE_DECAY_ROUNDS:
        return CODER_TEMPERATURE_END
    span = CODER_TEMPERATURE_DECAY_ROUNDS - 1
    progress = (round_no - 1) / max(1, span)
    return CODER_TEMPERATURE_START + (
        CODER_TEMPERATURE_END - CODER_TEMPERATURE_START
    ) * progress


def load_history_digest(persistence, project_id: str) -> str:
    """Load a bounded summary of recent rounds and repeated patterns."""
    if persistence is None:
        return ""
    try:
        rounds = persistence.load_loop_rounds(project_id, limit=MAX_HISTORY_ROUNDS)
    except Exception:
        logger.debug("Failed to load loop history for %s", project_id, exc_info=True)
        return ""
    if not rounds:
        return ""
    parts = []
    for round_data in rounds:
        verdict = "approved" if round_data.get("approve") else "rejected"
        score = round_data.get("score") or 0
        summary = (round_data.get("review_summary") or "")[:200]
        parts.append(
            f"- R{round_data.get('round')} {verdict} (score {score}): {summary}"
        )
    digest = "Previous loop history on this project:\n" + "\n".join(parts)
    advisory = detect_cross_loop_patterns(rounds)
    return digest + ("\n\n" + advisory if advisory else "")


def detect_cross_loop_patterns(rounds: List[dict]) -> str:
    """Detect repeated categories, flat scores, hot files, and infra streaks."""
    if len(rounds or []) < 3:
        return ""
    parsed = []
    for round_data in rounds:
        raw_review = round_data.get("review_json")
        if not raw_review:
            continue
        try:
            review = json.loads(raw_review) if isinstance(raw_review, str) else raw_review
        except (TypeError, ValueError):
            continue
        if isinstance(review, dict):
            parsed.append({**round_data, "_review": review})
    if len(parsed) < 3:
        return ""

    advisories = []

    def dominant_category(review: dict) -> str:
        counts = {}
        for issue in review.get("issues") or []:
            category = str(issue.get("category") or "unknown").lower()
            counts[category] = counts.get(category, 0) + 1
        return max(counts.items(), key=lambda item: item[1])[0] if counts else ""

    streak_category = ""
    streak_length = 0
    best_category = ""
    best_length = 0
    for entry in parsed:
        category = dominant_category(entry["_review"])
        if category and category == streak_category:
            streak_length += 1
        else:
            streak_category = category
            streak_length = 1 if category else 0
        if streak_length >= 3 and streak_length > best_length:
            best_category = category
            best_length = streak_length
    if best_category:
        advisories.append(
            f"- {best_category} has been the dominant issue category for "
            f"{best_length} consecutive rounds. Try a smaller diff, a new fix "
            "angle, or ask the user before repeating the same approach."
        )

    scores = [entry.get("score") or 0 for entry in parsed[-3:]]
    if (
        all(score > 0 for score in scores)
        and max(scores) - min(scores) <= 2
        and scores[-1] < APPROVE_SCORE_THRESHOLD
    ):
        advisories.append(
            f"- Score has stayed near {scores[-1]} for three rounds. Re-plan "
            "or reduce scope instead of repeating the current implementation."
        )

    file_counts = {}
    for entry in parsed:
        seen_this_round = set()
        for issue in entry["_review"].get("issues") or []:
            path = issue.get("file")
            if path:
                seen_this_round.add(path)
        for path in seen_this_round:
            file_counts[path] = file_counts.get(path, 0) + 1
    hot_files = sorted(
        ((path, count) for path, count in file_counts.items() if count >= 3),
        key=lambda item: -item[1],
    )[:3]
    if hot_files:
        rendered = ", ".join(f"{path} ({count}x)" for path, count in hot_files)
        advisories.append(
            f"- The same files recur across rounds: {rendered}. Check for a "
            "deeper structural cause before patching symptoms again."
        )

    failure_modes = [
        entry["_review"].get("_failure_mode") or "" for entry in parsed[-3:]
    ]
    if any(failure_modes) and all(
        mode in {"infra_fail", "parse_fail", "tool_limit"}
        for mode in failure_modes
    ):
        advisories.append(
            "- The last three rounds failed in review infrastructure. Do not "
            "make speculative code changes until the Reviewer recovers."
        )

    return "[CROSS-LOOP ADVISORY] " + " ".join(advisories) if advisories else ""