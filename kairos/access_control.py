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


def _settings_full_access() -> bool:
    """``KAIROS_FULL_ACCESS`` as loaded by ``kairos.config.settings``.

    A value written into the project's ``.env`` file is not in ``os.environ``
    -- pydantic-settings loads it onto the ``Settings`` object -- so reading
    that field is what makes a ``.env`` fallback take effect. Never raises.
    """
    try:
        from kairos.config.settings import settings

        value = str(getattr(settings, "full_access", "") or "").strip().lower()
    except Exception:  # noqa: BLE001 - a config read must not break the check
        return False
    return value in _TRUTHY


def is_full_access() -> bool:
    """Return ``True`` when the user has opted into unrestricted access.

    Three sources are consulted, in order, and *any* truthy one enables it:

    1. the process environment variable ``KAIROS_FULL_ACCESS`` (cheapest);
    2. the same variable as loaded by ``kairos.config.settings`` from the
       project's ``.env`` file (env vars are not the only way to set it);
    3. ``settings.json:fullAccess``.

    A falsey value at any tier does *not* force the switch off — the later
    tiers are still consulted, matching the "either source, whichever is true"
    contract. Any error while reading the settings store is treated as "not
    enabled" (the safe default). The truthy spellings are always
    ``{"1", "true", "yes", "on"}`` (trimmed, case-insensitive).
    """
    env_value = os.environ.get("KAIROS_FULL_ACCESS", "").strip().lower()
    if env_value in _TRUTHY:
        return True
    if _settings_full_access():
        return True
    try:
        from kairos.settings_store import get_store
        return bool(get_store().get().fullAccess)
    except Exception:  # noqa: BLE001
        return False
