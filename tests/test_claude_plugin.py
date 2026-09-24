"""The Claude plugin converter: licences, rewriting, and what actually landed.

Every test here runs offline. The fetcher is a parameter of every public
function and is resolved at call time, so the fixtures below can be — and are —
captured from the live endpoints rather than invented: the tree is a real
excerpt of GitHub's git-trees response for ``anthropics/claude-plugins-official``
(its real shas, its real sizes), the manifest is the real ``plugin.json`` of
``plugins/code-review``, the hooks file is ``plugins/hookify/hooks/hooks.json``
verbatim, and the MCP file is ``external_plugins/context7/.mcp.json`` verbatim.

The point of testing against the captured shapes instead of a convenient one is
the same as in ``test_market_sources.py``: a plugin's components are *not*
declared anywhere. ``plugin.json`` carries a name, a description and an author,
and everything else is found by the directory it lives in — so a converter that
believes its own idea of the layout would pass a hand-written fixture and fail
on every real plugin.
"""

from __future__ import annotations

import io
import json
import tarfile

import pytest
import yaml

from kairos.extensions import claude_plugin as cp
from kairos.extensions import market_sources as ms


@pytest.fixture(autouse=True)
def _clean_cache():
    ms.clear_cache()
    yield
    ms.clear_cache()


# --------------------------------------------------------------------------- fixtures

#: The real ``.claude-plugin/plugin.json`` of ``plugins/code-review``.
#: Read it for what it does *not* say: no commands, no agents, no skills.
CODE_REVIEW_PLUGIN_JSON = {
    "name": "code-review",
    "description": ("Automated code review for pull requests using multiple "
                    "specialized agents with confidence-based scoring"),
    "author": {"name": "Anthropic", "email": "support@anthropic.com"},
}

#: A real excerpt of
#: ``GET api.github.com/repos/anthropics/claude-plugins-official/git/trees/main?recursive=1``
#: — the shas and sizes are the ones the API returned, trimmed to this plugin
#: plus one file of a neighbouring plugin (which must not be copied).
CODE_REVIEW_TREE = {
    "sha": "6bfd4e0c6d3da6050984fa5ed8281d915fa7ed69",
    "truncated": False,
    "tree": [
        {"path": "plugins/code-review/.claude-plugin", "mode": "040000",
         "type": "tree", "sha": "f248f4a0b601c6cad117c9fa4e33ebd27d912c42"},
        {"path": "plugins/code-review/.claude-plugin/plugin.json", "mode": "100644",
         "type": "blob", "size": 234,
         "sha": "c48abfebfd8178c77596d85ec6f48a388d68ef58"},
        {"path": "plugins/code-review/LICENSE", "mode": "100644", "type": "blob",
         "size": 11358, "sha": "d645695673349e3947e8e5ae42332d0ac3164cd7"},
        {"path": "plugins/code-review/README.md", "mode": "100644", "type": "blob",
         "size": 7321, "sha": "1f0faeae"},
        {"path": "plugins/code-review/commands/code-review.md", "mode": "100644",
         "type": "blob", "size": 7422, "sha": "6b5a9d19"},
        {"path": "plugins/feature-dev/agents/code-architect.md", "mode": "100644",
         "type": "blob", "size": 4102, "sha": "7c1e0a34"},
    ],
}

#: The real text of ``plugins/hookify/hooks/hooks.json``, verbatim, including
#: the ``${CLAUDE_PLUGIN_ROOT}`` in every command.
HOOKS_JSON = """{
  "description": "Hookify plugin - User-configurable hooks from .local.md files",
  "hooks": {
    "PreToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \\"${CLAUDE_PLUGIN_ROOT}/hooks/pretooluse.py\\"",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
"""

#: The real ``external_plugins/context7/.mcp.json``, verbatim — note that the
#: credential is already a ``${VAR}`` reference, which is exactly the shape this
#: project's config rule asks for.
MCP_JSON = """{
  "mcpServers": {
    "context7": {
      "type": "http",
      "url": "https://mcp.context7.com/mcp?client=claude-code-plugin",
      "headers": {
        "Authorization": "${CONTEXT7_API_KEY:-}"
      }
    }
  }
}
"""

#: Real Apache-2.0 text (the header this repository's own bundled port carries).
APACHE_LICENSE = """                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.
"""

