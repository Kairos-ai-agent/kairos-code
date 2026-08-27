---
name: code-reviewer
description: Code review automation for TypeScript, JavaScript, Python, Go, Swift, Kotlin, C#, .NET, Java, C, C++, Rust, Ruby, PHP, and Dart/Flutter. Analyzes PRs for complexity and risk, checks code quality for SOLID violations and code smells, generates review reports. Use when reviewing pull requests, analyzing code quality, identifying issues, generating review checklists.
priority: 0.6
when:
  keyword: ['code', 'review', 'automation', 'typescript', 'javascript', 'python']
---

# Code Reviewer

Automated code review tools for analyzing pull requests, detecting code quality issues, and generating review reports.

## Tools

### PR Analyzer

Analyzes git diff between branches to assess review complexity and identify risks.

```bash
python scripts/pr_analyzer.py /path/to/repo
python scripts/pr_analyzer.py . --base main --head feature-branch
python scripts/pr_analyzer.py /path/to/repo --json
```

**What it detects (universal):**
- Hardcoded secrets (passwords, API keys, tokens, connection strings)
- SQL / query injection patterns
- Debug statements left in production code
- Lint / analyzer suppression annotations
- TODO/FIXME comments

**Output includes:**
- Complexity score (1-10)
- Risk categorization (critical, high, medium, low)
- File prioritization for review order
- Commit message validation

### Code Quality Checker

Analyzes source code for structural issues, code smells, and SOLID violations.

```bash
python scripts/code_quality_checker.py /path/to/code
python scripts/code_quality_checker.py . --language java
python scripts/code_quality_checker.py /path/to/code --json
```

**Universal thresholds:**

| Issue | Threshold |
|-------|-----------|
| Long function | >50 lines |
| Large file | >500 lines |
| God class | >20 methods |
| Too many params | >5 |
| Deep nesting | >4 levels |
| High complexity | >10 branches |

### Review Report Generator

Combines PR analysis and code quality findings into structured review reports.

```bash
python scripts/review_report_generator.py /path/to/repo
python scripts/review_report_generator.py . --format markdown --output review.md
```

**Verdicts:**

| Score | Verdict |
|-------|---------|
| 90+ with no high issues | Approve |
| 75+ with ≤2 high issues | Approve with suggestions |
| 50-74 | Request changes |
| <50 or critical issues | Block |

## Adding a New Language

To extend to a new language:

1. Create `languages/<name>.md` with sections: PR Analyzer Signals, Code Quality Checks, Security, Async, Resource Management, Exception Handling, Performance, Idioms.
2. Add the extension row to the dispatch table.
3. Add the extensions to `LANGUAGE_EXTENSIONS` in `scripts/code_quality_checker.py`.
4. Add `function` / `class` / `method` regex entries for the language.
5. Add `assets/sample_<name>_smells.<ext>` + `_clean` fixtures.

## Regression Fixtures

Labelled fixtures live in `assets/` with their committed `--json` output in `expected_outputs/`. Drift signals a behavior change in the analyzer:

```bash
python scripts/code_quality_checker.py assets/sample_java_smells.java --json \
  | diff - expected_outputs/sample_java_smells_quality.json
```


<!--
Adapted from alirezarezvani/claude-skills (MIT).
Upstream: https://github.com/alirezarezvani/claude-skills/tree/main/engineering-team/skills/code-reviewer
Adaptation: scripts/adapt_community_skills.py
-->
