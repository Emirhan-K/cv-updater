"""Issue 06 — deterministik ön filtre. Hiçbir test LLM'e ya da ağa dokunmaz."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from cv_updater.models.job import JobAnalysis, JobPosting, KeywordMapping, Requirement
from cv_updater.models.resume import (
    ExperienceItem,
    ProjectItem,
    Resume,
    SkillItem,
    XBullet,
    XMeta,
)
from cv_updater.steps.score import (
    CANDIDATES_FILENAME,
    MAX_BULLETS_PER_EXPERIENCE,
    Weights,
    build_pool,
    find_run,
    parse_period_end,
    parse_period_start,
    posting_tags,
    read_pool,
    recall_check,
    recency_bonus,
    reverse_chronological_key,
    seniority_fit,
    write_pool,
)
from cv_updater.tags import load_tags

#: Puanlamayı tarihe bağlı olmaktan çıkarır — "aynı girdi, aynı çıktı" ancak
#: bugünün tarihi de girdi sayılırsa doğru olur.
TODAY = date(2026, 8, 6)


@pytest.fixture
def vocabulary(tags_yaml_path: Path):
    return load_tags(tags_yaml_path)


def _bullet(bullet_id: str, text: str, tags: list[str], **meta: Any) -> XBullet:
    return XBullet(id=bullet_id, text=text, x_meta=XMeta(tags=tags, **meta))


@pytest.fixture
def resume() -> Resume:
    """İki deneyim, bir proje, dört yetenek — görüntü işleme ve backend karşı karşıya."""
    doc = Resume()
    doc.sections.experience.items = [
        ExperienceItem(
            id="exp-ai",
            company="Örnek AI",
            position="Yazılım Mühendisi",
            period="Ocak 2026 – Devam Ediyor",
            x_meta=XMeta(tags=["computer-vision"], seniority="mid"),
            x_bullets=[
                _bullet(
                    "exp-ai.vision.tr",
                    "Görüntü işleme modellerini bir veri hattına bağladı.",
                    ["computer-vision", "python", "data-pipeline"],
                    lang="tr",
                    impact="8 saatlik kaydı 12 dakikada işleyen hat",
                ),
                _bullet(
                    "exp-ai.vision.en",
                    "Connected computer-vision models into a data pipeline.",
                    ["computer-vision", "python", "data-pipeline"],
                    lang="en",
                ),
                _bullet(
                    "exp-ai.api.tr",
                    "Modelleri FastAPI servislerinin arkasına aldı.",
                    ["fastapi", "rest-api", "microservices"],
                    lang="tr",
                ),
                _bullet(
                    "exp-ai.api.en",
                    "Exposed the models behind FastAPI services.",
                    ["fastapi", "rest-api", "microservices"],
                    lang="en",
                ),
            ],
        ),
        ExperienceItem(
            id="exp-eski",
            company="Eski Yazılım",
            position="Stajyer",
            period="Ocak 2020 – Aralık 2020",
            x_bullets=[
                _bullet("exp-eski.sql.tr", "Raporlama sorguları yazdı.", ["sql", "reporting"]),
                _bullet("exp-eski.docs.tr", "Dokümantasyonu güncelledi.", ["documentation"]),
            ],
        ),
    ]
    doc.sections.projects.items = [
        ProjectItem(
            id="proj-cli",
            name="Örnek CLI",
            period="Temmuz 2026 - Devam Ediyor",
            x_bullets=[
                _bullet("proj-cli.tests.tr", "70 test yazdı.", ["testing", "open-source"]),
            ],
        )
    ]
    doc.sections.skills.items = [
        SkillItem(
            id="sk-python", name="Python", x_meta=XMeta(tags=["python"], always_include=True)
        ),
        SkillItem(id="sk-cv", name="Bilgisayarlı Görü", x_meta=XMeta(tags=["computer-vision"])),
        SkillItem(id="sk-fastapi", name="FastAPI", x_meta=XMeta(tags=["fastapi", "rest-api"])),
        SkillItem(
            id="sk-cv-en", name="Computer Vision", x_meta=XMeta(tags=["computer-vision"], lang="en")
        ),
    ]
    return doc


def _analysis(
    *,
    must: list[tuple[str, list[str]]],
    nice: list[tuple[str, list[str]]] | None = None,
    tags: list[str] | None = None,
    language: str = "tr",
    seniority: str = "mid",
    domain: str | None = None,
) -> JobAnalysis:
    requirements = [Requirement(text=text, kind="must", keywords=kw) for text, kw in must]
    requirements += [Requirement(text=text, kind="nice", keywords=kw) for text, kw in (nice or [])]
    return JobAnalysis(
        posting=JobPosting(
            company="Acme",
            title="Mühendis",
            language=language,  # type: ignore[arg-type]
            seniority=seniority,  # type: ignore[arg-type]
            work_mode="remote",
            requirements=requirements,
            domain=domain,
        ),
        keywords=KeywordMapping(tags=tags or []),
    )


VISION_JOB = "İş: Görüntü İşleme boru hattı kuracak mühendis. Python bilgisi şart."
BACKEND_JOB = "İş: FastAPI ile REST API geliştirecek backend mühendisi. Python bilgisi şart."


# --------------------------------------------------------------------------- #


class TestPeriodParsing:
    def test_ongoing_period_counts_as_today(self) -> None:
        assert parse_period_end("Eylül 2024 – Devam Ediyor", today=TODAY) == TODAY

    def test_turkish_month_and_year(self) -> None:
        assert parse_period_end("Şubat 2026 – Mayıs 2026", today=TODAY) == date(2026, 5, 28)

    def test_bare_year_range(self) -> None:
        assert parse_period_end("2018-2022", today=TODAY) == date(2022, 12, 28)

    def test_english_present(self) -> None:
        assert parse_period_end("January 2025 - Present", today=TODAY) == TODAY

    def test_unparseable_period_is_none(self) -> None:
        assert parse_period_end("bir ara", today=TODAY) is None

    def test_empty_period_is_none(self) -> None:
        assert parse_period_end("", today=TODAY) is None


class TestPeriodStart:
    """Başlangıç tarihi yalnızca sıralamada eşitlik bozmak için ayrıştırılıyor."""

    def test_turkish_month_and_year(self) -> None:
        assert parse_period_start("Şubat 2026 – Mayıs 2026") == date(2026, 2, 1)

    def test_english_month_and_year(self) -> None:
        assert parse_period_start("January 2025 - Present") == date(2025, 1, 1)

    def test_a_bare_year_starts_the_year(self) -> None:
        """Bitiş aralığın sonuna düşüyordu; başlangıç başına düşmeli."""
        assert parse_period_start("2018-2022") == date(2018, 1, 1)

    def test_unparseable_period_is_none(self) -> None:
        assert parse_period_start("bir ara") is None

    def test_empty_period_is_none(self) -> None:
        assert parse_period_start("") is None


class TestReverseChronologicalKey:
    def test_the_newest_period_sorts_first(self) -> None:
        periods = [
            "Ocak 2024 – Temmuz 2024",
            "Aralık 2025 – Devam Ediyor",
            "Eylül 2024 – Ocak 2026",
        ]

        ordered = sorted(
            periods, key=lambda p: reverse_chronological_key(p, today=TODAY), reverse=True
        )

        assert ordered == [
            "Aralık 2025 – Devam Ediyor",
            "Eylül 2024 – Ocak 2026",
            "Ocak 2024 – Temmuz 2024",
        ]

    def test_two_ongoing_periods_break_the_tie_on_the_start(self) -> None:
        older = reverse_chronological_key("Ocak 2024 – Devam Ediyor", today=TODAY)
        newer = reverse_chronological_key("Mayıs 2026 – Devam Ediyor", today=TODAY)

        assert newer > older

    def test_an_unreadable_period_sinks_to_the_bottom(self) -> None:
        """Tarih uydurulmuyor — okunamayan dönem `date.min`e düşüyor."""
        assert reverse_chronological_key("bir ara", today=TODAY) == (date.min, date.min)


class TestRecencyBonus:
    def test_ongoing_gets_the_full_bonus(self) -> None:
        assert recency_bonus("Ocak 2026 – Devam Ediyor", 1.0, today=TODAY) == 1.0

    def test_bonus_decays_with_age(self) -> None:
        one_year_ago = recency_bonus("Ocak 2025 – Ağustos 2025", 1.0, today=TODAY)
        assert 0.3 < one_year_ago < 0.7

    def test_older_than_the_window_gets_nothing(self) -> None:
        assert recency_bonus("2018-2020", 1.0, today=TODAY) == 0.0

    def test_unparseable_period_gets_nothing(self) -> None:
        assert recency_bonus("bir ara", 1.0, today=TODAY) == 0.0


class TestSeniorityFit:
    def test_exact_match_is_full(self) -> None:
        assert seniority_fit("mid", "mid", 0.5) == 0.5

    def test_one_step_apart_is_half(self) -> None:
        assert seniority_fit("senior", "mid", 0.5) == 0.25

    def test_two_steps_apart_is_zero(self) -> None:
        assert seniority_fit("lead", "junior", 0.5) == 0.0

    def test_unknown_posting_seniority_is_neutral(self) -> None:
        assert seniority_fit("unknown", "mid", 0.5) == 0.0

    def test_missing_item_seniority_is_neutral(self) -> None:
        assert seniority_fit("mid", None, 0.5) == 0.0


class TestRecallCheck:
    """Modelin bildirmediği ama ilanda geçen etiketler (TODO.md, model karşılaştırması)."""

    def test_turkish_terms_the_model_missed_are_caught(self, vocabulary: Any) -> None:
        text = "Görüntü İşleme ve Doğal Dil İşleme deneyimi aranıyor."
        check = recall_check(text, [], vocabulary=vocabulary)
        assert "computer-vision" in check.missed
        assert "nlp" in check.missed

    def test_already_reported_tags_are_not_repeated(self, vocabulary: Any) -> None:
        text = "Görüntü İşleme deneyimi aranıyor."
        check = recall_check(text, ["computer-vision"], vocabulary=vocabulary)
        assert check.missed == []

    def test_evidence_names_the_term_that_matched(self, vocabulary: Any) -> None:
        check = recall_check("Vektör veritabanları ile çalışmış.", [], vocabulary=vocabulary)
        assert check.evidence["vector-search"] == "vektör-veritabanları"

    def test_absent_tags_are_not_reported(self, vocabulary: Any) -> None:
        check = recall_check("Sadece Python.", [], vocabulary=vocabulary)
        assert check.missed == ["python"]


class TestPostingTags:
    def test_must_and_nice_are_separated(self, vocabulary: Any) -> None:
        analysis = _analysis(
            must=[("Python deneyimi", ["Python"])],
            nice=[("Docker artı", ["Docker"])],
        )
        profile = posting_tags(analysis, "", vocabulary=vocabulary)
        assert profile.must == ("python",)
        assert profile.nice == ("docker",)

    def test_a_tag_is_never_both_must_and_nice(self, vocabulary: Any) -> None:
        analysis = _analysis(
            must=[("Python zorunlu", ["Python"])],
            nice=[("Python artı", ["Python"])],
        )
        profile = posting_tags(analysis, "", vocabulary=vocabulary)
        assert profile.must == ("python",)
        assert "python" not in profile.nice

    def test_requirement_text_is_scanned_when_keywords_are_empty(self, vocabulary: Any) -> None:
        """Ucuz modeller gereksinimi yakalayıp `keywords`'ü boş bırakabiliyor."""
        analysis = _analysis(must=[("Görüntü İşleme tecrübesi", [])])
        profile = posting_tags(analysis, "", vocabulary=vocabulary)
        assert profile.must == ("computer-vision",)

    def test_recall_net_tags_land_in_nice(self, vocabulary: Any) -> None:
        analysis = _analysis(must=[("Python deneyimi", ["Python"])])
        profile = posting_tags(analysis, "Ayrıca Doğal Dil İşleme artı.", vocabulary=vocabulary)
        assert "nlp" in profile.nice
        assert "nlp" in profile.recall.missed


