"""Inbound hooks: an issue becomes a project, and only when it is signed.

The interesting cases are all refusals. A webhook endpoint is an unauthenticated
door with a URL that people paste into browsers, so the tests spend most of their
time proving it stays shut: no secret, a wrong signature, an event we do not act
on, a redelivery of the same issue.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

import api.deps as deps
from api.routes import hooks


class FakeProject:
    def __init__(self, project_id, name, description):
        self.id = project_id
        self.name = name
        self.description = description


class FakeOrchestrator:
    def __init__(self):
        self.projects = []
        self.created = []

    def list_projects(self):
        return list(self.projects)

    def create_project(self, name, description, work_dir=""):
        project = FakeProject("p%d" % (len(self.projects) + 1), name, description)
        self.projects.append(project)
        self.created.append(project)
        return project


@pytest.fixture
def orch():
    return FakeOrchestrator()


@pytest.fixture
def client(orch, monkeypatch):
    from api.app import app

    monkeypatch.setattr(deps, "orchestrator", orch)
    monkeypatch.delenv(hooks.GITHUB_SECRET_ENV, raising=False)
    monkeypatch.delenv(hooks.GITHUB_LABEL_ENV, raising=False)
    monkeypatch.delenv(hooks.LINEAR_SECRET_ENV, raising=False)
    with TestClient(app) as c:
        yield c


def test_status_says_which_doors_are_open(client, monkeypatch):
    listing = client.get("/api/hooks/status").json()
    assert {s["name"] for s in listing["sources"]} == {"github", "linear"}
    assert all(s["configured"] is False for s in listing["sources"])
    assert listing["starts_the_loop"] is False

    monkeypatch.setenv(hooks.GITHUB_SECRET_ENV, "s3cret")
    listing = client.get("/api/hooks/status").json()
    by_name = {s["name"]: s for s in listing["sources"]}
    assert by_name["github"]["configured"] is True
    assert by_name["linear"]["configured"] is False
    text = json.dumps(listing)
    assert "s3cret" not in text, "the status endpoint reports, it does not leak"
    assert by_name["github"]["secret_env"] == hooks.GITHUB_SECRET_ENV


def sign(secret: str, body: bytes, algo: str = "sha256") -> str:
    return hmac.new(secret.encode(), body, getattr(hashlib, algo)).hexdigest()


def github_body(number=7, action="opened", title="Fix the parser", labels=()):
    return json.dumps({
        "action": action,
        "repository": {"full_name": "Kairos-ai-agent/kairos-code"},
        "issue": {
            "number": number,
            "title": title,
            "body": "It crashes on empty input.",
            "html_url": "https://github.com/Kairos-ai-agent/kairos-code/issues/%d" % number,
            "user": {"login": "octocat"},
            "labels": [{"name": name} for name in labels],
        },
    }).encode("utf-8")


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

def test_unsigned_github_is_refused(client):
    response = client.post("/api/hooks/github", content=github_body())
    assert response.status_code == 503, "no secret configured means no acceptance"
    assert "SECRET" in response.text.upper()


def test_a_wrong_signature_is_refused(client, monkeypatch, orch):
    monkeypatch.setenv(hooks.GITHUB_SECRET_ENV, "s3cret")
    response = client.post("/api/hooks/github", content=github_body(),
                           headers={"X-Hub-Signature-256": "sha256=" + "0" * 64})
    assert response.status_code == 401
    assert orch.created == [], "a bad signature must not create anything"


def test_a_signed_issue_becomes_a_project(client, monkeypatch, orch):
    monkeypatch.setenv(hooks.GITHUB_SECRET_ENV, "s3cret")
    body = github_body()
    response = client.post(
        "/api/hooks/github", content=body,
        headers={"X-Hub-Signature-256": "sha256=" + sign("s3cret", body),
                 "X-GitHub-Event": "issues"})

    assert response.status_code == 200, response.text
    assert response.json() == {"created": True, "project_id": "p1", "reason": "created"}
    assert orch.created[0].name == "Fix the parser"
    assert "[github#7]" in orch.created[0].description, "the marker is the ledger"
    assert "https://github.com/" in orch.created[0].description


def test_a_redelivery_does_not_create_a_second_project(client, monkeypatch, orch):
    monkeypatch.setenv(hooks.GITHUB_SECRET_ENV, "s3cret")
    body = github_body()
    headers = {"X-Hub-Signature-256": "sha256=" + sign("s3cret", body),
               "X-GitHub-Event": "issues"}

    first = client.post("/api/hooks/github", content=body, headers=headers).json()
    second = client.post("/api/hooks/github", content=body, headers=headers).json()

    assert first["created"] is True and second["created"] is False
    assert second["reason"] == "already tracked"
    assert second["project_id"] == first["project_id"]
    assert len(orch.created) == 1


def test_other_events_are_answered_but_not_acted_on(client, monkeypatch, orch):
    monkeypatch.setenv(hooks.GITHUB_SECRET_ENV, "s3cret")
    body = github_body()
    response = client.post(
        "/api/hooks/github", content=body,
        headers={"X-Hub-Signature-256": "sha256=" + sign("s3cret", body),
                 "X-GitHub-Event": "push"})

    assert response.status_code == 200, "a 4xx would make GitHub retry forever"
    assert response.json()["created"] is False
    assert "push" in response.json()["reason"]
    assert orch.created == []


def test_a_closed_issue_is_not_acted_on(client, monkeypatch, orch):
    monkeypatch.setenv(hooks.GITHUB_SECRET_ENV, "s3cret")
    body = github_body(action="closed")
    response = client.post("/api/hooks/github", content=body,
                           headers={"X-Hub-Signature-256": "sha256=" + sign("s3cret", body)})
    assert response.json()["created"] is False
    assert orch.created == []


def test_a_label_filter_keeps_other_issues_out(client, monkeypatch, orch):
    monkeypatch.setenv(hooks.GITHUB_SECRET_ENV, "s3cret")
    monkeypatch.setenv(hooks.GITHUB_LABEL_ENV, "kairos")
    body = github_body(labels=["bug"])

    response = client.post("/api/hooks/github", content=body,
                           headers={"X-Hub-Signature-256": "sha256=" + sign("s3cret", body)})
    assert response.json()["created"] is False
    assert "kairos" in response.json()["reason"]
    assert orch.created == []

    with_label = github_body(labels=["bug", "Kairos"])
    response = client.post("/api/hooks/github", content=with_label,
                           headers={"X-Hub-Signature-256": "sha256=" + sign("s3cret", with_label)})
    assert response.json()["created"] is True, "the label match is case-insensitive"
    assert len(orch.created) == 1


# ---------------------------------------------------------------------------
# Linear
# ---------------------------------------------------------------------------

def linear_body(identifier="ENG-1", action="create"):
    return json.dumps({
        "action": action,
        "type": "Issue",
        "data": {"identifier": identifier, "title": "Ship the ledger",
                 "description": "As discussed.",
                 "url": "https://linear.app/acme/issue/%s" % identifier,
                 "team": {"name": "Core"}},
    }).encode("utf-8")


def test_linear_refuses_without_a_secret_and_with_a_bad_one(client, monkeypatch, orch):
    assert client.post("/api/hooks/linear", content=linear_body()).status_code == 503

    monkeypatch.setenv(hooks.LINEAR_SECRET_ENV, "l1near")
    response = client.post("/api/hooks/linear", content=linear_body(),
                           headers={"Linear-Signature": "deadbeef"})
    assert response.status_code == 401
    assert orch.created == []


def test_a_signed_linear_issue_becomes_a_project(client, monkeypatch, orch):
    monkeypatch.setenv(hooks.LINEAR_SECRET_ENV, "l1near")
    body = linear_body()
    response = client.post("/api/hooks/linear", content=body,
                           headers={"Linear-Signature": sign("l1near", body)})

    assert response.status_code == 200, response.text
    assert response.json()["created"] is True
    assert "[linear#ENG-1]" in orch.created[0].description
    assert orch.created[0].name == "Ship the ledger"


def test_linear_ignores_other_data_types(client, monkeypatch, orch):
    monkeypatch.setenv(hooks.LINEAR_SECRET_ENV, "l1near")
    body = json.dumps({"action": "create", "type": "Comment", "data": {}}).encode()
    response = client.post("/api/hooks/linear", content=body,
                           headers={"Linear-Signature": sign("l1near", body)})
    assert response.json()["created"] is False
    assert orch.created == []


def test_the_two_secrets_are_separate(client, monkeypatch, orch):
    """A GitHub secret must not open the Linear door, or one leak is two."""
    monkeypatch.setenv(hooks.GITHUB_SECRET_ENV, "s3cret")
    body = linear_body()
    response = client.post("/api/hooks/linear", content=body,
                           headers={"Linear-Signature": sign("s3cret", body)})
    assert response.status_code == 503, "Linear has no secret of its own yet"
    assert orch.created == []
