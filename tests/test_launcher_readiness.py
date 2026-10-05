"""The browser must not open onto a port that is not listening yet.

A double-click used to show "127.0.0.1 refused to connect": the launcher gave
the server 30 seconds, an import-time MCP startup could spend longer than that
(the five servers a user had configured cost 102 seconds), and the browser
opened anyway. These tests pin both halves -- the wait outlasts a slow start,
and the UI still comes up when the wait expires, so a broken start is visible
instead of silent.

The UI shell is the second half of "a double-click shows something". The
default is now a chrome-less window driven through the Chromium family
(``--shell app``); every path falls back to the default browser when no such
browser exists, and ``--shell browser`` restores the old behaviour. Pinning all
of it keeps one promise intact: the UI opens, whatever is installed.
"""

import sys
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import kairos_code_launcher as lch  # noqa: E402


def _health_answers_immediately(monkeypatch, tmp_path):
    """Point the log at tmp_path and make /api/health return 200 on poll #1."""
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

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return polls


def test_the_readiness_budget_outlasts_a_slow_first_start():
    text = (ROOT / "kairos_code_launcher.py").read_text(encoding="utf-8")
    assert "args=(url, 180.0, args.shell)" in text, (
        "the readiness budget shrank again, or the shell choice stopped "
        "reaching the opener thread"
    )
    assert "args=(url, 30.0" not in text


def test_it_keeps_polling_and_still_opens_the_browser(monkeypatch, tmp_path):
    monkeypatch.setenv("KAIROS_BROWSER_LOG", str(tmp_path / "browser.log"))
    polls = {"n": 0}

    def fake_urlopen(url, timeout=None):
        polls["n"] += 1
        raise OSError("nothing is listening on that port")

    opened = []
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(webbrowser, "open", lambda u: opened.append(u) or True)

    lch._open_browser_when_ready("http://127.0.0.1:9", 0.7, shell="browser")

    assert polls["n"] > 1, "it gave up after a single attempt"
    assert opened == ["http://127.0.0.1:9"], "the browser was never opened"


def test_a_ready_server_opens_the_browser_without_waiting(monkeypatch, tmp_path):
    polls = _health_answers_immediately(monkeypatch, tmp_path)
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda u: opened.append(u) or True)

    lch._open_browser_when_ready("http://127.0.0.1:9", 30.0, shell="browser")

    assert polls["n"] == 1, "it kept polling a server that had already answered"
    assert opened == ["http://127.0.0.1:9"]


def test_app_shell_opens_a_window_not_a_browser_tab(monkeypatch, tmp_path):
    """The default shell is a window. A tab in the default browser is not an
    acceptable silent substitute -- that is exactly what was complained about."""
    _health_answers_immediately(monkeypatch, tmp_path)
    windows, opened = [], []
    monkeypatch.setattr(lch, "_open_app_window",
                        lambda url: windows.append(url) or True)
    monkeypatch.setattr(webbrowser, "open", lambda u: opened.append(u) or True)

    lch._open_browser_when_ready("http://127.0.0.1:9", 30.0)

    assert windows == ["http://127.0.0.1:9"], "the app window was never opened"
    assert opened == [], "it also opened a browser tab"


def test_app_shell_falls_back_to_the_browser_without_chromium(monkeypatch, tmp_path):
    """No Edge/Chrome must not mean no UI."""
    _health_answers_immediately(monkeypatch, tmp_path)
    opened = []
    monkeypatch.setattr(lch, "_open_app_window", lambda url: False)
    monkeypatch.setattr(webbrowser, "open", lambda u: opened.append(u) or True)

    lch._open_browser_when_ready("http://127.0.0.1:9", 30.0)

    assert opened == ["http://127.0.0.1:9"], "the UI never came up at all"


def test_find_app_shell_is_optional_and_returns_a_real_path():
    """A machine with no Chromium family gets None, never an exception."""
    found = lch._find_app_shell()
    assert found is None or Path(found).exists()


def test_the_window_shell_stays_the_default():
    text = (ROOT / "kairos_code_launcher.py").read_text(encoding="utf-8")
    assert 'choices=("app", "browser"), default="app"' in text, (
        "the window shell must stay the default, with an explicit opt-out"
    )
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "--shell browser" in readme, (
        "the opt-out has to be discoverable, not just implemented"
    )