class TestRanking:
    """Kabul kriteri: ilan neyse o maddeler öne çıkmalı."""

    def test_vision_bullets_rank_top_for_a_vision_posting(
        self, resume: Resume, vocabulary: Any
    ) -> None:
        analysis = _analysis(
            must=[("Görüntü işleme boru hattı", ["Görüntü İşleme"]), ("Python", ["Python"])]
        )
        pool = build_pool(resume, analysis, VISION_JOB, vocabulary=vocabulary, today=TODAY)

        top = pool.experiences[0].bullets[0]
        assert top.id == "exp-ai.vision.tr"

    def test_api_bullets_rank_top_for_a_backend_posting(
        self, resume: Resume, vocabulary: Any
    ) -> None:
        analysis = _analysis(
            must=[("FastAPI ile REST API", ["FastAPI", "REST API"]), ("Python", ["Python"])]
        )
        pool = build_pool(resume, analysis, BACKEND_JOB, vocabulary=vocabulary, today=TODAY)

        top = pool.experiences[0].bullets[0]
        assert top.id == "exp-ai.api.tr"

    def test_must_outweighs_nice(self, resume: Resume, vocabulary: Any) -> None:
        analysis = _analysis(
            must=[("FastAPI", ["FastAPI"])],
            nice=[("Görüntü işleme artı", ["Görüntü İşleme"])],
        )
        pool = build_pool(resume, analysis, BACKEND_JOB, vocabulary=vocabulary, today=TODAY)

        bullets = {b.id: b.breakdown.total for b in pool.experiences[0].bullets}
        assert bullets["exp-ai.api.tr"] > bullets["exp-ai.vision.tr"]

    def test_recent_experience_outranks_an_equally_tagged_old_one(
        self, resume: Resume, vocabulary: Any
    ) -> None:
        resume.sections.experience.items[1].x_bullets = [
            _bullet("exp-eski.vision.tr", "Eskiden görüntü işleme yaptı.", ["computer-vision"])
        ]
        analysis = _analysis(must=[("Görüntü işleme", ["Görüntü İşleme"])])

        pool = build_pool(resume, analysis, VISION_JOB, vocabulary=vocabulary, today=TODAY)

        new = pool.experiences[0].bullets[0].breakdown
        old = pool.experiences[1].bullets[0].breakdown
        assert new.recency > old.recency == 0.0
        assert new.total > old.total

    def test_measurable_impact_breaks_a_tie(self, resume: Resume, vocabulary: Any) -> None:
        """`exp-ai.vision.tr` ile `.en` aynı etiketleri taşıyor; farkı impact yapıyor."""
        analysis = _analysis(must=[("Görüntü işleme", ["Görüntü İşleme"])], language="tr")
        pool = build_pool(resume, analysis, VISION_JOB, vocabulary=vocabulary, today=TODAY)

        top = pool.experiences[0].bullets[0]
        assert top.id == "exp-ai.vision.tr"
        assert top.breakdown.impact > 0


