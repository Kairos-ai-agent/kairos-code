"""The publish gate: a build that carries a credential must not be publishable.

The scanner's own rules are the easy half. The half that matters is the output:
a gate that echoes what it found is a leak with a nicer name, so every test here
that has a secret in it also asserts the secret is absent from what was printed.
"""
from __future__ import annotations

import importlib.util
import io
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

SCANNER = Path(__file__).resolve().parents[1] / "scripts" / "scan_binary_secrets.py"

REAL_SHAPED = "sk-aB3xK9mQ2pL7wR4tY6uZ1nE8sD5fG0hJ"

spec = importlib.util.spec_from_file_location("scan_binary_secrets", SCANNER)
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


def run_report(path: Path, **kwargs) -> tuple:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scanner.report(path, **kwargs)
    return code, out.getvalue() + err.getvalue()


# ---------------------------------------------------------------------------
# what is a secret
# ---------------------------------------------------------------------------

def test_a_real_looking_key_is_refused(tmp_path):
    art = tmp_path / "build.bin"
    art.write_bytes(b'{"apiKey": "' + REAL_SHAPED.encode() + b'"}')

    result = scanner.scan(art, local_values=[])
    assert [h["class"] for h in result["hits"]] == ["openai/deepseek-style key"]


def test_documentation_placeholders_are_not_secrets(tmp_path):
    """The bundled skills are full of examples; a gate that flags them is ignored."""
    art = tmp_path / "build.bin"
    art.write_bytes(
        b'const API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx";   // example\n'
        b'apiKey: "sk-REPLACE_ME_WITH_YOUR_OWN_KEY_VALUE"\n'
        b'AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n')

    assert scanner.scan(art, local_values=[])["hits"] == []


def test_a_token_of_another_shape_is_refused(tmp_path):
    art = tmp_path / "build.bin"
    art.write_bytes(b"GITHUB_TOKEN=ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8\n")
    assert [h["class"] for h in scanner.scan(art, local_values=[])["hits"]] == ["github token"]


def test_an_aws_key_of_the_real_shape_is_refused(tmp_path):
    """Upper-case and digits only: a mixed-case rule would reject the real thing."""
    art = tmp_path / "build.bin"
    art.write_bytes(b"AWS_ACCESS_KEY_ID=AKIA3XK9M2P7W4R6TYU8J\n")
    assert [h["class"] for h in scanner.scan(art, local_values=[])["hits"]] == [
        "aws access key id"]


def test_a_private_key_block_is_refused(tmp_path):
    art = tmp_path / "build.bin"
    art.write_bytes(b"-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNz...\n")
    assert [h["class"] for h in scanner.scan(art, local_values=[])["hits"]] == [
        "private key block"]


def test_a_value_from_this_machine_is_found_even_if_it_looks_like_nothing(tmp_path):
    """Shape matching cannot see a provider whose keys look like words."""
    art = tmp_path / "build.bin"
    art.write_bytes(b'{"apiKey": "totally-ordinary-looking-password-value"}')

    result = scanner.scan(art, local_values=[b"totally-ordinary-looking-password-value"])
    assert [h["kind"] for h in result["hits"]] == ["value"]
    assert result["local_values_checked"] == 1


def test_a_bundled_settings_file_is_refused_by_name(tmp_path):
    art = tmp_path / "build.zip"
    with zipfile.ZipFile(art, "w") as zf:
        zf.writestr("web/dist/index.html", "<div id='root'></div>")
        zf.writestr("data/settings.json", "{}")

    classes = [h["class"] for h in scanner.scan(art, local_values=[])["hits"]]
    assert "bundled data/settings.json" in classes


def test_the_string_settings_json_in_a_document_is_not_a_hit(tmp_path):
    """Our own README says `data/settings.json`. A byte-grep would red every build."""
    art = tmp_path / "build.zip"
    with zipfile.ZipFile(art, "w") as zf:
        zf.writestr("README.md", "copy data/settings.json next to the binary")

    assert scanner.scan(art, local_values=[])["hits"] == []


# ---------------------------------------------------------------------------
# what it prints, and what it must never print
# ---------------------------------------------------------------------------

