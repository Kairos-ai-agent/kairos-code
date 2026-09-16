"""R38.9 — the updater must never execute something it could not verify.

The dangerous paths are the interesting ones here: a release with no
``SHA256SUMS``, a checksum that does not match, an install we cannot write to,
and macOS (where in-place replacement is what Gatekeeper blocks). Each must end
with the install *untouched* and a reason the UI can show.
"""

import hashlib
import sys
import zipfile
from pathlib import Path

import pytest

from kairos import updater as u


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _release(version="0.1.5", *, with_sums=True, with_asset=True,
             digest=None, key=None):
    # Default to the asset name *this* platform's updater would look for. It was
    # hardcoded to "windows-x86_64", so the fixture only described a release an
    # author on Windows could pick up: everywhere else check_for_update() found
    # no matching asset, returned asset=None, and the assertions died with
    # "TypeError: 'NoneType' object is not subscriptable" — in CI, for weeks,
    # on a working updater.
    key = key or u.platform_key() or "windows-x86_64"
    suffix = ".zip" if key.startswith("windows") else ".tar.gz"
    name = f"kairos-code-{version}-{key}{suffix}"
    body = f"payload-{version}".encode()
    sha = hashlib.sha256(body).hexdigest() if digest is None else digest
    assets = []
    if with_asset:
        assets.append({"name": name,
                       "browser_download_url": f"https://example.invalid/{name}",
                       "size": len(body)})
    if with_sums:
        assets.append({"name": "SHA256SUMS",
                       "browser_download_url": "https://example.invalid/SHA256SUMS"})
    return ({"tag_name": f"v{version}", "html_url": "https://example.invalid/rel",
             "published_at": "2026-09-15T00:00:00Z", "assets": assets}, sha, name, body)


class FakeFetch:
    """Stands in for the GitHub API + the SHA256SUMS asset."""

    def __init__(self, release, sums_text=""):
        self.release = release
        self.sums_text = sums_text
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url.endswith("SHA256SUMS"):
            return self.sums_text
        return self.release


