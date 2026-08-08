"""Proje içi standart yollar.

Depo kökü, kurulu paketten yukarı çıkılarak bulunur; `uv run` ile depo içinden
çalıştırıldığında `data/` ve `out/` beklenen yerlere düşer.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent.parent

DATA_DIR = REPO_ROOT / "data"
OUT_DIR = REPO_ROOT / "out"
#: Başvuru arşivi (issue 12). `out/` deneme alanı, burası gönderilen sürüm.
#: Kişisel + şirket verisi taşıdığı için gitignore'da.
APPLICATIONS_DIR = REPO_ROOT / "applications"

MASTER_JSON = DATA_DIR / "master.json"
MASTER_EXAMPLE_JSON = DATA_DIR / "master.example.json"
TAGS_YAML = DATA_DIR / "tags.yaml"
