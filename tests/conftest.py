"""Shared pytest fixtures for the clicketysplit test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def experiment_dir(tmp_path: Path) -> Path:
    """Return a fresh empty directory suitable for use as an experiment root."""
    exp = tmp_path / "experiment"
    exp.mkdir()
    return exp