def test_a_clean_artefact_passes(tmp_path):
    art = tmp_path / "build.bin"
    art.write_bytes(b"def hello():\n    return 'nothing to see here'\n")

    code, output = run_report(art)
    assert code == 0
    assert "no credential shapes" in output


def test_a_hit_fails_the_build_without_echoing_the_value(tmp_path):
    art = tmp_path / "build.bin"
    art.write_bytes(b'apiKey: "' + REAL_SHAPED.encode() + b'"')

    code, output = run_report(art)
    assert code == 1, "a hit must fail the build"
    assert "openai/deepseek-style key" in output
    assert REAL_SHAPED not in output, "the report must not carry the value"


def test_a_local_value_is_reported_by_class_only(tmp_path):
    value = b"totally-ordinary-looking-password-value"
    art = tmp_path / "build.bin"
    art.write_bytes(b'apiKey: "' + value + b'"')

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scanner.report(art, local_values=[value])
    output = out.getvalue() + err.getvalue()

    assert code == 1, "a value from this machine must fail the build"
    assert "value from this machine" in output, "it has to say which class of hit"
    assert value.decode() not in output, "and never the value itself"


def test_a_missing_artefact_is_not_a_pass(tmp_path):
    code, output = run_report(tmp_path / "nope.bin")
    assert code == 2, "absent input must not look like a clean scan"
    assert "no such artefact" in output


@pytest.mark.parametrize("name", ["build.zip", "build.bin", "build"])
def test_any_container_is_scanned_as_raw_bytes(tmp_path, name):
    """The raw pass is what makes this a gate rather than a sampler."""
    art = tmp_path / name
    art.write_bytes(b"\x00\x01" + REAL_SHAPED.encode() + b"\x02\x03")

    assert [h["where"] for h in scanner.scan(art, local_values=[])["hits"]] == ["raw"]

# ---------------------------------------------------------------------------
# the wiring: a scanner nobody calls is decoration
# ---------------------------------------------------------------------------

BUILD = Path(__file__).resolve().parents[1] / "scripts" / "build_binary.py"


def _fake_build(tmp_path, monkeypatch, artefact_bytes, name="test-artefact"):
    """Run build_binary.main() with PyInstaller replaced by a stub.

    The tests above cover what the scanner refuses. This one covers whether the
    refusal is reachable at all: the question it answers is "could a build that
    carried a secret ever reach a user", and answering that means exercising the
    build's own main(). Only the slow step is stubbed -- the scanner stays real.
    """
    import os as _os
    import subprocess as sub
    import sys as _sys

    spec_b = importlib.util.spec_from_file_location("build_binary_under_test", BUILD)
    module = importlib.util.module_from_spec(spec_b)
    spec_b.loader.exec_module(module)

    out = tmp_path / "dist"
    real_run = sub.run

    def fake_run(cmd, *a, **kw):
        if any("PyInstaller" in str(part) for part in cmd):
            artefact = out / (f"{name}.exe" if _os.name == "nt" else name)
            artefact.parent.mkdir(parents=True, exist_ok=True)
            artefact.write_bytes(artefact_bytes)
            return sub.CompletedProcess(cmd, 0, b"", b"")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(_sys, "argv", ["build_binary.py", "--out", str(out), "--name", name])
    return module.main()


def test_a_build_that_carries_a_secret_refuses_to_hand_it_over(tmp_path, monkeypatch, capsys):
    code = _fake_build(tmp_path, monkeypatch, b"prefix" + REAL_SHAPED.encode())

    printed = capsys.readouterr()
    assert code == 1, "the build handed over an artefact that carried a secret"
    assert "refusing" in printed.err
    # and the refusal must not print what it found -- same rule as the scanner
    assert REAL_SHAPED not in printed.out + printed.err


def test_a_clean_build_still_succeeds(tmp_path, monkeypatch):
    assert _fake_build(tmp_path, monkeypatch, b"an ordinary artefact") == 0


def test_the_scan_runs_after_the_size_is_reported(tmp_path, monkeypatch, capsys):
    """Order matters for diagnosis: the OK line must appear before a refusal."""
    _fake_build(tmp_path, monkeypatch, b"prefix" + REAL_SHAPED.encode())
    printed = capsys.readouterr()
    assert "[build] OK" in printed.out, "the artefact size is reported before the scan"
