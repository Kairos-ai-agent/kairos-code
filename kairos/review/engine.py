"""Review Engine - performs code review using LLM agents."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from kairos.llm.base import LLMConfig, LLMMessage
from kairos.llm.provider_registry import create_provider

logger = logging.getLogger(__name__)

class ReviewIssue(BaseModel):
    """A single issue found during review."""

    category: str  # CRITICAL, MAJOR, MINOR, SUGGESTION
    file: str = ""
    line: int = 0
    description: str
    code_snippet: str = ""
    suggestion: str = ""

class FileReview(BaseModel):
    """Review result for a single file."""

    file_path: str
    issues: List[ReviewIssue] = []
    summary: str = ""
    score: int = 100  # 0-100

class ReviewReport(BaseModel):
    """Complete review report for a project."""

    project_path: str
    files_reviewed: int = 0
    total_issues: int = 0
    critical_count: int = 0
    major_count: int = 0
    minor_count: int = 0
    suggestion_count: int = 0
    overall_score: int = 100
    file_reviews: List[FileReview] = []
    summary: str = ""

REVIEW_PROMPT = """You are a senior code reviewer. Review the following code and identify issues.

Code to review (file: {file_path}):
```
{code}
```

Review for:
1. Security vulnerabilities (OWASP Top 10)
2. Performance issues
3. Code quality and readability
4. Error handling
5. Best practices

For each issue found, respond in this JSON format:
[
  {{
    "category": "CRITICAL|MAJOR|MINOR|SUGGESTION",
    "description": "Description of the issue",
    "code_snippet": "the problematic code",
    "suggestion": "how to fix it"
  }}
]

If no issues found, return an empty array [].
Be thorough but constructive. Focus on real problems, not style preferences."""

class ReviewEngine:
    """Code review engine using LLM-powered analysis."""

    def __init__(self, llm_config: LLMConfig):
        self._llm = create_provider(llm_config)

    async def review_file(self, file_path: str, code: str) -> FileReview:
        """Review a single file."""
        prompt = REVIEW_PROMPT.format(file_path=file_path, code=code[:8000])  # Limit code size

        messages = [LLMMessage(role="user", content=prompt)]
        response = await self._llm.complete(messages)

        issues = []
        try:
            content = response.content
            issues_data = None

            # Strategy 1: ```json ... ``` or ``` ... ```
            m = re.search(r"```(?:json|JSON)?\s*(\[.*?\])\s*```", content, re.DOTALL)
            if m:
                try:
                    issues_data = json.loads(m.group(1))
                except (json.JSONDecodeError, ValueError):
                    logger.debug("Fenced review JSON failed to parse", exc_info=True)

            # Strategy 2: bare JSON array (first '[' to its matching ']').
            # Non-greedy + bracket-aware so we don't span across unrelated [] in log lines.
            # Note: this regex has no capture group, so use m.group(0).
            # Iterate through all candidates — an earlier match may be a
            # non-JSON bracket list (e.g. log timestamps), so keep scanning
            # until one parses as a list-of-dicts (the schema of review issues)
            # or we run out of brackets.
            if issues_data is None:
                for m in re.finditer(r"\[(?:[^\[\]]|\[[^\[\]]*\])*\]", content, re.DOTALL):
                    try:
                        candidate = json.loads(m.group(0))
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if (isinstance(candidate, list) and candidate
                            and all(isinstance(it, dict) for it in candidate)):
                        issues_data = candidate
                        break

            if issues_data is not None and isinstance(issues_data, list):
                for item in issues_data:
                    if not isinstance(item, dict):
                        continue
                    try:
                        issues.append(ReviewIssue(
                            category=item.get("category", "MINOR"),
                            file=item.get("file", file_path),
                            line=item.get("line", 0),
                            description=item.get("description", ""),
                            code_snippet=item.get("code_snippet", ""),
                            suggestion=item.get("suggestion", ""),
                        ))
                    except (KeyError, TypeError, ValueError) as exc:
                        logger.debug("Skipping malformed review item: %s", exc)
            else:
                logger.warning("Failed to parse review JSON for %s", file_path)

        except Exception:
            logger.debug("Failed to parse review for %s", file_path, exc_info=True)

        critical = sum(1 for i in issues if i.category == "CRITICAL")
        major = sum(1 for i in issues if i.category == "MAJOR")
        minor = sum(1 for i in issues if i.category == "MINOR")
        score = max(0, 100 - critical * 20 - major * 10 - minor * 3)

        return FileReview(
            file_path=file_path,
            issues=issues,
            summary=f"Found {len(issues)} issues ({critical} critical, {major} major, {minor} minor)",
            score=score,
        )

    async def review_project(self, project_path: str, file_extensions: Optional[List[str]] = None) -> ReviewReport:
        """Review all code files in a project."""
        if file_extensions is None:
            file_extensions = [".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java"]

        project = Path(project_path)
        report = ReviewReport(project_path=project_path)

        # Find all code files
        code_files = []
        for ext in file_extensions:
            code_files.extend(project.rglob(f"*{ext}"))

        # Limit to reasonable number
        code_files = code_files[:50]

        for file_path in code_files:
            try:
                code = file_path.read_text(encoding="utf-8", errors="ignore")
                if len(code.strip()) < 10:
                    continue

                relative_path = str(file_path.relative_to(project))
                file_review = await self.review_file(relative_path, code)
                report.file_reviews.append(file_review)
                report.files_reviewed += 1
                report.total_issues += len(file_review.issues)
                report.critical_count += sum(1 for i in file_review.issues if i.category == "CRITICAL")
                report.major_count += sum(1 for i in file_review.issues if i.category == "MAJOR")
                report.minor_count += sum(1 for i in file_review.issues if i.category == "MINOR")
                report.suggestion_count += sum(1 for i in file_review.issues if i.category == "SUGGESTION")
            except Exception:
                logger.debug("Failed to review file %s", file_path, exc_info=True)
                continue

        # Calculate overall score
        if report.files_reviewed > 0:
            scores = [fr.score for fr in report.file_reviews]
            report.overall_score = int(sum(scores) / len(scores))

        report.summary = (
            f"Reviewed {report.files_reviewed} files. "
            f"Found {report.total_issues} issues "
            f"({report.critical_count} critical, {report.major_count} major, "
            f"{report.minor_count} minor, {report.suggestion_count} suggestions). "
            f"Overall score: {report.overall_score}/100"
        )

        return report
