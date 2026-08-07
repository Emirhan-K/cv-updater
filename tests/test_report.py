"""Issue 09 — uyum raporu. Skor deterministik, rapor LLM'e sormuyor."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from cv_updater.models.candidates import CandidatePool, RequirementCoverage
from cv_updater.models.job import JobAnalysis, JobPosting, KeywordMapping, Requirement
from cv_updater.models.resume import ExperienceItem, Resume, SkillItem, XBullet, XMeta
from cv_updater.models.tailor import (
    OmittedBullet,
    SelectedBullet,
    TailoredExperience,
    TailorPlan,
)
from cv_updater.steps.report import (
    REPORT_FILENAME,
    STRONG_FIT,
    WEAK_FIT,
    build,
    fit_score,
    write_report,
)
from cv_updater.steps.score import build_pool
from cv_updater.steps.verify import VerificationReport, Violation, verify
from cv_updater.tags import load_tags

TODAY = date(2026, 8, 6)


@pytest.fixture
def vocabulary(tags_yaml_path: Path):
    return load_tags(tags_yaml_path)


def _row(index: int, kind: str, status: str, **extra: Any) -> RequirementCoverage:
    return RequirementCoverage(
        index=index,
        text=f"gereksinim {index}",
        kind=kind,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        **extra,
    )


class TestFitScore:
    def test_everything_covered_is_a_hundred(self) -> None:
        rows = [_row(0, "must", "covered"), _row(1, "nice", "covered")]
        assert fit_score(rows).percent == 100

    def test_nothing_covered_is_zero(self) -> None:
        rows = [_row(0, "must", "missing"), _row(1, "nice", "missing")]
        assert fit_score(rows).percent == 0

    def test_indirect_counts_as_half(self) -> None:
        assert fit_score([_row(0, "must", "indirect")]).percent == 50

    def test_a_must_weighs_three_times_a_nice(self) -> None:
        """Puanlamadaki 3:1 oranının aynısı."""
        must_only = fit_score([_row(0, "must", "covered"), _row(1, "nice", "missing")])
        nice_only = fit_score([_row(0, "must", "missing"), _row(1, "nice", "covered")])
        assert must_only.percent == 75
        assert nice_only.percent == 25

    def test_counts_are_reported_per_kind(self) -> None:
        rows = [
            _row(0, "must", "covered"),
            _row(1, "must", "missing"),
            _row(2, "nice", "covered"),
        ]
        score = fit_score(rows)
        assert (score.must_covered, score.must_total) == (1, 2)
        assert (score.nice_covered, score.nice_total) == (1, 1)

    def test_an_empty_posting_scores_zero(self) -> None:
        assert fit_score([]).percent == 0

    def test_the_same_input_gives_the_same_score(self) -> None:
        rows = [_row(0, "must", "indirect"), _row(1, "nice", "covered")]
        assert fit_score(rows).percent == fit_score(rows).percent


class TestVerdict:
    def test_a_strong_fit_says_apply(self) -> None:
        assert fit_score([_row(0, "must", "covered")]).verdict == "başvur"

    def test_a_weak_fit_says_do_not_force_it(self) -> None:
        score = fit_score([_row(0, "must", "missing"), _row(1, "nice", "covered")])
        assert score.percent < WEAK_FIT
        assert score.verdict == "zorlama"
        assert score.weak

    def test_the_middle_band_says_prepare(self) -> None:
        score = fit_score([_row(0, "must", "indirect")])
        assert WEAK_FIT <= score.percent < STRONG_FIT
        assert "hazırlan" in score.verdict


# --------------------------------------------------------------------------- #


@pytest.fixture
def resume() -> Resume:
    doc = Resume()
    doc.sections.experience.items = [
        ExperienceItem(
            id="exp-ai",
            company="Örnek AI",
            position="Yazılım Mühendisi",
            period="Ocak 2026 – Devam Ediyor",
            x_bullets=[
                XBullet(
                    id="exp-ai.vision.tr",
                    text="Görüntü işleme modellerini veri hattına bağladı.",
                    x_meta=XMeta(tags=["computer-vision", "python"], lang="tr"),
                ),
                XBullet(
                    id="exp-ai.docs.tr",
                    text="Dokümantasyonu güncelledi.",
                    x_meta=XMeta(tags=["documentation"], lang="tr"),
                ),
            ],
        )
    ]
    doc.sections.skills.items = [
        SkillItem(id="sk-fastapi", name="FastAPI", x_meta=XMeta(tags=["fastapi", "rest-api"]))
    ]
    return doc


@pytest.fixture
def analysis() -> JobAnalysis:
    return JobAnalysis(
        posting=JobPosting(
            company="Acme",
            title="Görüntü İşleme Mühendisi",
            language="tr",
            seniority="mid",
            location="İstanbul",
            work_mode="remote",
            requirements=[
                Requirement(text="Görüntü işleme", kind="must", keywords=["Görüntü İşleme"]),
                Requirement(text="FastAPI ile servis", kind="must", keywords=["FastAPI"]),
                Requirement(text="Flutter artı", kind="nice", keywords=["Flutter"]),
            ],
            tech_keywords=["Görüntü İşleme", "FastAPI", "Flutter"],
        ),
        keywords=KeywordMapping(
            tags=["computer-vision", "fastapi", "flutter"], unmapped=["NVIDIA Jetson"]
        ),
        model="claude-opus-5",
    )


@pytest.fixture
def pool(resume: Resume, analysis: JobAnalysis, vocabulary: Any) -> CandidatePool:
    return build_pool(
        resume,
        analysis,
        "Görüntü İşleme, FastAPI ve Flutter bilgisi.",
        vocabulary=vocabulary,
        today=TODAY,
    )


@pytest.fixture
def plan() -> TailorPlan:
    return TailorPlan(
        summary="Görüntü işleme üzerine çalışan bir mühendis.",
        summary_source_ids=["exp-ai.vision.tr"],
        experiences=[
            TailoredExperience(
                source_id="exp-ai",
                bullets=[
                    SelectedBullet(
                        source_id="exp-ai.vision.tr",
                        rewritten="Görüntü işleme modellerini veri hattına bağladı.",
                        rationale="İlanın ilk zorunlu gereksinimini karşılıyor.",
                    )
                ],
            )
        ],
        skill_ids=["sk-fastapi"],
        omitted_reasons=[OmittedBullet(source_id="exp-ai.docs.tr", reason="İlanla ilgisi zayıf.")],
    )


@pytest.fixture
def markdown(analysis: JobAnalysis, pool: CandidatePool, plan: TailorPlan) -> str:
    return build(analysis, pool, plan)


class TestHeadline:
    def test_the_title_and_company_are_shown(self, markdown: str) -> None:
        assert "# Uyum raporu — Görüntü İşleme Mühendisi" in markdown
        assert "Acme" in markdown

    def test_the_headline_carries_the_score_and_verdict(self, markdown: str) -> None:
        assert "**Uyum: %" in markdown
        assert "karar:" in markdown

    def test_counts_appear_in_the_headline(self, markdown: str) -> None:
        assert "zorunlu" in markdown and "tercih" in markdown


class TestRequirementTable:
    def test_every_requirement_gets_a_row(self, markdown: str) -> None:
        for text in ("Görüntü işleme", "FastAPI ile servis", "Flutter artı"):
            assert text in markdown

    def test_a_covered_requirement_names_the_bullet(self, markdown: str) -> None:
        assert "✅ kapsanıyor" in markdown
        assert "Görüntü işleme modellerini" in markdown

    def test_a_skill_only_match_is_marked_indirect(self, markdown: str) -> None:
        assert "⚠️ dolaylı" in markdown
        assert "sk-fastapi" in markdown

    def test_a_missing_requirement_is_marked(self, markdown: str) -> None:
        assert "❌ eksik" in markdown

    def test_must_rows_come_before_nice_rows(self, markdown: str) -> None:
        assert markdown.index("Görüntü işleme |") < markdown.index("Flutter artı")

    def test_pipes_in_the_text_do_not_break_the_table(
        self, analysis: JobAnalysis, pool: CandidatePool, plan: TailorPlan
    ) -> None:
        pool.coverage[0].text = "Python | FastAPI bilgisi"
        assert "Python \\| FastAPI" in build(analysis, pool, plan)


class TestMissingKeywords:
    def test_tags_absent_from_the_superset_are_listed(self, markdown: str) -> None:
        assert "`flutter`" in markdown

    def test_the_question_is_do_you_actually_know_this(self, markdown: str) -> None:
        assert "gerçekten biliyor musun" in markdown

    def test_it_suggests_how_to_add_it(self, markdown: str) -> None:
        assert "x_meta.tags" in markdown
        assert "cv-updater validate" in markdown

    def test_it_warns_against_adding_what_you_do_not_know(self, markdown: str) -> None:
        assert "Bilmiyorsan ekleme" in markdown

    def test_unmapped_terms_are_listed_separately(self, markdown: str) -> None:
        assert "NVIDIA Jetson" in markdown

    def test_a_full_match_says_nothing_is_missing(
        self, analysis: JobAnalysis, pool: CandidatePool, plan: TailorPlan
    ) -> None:
        pool.missing_tags = []
        analysis.keywords.unmapped = []
        assert "her konusunun CV'de karşılığı var" in build(analysis, pool, plan)


class TestSelectionAndOmissions:
    def test_selected_bullets_carry_their_rationale(self, markdown: str) -> None:
        assert "İlanın ilk zorunlu gereksinimini karşılıyor." in markdown

    def test_dropped_bullets_are_listed_with_reasons(self, markdown: str) -> None:
        assert "Elenen güçlü maddeler" in markdown
        assert "İlanla ilgisi zayıf." in markdown

    def test_no_omissions_means_no_section(
        self, analysis: JobAnalysis, pool: CandidatePool, plan: TailorPlan
    ) -> None:
        plan.omitted_reasons = []
        assert "Elenen güçlü maddeler" not in build(analysis, pool, plan)


class TestInterviewPrep:
    def test_indirectly_covered_musts_are_flagged(self, markdown: str) -> None:
        assert "Mülakat hazırlığı" in markdown
        assert "FastAPI ile servis" in markdown

    def test_a_fully_covered_posting_needs_no_prep_section(
        self, analysis: JobAnalysis, pool: CandidatePool, plan: TailorPlan
    ) -> None:
        for row in pool.coverage:
            row.status = "covered"
        assert "Mülakat hazırlığı" not in build(analysis, pool, plan)


class TestGuardrailSection:
    def test_a_clean_run_is_reported_as_clean(
        self, analysis: JobAnalysis, pool: CandidatePool, plan: TailorPlan, resume: Resume
    ) -> None:
        _, verification = verify(plan, pool, resume)
        markdown = build(analysis, pool, plan, verification)

        assert "sıfır ihlal" in markdown

    def test_violations_are_tabulated(
        self, analysis: JobAnalysis, pool: CandidatePool, plan: TailorPlan
    ) -> None:
        verification = VerificationReport(
            violations=[
                Violation(
                    level="error",
                    rule="number.invented",
                    where="exp-ai.vision.tr",
                    message="Kaynakta olmayan sayı: 40",
                    repaired_with="kaynak metnine döndürüldü",
                )
            ],
            checked=2,
            repaired=1,
        )

        markdown = build(analysis, pool, plan, verification)

        assert "`number.invented`" in markdown
        assert "kaynak metnine döndürüldü" in markdown

    def test_without_a_verification_the_section_is_omitted(self, markdown: str) -> None:
        assert "## Guardrail" not in markdown


class TestWeakFitWarning:
    def test_a_weak_fit_prints_a_stop_sign(
        self, analysis: JobAnalysis, pool: CandidatePool, plan: TailorPlan
    ) -> None:
        for row in pool.coverage:
            row.status = "missing"
        markdown = build(analysis, pool, plan)

        assert "⛔" in markdown
        assert "zorlama" in markdown

    def test_a_strong_fit_does_not(
        self, analysis: JobAnalysis, pool: CandidatePool, plan: TailorPlan
    ) -> None:
        for row in pool.coverage:
            row.status = "covered"
        assert "⛔" not in build(analysis, pool, plan)


class TestFooter:
    def test_the_token_saving_is_reported(self, markdown: str) -> None:
        """Issue 06 kabul kriteri: kısılan metin raporda görünsün."""
        assert "daha az" in markdown
        assert "aday" in markdown

    def test_the_model_is_named(self, markdown: str) -> None:
        assert "claude-opus-5" in markdown

    def test_recall_net_findings_are_shown(self, markdown: str) -> None:
        assert "Geri çağırma ağı" in markdown or "recall" not in markdown


class TestPersistence:
    def test_it_writes_a_markdown_file(self, tmp_path: Path, markdown: str) -> None:
        path = write_report(tmp_path, markdown)

        assert path.name == REPORT_FILENAME
        assert path.read_text(encoding="utf-8").startswith("# Uyum raporu")

    def test_the_same_run_produces_the_same_report(
        self, analysis: JobAnalysis, pool: CandidatePool, plan: TailorPlan
    ) -> None:
        """Kabul kriteri: ikinci çalıştırma aynı skoru verir."""
        assert build(analysis, pool, plan) == build(analysis, pool, plan)