class TestCategoryBonus:
    """Komşuluk bonusu — yetenek bölümü ilan başına sekiz maddeye düşmesin."""

    def test_a_neighbour_tag_earns_the_small_bonus(self, resume: Resume, vocabulary: Any) -> None:
        analysis = _analysis(must=[("FastAPI", ["FastAPI"])])
        pool = build_pool(resume, analysis, BACKEND_JOB, vocabulary=vocabulary, today=TODAY)

        # `rest-api` ile `microservices` aynı kategoride (backend).
        api = next(b for b in pool.experiences[0].bullets if b.id == "exp-ai.api.tr")
        assert api.breakdown.category == 0.25

    def test_an_unrelated_item_gets_nothing(self, resume: Resume, vocabulary: Any) -> None:
        analysis = _analysis(must=[("FastAPI", ["FastAPI"])])
        pool = build_pool(resume, analysis, BACKEND_JOB, vocabulary=vocabulary, today=TODAY)

        docs = next(b for b in pool.experiences[1].bullets if b.id == "exp-eski.docs.tr")
        assert docs.breakdown.category == 0.0

    def test_it_never_outweighs_a_direct_match(self, resume: Resume, vocabulary: Any) -> None:
        analysis = _analysis(must=[("FastAPI", ["FastAPI"])])
        pool = build_pool(resume, analysis, BACKEND_JOB, vocabulary=vocabulary, today=TODAY)

        bullets = {b.id: b.breakdown.total for b in pool.experiences[0].bullets}
        assert bullets["exp-ai.api.tr"] > bullets["exp-ai.vision.tr"]

    def test_neighbours_fill_the_skill_section(self, resume: Resume, vocabulary: Any) -> None:
        """Doğrudan eşleşmeyen ama aynı kategoriden yetenek de havuza girer."""
        analysis = _analysis(must=[("Görüntü işleme", ["Görüntü İşleme"])])
        pool = build_pool(resume, analysis, VISION_JOB, vocabulary=vocabulary, today=TODAY)

        assert "sk-fastapi" not in [s.id for s in pool.selected_skills]  # kategori uzak
        assert "sk-cv" in [s.id for s in pool.selected_skills]


