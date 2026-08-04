"""`data/tags.yaml` kontrollü sözlüğünü okur.

Ön filtre (issue 06) ve `validate` lint'i (issue 03) aynı sözlüğü kullanır — etiketler
serbest metin olsaydı yazım farkı sessizce puan kaybettirirdi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from cv_updater.paths import TAGS_YAML


@dataclass(frozen=True)
class TagVocabulary:
    """Kanonik etiketler + ilan metninde geçebilecek diğer yazımları."""

    version: int
    categories: dict[str, tuple[str, ...]] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)

    @property
    def canonical(self) -> frozenset[str]:
        """Geçerli `x_meta.tags` değerlerinin tamamı."""
        return frozenset(tag for tags in self.categories.values() for tag in tags)

    def category_of(self, tag: str) -> str | None:
        for name, tags in self.categories.items():
            if tag in tags:
                return name
        return None

    def normalize(self, raw: str) -> str | None:
        """Serbest bir terimi kanonik etikete indirger; eşleşmezse None."""
        candidate = raw.strip().lower()
        candidate = self.aliases.get(candidate, candidate)
        return candidate if candidate in self.canonical else None

    def unknown(self, tags: list[str]) -> list[str]:
        """Sözlükte olmayan etiketler — `validate` bunları hata sayar."""
        known = self.canonical
        return [tag for tag in tags if tag not in known]


def load_tags(path: Path | None = None) -> TagVocabulary:
    """Sözlüğü diskten okur. Yol verilmezse `data/tags.yaml`."""
    target = path or TAGS_YAML
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    categories = {name: tuple(tags or ()) for name, tags in (data.get("categories") or {}).items()}
    aliases = {
        str(key).strip().lower(): str(value).strip().lower()
        for key, value in (data.get("aliases") or {}).items()
    }
    return TagVocabulary(
        version=int(data.get("version", 1)),
        categories=categories,
        aliases=aliases,
    )


@lru_cache(maxsize=1)
def default_vocabulary() -> TagVocabulary:
    """Varsayılan sözlük, bir kez okunur."""
    return load_tags()
