"""Refuse to publish a build that carries a secret.

The question "is the packaged exe sanitised?" was first answered by hand: unpack
the artefact, scan every archive entry and every raw byte for the keys that exist
on this machine, and report counts. That answer is only good for the build it was
run on, so this is the same check as a gate: it runs at the end of
`scripts/build_binary.py` and again in the release workflow, and a hit fails the
build.

Two layers, on purpose:

* **Shapes** — `sk-…`, `ghp_…`, `AKIA…`, private-key headers, JWTs — catch a key
  that came in from anywhere, including a dependency or an imported file.
* **The actual values** — when the machine that built the artefact has settings
  files (the developer's own), their secret values are searched for byte for
  byte. Shape matching cannot see a provider whose keys do not look like keys;
  this can, and it only ever reports a count.

Nothing here ever prints a secret, a prefix of one, or its length: a hit is
reported by class and location, because a scanner that echoes what it found is a
leak with a nicer name.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path

# Shapes worth refusing. Deliberately conservative: every one of these is long
# and structured enough that a false positive means someone typed a real-looking
# example into documentation, which is worth a look anyway.
SHAPES: list[tuple[str, re.Pattern[bytes]]] = [
    ("openai/deepseek-style key", re.compile(rb"sk-[A-Za-z0-9]{20,}")),
    ("anthropic-style key", re.compile(rb"sk-ant-[A-Za-z0-9\-_]{20,}")),
    ("github token", re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("github fine-grained token", re.compile(rb"github_pat_[A-Za-z0-9_]{50,}")),
    ("aws access key id", re.compile(rb"(?:AKIA|ASIA)[0-9A-Z]{16}")),
    ("slack token", re.compile(rb"xox[abpros]-[A-Za-z0-9\-]{10,}")),
    ("google api key", re.compile(rb"AIza[0-9A-Za-z\-_]{35}")),
    ("huggingface token", re.compile(rb"hf_[A-Za-z0-9]{30,}")),
    ("private key block",
     re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
]

# Local paths that must never be inside a published artefact.
FORBIDDEN_ENTRIES = ("data/settings.json", "/data/settings.json", "\\data\\settings.json")

SETTINGS_CANDIDATES = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "kairos-code" / "data" / "settings.json",
    Path.home() / ".kairos" / "data" / "settings.json",
)


def _looks_random(body: bytes) -> bool:
    """Is this actually a key, or a documentation placeholder?

    Shape alone is not enough: the bundled skills contain examples that match
    `sk-[A-Za-z0-9]{20,}` and would turn this gate red on every honest build,
    which is how a check gets ignored. A real key is mixed-case, carries digits,
    and uses a wide alphabet; `sk-xxxxxxxx…`, `sk-REPLACE_ME`, `sk-your-key-here`
    are none of those.
    """
    letters = [c for c in body if 65 <= c <= 90 or 97 <= c <= 122]
    if not letters:
        return False
    upper = sum(1 for c in letters if 65 <= c <= 90)
    lower = len(letters) - upper
    digits = sum(1 for c in body if 48 <= c <= 57)
    distinct = len(set(body))

    if upper >= 4 and lower >= 4 and digits >= 2 and distinct >= 12:
        return True
    # AWS access key ids are upper-case and digits only, so a mixed-case rule
    # rejects the real thing. The canonical documentation example
    # (AKIA + IOSFODNN7EXAMPLE) has 13 distinct characters; real ids are drawn
    # from base32 and land well above that, so require a wider alphabet.
    if lower == 0 and upper >= 10 and digits >= 2 and distinct >= 15:
        return True
    return False


def _local_secret_values() -> list[bytes]:
    """Values from this machine's settings files. Counts only, ever."""
    values: list[bytes] = []
    for path in SETTINGS_CANDIDATES:
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:  # noqa: BLE001 - a settings file we cannot read is not a failure
            continue
        for key, value in (data.items() if isinstance(data, dict) else []):
            if isinstance(value, str) and len(value) >= 24 and "key" in str(key).lower():
                values.append(value.encode("utf-8"))
    # Deduplicate; keep order for stable reporting.
    return list(dict.fromkeys(values))