class TestLanguageFilter:
    def test_english_bullets_are_dropped_for_a_turkish_posting(
        self, resume: Resume, vocabulary: Any
    ) -> None:
        analysis = _analysis(must=[("Python", ["Python"])], language="tr")
        pool = build_pool(resume, analysis, VISION_JOB, vocabulary=vocabulary, today=TODAY)

        ids = [b.id for b in pool.experiences[0].bullets]
        assert "exp-ai.vision.en" not in ids
        assert "exp-ai.vision.tr" in ids

    def test_turkish_bullets_are_dropped_for_an_english_posting(
        self, resume: Resume, vocabulary: Any
    ) -> None:
        analysis = _analysis(must=[("Python", ["Python"])], language="en")
        pool = build_pool(resume, analysis, VISION_JOB, vocabulary=vocabulary, today=TODAY)

        ids = [b.id for b in pool.experiences[0].bullets]
        assert "exp-ai.vision.tr" not in ids
        assert "exp-ai.vision.en" in ids

    def test_language_can_be_forced(self, resume: Resume, vocabulary: Any) -> None:
        """`--lang en` (issue 08) Türkçe ilana İngilizce CV üretebilmeli."""
        analysis = _analysis(must=[("Python", ["Python"])], language="tr")
        pool = build_pool(
            resume, analysis, VISION_JOB, language="en", vocabulary=vocabulary, today=TODAY
        )

        assert pool.language == "en"
        assert "exp-ai.vision.en" in [b.id for b in pool.experiences[0].bullets]

    def test_untagged_language_bullets_survive_both_languages(
        self, resume: Resume, vocabulary: Any
    ) -> None:
        analysis = _analysis(must=[("SQL", ["SQL"])], language="en")
        pool = build_pool(resume, analysis, "SQL reporting", vocabulary=vocabulary, today=TODAY)

        assert "exp-eski.sql.tr" in [b.id for b in pool.experiences[1].bullets]


