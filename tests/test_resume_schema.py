"""Issue 02 kabul kriterleri: şema uyumu, x_meta sözleşmesi, kararlı madde id'leri."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from cv_updater.models.resume import Resume, Sections, XBullet, XMeta


@pytest.fixture(scope="module")
def example(master_example_raw: str) -> Resume:
    return Resume.model_validate_json(master_example_raw)


# --- kabul kriteri 1: örnek dosya modelden geçiyor -------------------------- #


def test_master_example_parses(example: Resume) -> None:
    assert example.basics.name
    assert len(example.sections.experience.items) == 2
    assert len(example.sections.skills.items) == 6


# --- kabul kriteri 2: dump canlı şemaya uyuyor ------------------------------ #


def test_dump_validates_against_rxresume_schema(
    example: Resume, rxresume_schema: dict[str, Any]
) -> None:
    dumped = example.model_dump(mode="json", exclude_none=True)
    errors = sorted(Draft202012Validator(rxresume_schema).iter_errors(dumped), key=str)
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors[:10])


def test_x_extensions_survive_the_dump(example: Resume) -> None:
    """Reactive Resume bilinmeyen alanları yok sayar; biz onları kaybetmemeliyiz."""
    dumped = example.model_dump(mode="json", exclude_none=True)
    first_exp = dumped["sections"]["experience"]["items"][0]
    assert first_exp["x_meta"]["tags"]
    assert first_exp["x_bullets"], "x_bullets dump'ta kayboldu"


def test_schema_allows_unknown_fields(rxresume_schema: dict[str, Any]) -> None:
    """`x_meta` uzantısının dayandığı varsayım: hiçbir yerde additionalProperties=false yok.

    Bu doğru değilse uzantı stratejisi çöker — o yüzden varsayımı test ediyoruz.
    """

    def walk(node: object, path: str = "") -> list[str]:
        found: list[str] = []
        if isinstance(node, dict):
            if node.get("additionalProperties") is False:
                found.append(path or "<root>")
            for key, value in node.items():
                found += walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                found += walk(value, f"{path}[{index}]")
        return found

    strict = walk(rxresume_schema)
    # `metadata.stylesheet` kapalı, ama oraya x_meta koymuyoruz.
    unexpected = [p for p in strict if "stylesheet" not in p]
    assert not unexpected, f"beklenmedik kapalı nesneler: {unexpected}"


# --- kabul kriteri 3: her maddenin kararlı bir id'si var -------------------- #


def test_every_bullet_has_a_stable_id(example: Resume) -> None:
    pairs = example.iter_bullets()
    assert pairs, "örnekte hiç x_bullet yok"
    for owner_id, bullet in pairs:
        assert bullet.id, f"{owner_id} altında id'siz madde"
        assert bullet.text.strip()


def test_bullet_ids_are_unique(example: Resume) -> None:
    ids = [bullet.id for _, bullet in example.iter_bullets()]
    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"tekrar eden madde id'leri: {duplicates}"


def test_item_ids_are_unique(example: Resume) -> None:
    ids: list[str] = []
    for name in Sections.model_fields:
        section = getattr(example.sections, name)
        ids += [item.id for item in section.items]
    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"tekrar eden öğe id'leri: {duplicates}"


# --- kabul kriteri 4: depoda kişisel veri yok ------------------------------- #


def test_example_holds_no_personal_data(master_example_raw: str) -> None:
    """Anonim örnek gerçek iletişim bilgisi taşımamalı (issue 02 kabul kriteri).

    İsim aramaları kelime sınırıyla yapılır: düz alt dizgi araması "kuru"yu
    "kurulumdan" içinde bulup yanlış alarm verir.
    """
    lowered = master_example_raw.lower()

    for literal in ("1emirhankuru", "@gmail.com", "+90 535", "linkedin.com/in/emirhan"):
        assert literal not in lowered, f"örnek dosyada kişisel veri: {literal}"

    for word in ("emirhan", "kuru", "preunec", "vidinsight", "kodlab"):
        assert not re.search(rf"\b{word}\b", lowered), f"örnek dosyada kişisel veri: {word}"


def test_master_json_is_gitignored() -> None:
    gitignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(
        encoding="utf-8"
    )
    assert "data/master.json" in gitignore


# --- x_meta / x_bullets sözleşmesi ------------------------------------------ #


def test_x_meta_defaults() -> None:
    meta = XMeta()
    assert meta.tags == []
    assert meta.always_include is False
    assert meta.impact is None and meta.lang is None


def test_x_meta_rejects_unknown_lang() -> None:
    with pytest.raises(ValidationError):
        XMeta(lang="de")


def test_x_meta_rejects_unknown_seniority() -> None:
    with pytest.raises(ValidationError):
        XMeta(seniority="principal")


def test_bullet_requires_non_empty_text() -> None:
    with pytest.raises(ValidationError):
        XBullet(id="x", text="")


def test_unknown_top_level_fields_are_preserved() -> None:
    """rxresu.me yeni alan eklerse round-trip'te kaybetmemeliyiz."""
    payload = {"basics": {"name": "Ada", "brandNewField": 42}}
    resume = Resume.model_validate(payload)
    assert resume.model_dump()["basics"]["brandNewField"] == 42


def test_defaults_produce_a_schema_valid_resume(rxresume_schema: dict[str, Any]) -> None:
    """Boş bir `Resume()` bile geçerli bir CV üretmeli (issue 08 buna dayanacak)."""
    dumped = Resume().model_dump(mode="json", exclude_none=True)
    errors = sorted(Draft202012Validator(rxresume_schema).iter_errors(dumped), key=str)
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors[:10])


def test_real_export_shape_is_what_we_modelled(rxresume_schema: dict[str, Any]) -> None:
    """Şema anlık görüntüsü gerçekten `metadata.design` diyor mu (`theme` değil)?

    Dokümantasyon `theme` diyordu; gerçek export ve canlı şema `design` diyor.
    Bu ters giderse modeller sessizce yanlış olur.
    """
    metadata_props = rxresume_schema["properties"]["metadata"]["properties"]
    assert "design" in metadata_props
    assert "theme" not in metadata_props


def test_example_file_is_valid_json_with_trailing_newline(master_example_path: Path) -> None:
    text = master_example_path.read_text(encoding="utf-8")
    json.loads(text)
    assert text.endswith("\n")
