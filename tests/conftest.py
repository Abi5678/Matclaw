"""Shared pytest fixtures for MatClaw tests."""
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(tmp_path):
    """Return a fresh temporary SQLite path."""
    return str(tmp_path / "test.sqlite3")
