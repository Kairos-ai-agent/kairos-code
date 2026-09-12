"""Tests for the Gate Report (kairos/gate_report.py + api/routes/gate.py).

The report is the product's receipt — "you don't ship what the agent didn't
pass" — so the assertions here are about *truthfulness* as much as formatting:

  * the numbers must match what the loop persisted (rounds, rejections, scores);
  * an empty or unknown project must not crash and must not invent a verdict;
  * the HTML must be a single self-contained file (a PR attachment cannot
    fetch a CDN);
  * both languages must live in that one file (the in-place EN/中文 toggle);
  * the cost ledger must not double-count (entries hit both the in-process
    buffer and the JSONL);
  * the eval section must only appear when runs actually exist — no claiming
    "0 regressions" that was never measured.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kairos import cost as cost_mod
from kairos import gate_report as gr
from kairos.core.persistence import Persistence
from api.app import app

PID = "demo-relay-0001"
SID = "sess-9f31"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
def _seed_db(db_path: Path, *, last_round_approves: bool = True) -> str:
    """Create the real schema and a 3-round story: reject(2 bugs) → reject(1) → verdict."""
    Persistence(db_path)
    now = time.time()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO projects (id,name,description,workspace,work_dir,requirements,"
            "status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (PID, "demo-relay", "Relay retry policy", "E:/tmp", "E:/tmp/demo-relay",
             "Fix the retry backoff in relay.py", "completed", now - 600, now),
        )
        rounds = [
            (1, "Implemented backoff.", 60, 0, {
                "has_bugs": True, "summary": "Two bugs found.",
                "bugs": [
                    {"file": "relay.py", "line": 42,
                     "description": "sleep goes negative", "fix": "clamp the exponent"},
                    {"file": "relay.py", "line": 55,
                     "description": "retries one extra time | with a pipe", "fix": "range(max_retries)"},
                ]}, now - 540),
            (2, "Clamped the delay.", 80, 0, {
                "has_bugs": True, "summary": "One bug left.",
                "bugs": [{"file": "relay.py", "line": 61,
                          "description": "Retry-After is ignored", "fix": "apply the header"}]},
             now - 500),
            (3, "Applied Retry-After.", 100 if last_round_approves else 60,
             1 if last_round_approves else 0,
             {"has_bugs": not last_round_approves,
              "summary": "No bugs found." if last_round_approves else "Still broken.",
              "bugs": [] if last_round_approves else [
                  {"file": "relay.py", "line": 70,
                   "description": "still ignores Retry-After | breaks on 503",
                   "fix": "apply the header"}]},
             now - 460),
        ]
        for i, (rnd, summary, score, approve, review, ts) in enumerate(rounds):
            conn.execute(
                "INSERT INTO loop_rounds (project_id,session_id,round,coder_summary,"
                "review_summary,review_json,score,approve,created_at,insert_order) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (PID, SID, rnd, summary, review["summary"],
                 json.dumps(review, ensure_ascii=False), score, approve, ts, i + 1),
            )
            conn.execute(
                "INSERT INTO loop_checkpoints (project_id,session_id,round,sha,score,"
                "approved,summary,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (PID, SID, rnd, f"abcdef12345{i}", score, approve, summary, ts),
            )
        conn.execute(
            "INSERT INTO working_fixes (project_id,from_signature,fix_body,issue_category,"
            "success_count,failure_count,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (PID, "negative sleep", "clamp the exponent", "correctness", 2, 0, now, now),
        )
        conn.execute(
            "INSERT INTO project_skills (project_id,name,triggers,body,confidence,"
            "use_count,success_count,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (PID, "retry-backoff", '["retry"]', "Clamp derived delays.", 0.7, 3, 2, now, now),
        )
        conn.execute(
            "INSERT INTO project_notes (project_id,kind,title,body,source,use_count,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (PID, "convention", "Retry policy lives in relay.py", "…", "loop", 1, now, now),
        )
    return PID


@pytest.fixture
def seeded_db(tmp_path):
    """A project with a full 3-round history, plus an isolated cost ledger."""
    db = tmp_path / "kairos.db"
    _seed_db(db)
    cost_mod.set_log_path(tmp_path / "cost.jsonl")
    yield db
    cost_mod.set_log_path(Path("data") / "cost.jsonl")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient whose app reads a DB in tmp_path (the production layout)."""
    _seed_db(tmp_path / "kairos.db")
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    cost_mod.set_log_path(tmp_path / "cost.jsonl")
    return TestClient(app)


