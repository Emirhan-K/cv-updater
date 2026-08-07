"""Issue 07 — dürüstlük guardrail'i. Kasıtlı halüsinasyonlu planlar, sıfır ağ erişimi.

Bu dosya projenin en önemli testleri: her kural için en az bir "yakalıyor mu" ve
temiz plan için bir "yanlış alarm vermiyor mu" testi var.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from cv_updater.models.candidates import CandidatePool
from cv_updater.models.job import JobAnalysis, JobPosting, KeywordMapping, Requirement
from cv_updater.models.resume import (
    ExperienceItem,
    ProjectItem,
    Resume,
    SkillItem,
    Summary,
    XBullet,
    XMeta,
)
from cv_updater.models.tailor import (
    OmittedBullet,
    SelectedBullet,
    TailoredExperience,
    TailorPlan,
)
from cv_updater.steps.score import build_pool
from cv_updater.steps.verify import (
    VIOLATIONS_FILENAME,
    SourceText,
    extract_entities,
    index_sources,
    invented_numbers,
    numbers,
    read_report,
    verify,
    write_report,
)
from cv_updater.tags import load_tags

TODAY = date(2026, 8, 6)

VISION_TEXT = (
    "Görüntü işleme modellerini bir veri hattına bağlayarak 8 saatlik kaydı 12 dakikada işledi."
)
API_TEXT = (
    "Modelleri FastAPI servisleri arkasına alarak üç ekibin kullandığı bir REST arayüzü çıkardı."
)


@pytest.fixture
def vocabulary(tags_yaml_path: Path):
    return load_tags(tags_yaml_path)


@pytest.fixture
def resume() -> Resume:
    doc = Resume()
    doc.summary = Summary(content="<p>Süper setteki özet.</p>")
    doc.sections.experience.items = [
        ExperienceItem(
            id="exp-ai",
            company="Örnek AI",
            position="Yazılım Mühendisi",
            period="Ocak 2026 – Devam Ediyor",
            x_bullets=[
                XBullet(
                    id="exp-ai.vision.tr",
                    text=VISION_TEXT,
                    x_meta=XMeta(
                        tags=["computer-vision", "python", "data-pipeline"],
                        lang="tr",
                        impact="8 saatlik kaydı 12 dakikada",
                    ),
                ),
                XBullet(
                    id="exp-ai.api.tr",
                    text=API_TEXT,
                    x_meta=XMeta(tags=["fastapi", "rest-api"], lang="tr"),
                ),
                XBullet(
                    id="exp-ai.docs.tr",
                    text="Dokümantasyonu güncel tuttu.",
                    x_meta=XMeta(tags=["documentation"], lang="tr"),
                ),
            ],
        )
    ]
    doc.sections.projects.items = [
        ProjectItem(
            id="proj-cli",
            name="Örnek CLI",
            period="Temmuz 2026 - Devam Ediyor",
            x_bullets=[
                XBullet(
                    id="proj-cli.tests.tr",
                    text="Kütüphaneyi 70 testin altına aldı.",
                    x_meta=XMeta(tags=["testing"], lang="tr"),
                )
            ],
        )
    ]
    doc.sections.skills.items = [
        SkillItem(id="sk-python", name="Python", x_meta=XMeta(tags=["python"])),
        SkillItem(id="sk-fastapi", name="FastAPI", x_meta=XMeta(tags=["fastapi", "rest-api"])),
        SkillItem(id="sk-flutter", name="Flutter", x_meta=XMeta(tags=["flutter"])),
    ]
    return doc


@pytest.fixture
def analysis() -> JobAnalysis:
    return JobAnalysis(
        posting=JobPosting(
            company="Acme",
            title="Yazılım Mühendisi",
            language="tr",
            seniority="mid",
            work_mode="remote",
            requirements=[
                Requirement(text="Görüntü işleme", kind="must", keywords=["Görüntü İşleme"]),
                Requirement(text="FastAPI ile REST API", kind="must", keywords=["FastAPI"]),
                Requirement(text="Python", kind="must", keywords=["Python"]),
            ],
            tech_keywords=["Görüntü İşleme", "FastAPI", "Python"],
        ),
        keywords=KeywordMapping(tags=["computer-vision", "fastapi", "python"]),
    )


@pytest.fixture
def pool(resume: Resume, analysis: JobAnalysis, vocabulary: Any) -> CandidatePool:
    text = "Görüntü İşleme ve FastAPI ile Python geliştirici aranıyor."
    return build_pool(resume, analysis, text, vocabulary=vocabulary, today=TODAY)


def _plan(*bullets: SelectedBullet, summary: str = "", summary_ids: list[str] | None = None):
    return TailorPlan(
        summary=summary or "Görüntü işleme modelleriyle veri hatları kuran bir mühendis.",
        summary_source_ids=summary_ids if summary_ids is not None else ["exp-ai.vision.tr"],
        experiences=[TailoredExperience(source_id="exp-ai", bullets=list(bullets))],
        skill_ids=["sk-python"],
        project_ids=["proj-cli"],
        omitted_reasons=[OmittedBullet(source_id="exp-ai.docs.tr", reason="İlanla ilgisi zayıf.")],
    )


def _bullet(source_id: str, rewritten: str) -> SelectedBullet:
    return SelectedBullet(source_id=source_id, rewritten=rewritten, rationale="Uyuyor.")


# --------------------------------------------------------------------------- #


class TestCleanPlan:
    """Kabul kriteri: temiz bir plan sıfır ihlal üretmeli."""

    def test_no_violations(self, resume: Resume, pool: CandidatePool, vocabulary: Any) -> None:
        plan = _plan(
            _bullet("exp-ai.vision.tr", VISION_TEXT),
            _bullet("exp-ai.api.tr", API_TEXT),
        )

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert report.violations == []
        assert report.ok

    def test_faithful_rephrasing_passes(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        """Kelimeler değişebilir; sayılar ve adlar değişemez."""
        plan = _plan(
            _bullet(
                "exp-ai.vision.tr",
                "8 saatlik kaydı 12 dakikada işleyen bir görüntü işleme hattı kurdu.",
            )
        )

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert report.errors == []

    def test_tag_backed_translation_passes(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        """Türkçe kaynaktan "computer vision" yazmak çeviridir, uydurma değil."""
        plan = _plan(_bullet("exp-ai.vision.tr", "Built a computer vision pipeline."))

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert [v.rule for v in report.errors] == []


class TestSourceCheck:
    def test_an_unknown_id_is_caught(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        plan = _plan(_bullet("exp-ai.uydurma.tr", "Kubernetes kümesini yönetti."))

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert [v.rule for v in report.errors] == ["source.unknown"]

    def test_an_id_outside_the_pool_is_caught(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        """Model yalnızca havuzu gördü; havuzda olmayan id'yi kendi eklemiş demektir."""
        for entry in pool.experiences:
            for bullet in entry.bullets:
                if bullet.id == "exp-ai.api.tr":
                    bullet.selected = False

        plan = _plan(_bullet("exp-ai.api.tr", API_TEXT))
        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert [v.rule for v in report.errors] == ["source.not_offered"]

    def test_an_unknown_skill_id_is_caught(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        plan = _plan(_bullet("exp-ai.vision.tr", VISION_TEXT))
        plan.skill_ids = ["sk-python", "sk-kubernetes"]

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert any(v.rule == "source.unknown" for v in report.errors)

    def test_a_dropped_language_suffix_is_resolved_with_a_warning(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        """Regresyon: İngilizce çalıştırmada model `.en` sonekini ısrarla düşürüyor."""
        plan = _plan(_bullet("exp-ai.vision", VISION_TEXT))

        resolved, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert report.errors == []
        assert [v.rule for v in report.warnings] == ["source.suffix_dropped"]
        assert resolved.source_ids() == ["exp-ai.vision.tr"]

    def test_an_ambiguous_prefix_is_not_resolved(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        """Belirsizlikte tahmin yok: iki aday varsa ihlal olarak kalır."""
        entry = pool.experiences[0]
        entry.bullets.append(entry.bullets[0].model_copy(update={"id": "exp-ai.vision.en"}))

        plan = _plan(_bullet("exp-ai.vision", VISION_TEXT))
        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert [v.rule for v in report.errors] == ["source.unknown"]

    def test_a_duplicate_selection_is_reported(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        plan = _plan(
            _bullet("exp-ai.vision.tr", VISION_TEXT),
            _bullet("exp-ai.vision.tr", VISION_TEXT),
        )

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert [v.rule for v in report.warnings] == ["source.duplicate"]


class TestNumberCheck:
    """Halüsinasyonun en sık biçimi: sayının değişmesi."""

    def test_a_changed_number_is_caught(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        plan = _plan(
            _bullet(
                "exp-ai.vision.tr",
                "Görüntü işleme hattıyla 8 saatlik kaydı 3 dakikada işledi.",
            )
        )

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert [v.rule for v in report.errors] == ["number.invented"]
        assert "3" in report.errors[0].message

    def test_a_new_number_is_caught(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        plan = _plan(_bullet("exp-ai.api.tr", API_TEXT + " Gecikmeyi %40 azalttı."))

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert any(v.rule == "number.invented" for v in report.errors)

    def test_dropping_a_number_is_allowed(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        """Sayıyı çıkarmak dürüst; eklemek değil."""
        plan = _plan(
            _bullet("exp-ai.vision.tr", "Görüntü işleme modellerini veri hattına bağladı.")
        )

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert not any(v.rule == "number.invented" for v in report.violations)

    def test_thousands_separator_is_not_a_false_alarm(self) -> None:
        source = SourceText(id="x", text="1.500 kayıt işledi")
        assert invented_numbers("1500 kayıt işledi", source) == set()

    def test_numbers_are_extracted_without_separators(self) -> None:
        assert numbers("8 saatlik, 12,5 dakika, 1.500 kayıt") == {"8", "125", "1500"}


class TestEntityCheck:
    def test_an_invented_framework_is_caught(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        plan = _plan(_bullet("exp-ai.api.tr", "FastAPI ve Django ile mikroservisler kurdu."))

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        rules = [v.rule for v in report.errors]
        assert "entity.unknown_name" in rules or "entity.invented" in rules
        assert "Django" in " ".join(v.message for v in report.errors)

    def test_an_invented_cloud_provider_is_caught(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        plan = _plan(_bullet("exp-ai.api.tr", API_TEXT + " Servisleri AWS üzerinde ölçekledi."))

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert any("AWS" in v.message for v in report.errors)

    def test_an_invented_company_is_caught(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        plan = _plan(_bullet("exp-ai.api.tr", "Servisi Trendyol için FastAPI ile çıkardı."))

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert any("Trendyol" in v.message for v in report.errors)

    def test_the_experience_context_grounds_the_company_name(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        """Maddede geçmese de deneyimin şirketi doğrudur, uydurma sayılmamalı."""
        plan = _plan(_bullet("exp-ai.api.tr", "Örnek AI bünyesinde FastAPI servisleri çıkardı."))

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert not any("Örnek" in v.message for v in report.errors)

    def test_a_tag_component_grounds_an_acronym(self) -> None:
        """`rest-api` etiketi hem REST'i hem API'yi meşrulaştırır."""
        source = SourceText(id="x", text="Servisleri açtı.", tags=("rest-api",))
        entities = extract_entities("REST API arayüzü açtı.")
        assert "REST" in entities.technical
        from cv_updater.steps.verify import invented_entities

        assert invented_entities("REST API arayüzü açtı.", source).technical == []

    def test_a_hyphenated_compound_is_grounded_by_its_parts(self) -> None:
        """İlk İngilizce çalıştırmadan gelen regresyon: "LLM-based", "RAG-based"."""
        from cv_updater.steps.verify import invented_entities

        source = SourceText(id="x", text="Designed LLM and RAG pipelines.", tags=("llm", "rag"))
        found = invented_entities("Built LLM-based and RAG-based systems.", source)
        assert (found.technical, found.proper) == ([], [])

    def test_an_english_plural_is_grounded_by_its_singular(self) -> None:
        """Regresyon: kaynakta "LLM" varken "LLMs" yazmak uydurma değil."""
        from cv_updater.steps.verify import invented_entities

        source = SourceText(id="x", text="Built an LLM application.", tags=("llm",))
        found = invented_entities("Built LLMs for production.", source)
        assert (found.technical, found.proper) == ([], [])

    def test_a_known_abbreviation_is_grounded_by_the_tag(self, vocabulary: Any) -> None:
        """Regresyon: `machine-learning` etiketi "ML" kısaltmasını meşrulaştırır."""
        from cv_updater.steps.verify import invented_entities

        source = SourceText(
            id="x", text="Makine öğrenmesi hattı kurdu.", tags=("machine-learning",)
        )
        found = invented_entities("Built ML pipelines.", source, vocabulary)
        assert found.technical == []

    def test_a_compound_with_an_invented_part_is_still_caught(self) -> None:
        from cv_updater.steps.verify import invented_entities

        source = SourceText(id="x", text="Designed LLM pipelines.", tags=("llm",))
        found = invented_entities("Built a Django-powered service.", source)
        assert "Django-powered" in found.technical + found.proper

    def test_the_first_word_of_a_sentence_is_not_a_proper_noun(self) -> None:
        """Türkçede her cümle büyük harfle başlıyor; aksi halde hepsi yanlış alarm olurdu."""
        assert extract_entities("Modelleri servise aldı.").proper == []


class TestSummaryCheck:
    def test_an_ungrounded_summary_is_caught(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        plan = _plan(_bullet("exp-ai.vision.tr", VISION_TEXT), summary_ids=[])

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert any(v.rule == "summary.ungrounded" for v in report.errors)

    def test_an_invented_number_in_the_summary_is_caught(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        plan = _plan(
            _bullet("exp-ai.vision.tr", VISION_TEXT),
            summary="6 yıllık deneyimiyle görüntü işleme üzerine çalışan bir mühendis.",
        )

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert any(v.rule == "number.invented" and v.where == "summary" for v in report.errors)

    def test_a_selected_skill_grounds_the_summary(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        """İlk gerçek çalıştırmadan gelen regresyon.

        Model özette "FastAPI ile mikro servis mimarileri" yazmıştı. FastAPI
        seçilmiş bir yetenekti ama hiçbir **maddenin metninde** geçmiyordu, ve
        yalnızca `summary_source_ids`'e bakan kontrol bunu uydurma sayıyordu.
        Özet CV'yi özetler; CV'de duran bir şeyi tekrar etmesi uydurma değil.
        """
        plan = _plan(
            _bullet("exp-ai.vision.tr", VISION_TEXT),
            summary="Görüntü işleme ve FastAPI deneyimi olan bir mühendis.",
            summary_ids=["exp-ai.vision.tr"],
        )

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert report.errors == []

    def test_an_unselected_skill_does_not_ground_the_summary(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        """Gevşetme havuzla sınırlı: seçilmemiş bir yetenek özeti temellendiremez."""
        for skill in pool.skills:
            skill.selected = False

        plan = _plan(
            _bullet("exp-ai.vision.tr", VISION_TEXT),
            summary="Görüntü işleme ve Flutter deneyimi olan bir mühendis.",
            summary_ids=["exp-ai.vision.tr"],
        )

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert any("Flutter" in violation.message for violation in report.errors)

    def test_an_unknown_summary_source_is_caught(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        plan = _plan(_bullet("exp-ai.vision.tr", VISION_TEXT), summary_ids=["yok-boyle-bir-id"])

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert any(v.rule == "summary.unknown_source" for v in report.errors)


class TestDriftCheck:
    def test_an_inflated_rewrite_is_warned(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        plan = _plan(_bullet("exp-ai.docs.tr", "Dokümantasyonu güncel tuttu. " * 5))

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert any(v.rule == "text.drift" for v in report.warnings)

    def test_drift_does_not_block_on_its_own(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        plan = _plan(_bullet("exp-ai.docs.tr", "Dokümantasyonu güncel tuttu. " * 5))

        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        assert report.ok


class TestRepair:
    def test_an_invented_rewrite_reverts_to_the_source(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        plan = _plan(_bullet("exp-ai.api.tr", "FastAPI ve Django ile mikroservisler kurdu."))

        repaired, report = verify(plan, pool, resume, repair=True, vocabulary=vocabulary)

        assert repaired.experiences[0].bullets[0].rewritten == API_TEXT
        assert report.repaired == 1
        assert all(v.repaired_with for v in report.errors)

    def test_an_unknown_bullet_is_dropped(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        plan = _plan(
            _bullet("exp-ai.vision.tr", VISION_TEXT),
            _bullet("uydurma-id", "Kubernetes kümesini yönetti."),
        )

        repaired, _ = verify(plan, pool, resume, repair=True, vocabulary=vocabulary)

        assert [b.source_id for b in repaired.all_bullets()] == ["exp-ai.vision.tr"]

    def test_an_unknown_skill_id_is_dropped(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        plan = _plan(_bullet("exp-ai.vision.tr", VISION_TEXT))
        plan.skill_ids = ["sk-python", "sk-kubernetes"]

        repaired, _ = verify(plan, pool, resume, repair=True, vocabulary=vocabulary)

        assert repaired.skill_ids == ["sk-python"]

    def test_a_bad_summary_reverts_to_master(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        plan = _plan(
            _bullet("exp-ai.vision.tr", VISION_TEXT),
            summary="6 yıllık deneyimiyle çalışan bir mühendis.",
        )

        repaired, _ = verify(plan, pool, resume, repair=True, vocabulary=vocabulary)

        assert repaired.summary == "Süper setteki özet."

    def test_nothing_invented_survives_repair(
        self, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        """Kabul kriteri: uydurulan içerik **hiçbir bayrak altında** geçemez."""
        plan = _plan(
            _bullet("exp-ai.api.tr", "FastAPI, Django ve Kubernetes ile 12 mikroservis kurdu."),
            _bullet("uydurma-id", "Bir şey yaptı."),
        )

        repaired, _ = verify(plan, pool, resume, repair=True, vocabulary=vocabulary)
        _, second = verify(repaired, pool, resume, vocabulary=vocabulary)

        assert second.errors == []


class TestImmutableFields:
    def test_the_plan_schema_carries_no_company_title_or_date(self) -> None:
        """Guardrail'in en güçlü kuralı: o alanlar şemada yok, bozulamazlar."""
        fields = set(TailorPlan.model_json_schema()["properties"])
        forbidden = {"company", "position", "title", "period", "date", "education"}
        assert fields & forbidden == set()

    def test_selected_bullet_has_no_free_form_new_bullet_field(self) -> None:
        from cv_updater.models.tailor import SelectedBullet as Bullet

        assert set(Bullet.model_fields) == {"source_id", "rewritten", "rationale"}


class TestSourceIndex:
    def test_every_bullet_and_skill_is_indexed(self, resume: Resume) -> None:
        sources = index_sources(resume)
        assert "exp-ai.vision.tr" in sources
        assert "proj-cli.tests.tr" in sources
        assert "sk-python" in sources

    def test_the_experience_context_is_attached(self, resume: Resume) -> None:
        sources = index_sources(resume)
        assert "Örnek AI" in sources["exp-ai.vision.tr"].context


class TestPersistence:
    def test_round_trip(
        self, tmp_path: Path, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        plan = _plan(_bullet("exp-ai.api.tr", "Django ile servis kurdu."))
        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        path = write_report(tmp_path, report)
        loaded = read_report(path)

        assert path.name == VIOLATIONS_FILENAME
        assert [v.rule for v in loaded.violations] == [v.rule for v in report.violations]

    def test_a_clean_run_still_writes_the_file(
        self, tmp_path: Path, resume: Resume, pool: CandidatePool, vocabulary: Any
    ) -> None:
        """Boş ihlal listesi de kanıttır — dosyanın yokluğu "denetlenmedi" demek olurdu."""
        plan = _plan(_bullet("exp-ai.vision.tr", VISION_TEXT))
        _, report = verify(plan, pool, resume, vocabulary=vocabulary)

        path = write_report(tmp_path, report)

        assert path.exists()
        assert read_report(path).violations == []