def _frozen(monkeypatch, exe: Path, platform: str = "win32"):
    """Make the module believe it is the packaged binary at ``exe``."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(u, "executable_path", lambda: exe)


def _zip_with_binary(path: Path, payload: bytes) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("kairos-code-0.1.5-windows-x86_64/kairos-code.exe", payload)
    return path


# ---------------------------------------------------------------------------
# versions
# ---------------------------------------------------------------------------

def test_version_tuple_handles_tags_and_suffixes():
    assert u.version_tuple("v0.1.4") == (0, 1, 4)
    assert u.version_tuple("0.1.10") == (0, 1, 10)
    assert u.version_tuple("1.2.3-rc1") == (1, 2, 3)
    assert u.version_tuple("") == (0,)
    assert u.version_tuple("nonsense") == (0,)


def test_is_newer_is_numeric_not_lexicographic():
    assert u.is_newer("0.1.10", "0.1.4")          # the case a string compare gets wrong
    assert not u.is_newer("0.1.4", "0.1.4")
    assert not u.is_newer("0.1.3", "0.1.4")


def test_parse_sha256sums_accepts_the_real_format_and_rejects_junk():
    good = "a" * 64
    text = f"{good}  kairos-code-0.1.5-windows-x86_64.zip\n" \
           f"{'b' * 64} *kairos_code-0.1.5-py3-none-any.whl\n" \
           "not-a-digest  junk.zip\n\n# comment\n"
    parsed = u.parse_sha256sums(text)
    assert parsed["kairos-code-0.1.5-windows-x86_64.zip"] == good
    assert parsed["kairos_code-0.1.5-py3-none-any.whl"] == "b" * 64
    assert "junk.zip" not in parsed


def test_verify_sha256_refuses_anything_that_is_not_a_digest(tmp_path):
    f = tmp_path / "x"
    f.write_bytes(b"hello")
    assert u.verify_sha256(f, hashlib.sha256(b"hello").hexdigest())
    assert not u.verify_sha256(f, "abc123")
    assert not u.verify_sha256(f, "")


# ---------------------------------------------------------------------------
# the read-only check
# ---------------------------------------------------------------------------

def test_check_reports_the_update_and_its_published_checksum(tmp_path):
    release, sha, name, _ = _release()
    fetch = FakeFetch(release, f"{sha}  {name}\n")
    info = u.check_for_update(data_dir=tmp_path, current="0.1.4", fetch=fetch)
    assert info["hasUpdate"] is True
    assert info["latest"] == "0.1.5"
    assert info["asset"]["name"] == name
    assert info["asset"]["sha256"] == sha
    assert info["reason"] in ("ok", "not-a-packaged-build")


def test_check_marks_a_release_without_checksums(tmp_path):
    release, _, _, _ = _release(with_sums=False)
    info = u.check_for_update(data_dir=tmp_path, current="0.1.4",
                              fetch=FakeFetch(release))
    assert info["hasUpdate"] is True
    assert info["asset"]["sha256"] == ""
    assert info["reason"] == "no-checksum-published"


def test_the_check_picks_the_asset_for_this_platform(monkeypatch, tmp_path):
    """Answers for every platform, not only the one it was written on.

    _release() used to hardcode a windows-x86_64 asset while check_for_update()
    selects with platform_key(): on Linux the fixture described a release with
    nothing this machine could use, `asset` came back None, and two assertions
    died with "'NoneType' object is not subscriptable" — which reads like a
    broken updater and was a broken fixture. This walks all three platform keys
    the release pipeline publishes.
    """
    for key in ("windows-x86_64", "linux-x86_64", "macos-arm64"):
        release, sha, name, _ = _release(key=key)
        monkeypatch.setattr(u, "platform_key", lambda k=key: k)
        # A fresh data_dir per platform: check_for_update caches its resolved
        # answer there for 12 hours, so sharing tmp_path across iterations made
        # every round after the first read the first round's answer (and the
        # first round is this machine's own key). That is what made this test
        # fail while the fixture and the updater were both correct.
        info = u.check_for_update(data_dir=tmp_path / key, current="0.1.4",
                                  fetch=FakeFetch(release, f"{sha}  {name}\n"))
        assert info["hasUpdate"] is True, key
        assert info["asset"] is not None, f"{key}: no asset matched"
        assert info["asset"]["name"] == name, key
        assert info["asset"]["sha256"] == sha, key


def test_a_release_with_nothing_for_this_platform_is_reported_not_crashed(
        monkeypatch, tmp_path):
    """The behaviour the fixture accidentally hid: no asset is a normal answer."""
    release, _, _, _ = _release(key="windows-x86_64")
    monkeypatch.setattr(u, "platform_key", lambda: "linux-x86_64")
    info = u.check_for_update(data_dir=tmp_path, current="0.1.4",
                              fetch=FakeFetch(release))
    assert info["hasUpdate"] is True
    assert info["asset"] is None
    assert info["reason"] == "no-asset-for-platform"


def test_check_is_cached_within_the_ttl(tmp_path):
    release, sha, name, _ = _release()
    fetch = FakeFetch(release, f"{sha}  {name}\n")
    u.check_for_update(data_dir=tmp_path, current="0.1.4", fetch=fetch)
    u.check_for_update(data_dir=tmp_path, current="0.1.4", fetch=fetch)
    assert len(fetch.calls) == 2          # one release call + one SHA256SUMS call
    u.check_for_update(data_dir=tmp_path, current="0.1.4", fetch=fetch, force=True)
    assert len(fetch.calls) == 4          # the cache was bypassed


def test_check_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_NO_UPDATE_CHECK", "1")
    fetch = FakeFetch(_release()[0])
    info = u.check_for_update(data_dir=tmp_path, current="0.1.4", fetch=fetch)
    assert info["enabled"] is False
    assert info["reason"] == "disabled"
    assert fetch.calls == []


def test_check_never_raises_when_the_lookup_fails(tmp_path):
    def boom(_url):
        raise RuntimeError("no network")
    info = u.check_for_update(data_dir=tmp_path, current="0.1.4", fetch=boom)
    assert info["hasUpdate"] is False
    assert "no network" in info["error"]


def test_check_is_a_noop_when_already_current(tmp_path):
    release, sha, name, _ = _release(version="0.1.4")
    info = u.check_for_update(data_dir=tmp_path, current="0.1.4",
                              fetch=FakeFetch(release, f"{sha}  {name}\n"))
    assert info["hasUpdate"] is False
    assert info["asset"] is None


# ---------------------------------------------------------------------------
# applying it
# ---------------------------------------------------------------------------

def _ok_check(tmp_path, exe, *, digest, monkeypatch, platform="win32",
              build: bytes = b"NEW", name="kairos-code-0.1.5-windows-x86_64.zip"):
    """A check dict pointing at a real archive we can download."""
    archive = _zip_with_binary(tmp_path / name, build)
    _frozen(monkeypatch, exe, platform)
    info = {
        "hasUpdate": True, "current": "0.1.4", "latest": "0.1.5",
        "asset": {"name": name, "url": "https://example.invalid/" + name,
                  "size": archive.stat().st_size,
                  "sha256": digest if digest is not None else u.sha256_of(archive)},
    }
    return info, archive


def test_apply_refuses_without_a_checksum(tmp_path, monkeypatch):
    exe = tmp_path / "kairos-code.exe"
    exe.write_bytes(b"OLD")
    info, _ = _ok_check(tmp_path, exe, digest="", monkeypatch=monkeypatch)
    called = []
    out = u.apply_update(info, download=lambda url, dest: called.append(url),
                         spawn=False)
    assert out["ok"] is False
    assert out["reason"] == "no-checksum-published"
    assert called == []                  # nothing was fetched
    assert exe.read_bytes() == b"OLD"    # and nothing was touched


def test_apply_refuses_on_a_checksum_mismatch(tmp_path, monkeypatch):
    exe = tmp_path / "kairos-code.exe"
    exe.write_bytes(b"OLD")
    info, archive = _ok_check(tmp_path, exe, digest="0" * 64, monkeypatch=monkeypatch)

    def download(url, dest):
        dest.write_bytes(archive.read_bytes())
        return dest

    out = u.apply_update(info, download=download, spawn=False)
    assert out["ok"] is False
    assert out["reason"] == "checksum-mismatch"
    assert exe.read_bytes() == b"OLD"
    assert not (tmp_path / "kairos-code.exe.new").exists()


def test_apply_is_a_noop_for_a_source_install(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    info, archive = _ok_check(tmp_path, tmp_path / "kairos-code.exe", digest=None,
                              monkeypatch=monkeypatch)
    monkeypatch.setattr(sys, "frozen", False, raising=False)   # undo _frozen
    out = u.apply_update(info, download=lambda u_, d: d, spawn=False)
    assert out["ok"] is False
    assert out["reason"] == "not-a-packaged-build"


def test_apply_is_notify_only_on_macos(tmp_path, monkeypatch):
    exe = tmp_path / "kairos-code"
    exe.write_bytes(b"OLD")
    info, _ = _ok_check(tmp_path, exe, digest=None, monkeypatch=monkeypatch,
                        platform="darwin", name="kairos-code-0.1.5-macos-arm64.tar.gz")
    out = u.apply_update(info, download=lambda u_, d: d, spawn=False)
    assert out["ok"] is False
    assert out["reason"] == "macos-notify-only"


def test_apply_stages_the_verified_build_and_writes_the_swap_helper(tmp_path, monkeypatch):
    exe = tmp_path / "kairos-code.exe"
    exe.write_bytes(b"OLD")
    info, archive = _ok_check(tmp_path, exe, digest=None, monkeypatch=monkeypatch,
                              build=b"NEW-BUILD")

    def download(url, dest):
        dest.write_bytes(archive.read_bytes())
        return dest

    out = u.apply_update(info, download=download, spawn=False)
    assert out["ok"] is True, out
    assert out["restartRequired"] is True
    staged = Path(out["stage"])
    assert staged.name == "kairos-code.exe.new"
    assert staged.read_bytes() == b"NEW-BUILD"      # extracted from the archive
    assert out["archiveSha256"] == u.sha256_of(archive)
    helper = Path(out["helper"])
    assert helper.exists()
    body = helper.read_text(encoding="utf-8")
    assert "kairos-code.exe.new" in body            # it moves the staged file in
    assert str(exe) in body                         # over the old binary
    assert str(__import__("os").getpid()) in body   # after this process exits
    assert exe.read_bytes() == b"OLD"               # the swap has not happened yet


def test_extract_binary_reads_both_archives(tmp_path):
    payload = b"binary"
    z = _zip_with_binary(tmp_path / "a.zip", payload)
    assert u.extract_binary(z, tmp_path / "z").read_bytes() == payload

    import tarfile
    t = tmp_path / "a.tar.gz"
    inner = tmp_path / "kairos-code"
    inner.write_bytes(payload)
    with tarfile.open(t, "w:gz") as tf:
        tf.add(inner, arcname="kairos-code-0.1.5-linux-x86_64/kairos-code")
    assert u.extract_binary(t, tmp_path / "t").read_bytes() == payload


def test_extract_binary_rejects_an_archive_without_the_binary(tmp_path):
    z = tmp_path / "x.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("README.txt", "nothing here")
    with pytest.raises(RuntimeError):
        u.extract_binary(z, tmp_path / "w")
