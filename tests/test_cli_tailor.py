"""Issue 04 — `cv-updater tailor` uçtan uca. Ağa çıkmaz, API anahtarı istemez."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from cv_updater.cli import app
from cv_updater.models.job import JobAnalysis, JobPosting, KeywordMapping, Requirement
from cv_updater.steps.analyze_job import JOB_FILENAME
from cv_updater.steps.intake import CLEAN_FILENAME, RAW_FILENAME

runner = CliRunner()

#: Eşiği (200 karakter) rahatça aşan, arayüz artığı taşıyan gerçekçi bir ilan.
POSTING = """LinkedIn
Kaydet
Bu ilanı paylaş
142 başvuran

Novatek Teknoloji A.Ş.
Kıdemli Yapay Zeka Mühendisi (LLM)
İstanbul, Türkiye · Hibrit

Aranan Nitelikler
• Python ile en az 4 yıl profesyonel yazılım geliştirme deneyimi
• Büyük dil modelleri ile üretim seviyesinde uygulama geliştirmiş olmak
• RAG mimarileri ve vektör veritabanları konusunda pratik tecrübe
• FastAPI ile REST API tasarımı, Docker ve CI/CD süreçlerine hakimiyet
• ﬁne-tuning ve prompt mühendisliği süreçlerini yürütmek

Bu site çerez kullanmaktadır.
"""


@pytest.fixture(autouse=True)
def _out_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`out/` her testte geçici dizine gider — depo kirlenmesin."""
    target = tmp_path / "out"
    monkeypatch.setattr("cv_updater.steps.intake.OUT_DIR", target)
    # `--no-llm` önceki çalıştırmayı burada arıyor.
    monkeypatch.setattr("cv_updater.steps.analyze_job.OUT_DIR", target)
    return target


@pytest.fixture(autouse=True)
def _example_master(monkeypatch: pytest.MonkeyPatch, master_example_path: Path) -> None:
    """Ön filtre (issue 06) süper seti okuyor; testler **kişisel** dosyayı okumamalı.

    `data/master.json` gitignore'da ve temiz bir checkout'ta yok — süit ona
    yaslansaydı yalnızca bu makinede yeşil olurdu.
    """
    monkeypatch.setattr("cv_updater.cli.MASTER_JSON", master_example_path)


def _fake_analysis() -> JobAnalysis:
    """Analiz adımının kanonik sahte çıktısı — LLM'e gidilmez."""
    return JobAnalysis(
        posting=JobPosting(
            company="Novatek Teknoloji A.Ş.",
            title="Kıdemli Yapay Zeka Mühendisi (LLM)",
            language="tr",
            seniority="senior",
            location="İstanbul, Türkiye",
            work_mode="hybrid",
            requirements=[
                Requirement(text="Python ile 4 yıl deneyim", kind="must", keywords=["Python"]),
                Requirement(text="RAG mimarileri", kind="must", keywords=["RAG"]),
            ],
            tech_keywords=["Python", "FastAPI", "RAG", "Docker"],
            soft_signals=["mentorluk"],
            domain="kurumsal yazılım",
            tone_notes="Kurumsal ve resmi.",
        ),
        keywords=KeywordMapping(tags=["python", "fastapi", "rag", "docker"], unmapped=[]),
        model="claude-opus-5",
    )


