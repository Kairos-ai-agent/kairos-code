"""Kairos Code - Multi-Agent Collaboration Platform for Software Development."""

__version__ = "0.1.0"


def __getattr__(name: str):  # pragma: no cover - trivial import shim
    # Lazy attribute access so `import kairos` doesn't pull in the
    # agent / uvicorn stack until someone actually asks for it.
    if name == "cli":
        from kairos import cli as _cli
        return _cli
    raise AttributeError(f"module 'kairos' has no attribute {name!r}")

