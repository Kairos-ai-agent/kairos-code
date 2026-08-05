"""Shared pytest fixtures for Kairos tests."""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_workspace(tmp_path):
    """Provide an isolated workspace directory for tool tests."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace