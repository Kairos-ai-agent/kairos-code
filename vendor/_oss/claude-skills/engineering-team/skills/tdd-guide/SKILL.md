---
name: "tdd-guide"
description: "Test-driven development skill for writing unit tests, generating test fixtures and mocks, analyzing coverage gaps, and guiding red-green-refactor workflows across Jest, Pytest, JUnit, Vitest, and Mocha. Use when the user asks to write tests, improve test coverage, practice TDD, generate mocks or stubs, or mentions testing frameworks like Jest, pytest, or JUnit."
---

# TDD Guide

Test-driven development skill for generating tests, analyzing coverage, and guiding red-green-refactor workflows across Jest, Pytest, JUnit, and Vitest.

## Workflows

### Generate Tests from Code

1. Provide source code (TypeScript, JavaScript, Python, Java)
2. Specify target framework (Jest, Pytest, JUnit, Vitest)
3. Run `test_generator.py` with requirements
4. Review generated test stubs
5. **Validation:** Tests compile and cover happy path, error cases, edge cases

### Analyze Coverage Gaps

1. Generate coverage report from test runner (`npm test -- --coverage`)
2. Run `coverage_analyzer.py` on LCOV/JSON/XML report
3. Review prioritized gaps (P0/P1/P2)
4. Generate missing tests for uncovered paths
5. **Validation:** Coverage meets target threshold (typically 80%+)

### TDD New Feature

1. Write failing test first (RED)
2. Run `tdd_workflow.py --phase red` to validate
3. Implement minimal code to pass (GREEN)
4. Run `tdd_workflow.py --phase green` to validate
5. Refactor while keeping tests green (REFACTOR)
6. **Validation:** All tests pass after each cycle

## Examples

### Test Generation — Input → Output (Pytest)

**Input source function:**
```python
def divide(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b
```

**Command:**
```bash
python scripts/test_generator.py --input math_utils.py --framework pytest
```

**Generated test output:**
```python
import pytest
from math_utils import divide

class TestDivide:
    def test_divide_positive_numbers(self):
        assert divide(10, 2) == 5.0

    def test_divide_negative_numerator(self):
        assert divide(-10, 2) == -5.0

    def test_divide_float_result(self):
        assert divide(1, 3) == pytest.approx(0.333, rel=1e-3)

    def test_divide_by_zero_raises_value_error(self):
        with pytest.raises(ValueError, match="Cannot divide by zero"):
            divide(10, 0)
```

## Key Tools

| Tool | Purpose | Usage |
|------|---------|-------|
| `test_generator.py` | Generate test cases from code/requirements | `python scripts/test_generator.py --input source.py --framework pytest` |
| `coverage_analyzer.py` | Parse and analyze coverage reports | `python scripts/coverage_analyzer.py --report lcov.info --threshold 80` |
| `tdd_workflow.py` | Guide red-green-refactor cycles | `python scripts/tdd_workflow.py --phase red --test test_auth.py` |
| `fixture_generator.py` | Generate test data and mocks | `python scripts/fixture_generator.py --entity User --count 5` |

## Spec-First Workflow

1. **Write or receive a spec** — stored in `specs/<feature>.md`
2. **Extract acceptance criteria** — each criterion becomes one or more test cases
3. **Write failing tests (RED)** — one test per acceptance criterion
4. **Implement minimal code (GREEN)** — satisfy each test in order
5. **Refactor** — clean up while all tests stay green

**Tip:** Number your acceptance criteria in the spec. Reference the number in the test docstring for traceability.

## Property-Based Testing

Property-based testing generates random inputs to verify invariants.

### Python — Hypothesis
```python
from hypothesis import given, strategies as st
from app.serializers import serialize, deserialize

@given(st.text())
def test_roundtrip_serialization(data):
    assert deserialize(serialize(data)) == data
```

## Mutation Testing

Mutation testing modifies your production code and checks whether your tests catch the changes.

| Language | Tool | Command |
|----------|------|---------|
| TypeScript/JavaScript | **Stryker** | `npx stryker run` |
| Python | **mutmut** | `mutmut run --paths-to-mutate=src/` |
| Java | **PIT** | `mvn org.pitest:pitest-maven:mutationCoverage` |

**Recommendation:** Run mutation testing on critical paths (auth, payments, data processing) even if overall coverage is high. Target 85%+ mutation score on P0 modules.

## Limitations

- Unit test focus; integration/E2E need different patterns
- Best for TypeScript, JavaScript, Python, Java
- LCOV/JSON/XML coverage formats only