#: Real MIT text.
MIT_LICENSE = """MIT License

Copyright (c) 2024 Example

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software.
"""

#: Real GPL-3.0 opening — a licence this project cannot carry.
GPL_LICENSE = """                    GNU GENERAL PUBLIC LICENSE
                       Version 3, 29 June 2007

 Copyright (C) 2007 Free Software Foundation, Inc. <https://fsf.org/>
 Everyone is permitted to copy and distribute verbatim copies
 of this license document, but changing it is not allowed.
"""

#: A command whose body points at a Claude-only variable. It cannot be made to
#: work here, so it must be skipped rather than installed broken.
CLAUDE_SKILL_DIR_COMMAND = """---
description: A command that reads from its own skill directory
---

Read the job list:

- [scan](${CLAUDE_SKILL_DIR}/jobs/scan.md)
"""


ENTRY = {
    "name": "code-review",
    "description": CODE_REVIEW_PLUGIN_JSON["description"],
    "source": "anthropic-plugins",
    "upstream": "code-review",
    "kind": "plugin",
    "repo": "anthropics/claude-plugins-official",
    "ref": "main",
    "path": "plugins/code-review",
    "installable": True,
}

TREE_URL = "api.github.com/repos/anthropics/claude-plugins-official/git/trees/main"
MANIFEST_URL = ".claude-plugin/plugin.json"
LICENSE_URL = "/plugins/code-review/LICENSE"


def make_fetcher(payloads, seen=None):
    """A fetcher that answers from ``payloads``, keyed by a substring of the URL.

    Insertion order matters: the first matching key wins, so a caller with two
    files whose URLs overlap lists the more specific one first.
    """
    def fetch(url, timeout=None):
        if seen is not None:
            seen.append(url)
        for needle, payload in payloads.items():
            if needle in url:
                if isinstance(payload, Exception):
                    raise payload
                if isinstance(payload, (bytes, bytearray)):
                    return bytes(payload)
                if isinstance(payload, str):
                    return payload
                return json.loads(json.dumps(payload))
        raise AssertionError("unexpected URL: %s" % url)
    return fetch


def entry_with_tree(paths, **overrides):
    """The code-review entry, with a tree listing exactly ``paths``.

    Entries that exist in the captured tree keep their real sha and size; the
    rest are synthesised, because a test about an ``agents/`` directory should
    not have to carry a fixture for it.
    """
    entry = dict(ENTRY)
    entry.update(overrides)
    real = {t["path"]: t for t in CODE_REVIEW_TREE["tree"]}
    tree = {
        "sha": CODE_REVIEW_TREE["sha"],
        "truncated": False,
        "tree": [real.get(path, {"path": path, "mode": "100644", "type": "blob",
                                 "size": 120})
                 for path in paths],
    }
    return entry, tree


def converter_fetcher(tree, *, license_text=APACHE_LICENSE, files=None, manifest=None):
    payloads = {TREE_URL: tree}
    payloads.update(files or {})
    payloads.setdefault("commands/code-review.md",
                        "---\ndescription: review\n---\n\nDo it.\n")
    if manifest is not False:
        payloads[MANIFEST_URL] = manifest or CODE_REVIEW_PLUGIN_JSON
    if license_text is not None:
        payloads[LICENSE_URL] = license_text
    return make_fetcher(payloads)


# --------------------------------------------------------------------------- fetching


def test_the_manifest_is_fetched_from_the_plugins_own_directory():
    seen = []
    fetch = make_fetcher({MANIFEST_URL: CODE_REVIEW_PLUGIN_JSON}, seen)
    manifest = cp.fetch_plugin_manifest(ENTRY, fetch)

    assert manifest["name"] == "code-review"
    # jsDelivr, because raw.githubusercontent.com does not answer here; @main
    # because the entry names ref=main.
    assert seen == ["https://cdn.jsdelivr.net/gh/anthropics/claude-plugins-official"
                    "@main/plugins/code-review/.claude-plugin/plugin.json"]


def test_the_manifest_of_an_entry_with_no_repository_is_refused():
    with pytest.raises(ValueError) as err:
        cp.fetch_plugin_manifest({"name": "nowhere"}, make_fetcher({}))
    assert "GitHub repository" in str(err.value)


