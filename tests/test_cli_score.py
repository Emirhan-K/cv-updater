"""Issue 06 — `cv-updater score <slug>` uçtan uca. LLM yok, ağ yok, anahtar yok."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cv_updater.cli import app
from cv_updater.models.job import JobAnalysis, JobPosting, KeywordMapping, Requirement
from cv_updater.steps.analyze_job import write_analysis
from cv_updater.steps.intake import CLEAN_FILENAME
from cv_updater.steps.score import CANDIDATES_FILENAME

runner = CliRunner()

POSTING = """Örnek Teknoloji
Yazılım Mühendisi (Görüntü İşleme)

Aranan Nitelikler
- Python ile deneyim
- Görüntü İşleme alanında tecrübe
- FastAPI ile REST API geliştirme
"""


@pytest.fixture
def run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, master_example_path: Path) -> Path:
    """Analizi tamamlanmış bir çalıştırma dizini kurar."""
    out = tmp_path / "out"
    target = out / "ornek-20260806-120000"
    target.mkdir(parents=True)
    (target / CLEAN_FILENAME).write_text(POSTING, encoding="utf-8")

    write_analysis(
        target,
        JobAnalysis(
            posting=JobPosting(
                company="Örnek Teknoloji",
                title="Yazılım Mühendisi (Görüntü İşleme)",
                language="tr",
                seniority="mid",
                work_mode="onsite",
                requirements=[
                    Requirement(text="Python ile deneyim", kind="must", keywords=["Python"]),
                    Requirement(
                        text="Görüntü İşleme tecrübesi", kind="must", keywords=["Görüntü İşleme"]
                    ),
                    Requirement(text="FastAPI", kind="nice", keywords=["FastAPI"]),
                ],
                tech_keywords=["Python", "Görüntü İşleme", "FastAPI"],
            ),
            keywords=KeywordMapping(tags=["python", "computer-vision", "fastapi"]),
        ),
    )

    monkeypatch.setattr("cv_updater.steps.score.OUT_DIR", out, raising=False)
    monkeypatch.setattr("cv_updater.cli.MASTER_JSON", master_example_path)
    monkeypatch.setattr("cv_updater.paths.OUT_DIR", out)
    return target


class TestScoreCommand:
    def test_writes_candidates_json(self, run_dir: Path) -> None:
        result = runner.invoke(app, ["score", "ornek"])

        assert result.exit_code == 0, result.output
        assert (run_dir / CANDIDATES_FILENAME).exists()

    def test_output_is_readable_json(self, run_dir: Path) -> None:
        runner.invoke(app, ["score", "ornek"])

        payload = json.loads((run_dir / CANDIDATES_FILENAME).read_text(encoding="utf-8"))

        assert payload["language"] == "tr"
        assert "computer-vision" in payload["must_tags"]
        assert payload["experiences"]

    def test_no_api_key_is_needed(self, run_dir: Path) -> None:
        """Ön filtre LLM adımı değil — anahtarsız çalışmalı."""
        result = runner.invoke(app, ["score", "ornek"])
        assert result.exit_code == 0
        assert "LLM_API_KEY" not in result.output

    def test_lang_option_forces_the_language(self, run_dir: Path) -> None:
        result = runner.invoke(app, ["score", "ornek", "--lang", "en"])

        assert result.exit_code == 0, result.output
        payload = json.loads((run_dir / CANDIDATES_FILENAME).read_text(encoding="utf-8"))
        assert payload["language"] == "en"

    def test_invalid_lang_is_rejected(self, run_dir: Path) -> None:
        result = runner.invoke(app, ["score", "ornek", "--lang", "de"])
        assert result.exit_code == 2
        assert "--lang" in result.output

    def test_unknown_slug_exits_with_two(self, run_dir: Path) -> None:
        result = runner.invoke(app, ["score", "bulunmayan"])
        assert result.exit_code == 2
        assert "bulunamadı" in result.output

    def test_run_without_analysis_exits_with_two(self, run_dir: Path) -> None:
        (run_dir / "job.json").unlink()

        result = runner.invoke(app, ["score", "ornek"])

        assert result.exit_code == 2
        assert "tailor" in result.output

    def test_rerunning_is_idempotent(self, run_dir: Path) -> None:
        """Kabul kriteri: aynı girdi → aynı çıktı."""
        runner.invoke(app, ["score", "ornek"])
        first = json.loads((run_dir / CANDIDATES_FILENAME).read_text(encoding="utf-8"))
        runner.invoke(app, ["score", "ornek"])
        second = json.loads((run_dir / CANDIDATES_FILENAME).read_text(encoding="utf-8"))

        first.pop("scored_at")
        second.pop("scored_at")
        assert first == second

    def test_the_table_shows_scores(self, run_dir: Path) -> None:
        result = runner.invoke(app, ["score", "ornek"])
        assert "ön filtre" in result.output
        assert "kısılan metin" in result.output
