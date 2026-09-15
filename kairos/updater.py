"""Self-update for the packaged app — notify, then replace on one click.

Design notes, in the order they matter:

* **A checksum is not optional.** :func:`apply_update` refuses to touch the
  install unless the release publishes a ``SHA256SUMS`` entry for the asset it
  is about to run. Download-then-execute is the entire risk of an auto-updater,
  so without a checksum we fall back to *notifying only*. The checksums are
  produced by the same CI job that uploads the assets (``release.yml``), which
  is what makes them worth anything.
* **A running executable cannot replace itself.** We download the new build
  next to the old one, verify it, then hand the swap to a detached helper that
  waits for this process to exit, renames the file and starts it again. That is
  the sequence Windows allows, and it works on Linux too.
* **Nothing is silent.** :func:`check_for_update` is read-only (a cached
  GitHub Releases lookup); the download only happens when the user clicks, and
  macOS is notify-only — replacing an unsigned bundle in place is precisely
  what Gatekeeper exists to stop.
* **No network access is needed to *run* Kairos.** ``KAIROS_NO_UPDATE_CHECK=1``
  (or the ``updates.check`` setting) turns the whole thing off.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

REPO = "Kairos-ai-agent/kairos-code"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
CACHE_NAME = "update-check.json"
CACHE_TTL_SECONDS = 12 * 3600
HTTP_TIMEOUT = 10.0
USER_AGENT = "kairos-code-updater"


# ---------------------------------------------------------------------------
# version helpers
# ---------------------------------------------------------------------------

def current_version() -> str:
    from kairos import __version__
    return __version__


def version_tuple(v: str) -> Tuple[int, ...]:
    """``v0.1.4`` / ``0.1.4-rc1`` → ``(0, 1, 4)``.

    Anything we cannot parse sorts as ``(0,)`` so a malformed tag never looks
    newer than what is installed.
    """
    m = re.match(r"v?(\d+(?:\.\d+)*)", (v or "").strip())
    if not m:
        return (0,)
    return tuple(int(p) for p in m.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    return version_tuple(latest) > version_tuple(current)


# ---------------------------------------------------------------------------
# what are we running, and what should we run
# ---------------------------------------------------------------------------

def platform_key() -> Optional[str]:
    """The label ``release.yml`` uses for this machine, or ``None``."""
    machine = platform.machine().lower()
    if sys.platform.startswith("win"):
        return "windows-x86_64" if machine in ("amd64", "x86_64") else None
    if sys.platform == "darwin":
        return "macos-arm64" if machine in ("arm64", "aarch64") else None
    if sys.platform.startswith("linux"):
        return "linux-x86_64" if machine in ("x86_64", "amd64") else None
    return None


def asset_name(version: str, key: Optional[str] = None) -> Optional[str]:
    key = key or platform_key()
    if not key:
        return None
    suffix = ".zip" if key.startswith("windows") else ".tar.gz"
    return f"kairos-code-{version}-{key}{suffix}"


def install_kind() -> str:
    """``frozen`` (the packaged binary) | ``wheel`` | ``source``."""
    if getattr(sys, "frozen", False):
        return "frozen"
    if any(p.endswith("site-packages") for p in sys.path):
        return "wheel"
    return "source"


def executable_path() -> Optional[Path]:
    if install_kind() != "frozen":
        return None
    return Path(sys.executable).resolve()


def can_self_update() -> Tuple[bool, str]:
    """Can a click actually replace this install? ``(ok, reason)``."""
    if install_kind() != "frozen":
        return False, "not-a-packaged-build"
    if sys.platform == "darwin":
        return False, "macos-notify-only"
    exe = executable_path()
    if exe is None or not exe.exists():
        return False, "no-executable"
    if not os.access(str(exe.parent), os.W_OK):
        return False, "install-dir-not-writable"
    return True, "ok"


def _data_dir(explicit: Optional[Path] = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    try:
        from kairos.config.settings import settings
        return Path(settings.data_dir)
    except Exception:  # pragma: no cover - config import is best-effort
        return Path(os.environ.get("KAIROS_DATA_DIR")
                    or Path(__file__).resolve().parent.parent / "data")


def update_check_enabled() -> bool:
    if os.environ.get("KAIROS_NO_UPDATE_CHECK"):
        return False
    try:
        from kairos.settings_store import get_store
        return bool(getattr(get_store().get().updates, "check", True))
    except Exception:  # pragma: no cover - settings are best-effort
        return True


# ---------------------------------------------------------------------------
# the (read-only) check
# ---------------------------------------------------------------------------

def _fetch_json(url: str, timeout: float = HTTP_TIMEOUT) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def parse_sha256sums(text: str) -> Dict[str, str]:
    """``SHA256SUMS`` → ``{filename: hexdigest}`` (``sha256sum`` format)."""
    out: Dict[str, str] = {}
    for line in (text or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts[0].strip().lower(), parts[1].strip()
        if name.startswith("*"):
            name = name[1:]
        if re.fullmatch(r"[0-9a-f]{64}", digest) and name:
            out[Path(name).name] = digest
    return out


def check_for_update(
    *,
    force: bool = False,
    data_dir: Optional[Path] = None,
    current: Optional[str] = None,
    fetch: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """Look for a newer release. Never raises, never downloads an install.

    Returns a JSON-serialisable dict — the shape ``/api/update/check`` hands
    straight to the UI. The *resolved* result (release plus checksum) is cached
    for ``CACHE_TTL_SECONDS``, so a warm cache costs no network at all.
    """
    current = current or current_version()
    payload: Dict[str, Any] = {
        "current": current,
        "latest": current,
        "hasUpdate": False,
        "enabled": True,
        "notesUrl": RELEASES_PAGE,
        "publishedAt": "",
        "asset": None,
        "canSelfUpdate": False,
        "reason": "ok",
        "error": None,
        "checkedAt": 0,
    }

    if not update_check_enabled():
        payload["enabled"] = False
        payload["reason"] = "disabled"
        return payload

    ok, reason = can_self_update()
    payload["canSelfUpdate"] = ok
    payload["reason"] = reason

    ddir = _data_dir(data_dir)
    cache_path = ddir / CACHE_NAME
    now = time.time()

    # --- warm cache: no network, same answer ---
    if not force:
        try:
            blob = json.loads(cache_path.read_text(encoding="utf-8"))
            if now - float(blob.get("checkedAt") or 0) < CACHE_TTL_SECONDS:
                hit = blob.get("payload") or {}
                for key in ("latest", "publishedAt", "notesUrl", "asset"):
                    if key in hit:
                        payload[key] = hit[key]
                payload["checkedAt"] = float(blob.get("checkedAt") or 0)
                payload["hasUpdate"] = bool(
                    payload["latest"] and is_newer(str(payload["latest"]), current))
                if payload["hasUpdate"] and payload["asset"]:
                    payload["reason"] = ("ok" if payload["asset"].get("sha256")
                                         else "no-checksum-published")
                return payload
        except Exception:
            pass

    # --- cold: one release lookup plus one checksum lookup ---
    try:
        data = (fetch or _fetch_json)(RELEASES_API)
    except urllib.error.HTTPError as exc:
        payload["error"] = f"HTTP {exc.code} from GitHub"
        return payload
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
        return payload

    latest = str(data.get("tag_name") or "").lstrip("v")
    payload["latest"] = latest or current
    payload["publishedAt"] = str(data.get("published_at") or "")
    payload["notesUrl"] = str(data.get("html_url") or RELEASES_PAGE)
    payload["checkedAt"] = now
    payload["hasUpdate"] = bool(latest) and is_newer(latest, current)

    if payload["hasUpdate"]:
        want = asset_name(latest)
        assets = {str(a.get("name")): a for a in (data.get("assets") or [])}
        asset = assets.get(want) if want else None
        digest = ""
        sums_url = str((assets.get("SHA256SUMS") or {}).get("browser_download_url") or "")
        if sums_url:
            try:
                text = (fetch or _fetch_json)(sums_url)
                if isinstance(text, dict):          # a fake fetch may hand back a dict
                    text = str(text.get("_body") or "")
                digest = parse_sha256sums(str(text)).get(str(want), "")
            except Exception as exc:
                logger.debug("could not read SHA256SUMS: %s", exc)
        if asset:
            payload["asset"] = {
                "name": want,
                "url": str(asset.get("browser_download_url") or ""),
                "size": int(asset.get("size") or 0),
                "sha256": digest,
            }
        if not asset:
            payload["reason"] = "no-asset-for-platform"
        elif not digest:
            # Fine for *notifying*, fatal for applying -- see apply_update.
            payload["reason"] = "no-checksum-published"
        else:
            payload["reason"] = "ok"

    try:
        ddir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({
            "checkedAt": now,
            "payload": {k: payload[k] for k in
                        ("latest", "publishedAt", "notesUrl", "asset")},
        }, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.debug("could not cache the update check: %s", exc)

    return payload


# ---------------------------------------------------------------------------
# downloading + verifying
# ---------------------------------------------------------------------------

def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def verify_sha256(path: Path, expected: str) -> bool:
    expected = (expected or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return False
    return sha256_of(path) == expected


def _download(url: str, dest: Path) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60.0) as resp:  # noqa: S310
        with open(dest, "wb") as fh:
            shutil.copyfileobj(resp, fh)
    return dest


def extract_binary(archive: Path, workdir: Path) -> Path:
    """Pull ``kairos-code[.exe]`` out of the release archive."""
    wanted = {"kairos-code.exe", "kairos-code"}
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if Path(info.filename).name in wanted:
                    zf.extract(info, workdir)
                    return workdir / info.filename
    elif archive.name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                if member.isfile() and Path(member.name).name in wanted:
                    tf.extract(member, workdir, filter="data")
                    return workdir / member.name
    raise RuntimeError("the archive does not contain kairos-code")


# ---------------------------------------------------------------------------
# the swap
# ---------------------------------------------------------------------------

#: Windows: wait for the app to exit, replace it, start it again.
_WIN_HELPER = r"""@echo off
setlocal
set "PID={pid}"
:wait
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if not errorlevel 1 (
  timeout /t 1 /nobreak >nul
  goto wait
)
move /Y "{new}" "{exe}" >nul
start "" "{exe}"
del "%~f0"
"""

#: POSIX: same idea, no tasklist.
_POSIX_HELPER = """#!/bin/sh
pid={pid}
while kill -0 "$pid" 2>/dev/null; do sleep 1; done
mv -f "{new}" "{exe}"
chmod +x "{exe}"
nohup "{exe}" >/dev/null 2>&1 &
rm -f "$0"
"""


def _write_helper(exe: Path, staged: Path) -> Path:
    if sys.platform.startswith("win"):
        helper = exe.parent / "kairos-update.cmd"
        helper.write_text(
            _WIN_HELPER.format(pid=os.getpid(), new=str(staged), exe=str(exe)),
            encoding="utf-8")
    else:
        helper = exe.parent / "kairos-update.sh"
        helper.write_text(
            _POSIX_HELPER.format(pid=os.getpid(), new=str(staged), exe=str(exe)),
            encoding="utf-8")
        helper.chmod(helper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return helper


def _spawn_detached(helper: Path) -> None:
    if sys.platform.startswith("win"):
        subprocess.Popen(["cmd", "/c", str(helper)], close_fds=True,
                         creationflags=0x00000008 | 0x00000200,  # DETACHED | NEW_GROUP
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(["/bin/sh", str(helper)], close_fds=True,
                         start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def apply_update(
    check: Optional[Dict[str, Any]] = None,
    *,
    data_dir: Optional[Path] = None,
    download: Optional[Callable[[str, Path], Path]] = None,
    spawn: bool = True,
) -> Dict[str, Any]:
    """Download, verify, and stage the swap. Never raises.

    The install is only touched when every one of these holds: this is a frozen
    build, the platform allows self-replacement, the install directory is
    writable, and the release publishes a matching ``sha256``.
    """
    check = check if check is not None else check_for_update(data_dir=data_dir)
    out: Dict[str, Any] = {"ok": False, "stage": None, "restartRequired": False,
                           "reason": "ok", "error": None}

    if not check.get("hasUpdate"):
        out["reason"] = "no-update"
        return out
    asset = check.get("asset") or {}
    ok, reason = can_self_update()
    if not ok:
        out["reason"] = reason
        return out
    exe = executable_path()
    if exe is None:
        out["reason"] = "no-executable"
        return out
    if not asset.get("url"):
        out["reason"] = "no-asset-for-platform"
        return out
    if not re.fullmatch(r"[0-9a-f]{64}", str(asset.get("sha256") or "").lower()):
        # Deliberate: without a checksum we will not execute a download.
        out["reason"] = "no-checksum-published"
        return out

    work = Path(tempfile.mkdtemp(prefix="kairos-update-"))
    try:
        archive = work / str(asset["name"])
        (download or _download)(str(asset["url"]), archive)
        if not verify_sha256(archive, str(asset["sha256"])):
            out["reason"] = "checksum-mismatch"
            return out
        binary = extract_binary(archive, work)
        staged = exe.with_name(exe.name + ".new")
        shutil.copy2(binary, staged)
        out["stage"] = str(staged)
        out["sha256"] = sha256_of(staged)              # what we are about to run
        out["archiveSha256"] = str(asset["sha256"])    # what we verified
        helper = _write_helper(exe, staged)
        out["helper"] = str(helper)
        if spawn:
            _spawn_detached(helper)
        out["ok"] = True
        out["restartRequired"] = True
        return out
    except Exception as exc:
        out["reason"] = "download-failed"
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)