def _entries(path: Path) -> Iterable[tuple[str, bytes]]:
    """Archive members of a frozen build, when it has any.

    PyInstaller keeps its payload in its own CArchive, so the reader comes from
    PyInstaller rather than `zipfile`; anything else (a wheel, a zip) is read
    with `zipfile`. A file that is neither is still scanned as raw bytes.
    """
    try:
        from PyInstaller.archive.readers import CArchiveReader  # type: ignore

        archive = CArchiveReader(str(path))
        for name, entry in archive.toc.items():
            try:
                extracted = archive.extract(name)
                payload = extracted.read() if hasattr(extracted, "read") else extracted
            except Exception:  # noqa: BLE001 - unreadable member is not a scan failure
                continue
            if isinstance(payload, bytes):
                yield name, payload
        return
    except Exception:  # noqa: BLE001 - not a CArchive, or PyInstaller unavailable
        pass

    import zipfile

    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                try:
                    yield name, zf.read(name)
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001 - not a zip either; raw scan still applies
        return


def scan(path: Path, local_values: list[bytes] | None = None) -> dict[str, object]:
    if local_values is None:
        local_values = _local_secret_values()

    hits: list[dict[str, object]] = []
    scanned_entries = 0
    raw = path.read_bytes()
    blob = raw

    def check(where: str, payload: bytes) -> None:
        for label, pattern in SHAPES:
            for match in pattern.finditer(payload):
                body = match.group(0)
                # Private-key headers are structural, not random text.
                if label != "private key block" and not _looks_random(body):
                    continue
                hits.append({"class": label, "where": where, "kind": "shape"})
                break
        for index, value in enumerate(local_values, start=1):
            if value and value in payload:
                hits.append({"class": "value from this machine (#%d)" % index,
                             "where": where, "kind": "value"})
        # (filename checks live in the entry loop below: the string
        # "data/settings.json" appears in our own documentation, and a grep of
        # the bytes would flag every honest build.)

    for name, payload in _entries(path):
        scanned_entries += 1
        if any(fragment in name for fragment in FORBIDDEN_ENTRIES):
            hits.append({"class": "bundled data/settings.json",
                         "where": "archive:" + name, "kind": "entry"})
        check("archive:" + name, payload)

    # The raw pass is what makes this a gate rather than a sampler: it sees the
    # bootloader, the appended archive, and everything the reader above skipped.
    check("raw", blob)

    return {
        "artifact": str(path),
        "bytes": len(blob),
        "archive_entries": scanned_entries,
        "local_values_checked": len(local_values),
        "hits": hits,
    }


def report(path: Path, json_out: bool = False,
           local_values: list[bytes] | None = None) -> int:
    """`local_values` is injectable so a test can plant one and prove the report
    still does not echo it."""
    try:
        result = scan(path, local_values=local_values)
    except FileNotFoundError:
        print("FAIL: no such artefact: %s" % path, file=sys.stderr)
        return 2

    if json_out:
        print(json.dumps(result, indent=2, ensure_ascii=False))

    hits = result["hits"]  # type: ignore[assignment]
    if not hits:
        print("[secrets] %d bytes, %d archive entries, no credential shapes, "
              "no bundled settings" % (result["bytes"], result["archive_entries"]))
        return 0

    print("FAIL: %d suspected secret(s) inside %s" % (len(hits), path), file=sys.stderr)
    for hit in hits:  # type: ignore[union-attr]
        print("  - %s in %s (%s)" % (hit["class"], hit["where"], hit["kind"]),
              file=sys.stderr)
    print("  A published artefact carries no credentials. Nothing was printed "
          "that could identify the value; check the location above.", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="scan_binary_secrets.py")
    parser.add_argument("artifact", help="the built artefact to scan")
    parser.add_argument("--json", action="store_true", help="also print the report as JSON")
    args = parser.parse_args()
    return report(Path(args.artifact).expanduser().resolve(), args.json)


if __name__ == "__main__":
    raise SystemExit(main())
