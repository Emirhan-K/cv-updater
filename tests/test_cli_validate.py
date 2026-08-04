"""`cv-updater validate` uçtan uca davranışı."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cv_updater.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """validate API anahtarı olmadan çalışmalı (issue 01 kabul kriteri)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def write(tmp_path: Path, payload: dict) -> Path:
    target = tmp_path / "master.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return target


def test_missing_file_exits_with_code_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate", "-m", str(tmp_path / "yok.json")])
    assert result.exit_code == 2
    assert "Dosya yok" in result.output


def test_malformed_json_reports_schema_error(tmp_path: Path) -> None:
    target = tmp_path / "master.json"
    target.write_text('{"sections": {"experience": {"items": [{"hidden": false}]}}}', "utf-8")
    result = runner.invoke(app, ["validate", "-m", str(target)])
    assert result.exit_code == 2
    assert "Şema hatası" in result.output


def test_example_file_runs_and_reports(master_example_path: Path) -> None:
    result = runner.invoke(app, ["validate", "-m", str(master_example_path)])
    # Örnek dosya kasıtlı olarak küçük → uyarı var, hata yok → çıkış kodu 0.
    assert result.exit_code == 0
    assert "süper set" in result.output


def test_strict_turns_warnings_into_failure(master_example_path: Path) -> None:
    result = runner.invoke(app, ["validate", "-m", str(master_example_path), "--strict"])
    assert result.exit_code == 1


def test_placeholder_text_is_not_swallowed_by_rich(tmp_path: Path) -> None:
    """`[terfi tarihi]` rich markup'ı sanılıp yutulmamalı — kullanıcı ne eksik göremezdi."""
    payload = {
        "sections": {
            "experience": {
                "items": [
                    {"id": "x", "hidden": False, "company": "Örnek", "period": "[terfi tarihi]"}
                ]
            }
        }
    }
    result = runner.invoke(app, ["validate", "-m", str(write(tmp_path, payload))])
    assert "[terfi tarihi]" in result.output


def test_fix_rewrites_ligatures_in_place(tmp_path: Path) -> None:
    payload = {"summary": {"content": "<p>ﬁne-tuning</p>"}}
    target = write(tmp_path, payload)

    result = runner.invoke(app, ["validate", "-m", str(target), "--fix"])
    assert "ligatür" in result.output

    rewritten = json.loads(target.read_text(encoding="utf-8"))
    assert rewritten["summary"]["content"] == "<p>fine-tuning</p>"


def test_fix_is_a_noop_on_clean_file(master_example_path: Path, tmp_path: Path) -> None:
    target = tmp_path / "master.json"
    original = master_example_path.read_text(encoding="utf-8")
    target.write_text(original, encoding="utf-8")

    runner.invoke(app, ["validate", "-m", str(target), "--fix"])
    assert target.read_text(encoding="utf-8") == original


def test_clean_resume_reports_zero_errors_zero_warnings(tmp_path: Path) -> None:
    """Kabul kriterinin pozitif tarafı: temiz bir CV gerçekten temiz raporlanıyor mu?"""
    bullets = [
        {
            "id": f"exp-{n}.topic.{lang}",
            "text": "Bir şey yaptı." if lang == "tr" else "Did a thing.",
            "x_meta": {"tags": ["python"], "lang": lang, "impact": "bir sayı"},
        }
        for n in range(3)
        for lang in ("tr", "en")
    ]
    payload = {
        "basics": {
            "email": "ada@example.com",
            "customFields": [
                {"id": "g", "icon": "", "text": "github.com/ada-k"},
                {"id": "l", "icon": "", "text": "linkedin.com/in/ada-k"},
            ],
        },
        "sections": {
            "education": {
                "items": [{"id": "e", "hidden": False, "school": "Örnek", "degree": "Lisans"}]
            },
            "experience": {
                "items": [
                    {
                        "id": f"exp-{n}",
                        "hidden": False,
                        "company": f"Şirket {n}",
                        "x_bullets": [b for b in bullets if b["id"].startswith(f"exp-{n}.")] * 2,
                    }
                    for n in range(3)
                ]
            },
            "skills": {
                "items": [
                    {
                        "id": f"sk-{n}",
                        "hidden": False,
                        "name": f"Yetenek {n}",
                        "x_meta": {"tags": ["python"]},
                    }
                    for n in range(20)
                ]
            },
        },
    }
    # Madde id'leri tekil olmalı: yukarıdaki `* 2` çoğaltmasını benzersizleştir.
    for item in payload["sections"]["experience"]["items"]:
        seen: set[str] = set()
        for index, bullet in enumerate(item["x_bullets"]):
            bullet = dict(bullet)
            while bullet["id"] in seen:
                bullet["id"] = f"{bullet['id']}.{index}"
            seen.add(bullet["id"])
            item["x_bullets"][index] = bullet

    result = runner.invoke(app, ["validate", "-m", str(write(tmp_path, payload)), "--strict"])
    assert result.exit_code == 0, result.output
    assert "Sıfır hata, sıfır uyarı" in result.output
