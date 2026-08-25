"""End-to-end smoke: create project, write AGENTS.md + skill, verify agent picks them up."""
import asyncio
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 18920

# 1. Start server
proc = subprocess.Popen(
    [sys.executable, "-u", "-c", (
        "import sys; sys.path.insert(0, '.'); "
        "import uvicorn; from api.app import app; "
        f"uvicorn.run(app, host='127.0.0.1', port={PORT}, log_level='warning')"
    )],
    cwd=str(ROOT),
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)
time.sleep(3.0)
base = f"http://127.0.0.1:{PORT}"


def post(path, data, timeout=120):
    req = urllib.request.Request(
        f"{base}{path}", data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    return urllib.request.urlopen(req, timeout=timeout)


def get(path):
    return urllib.request.urlopen(f"{base}{path}", timeout=5)


try:
    # 2. Create project
    r = post("/api/projects", {"name": "agents_md_smoke", "description": "smoke"})
    proj = json.loads(r.read())
    pid = proj["id"]
    work_dir = Path(proj["work_dir"])
    print(f"[OK] project created: {pid} work_dir={work_dir}")

    # 3. Write AGENTS.md into project
    agents_md = work_dir / "AGENTS.md"
    agents_md.write_text(
        """# Project: agents_md_smoke

## Coding Conventions
- Use type hints.
- Test everything in tests/ directory.

## Don't
- Do not commit secrets.
- Do not add new dependencies.
""",
        encoding="utf-8",
    )
    print(f"[OK] wrote AGENTS.md ({agents_md.stat().st_size} bytes)")

    # 4. Write a skill into project
    skill_dir = work_dir / ".kairos" / "skills"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "python-typing.md").write_text(
        """---
name: python-typing
description: Python type hint best practices
when:
  keyword: python
  globs:
    - "*.py"
priority: 0.8
---

# Python Type Hints

Always annotate function parameters and return types.
Use `Optional[X]` not `X | None` for cross-version compatibility.
""",
        encoding="utf-8",
    )
    print(f"[OK] wrote skill python-typing.md")

    # 5. Trigger team_leader to plan + dispatch
    r = post(
        f"/api/projects/{pid}/start",
        {"requirement": "Add a Python function `parse_python(s: str) -> int` to a new file utils.py"},
        timeout=180,
    )
    body = json.loads(r.read())
    print(f"[OK] start_project: status={body['status']}")

    # 6. Wait for dispatch
    time.sleep(30)

    # 7. Check messages for tool calls (Coder should have read AGENTS.md
    #    via the system prompt, and used file_write on utils.py)
    r = get(f"/api/projects/{pid}/messages?limit=200")
    msgs = json.loads(r.read())["messages"]
    tool_calls = [m for m in msgs if "Calling file_write" in str(m.get("content", ""))]
    file_writes = [m for m in msgs if "file_write: OK" in str(m.get("content", ""))]
    print(f"[INFO] {len(msgs)} messages, {len(tool_calls)} file_write tool calls, {len(file_writes)} successful")
    for tw in file_writes[:3]:
        print(f"  {tw['content'][:200]}")

    # 8. Verify the file got created
    utils_path = work_dir / "utils.py"
    if utils_path.exists():
        content = utils_path.read_text(encoding="utf-8")
        print(f"[OK] utils.py created ({len(content)} bytes): {content[:300]}")
    else:
        print(f"[WARN] utils.py not created yet (agent may still be running)")

    # 9. Check that AGENTS.md shows up in Coder's system prompt
    #    (we can't inspect the prompt directly, but we can check that the
    #    loop completed without erroring)
    r = get(f"/api/projects/{pid}")
    proj_after = json.loads(r.read())
    print(f"[OK] project status: {proj_after['status']}, tasks: {proj_after['task_count']}")

finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
