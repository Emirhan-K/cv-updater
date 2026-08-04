"""Paylaşılan fixture'lar. Hiçbir test ağa çıkmaz."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: `https://rxresu.me/schema.json` anlık görüntüsü. Testler ağa çıkmasın diye
#: depoda tutuluyor; şema değişirse yeniden indirilip commit'lenmeli.
RXRESUME_SCHEMA_PATH = FIXTURES / "rxresume.schema.json"


@pytest.fixture(scope="session")
def rxresume_schema() -> dict[str, Any]:
    return json.loads(RXRESUME_SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def master_example_path() -> Path:
    return REPO_ROOT / "data" / "master.example.json"


@pytest.fixture(scope="session")
def master_example_raw(master_example_path: Path) -> str:
    return master_example_path.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def tags_yaml_path() -> Path:
    return REPO_ROOT / "data" / "tags.yaml"