def test_the_file_listing_is_relative_to_the_plugin_and_skips_neighbours():
    fetch = make_fetcher({TREE_URL: CODE_REVIEW_TREE})
    files = cp.fetch_plugin_files(ENTRY, fetch)

    paths = [f["path"] for f in files]
    assert paths == [".claude-plugin/plugin.json", "LICENSE", "README.md",
                     "commands/code-review.md"]
    # feature-dev's file is in the same tree and must not appear here.
    assert not any("feature-dev" in p for p in paths)
    # Directories are not files.
    assert ".claude-plugin" not in paths


def test_a_truncated_repository_tree_is_still_reported():
    tree = dict(CODE_REVIEW_TREE, truncated=True)
    fetch = make_fetcher({TREE_URL: tree})
    _files, truncated = cp._tree(ENTRY, fetch, 20.0)
    assert truncated is True


# --------------------------------------------------------------------------- the archive
#
# The repository tarball is the primary listing now. It is the only source
# measured to be both complete and unthrottled: jsDelivr's file API came back
# empty for whole plugin directories of this very marketplace (claude-security 42
# files, code-modernization 29, cwc-makers 6 — all zero through the CDN), and the
# anonymous git-trees API allows 60 requests an hour, which is how a real install
# failed with "403 rate limit exceeded". These tests drive the archive with a real
# in-memory tar.gz and check the two properties the choice rests on: the file list
# and the file bodies come from the archive, and a member that would escape the
# destination is dropped.


