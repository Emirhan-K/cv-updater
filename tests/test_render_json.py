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
            assert match.period == original.period
            # Rol taşıyan deneyimde unvanı roller taşır (aşağıdaki test).
            if not original.roles:
                assert match.position == original.position

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

    def test_experiences_are_reverse_chronological(self, rendered: Resume) -> None:
        """Sıra plandan değil `period`'dan geliyor — en güncel iş en üstte.

        Dış bir denetleyicinin "HR Red Flags" bulgusuydu: süper setin yazım
        sırası olduğu gibi basılıyordu.
        """
        assert rendered.sections.experience.items[0].company == "Örnek AI"

    def test_an_experience_missing_from_the_plan_is_hidden_not_deleted(
        self, rendered: Resume
    ) -> None:
        old = next(i for i in rendered.sections.experience.items if i.company == "Eski Yazılım")
        assert old.hidden is True

    def test_the_plan_order_does_not_survive_an_out_of_order_plan(
        self, resume: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        """Model eski deneyimi başa koysa bile sayfada en güncel iş üstte."""
        plan.experiences = [
            TailoredExperience(source_id="exp-eski", bullets=[]),
            TailoredExperience(source_id="exp-ai", bullets=[]),
        ]
        doc = render(resume, plan, pool, run_id=RUN)

        assert [item.company for item in doc.sections.experience.items] == [
            "Örnek AI",
            "Eski Yazılım",
        ]

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

    def test_the_item_title_is_cleared_when_roles_carry_it(self, rendered: Resume) -> None:
        """Regresyon: Preunec'te "AI Team Lead" arka arkaya iki kez yazıyordu.

        Reactive Resume öğenin `position`'ını **ve** rol satırlarını birden
        basıyor; en güncel rolün unvanı öğeninkiyle aynı olduğunda (terfi
        çizgisinin normal hali) aynı satır iki kez görünüyor.
        """
        item = rendered.sections.experience.items[0]
        assert item.roles
        assert item.position == ""
        assert item.company == "Örnek AI"

    def test_a_roleless_item_keeps_its_title(self, rendered: Resume) -> None:
        old = next(i for i in rendered.sections.experience.items if i.company == "Eski Yazılım")
        assert old.position == "Stajyer"


class TestBulletsPerRole:
    """Regresyon: terfi öncesi rol sayfada başlığı olup gövdesi olmayan bir satırdı.

    Maddelerin hepsi en güncel role yığılıyordu; süper set artık madde başına
    `role` taşıyabiliyor ve dağıtım ona bakıyor.
    """

    @pytest.fixture
    def promoted(self, resume: Resume) -> Resume:
        item = resume.sections.experience.items[0]
        item.roles = [
            Role(id="role-eski", position="Junior", period="Ocak 2025 – Mayıs 2026"),
            Role(id="role-yeni", position="Kıdemli", period="Mayıs 2026 – Devam Ediyor"),
        ]
        item.x_bullets[0].role = "role-eski"
        item.x_bullets[1].role = "role-yeni"
        return resume

    @pytest.fixture
    def both_bullets(self) -> TailorPlan:
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
                        ),
                        SelectedBullet(
                            source_id="exp-ai.docs.tr",
                            rewritten="Dokümantasyonu güncelledi.",
                            rationale="Uyuyor.",
                        ),
                    ],
                )
            ],
            skill_ids=["sk-cv"],
            project_ids=[],
        )

    def test_each_bullet_lands_on_its_own_role(
        self, promoted: Resume, both_bullets: TailorPlan, pool: CandidatePool
    ) -> None:
        doc = render(promoted, both_bullets, pool, run_id=RUN)

        roles = {role.position: role.description for role in doc.sections.experience.items[0].roles}
        assert "Görüntü işleme" in roles["Junior"]
        assert "Dokümantasyonu" in roles["Kıdemli"]
        assert "Dokümantasyonu" not in roles["Junior"]

    def test_an_unassigned_bullet_falls_to_the_most_recent_role(
        self, promoted: Resume, both_bullets: TailorPlan, pool: CandidatePool
    ) -> None:
        """`role` yazılmamış madde eski davranışı korur — güncel role gider."""
        promoted.sections.experience.items[0].x_bullets[0].role = ""

        doc = render(promoted, both_bullets, pool, run_id=RUN)

        roles = {role.position: role.description for role in doc.sections.experience.items[0].roles}
        assert "Görüntü işleme" in roles["Kıdemli"]
        assert roles["Junior"] == ""

    def test_an_unknown_role_id_does_not_lose_the_bullet(
        self, promoted: Resume, both_bullets: TailorPlan, pool: CandidatePool
    ) -> None:
        """Silinmiş bir role atıf sessizce maddeyi düşürmemeli."""
        promoted.sections.experience.items[0].x_bullets[0].role = "role-yok"

        doc = render(promoted, both_bullets, pool, run_id=RUN)

        roles = {role.position: role.description for role in doc.sections.experience.items[0].roles}
        assert "Görüntü işleme" in roles["Kıdemli"]

    def test_the_role_field_does_not_leak_into_the_output(
        self, promoted: Resume, both_bullets: TailorPlan, pool: CandidatePool
    ) -> None:
        doc = render(promoted, both_bullets, pool, run_id=RUN)

        assert '"role"' not in to_json(doc)


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

    def test_a_restricted_font_still_loads_its_bold_face(
        self, resume: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        """Regresyon: şirket ve proje adları PDF'te kalın çıkmıyordu.

        Süper set gövde için `["400", "500"]` istiyor, Tinos 500 taşımıyor.
        İlk sürüm her ağırlığı en yakınına indirgiyordu ve liste `["400"]`e
        çöküyordu — kalın yüz PDF'e hiç girmiyordu, şablonun kalın istediği
        her yer sessizce normale düşüyordu. `fontWeights` "şu ağırlıkta yaz"
        değil "şu yüzleri göm" demek: fontun taşıdığı ne varsa yüklenmeli.
        """
        resume.metadata.typography.body.fontWeights = ["400", "500"]
        resume.metadata.typography.heading.fontWeights = ["600"]

        doc = render(resume, plan, pool, run_id=RUN)

        assert doc.metadata.typography.body.fontWeights == ["400", "700"]
        assert doc.metadata.typography.heading.fontWeights == ["400", "700"]

    def test_a_font_can_be_forced(
        self, resume: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        doc = render(resume, plan, pool, run_id=RUN, font="Inter")

        assert doc.metadata.typography.body.fontFamily == "Inter"
        # Inter her ağırlığı taşıyor; eşleme yapılmamalı.
        assert (
            doc.metadata.typography.body.fontWeights == resume.metadata.typography.body.fontWeights
        )

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


class TestSectionTitles:
    """Regresyon: İngilizce CV'nin başlıkları Türkçe kalıyordu.

    İçerik İngilizceydi, üstünde "Özet" ve "Deneyim" yazıyordu — süper set tek
    dilde yazıldığı için başlıklar olduğu gibi kopyalanıyordu.
    """

    @pytest.fixture
    def titled(self, resume: Resume) -> Resume:
        resume.summary.title = "Özet"
        resume.sections.experience.title = "Deneyim"
        resume.sections.projects.title = "Projeler"
        resume.sections.skills.title = "Yetenekler"
        resume.sections.education.title = "Eğitim"
        return resume

    def test_english_titles_replace_the_turkish_ones(
        self, titled: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        doc = render(titled, plan, pool, language="en", run_id=RUN)

        assert doc.summary.title == "Summary"
        assert doc.sections.experience.title == "Experience"
        assert doc.sections.projects.title == "Projects"
        assert doc.sections.skills.title == "Skills"
        assert doc.sections.education.title == "Education"

    def test_turkish_titles_are_left_alone(
        self, titled: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        doc = render(titled, plan, pool, language="tr", run_id=RUN)
        assert doc.summary.title == "Özet"
        assert doc.sections.experience.title == "Deneyim"

    def test_an_empty_title_stays_empty(
        self, titled: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        """Boş başlık "varsayılanı kullan" demek; oraya metin yazmak bölüm uydurur."""
        titled.sections.languages.title = ""

        doc = render(titled, plan, pool, language="en", run_id=RUN)

        assert doc.sections.languages.title == ""


class TestPeriodLanguage:
    """Regresyon: İngilizce CV'de sağ sütundaki her tarih Türkçeydi.

    Tarih bir olgu, süper sette varyantı yok; çeviri deterministik ve sözlük
    tabanlı (`cv_updater.periods`), LLM'e gitmiyor.
    """

    def test_experience_and_role_periods_are_translated(
        self, resume: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        doc = render(resume, plan, pool, language="en", run_id=RUN)

        item = doc.sections.experience.items[0]
        assert item.period == "January 2026 – Present"
        assert item.roles[0].period == "May 2026 – Present"

    def test_project_periods_are_translated(
        self, resume: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        doc = render(resume, plan, pool, language="en", run_id=RUN)
        assert doc.sections.projects.items[0].period == "July 2026 - Present"

    def test_a_plain_year_range_is_left_alone(
        self, resume: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        doc = render(resume, plan, pool, language="en", run_id=RUN)
        assert doc.sections.education.items[0].period == "2018-2022"

    def test_turkish_runs_keep_turkish_dates(self, rendered: Resume) -> None:
        assert rendered.sections.experience.items[0].period == "Ocak 2026 – Devam Ediyor"


class TestFieldTranslations:
    """Regresyon: İngilizce CV'de unvanlar, derece ve dil seviyeleri Türkçeydi.

    Maddeler iki dilde yaşıyor, unvan yaşamıyor: bir deneyimin bir `position`'ı
    var. Çeviri süper sette elle yazılıyor (`x_i18n`) ve modele hiç sorulmuyor.
    """

    @pytest.fixture
    def translated(self, resume: Resume) -> Resume:
        resume.basics.headline = "Yazılım Mühendisi"
        resume.basics.x_i18n = {"en": {"headline": "Software Engineer"}}
        item = resume.sections.experience.items[0]
        item.x_i18n = {"en": {"position": "Software Engineer"}}
        item.roles[0].x_i18n = {"en": {"position": "Senior"}}
        resume.sections.education.items[0].x_i18n = {"en": {"degree": "BSc"}}
        return resume

    def test_english_overrides_are_applied(
        self, translated: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        doc = render(translated, plan, pool, language="en", run_id=RUN)

        assert doc.basics.headline == "Software Engineer"
        assert doc.sections.experience.items[0].roles[0].position == "Senior"
        assert doc.sections.education.items[0].degree == "BSc"

    def test_the_turkish_run_keeps_the_superset_values(
        self, translated: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        doc = render(translated, plan, pool, language="tr", run_id=RUN)

        assert doc.basics.headline == "Yazılım Mühendisi"
        assert doc.sections.experience.items[0].roles[0].position == "Kıdemli"

    def test_an_unknown_field_name_is_ignored(
        self, translated: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        """Yazım hatası yeni bir alan uydurmamalı — `validate` ayrıca raporluyor."""
        translated.basics.x_i18n = {"en": {"headlien": "Typo"}}

        doc = render(translated, plan, pool, language="en", run_id=RUN)

        assert doc.basics.headline == "Yazılım Mühendisi"
        assert not hasattr(doc.basics, "headlien")

    def test_translations_do_not_leak_into_the_output(
        self, translated: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        doc = render(translated, plan, pool, language="en", run_id=RUN)
        assert "x_i18n" not in to_json(doc)

    def test_a_cleared_field_is_not_refilled_by_a_translation(
        self, translated: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        """Regresyon: çeviri, silinmiş unvanı geri yazıp tekrarı diriltiyordu.

        Rol taşıyan deneyimde öğe seviyesindeki unvan bilerek siliniyor
        (`_attach_bullets`); `x_i18n` onu geri yazarsa Preunec'te "AI Team Lead"
        yine arka arkaya iki kez görünür.
        """
        doc = render(translated, plan, pool, language="en", run_id=RUN)

        item = doc.sections.experience.items[0]
        assert item.roles
        assert item.position == ""

    def test_an_explicit_period_translation_wins(
        self, translated: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        """Sözlük çevirisi genel kural; elle yazılan çeviri onu ezebilmeli."""
        translated.sections.experience.items[0].x_i18n = {"en": {"period": "Since 2026"}}

        doc = render(translated, plan, pool, language="en", run_id=RUN)

        assert doc.sections.experience.items[0].period == "Since 2026"


class TestSummaryTranslation:
    """Özetin yedeği de tek örnekli bir alan — `x_i18n` ile çevriliyor.

    Uyarlanmış CV'nin özetini normalde model yazıyor ve ilanın dilinde yazıyor.
    Süper setteki metin yalnızca plan bir özet üretemediğinde sayfaya düşüyor,
    ama tek dilde olduğu için İngilizce çalıştırmada içerik İngilizce, tepesindeki
    paragraf Türkçe kalıyordu.
    """

    @pytest.fixture
    def bilingual(self, resume: Resume) -> Resume:
        resume.summary.x_i18n = {"en": {"content": "<p>The superset summary.</p>"}}
        return resume

    def test_the_fallback_summary_is_translated(
        self, bilingual: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        plan.summary = ""

        doc = render(bilingual, plan, pool, language="en", run_id=RUN)

        assert doc.summary.content == "<p>The superset summary.</p>"

    def test_the_turkish_run_keeps_the_turkish_fallback(
        self, bilingual: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        plan.summary = ""

        doc = render(bilingual, plan, pool, language="tr", run_id=RUN)

        assert doc.summary.content == "<p>Süper setteki özet.</p>"

    def test_the_translation_never_overwrites_the_tailored_summary(
        self, bilingual: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        """Çeviri yedeği çeviriyor, üretilen özeti değil.

        Sıra tersine dönerse İngilizce çalıştırmalar uyarlanmış özetini sessizce
        kaybeder ve süper setin genel metnine düşer.
        """
        doc = render(bilingual, plan, pool, language="en", run_id=RUN)

        assert doc.summary.content == "<p>Görüntü işleme üzerine çalışan bir mühendis.</p>"

    def test_the_translation_does_not_leak_into_the_output(
        self, bilingual: Resume, plan: TailorPlan, pool: CandidatePool
    ) -> None:
        doc = render(bilingual, plan, pool, language="en", run_id=RUN)

        assert "x_i18n" not in to_json(doc)


class TestContactIcons:
    """İletişim ikonları kapalı: ATS için gürültü, metin katmanında karşılığı yok."""

    def test_icons_are_hidden(self, rendered: Resume) -> None:
        assert rendered.metadata.page.hideIcons is True
        assert rendered.metadata.page.hideSectionIcons is True

    def test_the_address_itself_survives(self, rendered: Resume) -> None:
        """İkon gidiyor, adres kalıyor — ATS'in okuduğu şey bu."""
        assert rendered.basics.email == "ada@example.com"


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