class TestSelection:
    def test_always_include_survives_a_zero_score(self, resume: Resume, vocabulary: Any) -> None:
        analysis = _analysis(must=[("Rust", [])], language="tr")
        pool = build_pool(resume, analysis, "Rust geliştirici", vocabulary=vocabulary, today=TODAY)

        python = next(s for s in pool.skills if s.id == "sk-python")
        assert python.selected

    def test_an_experience_is_never_left_empty(self, resume: Resume, vocabulary: Any) -> None:
        """İstihdam boşluğu, konu dışı bir maddeden daha kötü okunur."""
        analysis = _analysis(must=[("Görüntü işleme", ["Görüntü İşleme"])], language="tr")
        pool = build_pool(resume, analysis, VISION_JOB, vocabulary=vocabulary, today=TODAY)

        old = next(entry for entry in pool.experiences if entry.id == "exp-eski")
        assert old.selected_bullets

    def test_bullet_cap_per_experience_is_enforced(self, resume: Resume, vocabulary: Any) -> None:
        resume.sections.experience.items[0].x_bullets = [
            _bullet(f"exp-ai.b{index}", f"Madde {index}", ["python"]) for index in range(20)
        ]
        analysis = _analysis(must=[("Python", ["Python"])], language="tr")

        pool = build_pool(resume, analysis, VISION_JOB, vocabulary=vocabulary, today=TODAY)

        assert len(pool.experiences[0].selected_bullets) == MAX_BULLETS_PER_EXPERIENCE

    def test_hidden_items_never_enter_the_pool(self, resume: Resume, vocabulary: Any) -> None:
        resume.sections.experience.items[1].hidden = True
        analysis = _analysis(must=[("SQL", ["SQL"])], language="tr")

        pool = build_pool(resume, analysis, "SQL", vocabulary=vocabulary, today=TODAY)

        assert [entry.id for entry in pool.experiences] == ["exp-ai"]


