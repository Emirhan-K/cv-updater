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
from typing import Any

SCHEMA_URL = "https://rxresu.me/schema.json"
SCHEMA_FILENAME = "rxresume.schema.json"


@lru_cache(maxsize=1)
def rxresume_schema() -> dict[str, Any]:
    """Depodaki şema anlık görüntüsünü okur."""
    resource = files(__package__).joinpath(SCHEMA_FILENAME)
    return json.loads(resource.read_text(encoding="utf-8"))
