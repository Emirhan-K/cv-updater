"""Issue 08 — `tailored.json` derlemesi. LLM yok, ağ yok."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from cv_updater.models.candidates import CandidatePool
from cv_updater.models.job import JobAnalysis, JobPosting, KeywordMapping, Requirement
from cv_updater.models.resume import (
    EducationItem,
    ExperienceItem,
    ProfileItem,
    ProjectItem,
    Resume,
    Role,
    SkillItem,
    Summary,
    Website,
    XBullet,
    XMeta,
)
from cv_updater.models.tailor import SelectedBullet, TailoredExperience, TailorPlan
from cv_updater.steps.render_json import (
    DEFAULT_FONT,
    LIGATURE_FREE_FONTS,
    TAILORED_FILENAME,
    bullet_list,
    has_ligatures,
    nearest_weight,
    new_id,
    paragraph,
    read_tailored,
    render,
    to_json,
    write_tailored,
)
from cv_updater.steps.score import build_pool
from cv_updater.tags import load_tags

TODAY = date(2026, 8, 6)
RUN = "acme-20260806-120000"


@pytest.fixture
def vocabulary(tags_yaml_path: Path):
    return load_tags(tags_yaml_path)


@pytest.fixture
def resume() -> Resume:
    doc = Resume()
    doc.basics.name = "Ada Örnek"
    doc.basics.email = "ada@example.com"
    doc.summary = Summary(content="<p>Süper setteki özet.</p>")
    doc.sections.profiles.items = [
        ProfileItem(
            id="pr-github",
            network="GitHub",
            username="ada",
            website=Website(url="https://github.com/ada-ornek"),
        )
    ]
    doc.sections.experience.items = [
        ExperienceItem(
            id="exp-ai",
            company="Örnek AI",
            position="Yazılım Mühendisi",
            period="Ocak 2026 – Devam Ediyor",
            roles=[Role(id="role-1", position="Kıdemli", period="Mayıs 2026 – Devam Ediyor")],
            x_meta=XMeta(tags=["computer-vision"]),
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
        ),
        ExperienceItem(
            id="exp-eski",
            company="Eski Yazılım",
            position="Stajyer",
            period="2019-2020",
            x_bullets=[
                XBullet(
                    id="exp-eski.sql.tr",
                    text="Raporlama sorguları yazdı.",
                    x_meta=XMeta(tags=["sql"], lang="tr"),
                )
            ],
        ),
    ]
    doc.sections.education.items = [
        EducationItem(id="edu-1", school="Örnek Üniversitesi", degree="Lisans", period="2018-2022")
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
        SkillItem(id="sk-cv", name="Bilgisayarlı Görü", x_meta=XMeta(tags=["computer-vision"])),
        SkillItem(id="sk-flutter", name="Flutter", x_meta=XMeta(tags=["flutter"])),
    ]
    return doc


@pytest.fixture
def pool(resume: Resume, vocabulary: Any) -> CandidatePool:
    analysis = JobAnalysis(
        posting=JobPosting(
            company="Acme",
            title="Mühendis",
            language="tr",
            seniority="mid",
            work_mode="remote",
            requirements=[
                Requirement(text="Görüntü işleme", kind="must", keywords=["Görüntü İşleme"]),
                Requirement(text="Python", kind="must", keywords=["Python"]),
            ],
        ),
        keywords=KeywordMapping(tags=["computer-vision", "python"]),
    )
    return build_pool(
        resume, analysis, "Görüntü İşleme ve Python", vocabulary=vocabulary, today=TODAY, slug=RUN
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
                        rationale="Uyuyor.",
                    )
                ],
            )
        ],
        skill_ids=["sk-cv", "sk-python"],
        project_ids=["proj-cli"],
    )


@pytest.fixture
def rendered(resume: Resume, plan: TailorPlan, pool: CandidatePool) -> Resume:
    return render(resume, plan, pool, run_id=RUN)


class TestHtmlHelpers:
    def test_bullets_become_a_list(self) -> None:
        assert bullet_list(["bir", "iki"]) == "<ul><li>bir</li><li>iki</li></ul>"

    def test_empty_input_is_an_empty_string(self) -> None:
        assert bullet_list([]) == ""

    def test_html_is_escaped(self) -> None:
        """Madde metnindeki `<` HTML'i bozmamalı."""
        assert "&lt;script&gt;" in bullet_list(["<script>"])

    def test_summary_becomes_a_paragraph(self) -> None:
        assert paragraph("Merhaba") == "<p>Merhaba</p>"

    def test_newlines_collapse(self) -> None:
        assert paragraph("iki\n   satır") == "<p>iki satır</p>"