def _archive_bytes(members, top="claude-plugins-official-main"):
    """A real gzipped tar, laid out the way codeload lays one out."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for rel, body in members.items():
            data = body.encode("utf-8") if isinstance(body, str) else body
            info = tarfile.TarInfo("%s/%s" % (top, rel))
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _archive_url(entry=None):
    """Where the archive of ``entry`` (default: the code-review one) is fetched."""
    entry = entry or ENTRY
    return cp.ARCHIVE_URL % (entry["repo"], entry.get("ref") or "HEAD")


def test_the_file_list_comes_from_the_archive_and_never_from_the_api():
    """The API is not asked at all — an unlisted URL would raise in the fetcher."""
    seen = []
    fetch = make_fetcher({_archive_url(): _archive_bytes({
        "plugins/code-review/commands/code-review.md": "---\ndescription: review\n---\n\nDo it.\n",
        "plugins/code-review/LICENSE": APACHE_LICENSE,
        "plugins/feature-dev/agents/code-architect.md": "not mine",
    })}, seen)

    files = cp.fetch_plugin_files(ENTRY, fetch)

    assert [f["path"] for f in files] == ["LICENSE", "commands/code-review.md"]
    # Sizes come from the archive's own bytes, not from re-encoding the text.
    assert [f["size"] for f in files] == [len(APACHE_LICENSE), 36]
    assert len(seen) == 1 and seen[0] == _archive_url()


def test_a_file_is_read_from_the_archive_rather_than_the_cdn():
    seen = []
    fetch = make_fetcher({_archive_url(): _archive_bytes({
        "plugins/code-review/LICENSE": APACHE_LICENSE,
    })}, seen)

    assert cp.fetch_plugin_file(ENTRY, "LICENSE", fetch) == APACHE_LICENSE
    assert seen == [_archive_url()]


def test_an_archive_member_that_would_escape_the_destination_is_dropped():
    """A member named under the plugin but resolving above it must not be written."""
    fetch = make_fetcher({_archive_url(): _archive_bytes({
        "plugins/code-review/../../../evil.sh": "rm -rf /",
        "plugins/code-review/commands/code-review.md": "---\ndescription: review\n---\n\nDo it.\n",
    })})

    files = cp.fetch_plugin_files(ENTRY, fetch)

    assert [f["path"] for f in files] == ["commands/code-review.md"]


def test_an_unreadable_archive_falls_back_to_the_api_listing():
    """The archive is the fast road; the API is still there when it is closed."""
    fetch = make_fetcher({
        "codeload.github.com": OSError("connection reset by peer"),
        TREE_URL: CODE_REVIEW_TREE,
    })

    files, truncated = cp._tree(ENTRY, fetch, 20.0)

    assert truncated is False
    assert "commands/code-review.md" in [f["path"] for f in files]


def test_a_plugin_installs_from_the_archive_without_the_cdn_or_the_api(tmp_path):
    """The whole install, on the archive plus the one manifest fetch it still needs."""
    seen = []
    fetch = make_fetcher({
        _archive_url(): _archive_bytes({
            "plugins/code-review/commands/code-review.md": "---\ndescription: review\n---\n\nDo it.\n",
            "plugins/code-review/LICENSE": APACHE_LICENSE,
            "plugins/code-review/.claude-plugin/plugin.json": json.dumps(CODE_REVIEW_PLUGIN_JSON),
        }),
        MANIFEST_URL: CODE_REVIEW_PLUGIN_JSON,
    }, seen)
    entry, _tree = entry_with_tree(["plugins/code-review/commands/code-review.md"])

    installed, warnings = cp.convert(entry, tmp_path / "code-review", fetch)

    assert warnings == []
    assert installed == tmp_path / "code-review"
    assert (installed / "kairos-plugin.yaml").exists()
    assert (installed / "LICENSE").read_text(encoding="utf-8") == APACHE_LICENSE
    assert (installed / "commands" / "code-review.md").exists()
    manifest = yaml.safe_load((installed / "kairos-plugin.yaml").read_text(encoding="utf-8"))
    assert manifest["capabilities"] == ["commands"]
    # No API call for the listing, and no per-file trip to the CDN.
    assert not any("api.github.com" in url for url in seen), seen
    assert not any("commands/code-review.md" in url for url in seen), seen


# --------------------------------------------------------------------------- licence


def test_the_licence_is_recognised_by_its_words_not_its_filename():
    assert cp.licence_kind(APACHE_LICENSE) == "Apache-2.0"
    assert cp.licence_kind(MIT_LICENSE) == "MIT"
    assert cp.licence_kind(GPL_LICENSE) is None
    assert cp.refused_licence(GPL_LICENSE) == "GPL"
    assert cp.licence_kind("All rights reserved. Contact sales.") is None
    assert cp.refused_licence("All rights reserved.") == "proprietary"


def test_the_root_licence_wins_over_a_nested_one():
    files = [{"path": "vendor/dep/LICENSE"}, {"path": "LICENSE"}]
    assert cp._find_licence(files) == "LICENSE"
    assert cp._find_licence([{"path": "README.md"}]) is None


# --------------------------------------------------------------------------- converting


def test_a_plugin_becomes_a_kairos_plugin_directory(tmp_path):
    entry, tree = entry_with_tree(["plugins/code-review/commands/code-review.md",
                                   "plugins/code-review/LICENSE"])
    dest = tmp_path / "code-review"
    installed, warnings = cp.convert(entry, dest, converter_fetcher(tree))

    assert installed == dest
    assert warnings == []
    assert (dest / "commands" / "code-review.md").exists()
    # The licence travels with the plugin — that is the condition for carrying it.
    assert (dest / "LICENSE").read_text(encoding="utf-8") == APACHE_LICENSE
    assert (dest / "ATTRIBUTION.md").exists()
    assert (dest / "kairos-plugin.yaml").exists()


def test_the_capabilities_are_the_directories_that_actually_exist(tmp_path):
    """The manifest declares nothing, so the layout is the only evidence."""
    paths = ["plugins/code-review/commands/code-review.md",
             "plugins/code-review/agents/reviewer.md",
             "plugins/code-review/skills/review/SKILL.md",
             "plugins/code-review/LICENSE"]
    entry, tree = entry_with_tree(paths)
    files = {"agents/reviewer.md": "---\ndescription: r\n---\n",
             "skills/review/SKILL.md": "---\nname: review\n---\n"}
    dest = tmp_path / "code-review"
    cp.convert(entry, dest, converter_fetcher(tree, files=files))

    manifest = yaml.safe_load((dest / "kairos-plugin.yaml").read_text(encoding="utf-8"))
    assert manifest["capabilities"] == ["skills", "agents", "commands"]
    # Nothing was declared, so nothing may be claimed.
    assert "hooks" not in manifest["capabilities"]
    assert "mcp" not in manifest["capabilities"]
    assert manifest["name"] == "code-review"
    assert manifest["source"] == "claude-plugins-official/code-review"
    # The description comes from the upstream manifest, folded like the bundled ports.
    assert "Automated code review" in manifest["description"]


def test_the_plugin_root_variable_is_rewritten_to_the_installed_directory(tmp_path):
    """The real hookify hooks file, which runs its scripts out of the plugin root."""
    entry, tree = entry_with_tree(["plugins/code-review/commands/code-review.md",
                                   "plugins/code-review/hooks/hooks.json",
                                   "plugins/code-review/hooks/pretooluse.py",
                                   "plugins/code-review/LICENSE"])
    files = {"hooks/hooks.json": HOOKS_JSON, "hooks/pretooluse.py": "print('ok')\n"}
    dest = tmp_path / "code-review"
    cp.convert(entry, dest, converter_fetcher(tree, files=files))

    hooks = (dest / "hooks" / "hooks.json").read_text(encoding="utf-8")
    assert "${CLAUDE_PLUGIN_ROOT}" not in hooks
    assert str(dest).replace("\\", "/") + "/hooks/pretooluse.py" in hooks
    # The rest of the file survives the rewrite.
    assert "PreToolUse" in hooks


def test_the_root_rewrite_can_name_the_final_directory_while_staging(tmp_path):
    """The installer stages in a temp dir but the file must point at the real one."""
    entry, tree = entry_with_tree(["plugins/code-review/commands/code-review.md",
                                   "plugins/code-review/LICENSE"])
    files = {"commands/code-review.md": "Run ${CLAUDE_PLUGIN_ROOT}/scripts/x.sh\n"}
    staging = tmp_path / "staging"
    final = tmp_path / "plugins" / "code-review"
    cp.convert(entry, staging, converter_fetcher(tree, files=files), root=final)

    body = (staging / "commands" / "code-review.md").read_text(encoding="utf-8")
    assert str(final).replace("\\", "/") in body
    assert "staging" not in body


def test_a_file_that_cannot_be_rewritten_is_skipped_and_named(tmp_path):
    entry, tree = entry_with_tree(["plugins/code-review/commands/code-review.md",
                                   "plugins/code-review/commands/scan.md",
                                   "plugins/code-review/LICENSE"])
    files = {"commands/scan.md": CLAUDE_SKILL_DIR_COMMAND}
    dest = tmp_path / "code-review"
    installed, warnings = cp.convert(entry, dest, converter_fetcher(tree, files=files))

    assert installed == dest
    # Not silently dropped: gone from disk, present in the warnings, by name.
    assert not (dest / "commands" / "scan.md").exists()
    assert (dest / "commands" / "code-review.md").exists()
    assert any("scan.md" in w and "CLAUDE_SKILL_DIR" in w for w in warnings), warnings


def test_a_plugin_with_no_licence_is_refused(tmp_path):
    entry, tree = entry_with_tree(["plugins/code-review/commands/code-review.md"])
    dest = tmp_path / "code-review"
    installed, warnings = cp.convert(entry, dest,
                                     converter_fetcher(tree, license_text=None))

    assert installed is None
    assert any("LICENSE" in w for w in warnings), warnings
    # Nothing was written: a plugin directory without its licence is not carried.
    assert not (dest / "kairos-plugin.yaml").exists()


def test_a_copyleft_licence_is_refused(tmp_path):
    entry, tree = entry_with_tree(["plugins/code-review/commands/code-review.md",
                                   "plugins/code-review/LICENSE"])
    dest = tmp_path / "code-review"
    installed, warnings = cp.convert(entry, dest,
                                     converter_fetcher(tree, license_text=GPL_LICENSE))

    assert installed is None
    assert any("GPL" in w for w in warnings), warnings
    assert not dest.exists()


def test_a_licence_that_cannot_be_read_is_refused(tmp_path):
    tree = dict(CODE_REVIEW_TREE)
    fetch = make_fetcher({TREE_URL: tree, MANIFEST_URL: CODE_REVIEW_PLUGIN_JSON,
                          LICENSE_URL: OSError("connection reset")})
    installed, warnings = cp.convert(ENTRY, tmp_path / "code-review", fetch)

    assert installed is None
    assert any("licence" in w and "connection reset" in w for w in warnings), warnings


def test_a_plugin_with_nothing_to_install_is_refused(tmp_path):
    entry, tree = entry_with_tree(["plugins/code-review/README.md",
                                   "plugins/code-review/LICENSE"])
    installed, warnings = cp.convert(entry, tmp_path / "code-review",
                                     converter_fetcher(tree))
    assert installed is None
    assert any("nothing to install" in w for w in warnings), warnings


def test_an_unreadable_file_listing_is_refused_with_the_reason(tmp_path):
    fetch = make_fetcher({TREE_URL: OSError("name or service not known")})
    installed, warnings = cp.convert(ENTRY, tmp_path / "code-review", fetch)

    assert installed is None
    assert any("listing" in w and "name or service not known" in w for w in warnings)


def test_the_attribution_records_the_upstream_url_and_the_original_manifest(tmp_path):
    entry, tree = entry_with_tree(["plugins/code-review/commands/code-review.md",
                                   "plugins/code-review/LICENSE"])
    dest = tmp_path / "code-review"
    cp.convert(entry, dest, converter_fetcher(tree))

    text = (dest / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "claude-plugins-official/code-review" in text
    assert "https://github.com/anthropics/claude-plugins-official/tree/main/plugins/code-review" in text
    # The manifest verbatim, so the record is checkable against the upstream.
    assert '"name": "code-review"' in text
    assert '"email": "support@anthropic.com"' in text


def test_the_attribution_says_so_when_the_manifest_could_not_be_read(tmp_path):
    entry, tree = entry_with_tree(["plugins/code-review/commands/code-review.md",
                                   "plugins/code-review/LICENSE"])
    dest = tmp_path / "code-review"
    _installed, warnings = cp.convert(entry, dest,
                                      converter_fetcher(tree, manifest=False))

    # A missing manifest is not a reason to refuse: the plugin still works.
    assert any("plugin.json" in w for w in warnings), warnings
    text = (dest / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "Original manifest: unavailable" in text


# --------------------------------------------------------------------------- mcp


def test_the_dot_mcp_json_is_kept_and_translated(tmp_path):
    """Claude's shape is kept verbatim; the shape this project loads is written too."""
    entry, tree = entry_with_tree(["plugins/code-review/commands/code-review.md",
                                   "plugins/code-review/.mcp.json",
                                   "plugins/code-review/LICENSE"])
    files = {".mcp.json": MCP_JSON}
    dest = tmp_path / "code-review"
    cp.convert(entry, dest, converter_fetcher(tree, files=files))

    assert (dest / ".mcp.json").read_text(encoding="utf-8") == MCP_JSON
    translated = yaml.safe_load((dest / "mcp.yaml").read_text(encoding="utf-8"))
    assert translated["mcp_servers"]["context7"]["transport"] == "http"
    assert translated["mcp_servers"]["context7"]["url"].startswith("https://mcp.context7.com/")
    # The credential stays a reference: a config file names it, never holds it.
    assert (translated["mcp_servers"]["context7"]["headers"]["Authorization"]
            == "${CONTEXT7_API_KEY:-}")
    manifest = yaml.safe_load((dest / "kairos-plugin.yaml").read_text(encoding="utf-8"))
    assert "mcp" in manifest["capabilities"]