# ---------------------------------------------------------------------------
# Collection: the numbers must match the persisted rounds
# ---------------------------------------------------------------------------
def test_collect_matches_persisted_rounds(seeded_db):
    report = gr.collect(PID, db_path=seeded_db)
    assert report.state() == "passed"
    assert report.rounds_total == 3
    assert report.rejected_rounds == 2
    assert report.first_pass is False
    assert report.score_curve == [60, 80, 100]
    assert report.final_score == 100
    assert report.issues_total == 3
    assert report.issues_by_severity() == {"BUG": 3}
    assert report.session_id == SID
    assert report.project_name == "demo-relay"


def test_open_issues_only_from_the_last_round(tmp_path):
    db = tmp_path / "kairos.db"
    _seed_db(db, last_round_approves=False)
    report = gr.collect(PID, db_path=db)
    assert report.state() == "rejected"
    assert report.first_pass is False
    assert len(report.open_issues) == 1
    issue = report.open_issues[0]
    assert issue.file == "relay.py" and issue.line == 70
    assert issue.location == "relay.py:70"
    assert issue.severity == "BUG"
    assert "Retry-After" in issue.description


def test_legacy_rubric_verdict_still_parses(tmp_path):
    """Sessions written before the reviewer was simplified must still render."""
    db = tmp_path / "kairos.db"
    Persistence(db)
    legacy = {"approve": False, "score": 55, "summary": "Rubric review",
              "issues": [{"category": "correctness", "severity": "MAJOR",
                          "file": "relay.py", "line": 12,
                          "description": "off-by-one", "fix_instruction": "use <="}]}
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO projects (id,name,status) VALUES (?,?,?)", (PID, "legacy", "ready"))
        conn.execute(
            "INSERT INTO loop_rounds (project_id,session_id,round,coder_summary,"
            "review_summary,review_json,score,approve,created_at,insert_order) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (PID, SID, 1, "first try", "Rubric review", json.dumps(legacy), 55, 0,
             time.time(), 1))
    report = gr.collect(PID, db_path=db)
    assert report.state() == "rejected"
    issues = report.rounds[0].issues
    assert len(issues) == 1
    assert issues[0].severity == "MAJOR"
    assert issues[0].fix == "use <="
    assert issues[0].category == "correctness"