class TestImmutableFields:
    """Kabul kriteri: unvan / şirket / tarih `master.json` ile birebir aynı."""

    def test_company_title_and_period_survive_unchanged(
        self, resume: Resume, rendered: Resume
    ) -> None:
        for original in resume.sections.experience.items:
            match = next(
                item
                for item in rendered.sections.experience.items
                if item.company == original.company
            )
            assert match.position == original.position
            assert match.period == original.period

    def test_roles_survive_unchanged(self, resume: Resume, rendered: Resume) -> None:
        original = resume.sections.experience.items[0].roles[0]
        rendered_role = rendered.sections.experience.items[0].roles[0]
        assert (rendered_role.position, rendered_role.period) == (
            original.position,
            original.period,
        )

    def test_basics_and_education_are_copied(self, resume: Resume, rendered: Resume) -> None:
        assert rendered.basics.name == resume.basics.name
        assert rendered.basics.email == resume.basics.email
        assert rendered.sections.education.items[0].school == "Örnek Üniversitesi"

    def test_the_source_resume_is_not_mutated(self, resume: Resume, rendered: Resume) -> None:
        assert resume.sections.experience.items[0].id == "exp-ai"
        assert resume.sections.experience.items[0].x_bullets


class TestCompilation:
    def test_summary_comes_from_the_plan(self, rendered: Resume) -> None:
        assert rendered.summary.content == "<p>Görüntü işleme üzerine çalışan bir mühendis.</p>"

    def test_experience_description_is_built_from_selected_bullets(self, rendered: Resume) -> None:
        item = rendered.sections.experience.items[0]
        markup = item.roles[0].description if item.roles else item.description
        assert markup.startswith("<ul><li>Görüntü işleme")
        assert "Dokümantasyonu" not in markup

    def test_experiences_follow_the_plan_order(self, rendered: Resume) -> None:
        assert rendered.sections.experience.items[0].company == "Örnek AI"

    def test_an_experience_missing_from_the_plan_is_hidden_not_deleted(
        self, rendered: Resume
    ) -> None:
        old = next(i for i in rendered.sections.experience.items if i.company == "Eski Yazılım")
        assert old.hidden is True

    def test_skills_follow_the_plan_order(self, rendered: Resume) -> None:
        names = [item.name for item in rendered.sections.skills.items if not item.hidden]
        assert names == ["Bilgisayarlı Görü", "Python"]

    def test_unselected_skills_are_hidden_not_dropped(self, rendered: Resume) -> None:
        flutter = next(item for item in rendered.sections.skills.items if item.name == "Flutter")
        assert flutter.hidden is True

    def test_project_bullets_come_from_the_pool_verbatim(self, rendered: Resume) -> None:
        """Proje metni modelden geçmiyor — yeniden ifade yoksa uydurma da yok."""
        project = rendered.sections.projects.items[0]
        assert project.description == "<ul><li>Kütüphaneyi 70 testin altına aldı.</li></ul>"

    def test_profile_label_becomes_the_full_url(self, rendered: Resume) -> None:
        """Çoğu ATS gömülü linki okumaz; okuduğu şey görünen metin."""
        assert rendered.sections.profiles.items[0].website.label == "github.com/ada-ornek"


