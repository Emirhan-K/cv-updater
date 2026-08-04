"""Paylaşılan fixture'lar. Hiçbir test ağa çıkmaz."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def rxresume_schema() -> dict[str, Any]:
    """Pakete gömülü şema anlık görüntüsü — `validate` de çalışma zamanında bunu okur."""
    from cv_updater.schema import rxresume_schema as load

    return load()


@pytest.fixture(scope="session")
def master_example_path() -> Path:
    return REPO_ROOT / "data" / "master.example.json"


@pytest.fixture(scope="session")
def master_example_raw(master_example_path: Path) -> str:
    return master_example_path.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def tags_yaml_path() -> Path:
    return REPO_ROOT / "data" / "tags.yaml"
