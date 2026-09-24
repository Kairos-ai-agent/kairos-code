"""The browser must not open onto a port that is not listening yet.

A double-click used to show "127.0.0.1 refused to connect": the launcher gave
the server 30 seconds, an import-time MCP startup could spend longer than that
(the five servers a user had configured cost 102 seconds), and the browser
opened anyway. These tests pin both halves -- the wait outlasts a slow start,
and the browser still opens when the wait expires, so a broken start is
visible instead of silent.
"""

import sys
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import kairos_code_launcher as lch  # noqa: E402


def test_the_readiness_budget_outlasts_a_slow_first_start():
    text = (ROOT / "kairos_code_launcher.py").read_text(encoding="utf-8")
    assert "args=(url, 180.0)" in text, "the readiness budget shrank again"
    assert "args=(url, 30.0)" not in text


def test_it_keeps_polling_and_still_opens_the_browser(monkeypatch, tmp_path):
    monkeypatch.setenv("KAIROS_BROWSER_LOG", str(tmp_path / "browser.log"))
    polls = {"n": 0}

    def fake_urlopen(url, timeout=None):
        polls["n"] += 1
        raise OSError("nothing is listening on that port")

    opened = []
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(webbrowser, "open", lambda u: opened.append(u) or True)

    lch._open_browser_when_ready("http://127.0.0.1:9", 0.7)

    assert polls["n"] > 1, "it gave up after a single attempt"
    assert opened == ["http://127.0.0.1:9"], "the browser was never opened"


def test_a_ready_server_opens_the_browser_without_waiting(monkeypatch, tmp_path):
    monkeypatch.setenv("KAIROS_BROWSER_LOG", str(tmp_path / "browser.log"))

    class Reply:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    polls = {"n": 0}

    def fake_urlopen(url, timeout=None):
        polls["n"] += 1
        return Reply()

    opened = []
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(webbrowser, "open", lambda u: opened.append(u) or True)

    lch._open_browser_when_ready("http://127.0.0.1:9", 30.0)

    assert polls["n"] == 1, "it kept polling a server that had already answered"
    assert opened == ["http://127.0.0.1:9"]