def test_empty_project_does_not_invent_a_verdict(tmp_path):
    db = tmp_path / "kairos.db"
    Persistence(db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO projects (id,name,status) VALUES (?,?,?)", (PID, "blank", "ready"))
    report = gr.collect(PID, db_path=db)
    assert report.state() == "empty"
    assert report.rounds_total == 0
    assert report.passed is False          # not "passed by default"
    assert report.final_score == 0
    assert "还没有" in report.verdict_text("zh")
    md = report.to_markdown("en")
    assert "No rounds have run yet" in md
    assert "Traceback" not in md
    assert report.to_html("en").startswith("<!DOCTYPE html>")


def test_corrupt_review_json_does_not_break_collection(tmp_path):
    db = tmp_path / "kairos.db"
    Persistence(db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO projects (id,name,status) VALUES (?,?,?)", (PID, "bad", "ready"))
        conn.execute(
            "INSERT INTO loop_rounds (project_id,session_id,round,coder_summary,"
            "review_summary,review_json,score,approve,created_at,insert_order) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (PID, SID, 1, "x", "y", "{not json", 40, 0, time.time(), 1))
    report = gr.collect(PID, db_path=db)
    assert report.rounds_total == 1
    assert report.rounds[0].issues == []
    assert report.state() == "rejected"


def test_find_project_by_id_prefix_and_name(seeded_db):
    assert gr.find_project(PID, db_path=seeded_db)["id"] == PID
    assert gr.find_project("demo-relay", db_path=seeded_db)["id"] == PID
    assert gr.find_project("demo-rel", db_path=seeded_db)["id"] == PID
    assert gr.find_project("nope-nothing", db_path=seeded_db) is None


def test_missing_db_degrades_to_empty_report(tmp_path):
    report = gr.collect(PID, db_path=tmp_path / "does-not-exist.db")
    assert report.state() == "empty"
    assert report.rounds_total == 0
    assert gr.find_project(PID, db_path=tmp_path / "does-not-exist.db") is None


# ---------------------------------------------------------------------------
# Cost ledger: no double counting
# ---------------------------------------------------------------------------
def _entry(call_id: str, usd: float, model: str = "deepseek-chat") -> dict:
    return {"timestamp": time.time(), "model": model, "provider": "deepseek",
            "prompt_tokens": 100, "completion_tokens": 50, "cost_usd": usd,
            "duration_ms": 1200, "call_id": call_id}


def test_cost_jsonl_is_deduped(tmp_path, monkeypatch):
    db = tmp_path / "kairos.db"
    _seed_db(db)
    log = tmp_path / "cost.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in [
        _entry("c1", 0.01), _entry("c2", 0.02), _entry("c2", 0.02),  # duplicate call_id
        _entry("c3", 0.03, model="gpt-4o-mini"),
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(cost_mod, "_BUFFER", [])   # ignore other tests' in-process calls
    cost_mod.set_log_path(log)
    report = gr.collect(PID, db_path=db)
    assert report.cost_calls == 3
    assert report.cost_usd == pytest.approx(0.06)
    assert report.cost_by_model["deepseek-chat"]["calls"] == 2
    assert report.cost_by_model["gpt-4o-mini"]["cost_usd"] == pytest.approx(0.03)
    cost_mod.set_log_path(Path("data") / "cost.jsonl")


def test_cost_jsonl_tolerates_garbage_lines(tmp_path, monkeypatch):
    db = tmp_path / "kairos.db"
    _seed_db(db)
    log = tmp_path / "cost.jsonl"
    log.write_text("not json\n" + json.dumps(_entry("c1", 0.05)) + "\n[1,2,3]\n",
                   encoding="utf-8")
    monkeypatch.setattr(cost_mod, "_BUFFER", [])
    cost_mod.set_log_path(log)
    report = gr.collect(PID, db_path=db)
    assert report.cost_calls == 1
    assert report.cost_usd == pytest.approx(0.05)
    cost_mod.set_log_path(Path("data") / "cost.jsonl")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def test_markdown_contains_the_receipt(seeded_db):
    md = gr.collect(PID, db_path=seeded_db).to_markdown("en")
    assert "Kairos Gate Report" in md
    assert "| Round | Score | Verdict | Issues | Coder summary |" in md
    assert "| R3 | 100 | approved | 0 |" in md
    assert "60 → 100" in md                # the score sparkline tail
    assert "`" in md
    assert "working fixes" in md
    assert "Checkpoints (rollback points)" in md
    assert md.count("|") > 20


def test_markdown_escapes_pipes_in_user_text(tmp_path):
    db = tmp_path / "kairos.db"
    _seed_db(db, last_round_approves=False)
    md = gr.collect(PID, db_path=db).to_markdown("en")
    # the open issue's description contains a literal "|" — it must not split the cell
    assert "Retry-After \\| breaks on 503" in md
    # every table row must have a consistent cell count
    for line in md.splitlines():
        if line.startswith("| ") and "---" not in line:
            assert line.count("|") >= 4


def test_html_is_self_contained(seeded_db):
    html = gr.collect(PID, db_path=seeded_db).to_html("en")
    assert html.startswith("<!DOCTYPE html>")
    assert "<style>" in html and "</html>" in html
    assert "http://" not in html and "https://" not in html
    assert "<script src" not in html and "<link" not in html
    assert "<img" not in html                       # no external assets at all
    assert "@media (prefers-color-scheme: dark)" in html


def test_html_carries_both_languages(seeded_db):
    report = gr.collect(PID, db_path=seeded_db)
    html = report.to_html("zh")
    assert 'data-en="Rounds"' in html and 'data-zh="轮次"' in html
    assert 'data-en="Verdict"' in html and 'data-zh="结论"' in html
    assert 'id="btn-en"' in html and 'id="btn-zh"' in html
    assert 'html lang="zh"' in html
    en_html = report.to_html("en")
    assert 'data-zh="轮次"' in en_html               # toggle data survives in en too


def test_html_english_render_has_no_visible_chinese(seeded_db):
    """The EN render must carry Chinese only inside data-zh attributes / the 中文 button.

    Regression guard: h1, the verdict sentence and the footer were once rendered in
    one language only, so clicking EN left Chinese on screen.
    """
    html = gr.collect(PID, db_path=seeded_db).to_html("en")
    stripped = re.sub(r'data-zh="[^"]*"', "", html)
    stripped = re.sub(r"<script.*?</script>", "", stripped, flags=re.S)
    stripped = re.sub(r"<style.*?</style>", "", stripped, flags=re.S)
    stripped = re.sub(r"<button[^>]*>.*?</button>", "", stripped, flags=re.S)
    leftover = re.search(r"[\u4e00-\u9fff][^\n]{0,60}", stripped)
    assert leftover is None, f"visible Chinese in EN render: {leftover.group(0)!r}"


def test_html_zh_render_has_no_english_labels(seeded_db):
    html = gr.collect(PID, db_path=seeded_db).to_html("zh")
    for english_label in (">Rounds<", ">Rejected rounds<", ">Final score<",
                          "Kairos Gate Report</h1>"):
        assert english_label not in html, english_label
    assert ">轮次<" in html and ">被打回轮次<" in html


def test_html_escapes_untrusted_text(tmp_path):
    db = tmp_path / "kairos.db"
    Persistence(db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO projects (id,name,status) VALUES (?,?,?)",
                     (PID, "<script>alert(1)</script>", "ready"))
        conn.execute(
            "INSERT INTO loop_rounds (project_id,session_id,round,coder_summary,"
            "review_summary,review_json,score,approve,created_at,insert_order) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (PID, SID, 1, "<img src=x onerror=alert(1)>", "x",
             json.dumps({"bugs": [{"file": "a.py", "line": 1,
                                   "description": "<b>bold</b>", "fix": "<i>x</i>"}]}),
             30, 0, time.time(), 1))
    html = gr.collect(PID, db_path=db).to_html("en")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<img src=x onerror=alert(1)>" not in html
    assert "<b>bold</b>" not in html


def test_json_payload_has_stable_keys(seeded_db):
    payload = gr.collect(PID, db_path=seeded_db).to_dict()
    for key in ("project_id", "project_name", "state", "passed", "first_pass",
                "rounds_total", "rejected_rounds", "final_score", "score_curve",
                "issues_total", "issues_by_severity", "cost_usd", "cost_by_model",
                "rounds", "checkpoints", "learned", "badge"):
        assert key in payload, key
    assert json.loads(json.dumps(payload))["score_curve"] == [60, 80, 100]
    assert payload["rounds"][0]["issues"][0]["location"] == "relay.py:42" or \
           payload["rounds"][0]["issues"][0]["file"] == "relay.py"


def test_share_badge_is_pasteable(seeded_db):
    badge = gr.collect(PID, db_path=seeded_db).share_badge("en")
    assert badge.startswith("Kairos Gate ✅")
    assert "3 round(s)" in badge


# ---------------------------------------------------------------------------
# Eval regression section — only when runs exist
# ---------------------------------------------------------------------------
def test_no_eval_section_without_runs(seeded_db, monkeypatch):
    monkeypatch.delenv("KAIROS_RESULTS_DIR", raising=False)
    report = gr.collect(PID, db_path=seeded_db)
    if (gr.REPO_ROOT / "results").is_dir():
        pytest.skip("repo has results/ runs; covered by the with-runs test")
    assert report.eval_latest is None
    assert "Regression check" not in report.to_markdown("en")


def test_eval_section_reports_pass_rate_and_delta(seeded_db, tmp_path, monkeypatch):
    results = tmp_path / "results"
    results.mkdir()
    for i, (pass_rate, passed) in enumerate([(0.5, 1), (1.0, 2)], start=1):
        (results / f"run-{i}.json").write_text(json.dumps({
            "run_id": f"run-{i}", "suite_name": "smoke", "pass_rate": pass_rate,
            "cases": [{"passed": bool(j < passed), "cost_usd": 0.001, "duration_ms": 100}
                      for j in range(2)],
        }), encoding="utf-8")
    monkeypatch.setenv("KAIROS_RESULTS_DIR", str(results))
    report = gr.collect(PID, db_path=seeded_db)
    assert report.eval_latest is not None
    assert report.eval_latest["cases"] == 2
    assert report.eval_latest["pass_rate"] == pytest.approx(1.0)
    assert report.eval_latest["pass_rate_delta"] == pytest.approx(0.5)
    md = report.to_markdown("en")
    assert "Regression check (eval suite)" in md
    assert "+50.0%" in md


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def test_cli_report_writes_html(seeded_db, tmp_path, capsys):
    out = tmp_path / "receipt"
    code = gr.main(["report", "--project", "demo-relay", "--db", str(seeded_db),
                    "--out", str(out)])
    assert code == 0
    written = out.with_suffix(".html")
    assert written.exists()
    assert written.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
    stdout = capsys.readouterr().out
    assert "3 round(s)" in stdout
    assert str(written) in stdout


def test_cli_formats(seeded_db, tmp_path):
    for fmt, suffix, needle in [("md", ".md", "Kairos"),
                                ("json", ".json", '"score_curve"')]:
        out = tmp_path / f"r-{fmt}"
        assert gr.main(["report", "--project", PID, "--db", str(seeded_db),
                        "--out", str(out), "--format", fmt, "--quiet"]) == 0
        assert needle in out.with_suffix(suffix).read_text(encoding="utf-8")


def test_cli_language(seeded_db, tmp_path):
    out = tmp_path / "zh.html"
    assert gr.main(["report", "--project", PID, "--db", str(seeded_db),
                    "--out", str(out), "--lang", "zh", "--quiet"]) == 0
    text = out.read_text(encoding="utf-8")
    assert "结论" in text and "轮次明细" in text


def test_cli_unknown_project_exits_3(tmp_path, capsys):
    code = gr.main(["report", "--project", "ghost", "--db", str(tmp_path / "kairos.db"),
                    "--out", str(tmp_path / "x.html")])
    assert code == 3
    assert "project not found" in capsys.readouterr().err


def test_cli_no_subcommand_prints_help(capsys):
    assert gr.main([]) == 3
    assert "report" in capsys.readouterr().out


def test_top_level_cli_dispatches_to_gate(seeded_db, tmp_path):
    """`kairos gate report` must reach the report module (not the server)."""
    from kairos import cli
    out = tmp_path / "g.html"
    code = cli.main(["gate", "report", "--project", PID, "--db", str(seeded_db),
                     "--out", str(out), "--quiet"])
    assert code == 0
    assert out.exists()


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------
def test_api_default_is_html(client):
    resp = client.get(f"/api/projects/{PID}/gate-report")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert resp.text.startswith("<!DOCTYPE html>")
    assert "Kairos Gate Report" in resp.text


def test_api_json(client):
    resp = client.get(f"/api/projects/{PID}/gate-report", params={"format": "json"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["state"] == "passed"
    assert payload["score_curve"] == [60, 80, 100]
    assert payload["checkpoints"]


def test_api_markdown_and_download_header(client):
    resp = client.get(f"/api/projects/{PID}/gate-report", params={"format": "md"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "| R3 | 100 |" in resp.text

    resp = client.get(f"/api/projects/{PID}/gate-report", params={"download": "1"})
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith("attachment;")
    assert PID[:8] in resp.headers["content-disposition"]


def test_api_language_and_fallback(client):
    assert "结论" in client.get(f"/api/projects/{PID}/gate-report",
                                params={"lang": "zh"}).text
    # an unsupported language falls back to English instead of erroring
    assert "Verdict" in client.get(f"/api/projects/{PID}/gate-report",
                                   params={"lang": "fr"}).text


def test_api_rejects_bad_format(client):
    resp = client.get(f"/api/projects/{PID}/gate-report", params={"format": "pdf"})
    assert resp.status_code == 400
    assert "format must be one of" in resp.json()["detail"]


def test_api_unknown_project_is_404(client):
    resp = client.get("/api/projects/does-not-exist/gate-report")
    assert resp.status_code == 404