class TestRoles:
    """Regresyon: ilk üretilen PDF'te Preunec maddesiz çıktı.

    Reactive Resume bir deneyimde `roles[]` doluysa öğenin kendi
    `description`'ını render etmiyor — rollerin kendi açıklamasına bakıyor.
    """

    def test_bullets_go_to_the_role_when_roles_exist(self, rendered: Resume) -> None:
        item = rendered.sections.experience.items[0]
        assert item.roles
        assert item.roles[0].description.startswith("<ul><li>")

    def test_the_item_description_is_cleared_to_avoid_double_rendering(
        self, rendered: Resume
    ) -> None:
        assert rendered.sections.experience.items[0].description == ""

    def test_bullets_stay_on_the_item_when_there_are_no_roles(self, rendered: Resume) -> None:
        """Rolsüz deneyimlerin davranışı değişmemeli."""
        old = next(i for i in rendered.sections.experience.items if i.company == "Eski Yazılım")
        assert old.roles == []

    def test_the_most_recent_role_wins(
        self, resume: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        """Roller ters sırada verilse de maddeler güncel role gitmeli."""
        item = resume.sections.experience.items[0]
        item.roles = [
            Role(id="role-eski", position="Junior", period="Ocak 2025 – Mayıs 2026"),
            Role(id="role-yeni", position="Kıdemli", period="Mayıs 2026 – Devam Ediyor"),
        ]

        doc = render(resume, plan, pool, run_id=RUN)

        roles = doc.sections.experience.items[0].roles
        senior = next(role for role in roles if role.position == "Kıdemli")
        junior = next(role for role in roles if role.position == "Junior")
        assert senior.description.startswith("<ul><li>")
        assert junior.description == ""

    def test_role_titles_and_periods_are_untouched(self, resume: Resume, rendered: Resume) -> None:
        original = resume.sections.experience.items[0].roles[0]
        rendered_role = rendered.sections.experience.items[0].roles[0]
        assert (rendered_role.position, rendered_role.period) == (
            original.position,
            original.period,
        )


class TestIds:
    def test_ids_are_fresh(self, rendered: Resume) -> None:
        ids = [item.id for item in rendered.sections.experience.items]
        assert "exp-ai" not in ids

    def test_ids_are_unique(self, rendered: Resume) -> None:
        payload = json.loads(to_json(rendered))
        found: list[str] = []
        for section in payload["sections"].values():
            found += [item["id"] for item in section["items"]]
        assert len(found) == len(set(found))

    def test_ids_are_stable_within_a_run(self) -> None:
        assert new_id(RUN, "exp-ai") == new_id(RUN, "exp-ai")

    def test_ids_differ_between_runs(self) -> None:
        assert new_id(RUN, "exp-ai") != new_id("baska-20260807-090000", "exp-ai")


class TestOutput:
    def test_the_superset_does_not_leak(self, rendered: Resume) -> None:
        """Seçilmemiş 72 maddeyi taşıyan bir "uyarlanmış CV" uyarlanmış değildir."""
        payload = to_json(rendered)
        assert "x_meta" not in payload
        assert "x_bullets" not in payload
        assert "Dokümantasyonu güncelledi" not in payload

    def test_it_validates_against_the_live_schema(
        self, rendered: Resume, rxresume_schema: dict[str, Any]
    ) -> None:
        errors = list(
            Draft202012Validator(rxresume_schema).iter_errors(json.loads(to_json(rendered)))
        )
        assert errors == [], [str(error) for error in errors[:3]]

    def test_no_ligature_characters_anywhere(self, rendered: Resume) -> None:
        """Issue 08 kabul kriteri, doğrudan."""
        assert has_ligatures(to_json(rendered)) == []

    def test_ligatures_in_the_master_are_scrubbed(
        self, resume: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        resume.basics.headline = "ﬁne-tuning uzmanı"
        doc = render(resume, plan, pool, run_id=RUN)

        payload = to_json(doc)

        assert has_ligatures(payload) == []
        assert "fine-tuning" in payload

    def test_the_stylesheet_disables_ligatures(self, rendered: Resume) -> None:
        stylesheet = json.loads(to_json(rendered))["metadata"]["stylesheet"]
        assert stylesheet["mode"] == "legacy"
        assert "font-variant-ligatures: none" in stylesheet["source"]["text"]


class TestTypography:
    """Regresyon: ilk PDF'te ligatürler duruyordu.

    `metadata.stylesheet` ile `font-variant-ligatures: none` koymak yetmiyor —
    Reactive Resume PDF'i `@react-pdf/renderer` ile üretiyor ve o bu CSS
    özelliğini tanımıyor. Tek gerçek koruma ligatürsüz font.
    """

    def test_the_default_font_replaces_the_master_font(self, rendered: Resume) -> None:
        assert rendered.metadata.typography.body.fontFamily == DEFAULT_FONT
        assert rendered.metadata.typography.heading.fontFamily == DEFAULT_FONT

    def test_the_default_font_is_measured_ligature_free(self) -> None:
        assert DEFAULT_FONT in LIGATURE_FREE_FONTS

    def test_weights_are_clamped_to_what_the_font_carries(
        self, resume: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        """Tinos yalnızca 400/700 taşıyor; 500 ve 600 istenirse font bulunamaz."""
        resume.metadata.typography.body.fontWeights = ["400", "500"]
        resume.metadata.typography.heading.fontWeights = ["600"]

        doc = render(resume, plan, pool, run_id=RUN)

        assert doc.metadata.typography.body.fontWeights == ["400"]
        assert doc.metadata.typography.heading.fontWeights == ["700"]

    def test_a_font_can_be_forced(
        self, resume: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        doc = render(resume, plan, pool, run_id=RUN, font="Inter")

        assert doc.metadata.typography.body.fontFamily == "Inter"
        # Inter her ağırlığı taşıyor; eşleme yapılmamalı.
        assert (
            doc.metadata.typography.body.fontWeights == resume.metadata.typography.body.fontWeights
        )

    def test_nearest_weight_prefers_the_bolder_side_on_a_tie(self) -> None:
        assert nearest_weight("550", ("400", "700")) == "700"
        assert nearest_weight("500", ("400", "700")) == "400"
        assert nearest_weight("400", ("400", "700")) == "400"

    def test_the_stylesheet_is_still_written_for_the_web_preview(self, rendered: Resume) -> None:
        """PDF'i kurtarmıyor ama web önizlemesinde işe yarıyor; zararsız."""
        stylesheet = json.loads(to_json(rendered))["metadata"]["stylesheet"]
        assert "font-variant-ligatures: none" in stylesheet["source"]["text"]


class TestLanguage:
    def test_turkish_posting_sets_a_turkish_locale(self, rendered: Resume) -> None:
        assert rendered.metadata.page.locale == "tr-TR"

    def test_english_can_be_forced(
        self, resume: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        doc = render(resume, plan, pool, language="en", run_id=RUN)
        assert doc.metadata.page.locale == "en-US"


class TestPersistence:
    def test_round_trip(self, tmp_path: Path, rendered: Resume) -> None:
        path = write_tailored(tmp_path, rendered)
        loaded = read_tailored(path)

        assert path.name == TAILORED_FILENAME
        assert loaded.summary.content == rendered.summary.content

    def test_turkish_characters_stay_readable(self, tmp_path: Path, rendered: Resume) -> None:
        path = write_tailored(tmp_path, rendered)
        assert "Görüntü" in path.read_text(encoding="utf-8")

    def test_rendering_twice_gives_the_same_file(
        self, resume: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        first = to_json(render(resume, plan, pool, run_id=RUN))
        second = to_json(render(resume, plan, pool, run_id=RUN))
        assert first == second