def test_an_mcp_file_with_no_usable_server_is_not_claimed(tmp_path):
    entry, tree = entry_with_tree(["plugins/code-review/commands/code-review.md",
                                   "plugins/code-review/.mcp.json",
                                   "plugins/code-review/LICENSE"])
    dest = tmp_path / "code-review"
    _installed, warnings = cp.convert(
        entry, dest, converter_fetcher(tree, files={".mcp.json": '{"mcpServers": {}}'}))

    assert not (dest / ".mcp.json").exists()
    assert any(".mcp.json" in w for w in warnings), warnings
    manifest = yaml.safe_load((dest / "kairos-plugin.yaml").read_text(encoding="utf-8"))
    assert "mcp" not in manifest["capabilities"]


# --------------------------------------------------------------------------- the install path


def test_installing_a_plugin_is_idempotent_and_lands_in_the_plugin_directory(tmp_path):
    from kairos.extensions_install import install_plugin

    entry, tree = entry_with_tree(["plugins/code-review/commands/code-review.md",
                                   "plugins/code-review/LICENSE"])
    fetch = converter_fetcher(tree)
    dest = tmp_path / "plugins" / "code-review"

    first = install_plugin(entry, user_dir=tmp_path, fetch=fetch)
    assert first["ok"] is True and first["changed"] is True
    assert first["installed_path"] == str(dest)
    assert first["warnings"] == []
    assert (dest / "kairos-plugin.yaml").exists()

    # Idempotent: the second install writes nothing at all.
    second = install_plugin(entry, user_dir=tmp_path, fetch=fetch)
    assert second["changed"] is False
    assert (dest / "kairos-plugin.yaml").exists()

    # A refused conversion leaves no directory half-written behind it. (The
    # cache is dropped first: it is keyed by URL, and this re-uses one URL with
    # different bytes.)
    ms.clear_cache()
    refused, warnings = cp.convert(entry, tmp_path / "other",
                                   converter_fetcher(tree, license_text=GPL_LICENSE))
    assert refused is None and warnings
    assert not list(tmp_path.glob("code-review.*.staging"))


