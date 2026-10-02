"""Global "full access" switch for the agent's tool sandbox.

Kairos normally confines the agent's tools to the project directory:

* the terminal tool only runs an allow-listed set of commands through
  ``create_subprocess_exec`` (no shell), rejects shell operators, and
  refuses any argv token that resolves to an existing path outside the
  working directory;
* the file tools raise ``PermissionError("Path outside project
  directory")`` for anything outside the project root, and the
  auto-checkpointer / enhanced sandbox refuse paths outside their root.

Some users run Kairos entirely on their own machine, on their own
input, and want the agent to be able to touch any file and run any
command — shell pipelines included. This module is the single source
of truth for that opt-in. It is enabled when either source is true:

* environment variable ``KAIROS_FULL_ACCESS`` set to a truthy value
  (``1`` / ``true`` / ``yes`` / ``on``, case-insensitive), or
* ``fullAccess: true`` in ``settings.json`` (the ``Settings`` field,
  settable through ``POST /api/projects/settings``).

Both default to off, so behaviour is unchanged unless the user
explicitly opts in. The check is cheap (an env lookup plus an in-memory
settings read) and is re-evaluated on every call, so flipping the
settings value takes effect without a restart.
"""
from __future__ import annotations

import os

_TRUTHY = {"1", "true", "yes", "on"}


def is_full_access() -> bool:
    """Return ``True`` when the user has opted into unrestricted access.

    The environment variable is checked first (cheapest), then
    ``settings.json:fullAccess``. Either source being truthy enables
    it. A falsey env value does *not* force it off — the settings value
    is still consulted, matching the "either source, whichever is true"
    contract. Any error while reading the settings store is treated as
    "not enabled" (the safe default).
    """
    env_value = os.environ.get("KAIROS_FULL_ACCESS", "").strip().lower()
    if env_value in _TRUTHY:
        return True
    try:
        from kairos.settings_store import get_store
        return bool(get_store().get().fullAccess)
    except Exception:  # noqa: BLE001
        return False
