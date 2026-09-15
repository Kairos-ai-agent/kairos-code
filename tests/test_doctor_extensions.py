"""R38.11 — ``kairos doctor`` must notice the problems this round fixed.

The skills check used to assert "more than zero skills discovered", which stayed
green while the bundled set was unreachable from the API layer. The bundled MCP
servers had no check at all, so "works out of the box" was an assertion nobody
verified. Both are asked directly now.
"""

from kairos.doctor import (DEFAULT_CHECKS, check_bundled_mcp_servers,
                           check_skills_loadable)


def test_the_bundled_servers_check_is_wired_in():
    assert check_bundled_mcp_servers in DEFAULT_CHECKS, (
        "a check nobody runs is documentation")
    assert check_skills_loadable in DEFAULT_CHECKS


def test_the_skills_check_reports_the_real_counts():
    result = check_skills_loadable()
    assert result.status in ("ok", "warn"), result.message
    # "N skills loaded (M shipped files)" — both numbers, not just "> 0".
    assert "loaded" in result.message
    assert "shipped" in result.message
    assert "(0 shipped files)" not in result.message


def test_the_bundled_servers_check_reports_tools_and_no_download():
    result = check_bundled_mcp_servers()
    assert result.status == "ok", result.message
    assert "offline servers" in result.message
    assert "no download" in result.message
    # Five servers are advertised; the check counts the four this module serves
    # plus filesystem, which lives in its own module.
    assert "5 offline servers" in result.message


def test_a_broken_server_would_be_reported(monkeypatch):
    """The check must fail loudly rather than report a happy count."""
    import kairos.doctor as doctor
    import kairos.mcp_local_servers as mls

    def boom(_server, _root=None):
        raise RuntimeError("tool table exploded")

    monkeypatch.setattr(mls, "tools_for", boom)
    result = check_bundled_mcp_servers()
    assert result.status == "fail"
    assert "tool table exploded" in (result.hint or "")
