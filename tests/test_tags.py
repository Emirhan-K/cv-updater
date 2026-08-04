"""Kontrollü etiket sözlüğü — `x_meta.tags` serbest metin olmamalı."""

from __future__ import annotations

from pathlib import Path

from cv_updater.models.resume import Resume, Sections
from cv_updater.tags import load_tags


def test_vocabulary_loads(tags_yaml_path: Path) -> None:
    vocab = load_tags(tags_yaml_path)
    assert vocab.version >= 1
    assert vocab.categories
    assert "python" in vocab.canonical


def test_no_duplicate_tags_across_categories(tags_yaml_path: Path) -> None:
    vocab = load_tags(tags_yaml_path)
    flat = [tag for tags in vocab.categories.values() for tag in tags]
    duplicates = {tag for tag in flat if flat.count(tag) > 1}
    assert not duplicates, f"birden fazla kategoride: {duplicates}"


def test_tags_are_lowercase_kebab(tags_yaml_path: Path) -> None:
    vocab = load_tags(tags_yaml_path)
    bad = [t for t in vocab.canonical if t != t.lower().strip() or " " in t]
    assert not bad, f"kanonik olmayan yazım: {bad}"


def test_aliases_point_at_canonical_tags(tags_yaml_path: Path) -> None:
    vocab = load_tags(tags_yaml_path)
    dangling = {a: t for a, t in vocab.aliases.items() if t not in vocab.canonical}
    assert not dangling, f"var olmayan etikete işaret eden alias'lar: {dangling}"


def test_aliases_are_not_themselves_canonical(tags_yaml_path: Path) -> None:
    """Bir alias aynı zamanda kanonik etiket olamaz — normalize sonsuz belirsizleşir."""
    vocab = load_tags(tags_yaml_path)
    clashing = [a for a in vocab.aliases if a in vocab.canonical]
    assert not clashing, f"hem alias hem kanonik: {clashing}"


def test_normalize_resolves_aliases(tags_yaml_path: Path) -> None:
    vocab = load_tags(tags_yaml_path)
    assert vocab.normalize("Next.js") == "nextjs"
    assert vocab.normalize("MSSQL") == "sql-server"
    assert vocab.normalize("  Python  ") == "python"
    assert vocab.normalize("cobol") is None


def test_category_lookup(tags_yaml_path: Path) -> None:
    vocab = load_tags(tags_yaml_path)
    assert vocab.category_of("python") == "dil"
    assert vocab.category_of("erp") == "kurumsal"
    assert vocab.category_of("yok-boyle-bir-sey") is None


def test_unknown_reports_only_unknown_tags(tags_yaml_path: Path) -> None:
    vocab = load_tags(tags_yaml_path)
    assert vocab.unknown(["python", "uydurma-etiket"]) == ["uydurma-etiket"]


def test_example_file_uses_only_known_tags(
    tags_yaml_path: Path, master_example_raw: str
) -> None:
    """Örnek dosya sözlüğün dışına çıkmamalı — yoksa lint'in kendi örneği hata verir."""
    vocab = load_tags(tags_yaml_path)
    resume = Resume.model_validate_json(master_example_raw)

    offenders: list[str] = []
    for name in Sections.model_fields:
        for item in getattr(resume.sections, name).items:
            if item.x_meta:
                offenders += [f"{item.id}:{t}" for t in vocab.unknown(item.x_meta.tags)]
    for owner_id, bullet in resume.iter_bullets():
        if bullet.x_meta:
            offenders += [f"{owner_id}/{bullet.id}:{t}" for t in vocab.unknown(bullet.x_meta.tags)]

    assert not offenders, f"sözlük dışı etiketler: {offenders}"