class TestProjectPool:
    """Proje sayısında sabit sınır yok — kararı model veriyor (`MAX_PROJECTS`)."""

    @staticmethod
    def _many_projects(resume: Resume, count: int) -> Resume:
        resume.sections.projects.items = [
            ProjectItem(
                id=f"proj-{index}",
                name=f"Proje {index}",
                period="Temmuz 2026 - Devam Ediyor",
                x_bullets=[
                    _bullet(f"proj-{index}.tests.tr", f"{index} test yazdı.", ["testing", "python"])
                ],
            )
            for index in range(8)
        ][:count]
        return resume

    def test_every_relevant_project_reaches_the_model(
        self, resume: Resume, vocabulary: Any
    ) -> None:
        """Eskiden beşte kesiliyordu; sekizi de ilanla ilgiliyse sekizi de gitmeli."""
        self._many_projects(resume, 8)
        analysis = _analysis(must=[("Python", ["Python"])], language="tr")

        pool = build_pool(resume, analysis, BACKEND_JOB, vocabulary=vocabulary, today=TODAY)

        assert len([entry for entry in pool.projects if entry.selected_bullets]) == 8

    def test_a_weak_project_ranks_last_instead_of_being_cut(
        self, resume: Resume, vocabulary: Any
    ) -> None:
        """Eleme değil sıralama: ilgisiz proje en sona düşer, kararı model verir.

        Ön filtre projeyi kesmiyor çünkü "bu ilana uzak" ile "bu CV'de yeri yok"
        aynı şey değil — deneyimi zayıf bir adayda tematik olarak uzak bir proje
        hâlâ sayfanın en iyi kartı olabilir. Sıralama modele öneri olarak gidiyor.
        """
        resume.sections.projects.items = [
            ProjectItem(
                id="proj-alakasiz",
                name="Alakasız",
                period="Temmuz 2026 - Devam Ediyor",
                x_bullets=[_bullet("proj-alakasiz.tr", "Flutter arayüzü yazdı.", ["flutter"])],
            ),
            ProjectItem(
                id="proj-uygun",
                name="Uygun",
                period="Temmuz 2026 - Devam Ediyor",
                x_bullets=[_bullet("proj-uygun.tr", "Python aracı yazdı.", ["python"])],
            ),
        ]
        analysis = _analysis(must=[("Python", ["Python"])], language="tr")

        pool = build_pool(resume, analysis, BACKEND_JOB, vocabulary=vocabulary, today=TODAY)

        assert [entry.id for entry in pool.projects] == ["proj-uygun", "proj-alakasiz"]
        # Kesilmedi — modele hâlâ gösteriliyor.
        assert pool.projects[-1].selected_bullets

    def test_projects_are_ordered_by_score(self, resume: Resume, vocabulary: Any) -> None:
        """Sıralama modele öneri olarak gidiyor — en güçlü proje başta."""
        self._many_projects(resume, 4)
        analysis = _analysis(must=[("Python", ["Python"])], language="tr")

        pool = build_pool(resume, analysis, BACKEND_JOB, vocabulary=vocabulary, today=TODAY)

        scores = [entry.score for entry in pool.projects]
        assert scores == sorted(scores, reverse=True)


class TestDeterminism:
    def test_same_input_gives_the_same_output(self, resume: Resume, vocabulary: Any) -> None:
        """Issue 06 kabul kriteri, doğrudan."""
        analysis = _analysis(must=[("Görüntü işleme", ["Görüntü İşleme"])])

        first = build_pool(resume, analysis, VISION_JOB, vocabulary=vocabulary, today=TODAY)
        second = build_pool(resume, analysis, VISION_JOB, vocabulary=vocabulary, today=TODAY)

        assert first.model_dump(exclude={"scored_at"}) == second.model_dump(exclude={"scored_at"})

    def test_ties_are_broken_by_id_not_by_dict_order(self, resume: Resume, vocabulary: Any) -> None:
        analysis = _analysis(must=[("Python", ["Python"])], language="tr")
        pool = build_pool(resume, analysis, VISION_JOB, vocabulary=vocabulary, today=TODAY)

        scores = [(-b.breakdown.total, b.id) for b in pool.experiences[0].bullets]
        assert scores == sorted(scores)


