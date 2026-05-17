"""Bundled demo recording and stimulus list.

Real demo data lands in task 14.
"""

from __future__ import annotations

from pathlib import Path


def setup_demo_experiment() -> Path:
    """Extract bundled demo data to a temporary directory and return its root.

    Raises
    ------
    NotImplementedError
        Always; populated in task 14.
    """
    raise NotImplementedError("demo data is provided by task 14")


__all__ = ["setup_demo_experiment"]
