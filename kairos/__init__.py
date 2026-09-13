"""Kairos Code - Multi-Agent Collaboration Platform for Software Development."""

__version__ = "0.1.2"


def __getattr__(name: str):  # pragma: no cover - trivial import shim
    # Lazy attribute access so `import kairos` doesn't pull in the
    # agent / uvicorn stack until someone actually asks for it.
    if name == "cli":
        # importlib (not `from kairos import cli`) — the latter re-enters this
        # same __getattr__ and recurses until the interpreter gives up.
        import importlib
        return importlib.import_module("kairos.cli")
    raise AttributeError(f"module 'kairos' has no attribute {name!r}")