def test_a_refused_plugin_install_stages_nothing(tmp_path):
    from kairos.extensions_install import install_plugin

    entry, tree = entry_with_tree(["plugins/code-review/commands/code-review.md"])
    result = install_plugin(entry, user_dir=tmp_path,
                            fetch=converter_fetcher(tree, license_text=None))

    assert result["ok"] is False and result["changed"] is False
    assert result["installed_path"] is None
    assert any("LICENSE" in w for w in result["warnings"])
    assert not (tmp_path / "plugins" / "code-review").exists()
    # The staging directory is gone too: a temp tree left behind is a plugin
    # directory that looks installed to everything that globs.
    assert not list((tmp_path / "plugins").glob("code-review.*.staging"))


def test_the_api_installs_a_converted_plugin_and_reports_its_kind(tmp_path, monkeypatch):
    """The contract the extensions UI reads: ok, changed, kind, path, warnings."""
    from fastapi.testclient import TestClient

    import api.app as app_module

    entry, tree = entry_with_tree(["plugins/code-review/commands/code-review.md",
                                   "plugins/code-review/LICENSE"])
    # The marketplace directory as the API fetches it: one plugin, the
    # repository-relative source shape.
    marketplace = {"name": "claude-plugins-official", "plugins": [{
        "name": "code-review",
        "description": CODE_REVIEW_PLUGIN_JSON["description"],
        "author": CODE_REVIEW_PLUGIN_JSON["author"],
        "category": "development",
        "source": "./plugins/code-review",
        "homepage": "https://github.com/anthropics/claude-plugins-official",
    }]}
    payloads = {"marketplace.json": marketplace}
    payloads.update({TREE_URL: tree, MANIFEST_URL: CODE_REVIEW_PLUGIN_JSON,
                     LICENSE_URL: APACHE_LICENSE,
                     "commands/code-review.md": "---\ndescription: review\n---\n\nDo it.\n"})
    monkeypatch.setattr(ms, "_http_json", make_fetcher(payloads))
    monkeypatch.setattr(ms, "_http_text", make_fetcher(payloads))
    # The archive transport too: the converter reads the repository tarball before
    # it asks the API, and a run that left this one real would download 3 MB from
    # codeload inside a test that is supposed to be offline — and then assert
    # against live data.
    monkeypatch.setattr(ms, "_http_bytes", make_fetcher({
        "codeload.github.com": _archive_bytes({
            "plugins/code-review/commands/code-review.md": (
                "---\ndescription: review\n---\n\nDo it.\n"),
            "plugins/code-review/LICENSE": APACHE_LICENSE,
        })}))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    ms.clear_cache()

    installed = tmp_path / ".kairos" / "plugins" / "code-review"
    with TestClient(app_module.app) as client:
        got = client.post("/api/extensions/market/install",
                          json={"source": "anthropic-plugins", "id": "code-review"})
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["ok"] is True and body["changed"] is True
        assert body["kind"] == "plugin"
        assert body["installed_path"] == str(installed)
        assert body["warnings"] == []
        assert (installed / "kairos-plugin.yaml").exists()
        assert (installed / "commands" / "code-review.md").exists()

        # The plugin satisfies the loader this project already has.
        from kairos.plugins import PluginManager
        info = PluginManager(plugins_root=installed.parent).list_installed()
        assert [p.name for p in info] == ["code-review"]
        assert info[0].capabilities == ["commands"]

        again = client.post("/api/extensions/market/install",
                            json={"source": "anthropic-plugins", "id": "code-review"})
        assert again.status_code == 200
        assert again.json()["changed"] is False
