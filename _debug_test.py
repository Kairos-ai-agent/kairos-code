"""Debug: reproduce the empty POST response seen in the full test suite."""
import os
import sys
import uuid

os.environ.setdefault("KAIROS_WORKSPACE", "./_api_test_workspace")
# Run this from project root
os.chdir(r"D:\software_bak\Kairos_code")

from fastapi.testclient import TestClient
from api.app import app

client = TestClient(app)
projects = client.get("/api/projects").json().get("projects", [])
print(f"Project count before: {len(projects)}")
print("First 3 projects:", [p.get("name") for p in projects[:3]])

unique_name = f"mem-api-test-{uuid.uuid4().hex[:8]}"
resp = client.post("/api/projects", json={"name": unique_name, "description": "tmp", "work_dir": "./_api_test_workspace"})
print(f"Status: {resp.status_code}")
print(f"Body: {resp.text!r}")
print(f"JSON: {resp.json()!r}")