@pytest.fixture(autouse=True)
def _stub_analyze(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM adımı sahte: bu dosya girdi/çıktı akışını sınar, modeli değil."""
    monkeypatch.setattr(
        "cv_updater.cli.analyze",
        lambda text, **kwargs: _fake_analysis(),
    )
    monkeypatch.setattr("cv_updater.cli.has_api_key", lambda: True)


def _only_run_dir(out_dir: Path) -> Path:
    runs = sorted(out_dir.iterdir())
    assert len(runs) == 1, f"beklenen tek çalıştırma dizini, bulunan: {runs}"
    return runs[0]


class TestFilePath:
    def test_writes_both_files(self, tmp_path: Path, _out_dir: Path) -> None:
        posting = tmp_path / "novatek-ai-muhendisi.txt"
        posting.write_text(POSTING, encoding="utf-8")

        result = runner.invoke(app, ["tailor", str(posting)])

        assert result.exit_code == 0, result.output
        run_dir = _only_run_dir(_out_dir)
        assert run_dir.name.startswith("novatek-ai-muhendisi-")
        assert (run_dir / RAW_FILENAME).exists()
        assert (run_dir / CLEAN_FILENAME).exists()

    def test_clean_file_is_normalized(self, tmp_path: Path, _out_dir: Path) -> None:
        posting = tmp_path / "job.txt"
        posting.write_text(POSTING, encoding="utf-8")

        runner.invoke(app, ["tailor", str(posting)])

        clean = (_only_run_dir(_out_dir) / CLEAN_FILENAME).read_text(encoding="utf-8")
        assert "fine-tuning" in clean  # ligatür açıldı
        assert "- Python ile en az 4 yıl" in clean  # madde işareti normalize
        assert "Kaydet" not in clean  # arayüz artığı gitti
        assert "çerez" not in clean.lower()

    def test_raw_file_keeps_the_chrome(self, tmp_path: Path, _out_dir: Path) -> None:
        posting = tmp_path / "job.txt"
        posting.write_text(POSTING, encoding="utf-8")

        runner.invoke(app, ["tailor", str(posting)])

        raw = (_only_run_dir(_out_dir) / RAW_FILENAME).read_text(encoding="utf-8")
        assert "Kaydet" in raw
        assert "ﬁne-tuning" in raw

    def test_missing_file_exits_with_two(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["tailor", str(tmp_path / "yok.txt")])
        assert result.exit_code == 2
        assert "Dosya yok" in result.output


class TestStdinPath:
    def test_reads_stdin(self, _out_dir: Path) -> None:
        result = runner.invoke(app, ["tailor", "-"], input=POSTING)

        assert result.exit_code == 0, result.output
        clean = (_only_run_dir(_out_dir) / CLEAN_FILENAME).read_text(encoding="utf-8")
        assert "FastAPI" in clean

    def test_slug_falls_back_to_first_line(self, _out_dir: Path) -> None:
        runner.invoke(app, ["tailor", "-"], input=POSTING)
        assert _only_run_dir(_out_dir).name.startswith("novatek-teknoloji-a-s-")


class TestPastePath:
    def test_editor_content_is_used(self, monkeypatch: pytest.MonkeyPatch, _out_dir: Path) -> None:
        monkeypatch.setattr(
            "cv_updater.steps.intake._launch_editor",
            lambda path: path.write_text(POSTING, encoding="utf-8"),
        )

        result = runner.invoke(app, ["tailor", "--paste"])

        assert result.exit_code == 0, result.output
        clean = (_only_run_dir(_out_dir) / CLEAN_FILENAME).read_text(encoding="utf-8")
        assert "RAG mimarileri" in clean


class TestShortPosting:
    def test_asks_before_continuing(self, tmp_path: Path, _out_dir: Path) -> None:
        posting = tmp_path / "kisa.txt"
        posting.write_text("Python geliştirici aranıyor.", encoding="utf-8")

        result = runner.invoke(app, ["tailor", str(posting)], input="n\n")

        assert result.exit_code == 1
        assert "çok kısa" in result.output
        assert not _out_dir.exists() or list(_out_dir.iterdir()) == []

    def test_yes_continues(self, tmp_path: Path, _out_dir: Path) -> None:
        posting = tmp_path / "kisa.txt"
        posting.write_text("Python geliştirici aranıyor.", encoding="utf-8")

        result = runner.invoke(app, ["tailor", str(posting), "--yes"])

        assert result.exit_code == 0, result.output
        assert (_only_run_dir(_out_dir) / CLEAN_FILENAME).exists()

    def test_confirming_continues(self, tmp_path: Path, _out_dir: Path) -> None:
        posting = tmp_path / "kisa.txt"
        posting.write_text("Python geliştirici aranıyor.", encoding="utf-8")

        result = runner.invoke(app, ["tailor", str(posting)], input="y\n")

        assert result.exit_code == 0, result.output
        assert (_only_run_dir(_out_dir) / CLEAN_FILENAME).exists()


class TestNoOverwrite:
    def test_two_runs_of_the_same_posting_keep_both(self, tmp_path: Path, _out_dir: Path) -> None:
        """Kabul kriteri: önceki çıktı asla ezilmez."""
        posting = tmp_path / "job.txt"
        posting.write_text(POSTING, encoding="utf-8")

        runner.invoke(app, ["tailor", str(posting)])
        runner.invoke(app, ["tailor", str(posting)])

        assert len(list(_out_dir.iterdir())) == 2


class TestSlugOption:
    def test_explicit_slug_is_used(self, tmp_path: Path, _out_dir: Path) -> None:
        posting = tmp_path / "job.txt"
        posting.write_text(POSTING, encoding="utf-8")

        runner.invoke(app, ["tailor", str(posting), "--slug", "Acme Kıdemli Geliştirici"])

        assert _only_run_dir(_out_dir).name.startswith("acme-kidemli-gelistirici-")


class TestAnalysisOutput:
    """Issue 05 — `job.json` yazılıyor ve özet ekrana basılıyor."""

    def _run(self, tmp_path: Path) -> Any:
        posting = tmp_path / "job.txt"
        posting.write_text(POSTING, encoding="utf-8")
        return runner.invoke(app, ["tailor", str(posting)])

    def test_job_json_is_written(self, tmp_path: Path, _out_dir: Path) -> None:
        result = self._run(tmp_path)

        assert result.exit_code == 0, result.output
        job_path = _only_run_dir(_out_dir) / JOB_FILENAME
        assert job_path.exists()

    def test_job_json_round_trips(self, tmp_path: Path, _out_dir: Path) -> None:
        self._run(tmp_path)

        saved = JobAnalysis.model_validate_json(
            (_only_run_dir(_out_dir) / JOB_FILENAME).read_text(encoding="utf-8")
        )
        assert saved.posting.company == "Novatek Teknoloji A.Ş."
        assert saved.posting.language == "tr"
        assert saved.keywords.tags == ["python", "fastapi", "rag", "docker"]

    def test_summary_is_printed(self, tmp_path: Path, _out_dir: Path) -> None:
        result = self._run(tmp_path)

        assert "Novatek" in result.output
        assert "senior" in result.output


class TestMissingApiKey:
    def test_clear_error_when_key_is_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _out_dir: Path
    ) -> None:
        """Kabul kriteri: anahtar yokken net bir hata mesajı."""
        monkeypatch.setattr("cv_updater.cli.has_api_key", lambda: False)
        posting = tmp_path / "job.txt"
        posting.write_text(POSTING, encoding="utf-8")

        result = runner.invoke(app, ["tailor", str(posting)])

        assert result.exit_code == 2
        assert "LLM_API_KEY" in result.output
        # Normalizasyon çıktısı yine de yazılmış olmalı — emek çöpe gitmesin.
        assert (_only_run_dir(_out_dir) / CLEAN_FILENAME).exists()


class TestNoLlm:
    def test_reuses_the_latest_job_json(self, tmp_path: Path, _out_dir: Path) -> None:
        """Kabul kriteri: `--no-llm` API çağrısı olmadan boru hattını yürütür."""
        posting = tmp_path / "job.txt"
        posting.write_text(POSTING, encoding="utf-8")
        runner.invoke(app, ["tailor", str(posting)])

        result = runner.invoke(app, ["tailor", str(posting), "--no-llm"])

        assert result.exit_code == 0, result.output
        runs = sorted(_out_dir.iterdir())
        assert len(runs) == 2
        reused = JobAnalysis.model_validate_json(
            (runs[1] / JOB_FILENAME).read_text(encoding="utf-8")
        )
        assert reused.reused_from == runs[0].name
        assert reused.posting.company == "Novatek Teknoloji A.Ş."

    def test_no_llm_does_not_call_the_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _out_dir: Path
    ) -> None:
        posting = tmp_path / "job.txt"
        posting.write_text(POSTING, encoding="utf-8")
        runner.invoke(app, ["tailor", str(posting)])

        def _explode(*args: object, **kwargs: object) -> None:
            raise AssertionError("--no-llm modelini çağırmamalı")

        monkeypatch.setattr("cv_updater.cli.analyze", _explode)
        result = runner.invoke(app, ["tailor", str(posting), "--no-llm"])

        assert result.exit_code == 0, result.output

    def test_no_llm_without_previous_run_fails_clearly(
        self, tmp_path: Path, _out_dir: Path
    ) -> None:
        posting = tmp_path / "job.txt"
        posting.write_text(POSTING, encoding="utf-8")

        result = runner.invoke(app, ["tailor", str(posting), "--no-llm"])

        assert result.exit_code == 2
        assert "job.json" in result.output

    def test_no_llm_works_without_an_api_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _out_dir: Path
    ) -> None:
        posting = tmp_path / "job.txt"
        posting.write_text(POSTING, encoding="utf-8")
        runner.invoke(app, ["tailor", str(posting)])

        monkeypatch.setattr("cv_updater.cli.has_api_key", lambda: False)
        result = runner.invoke(app, ["tailor", str(posting), "--no-llm"])

        assert result.exit_code == 0, result.output
