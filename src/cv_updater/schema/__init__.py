"""Reactive Resume JSON Schema anlık görüntüsü.

`https://rxresu.me/schema.json` çalışma zamanında **çekilmez** — şema depoda tutulur,
böylece `validate` çevrimdışı çalışır ve testler ağa çıkmaz. Reactive Resume şemayı
değiştirirse bu dosya elle yenilenip commit'lenmeli:

    Invoke-WebRequest https://rxresu.me/schema.json `
      -OutFile src/cv_updater/schema/rxresume.schema.json
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

SCHEMA_URL = "https://rxresu.me/schema.json"
SCHEMA_FILENAME = "rxresume.schema.json"


@lru_cache(maxsize=1)
def rxresume_schema() -> dict[str, Any]:
    """Depodaki şema anlık görüntüsünü okur."""
    resource = files(__package__).joinpath(SCHEMA_FILENAME)
    return json.loads(resource.read_text(encoding="utf-8"))


def schema_path() -> Path:
    """Anlık görüntünün disk yolu — yenileme buraya yazıyor."""
    return Path(str(files(__package__).joinpath(SCHEMA_FILENAME)))


def refresh_schema(url: str = SCHEMA_URL, *, timeout: float = 30.0) -> tuple[Path, bool]:
    """Canlı şemayı çeker ve anlık görüntüyü günceller.

    `(yol, değişti mi)` döndürür. **Ağa çıkan tek yardımcı bu**; yalnızca
    `validate --refresh-schema` çağırıyor, normal akış hiçbir zaman çağırmıyor —
    şemanın çevrimdışı okunabilmesi anlık görüntünün varlık sebebi.
    """
    import urllib.request

    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))

    target = schema_path()
    fresh = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    changed = not target.exists() or target.read_text(encoding="utf-8") != fresh
    if changed:
        target.write_text(fresh, encoding="utf-8", newline="\n")
        rxresume_schema.cache_clear()
    return target, changed