class TestCoverage:
    def test_a_requirement_with_a_selected_bullet_is_covered(
        self, resume: Resume, vocabulary: Any
    ) -> None:
        analysis = _analysis(must=[("Görüntü işleme", ["Görüntü İşleme"])])
        pool = build_pool(resume, analysis, VISION_JOB, vocabulary=vocabulary, today=TODAY)

        row = pool.coverage[0]
        assert row.status == "covered"
        assert "exp-ai.vision.tr" in row.bullet_ids

    def test_a_requirement_with_no_match_is_missing(self, resume: Resume, vocabulary: Any) -> None:
        analysis = _analysis(must=[("Kubernetes", ["Kubernetes"]), ("Python", ["Python"])])
        pool = build_pool(
            resume, analysis, "Kubernetes ve Python", vocabulary=vocabulary, today=TODAY
        )

        row = pool.coverage[0]
        assert row.status == "missing"
        assert row.bullet_ids == []

    def test_a_skill_only_match_is_indirect(self, resume: Resume, vocabulary: Any) -> None:
        resume.sections.experience.items[0].x_bullets = []
        resume.sections.experience.items[1].x_bullets = []
        resume.sections.projects.items = []
        analysis = _analysis(must=[("FastAPI", ["FastAPI"])])

        pool = build_pool(resume, analysis, BACKEND_JOB, vocabulary=vocabulary, today=TODAY)

        row = pool.coverage[0]
        assert row.status == "indirect"
        assert row.skill_ids == ["sk-fastapi"]


class TestMissingTagsAndBudget:
    def test_tags_absent_from_the_superset_are_listed(
        self, resume: Resume, vocabulary: Any
    ) -> None:
        analysis = _analysis(must=[("Flutter", ["Flutter"]), ("Python", ["Python"])])
        pool = build_pool(resume, analysis, "Flutter ve Python", vocabulary=vocabulary, today=TODAY)

        assert "flutter" in pool.missing_tags
        assert "python" not in pool.missing_tags

    def test_the_pool_is_smaller_than_the_superset(self, resume: Resume, vocabulary: Any) -> None:
        """Kabul kriteri: LLM'e giden metin süper setten belirgin olarak az."""
        analysis = _analysis(must=[("Görüntü işleme", ["Görüntü İşleme"])])
        pool = build_pool(resume, analysis, VISION_JOB, vocabulary=vocabulary, today=TODAY)

        assert pool.budget.candidate_chars < pool.budget.superset_chars
        assert pool.budget.saved_ratio > 0


class TestWeights:
    def test_weights_are_recorded_in_the_output(self, resume: Resume, vocabulary: Any) -> None:
        analysis = _analysis(must=[("Python", ["Python"])])
        pool = build_pool(resume, analysis, VISION_JOB, vocabulary=vocabulary, today=TODAY)
        assert pool.weights.must == 3.0

    def test_custom_weights_change_the_ranking(self, resume: Resume, vocabulary: Any) -> None:
        analysis = _analysis(
            must=[("FastAPI", ["FastAPI"])],
            nice=[("Görüntü işleme", ["Görüntü İşleme"])],
        )
        flat = Weights(must=1.0, nice=1.0)

        pool = build_pool(
            resume, analysis, BACKEND_JOB, vocabulary=vocabulary, weights=flat, today=TODAY
        )

        api = next(b for b in pool.experiences[0].bullets if b.id == "exp-ai.api.tr")
        assert api.breakdown.must == 1.0


class TestPersistence:
    def test_round_trip(self, tmp_path: Path, resume: Resume, vocabulary: Any) -> None:
        analysis = _analysis(must=[("Python", ["Python"])])
        pool = build_pool(resume, analysis, VISION_JOB, vocabulary=vocabulary, today=TODAY)

        path = write_pool(tmp_path, pool)
        loaded = read_pool(path)

        assert path.name == CANDIDATES_FILENAME
        assert loaded.must_tags == pool.must_tags
        assert len(loaded.experiences) == len(pool.experiences)

    def test_find_run_matches_a_partial_slug(self, tmp_path: Path) -> None:
        (tmp_path / "acme-ai-20260806-100000").mkdir()
        (tmp_path / "başka-20260806-110000").mkdir()

        found = find_run("acme", tmp_path)

        assert found is not None
        assert found.name == "acme-ai-20260806-100000"

    def test_find_run_picks_the_newest_among_matches(self, tmp_path: Path) -> None:
        (tmp_path / "acme-20260806-100000").mkdir()
        (tmp_path / "acme-20260806-120000").mkdir()

        found = find_run("acme", tmp_path)

        assert found is not None
        assert found.name == "acme-20260806-120000"

    def test_find_run_returns_none_when_nothing_matches(self, tmp_path: Path) -> None:
        assert find_run("yok", tmp_path) is None
