"""Build hook: ship the built Web UI inside the wheel, but only if it exists.

`pip install kairos-code` should give you the API *and* the interface
(`api/app.py` serves `<site-packages>/web/dist`). Two things were tried:

* `artifacts = ["web/dist"]` — measured: the wheel contained 0 `web/dist`
  entries, whether the option sat at `[tool.hatch.build]` or on the wheel
  target. It does not do what the name suggests for the wheel.
* a static `force-include` mapping — works, but hard-fails the build when the
  directory is absent:
  `FileNotFoundError: Forced include not found: .../web/dist`. That breaks
  `pip install -e .` on a fresh clone (the frontend is built after the Python
  package), which is exactly the first thing a new contributor does.

So the mapping is added conditionally: the directory is gitignored, and when it
has not been built yet the wheel simply ships the backend alone. Build it with
`cd web && npm install && npm run build` (the Dockerfile and
`scripts/ci_local.sh` do this).
"""
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Hatchling built-in `custom` hook (class name is fixed by the plugin)."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        dist = Path(self.root) / "web" / "dist"
        if not (dist / "index.html").is_file():
            self.app.display_info(
                "web/dist not built — the wheel will ship the API/CLI without "
                "the Web UI (run `cd web && npm install && npm run build` to "
                "include it)")
            return
        build_data.setdefault("force_include", {})["web/dist"] = "web/dist"
        self.app.display_info("web/dist found — including the Web UI in the wheel")
