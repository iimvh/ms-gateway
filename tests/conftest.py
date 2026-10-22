from pathlib import Path

import pytest

from gateway.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(tap_dir=tmp_path / "tap")
