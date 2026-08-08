"""Issue 11 — Render v2. Dönüşüm testleri RenderCV **kurulu olmadan** çalışır."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cv_updater.models.resume import (
    CertificationItem,
    EducationItem,
    ExperienceItem,
    LanguageItem,
    ProfileItem,
    ProjectItem,
    Resume,
    Role,
    SkillItem,
    Website,
)
from cv_updater.steps.render_pdf import (
    DEFAULT_FONT,
    DEFAULT_THEME,
    RenderCVMissingError,
    build_document,
    bullets,
    connections,
    education_entries,
    experience_entries,
    language_entries,
    overflow_advice,
    page_count,
    paragraphs,
    parse_period,
    project_entries,
    render,
    skill_entries,
    to_yaml,
    write_yaml,
)
from pdf_builder import write_pdf


def _resume(**overrides) -> Resume:
    """Küçük ama her bölümü dolu bir CV."""
    resume = Resume()
    resume.basics.name = "Emirhan Kuru"
    resume.basics.headline = "Yazılım Mühendisi"
    resume.basics.email = "emirhankuru@example.com"
    resume.basics.phone = "+90 535 950 4623"
    resume.basics.location = "İstanbul, Türkiye"
    resume.summary.content = "<p>Yapay zeka odaklı yazılım mühendisi.</p>"

    resume.sections.experience.items = [
        ExperienceItem(
            id="exp-vidinsight",
            company="VidInsight",
            position="Yazılım Mühendisi",
            period="Eylül 2024 – Devam Ediyor",
            description="<ul><li>Mikro servisler geliştirildi.</li></ul>",
        )
    ]
    resume.sections.education.items = [
        EducationItem(
            id="edu-firat",
            school="Fırat Üniversitesi",
            degree="Lisans",
            area="Yazılım Mühendisliği",
            period="2018-2022",
        )
    ]
    resume.sections.skills.items = [
        SkillItem(id="sk-python", name="Python"),
        SkillItem(id="sk-docker", name="Docker"),
    ]
    resume.sections.profiles.items = [
        ProfileItem(
            id="pr-github",
            network="GitHub",
            username="Emirhan-K",
            website=Website(url="https://github.com/Emirhan-K"),
        )
    ]
    for key, value in overrides.items():
        setattr(resume, key, value)
    return resume


# --------------------------------------------------------------------------- #
# HTML → düz metin
# --------------------------------------------------------------------------- #


def test_bullets_from_list_markup() -> None:
    assert bullets("<ul><li>Bir</li><li>İki &amp; üç</li></ul>") == ["Bir", "İki & üç"]


def test_bullets_from_plain_text() -> None:
    assert bullets("Tek bir açıklama") == ["Tek bir açıklama"]


def test_bullets_of_empty_markup() -> None:
    assert bullets("<ul></ul>") == []


def test_paragraphs() -> None:
    assert paragraphs("<p>Bir cümle.</p><p>İkinci.</p>") == ["Bir cümle.", "İkinci."]


# --------------------------------------------------------------------------- #
# Dönem ayrıştırma
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("period", "start", "end"),
    [
        ("Eylül 2024 – Devam Ediyor", "2024-09", "present"),
        ("Şubat 2026 – Mayıs 2026", "2026-02", "2026-05"),
        ("September 2024 - Present", "2024-09", "present"),
        ("2018-2022", "2018", "2022"),
        ("Ocak 2024 – Temmuz 2024", "2024-01", "2024-07"),
    ],
)
def test_parse_period(period: str, start: str, end: str) -> None:
    parsed = parse_period(period)
    assert (parsed.start, parsed.end) == (start, end)
    assert parsed.fields == {"start_date": start, "end_date": end}


def test_single_endpoint_stays_free_text() -> None:
    """Tek uçlu dönem serbest metin olarak geçer — yarım tarih aralığı üretilmez."""
    parsed = parse_period("2021")
    assert parsed.fields == {"date": "2021"}


def test_unparseable_period_is_not_invented() -> None:
    parsed = parse_period("Bahar dönemi")
    assert parsed.fields == {"date": "Bahar dönemi"}


def test_empty_period_produces_no_date_fields() -> None:
    assert parse_period("").fields == {}


# --------------------------------------------------------------------------- #
# Bölüm eşlemesi
# --------------------------------------------------------------------------- #


def test_experience_without_roles_is_one_entry() -> None:
    (entry,) = experience_entries(_resume().sections.experience.items[0])

    assert entry["company"] == "VidInsight"
    assert entry["position"] == "Yazılım Mühendisi"
    assert entry["start_date"] == "2024-09"
    assert entry["end_date"] == "present"
    assert entry["highlights"] == ["Mikro servisler geliştirildi."]


def test_roles_become_separate_entries_newest_first() -> None:
    """Terfi çizgisi düzleştirilmiyor: her rol kendi satırında, en güncel üstte."""
    item = ExperienceItem(
        id="exp-preunec",
        company="Preunec",
        period="Aralık 2024 – Devam Ediyor",
        roles=[
            Role(
                id="r1",
                position="Yazılım Mühendisi",
                period="Aralık 2024 – Nisan 2026",
                description="<ul><li>Eski madde</li></ul>",
            ),
            Role(
                id="r2",
                position="AI Ekip Lideri",
                period="Mayıs 2026 – Devam Ediyor",
                description="<ul><li>Güncel madde</li></ul>",
            ),
        ],
    )
    entries = experience_entries(item)

    assert [entry["position"] for entry in entries] == ["AI Ekip Lideri", "Yazılım Mühendisi"]
    assert entries[0]["highlights"] == ["Güncel madde"]
    assert entries[0]["start_date"] == "2026-05"
    assert all(entry["company"] == "Preunec" for entry in entries)


def test_hidden_items_are_skipped() -> None:
    resume = _resume()
    resume.sections.education.items[0].hidden = True
    assert education_entries(resume) == []


def test_education_mapping() -> None:
    (entry,) = education_entries(_resume())
    assert entry["institution"] == "Fırat Üniversitesi"
    assert entry["area"] == "Yazılım Mühendisliği"
    assert entry["degree"] == "Lisans"
    assert entry["start_date"] == "2018"


def test_project_mapping() -> None:
    resume = _resume()
    resume.sections.projects.items = [
        ProjectItem(
            id="proj-echolog",
            name="EchoLog",
            period="2025",
            description="<ul><li>Sesli günlük</li></ul>",
        )
    ]
    (entry,) = project_entries(resume)
    assert entry == {"name": "EchoLog", "date": "2025", "highlights": ["Sesli günlük"]}


def test_bare_skills_collapse_to_one_line() -> None:
    """Otuz tane "Python:" satırı ne okunur ne ATS'e faydalı — tek virgüllü satır."""
    assert skill_entries(_resume()) == ["Python, Docker"]


def test_skills_with_keywords_become_labelled_entries() -> None:
    resume = _resume()
    resume.sections.skills.items = [
        SkillItem(id="sk-lang", name="Diller", keywords=["Python", "SQL"]),
        SkillItem(id="sk-ml", name="ML", keywords=["PyTorch"]),
    ]
    assert skill_entries(resume) == [
        {"label": "Diller", "details": "Python, SQL"},
        {"label": "ML", "details": "PyTorch"},
    ]


def test_mixed_skills_stay_single_typed() -> None:
    """RenderCV bir bölümde tek girdi tipi kabul ediyor; karışıkta tek satıra düşülür."""
    resume = _resume()
    resume.sections.skills.items = [
        SkillItem(id="sk-lang", name="Diller", keywords=["Python", "SQL"]),
        SkillItem(id="sk-docker", name="Docker"),
    ]
    entries = skill_entries(resume)
    assert entries == ["Diller, Python, SQL, Docker"]
    assert all(isinstance(entry, str) for entry in entries)


def test_language_mapping() -> None:
    resume = _resume()
    resume.sections.languages.items = [
        LanguageItem(id="lang-tr", language="Türkçe", fluency="Ana dil")
    ]
    assert language_entries(resume) == [{"label": "Türkçe", "details": "Ana dil"}]


# --------------------------------------------------------------------------- #
# Linkler
# --------------------------------------------------------------------------- #


def test_connection_shows_the_full_url_as_text() -> None:
    """ATS gömülü linki okumuyor — görünen metin tam adres olmalı (`docs/ATS.md`)."""
    (entry,) = connections(_resume())
    assert entry["placeholder"] == "github.com/Emirhan-K"
    assert entry["url"] == "https://github.com/Emirhan-K"


def test_connection_icon_is_a_bare_name() -> None:
    """Regresyon: `"fa brands fa-github"` verilince Typst çözemeyip **düz metin** basıyordu."""
    (entry,) = connections(_resume())
    assert entry["fontawesome_icon"] == "github"
    assert "fa " not in entry["fontawesome_icon"]


def test_unknown_network_gets_the_generic_icon() -> None:
    resume = _resume()
    resume.sections.profiles.items = [
        ProfileItem(
            id="pr-blog",
            network="Kişisel Blog",
            website=Website(url="https://blog.example.com"),
        )
    ]
    (entry,) = connections(resume)
    assert entry["fontawesome_icon"] == "link"
    assert entry["placeholder"] == "blog.example.com"


def test_explicit_label_wins_over_url() -> None:
    resume = _resume()
    resume.sections.profiles.items[0].website.label = "github.com/başka"
    (entry,) = connections(resume)
    assert entry["placeholder"] == "github.com/başka"


# --------------------------------------------------------------------------- #
# Belge
# --------------------------------------------------------------------------- #


def test_document_uses_turkish_section_titles() -> None:
    document = build_document(_resume(), language="tr")
    assert list(document["cv"]["sections"]) == ["Özet", "Deneyim", "Eğitim", "Yetenekler"]
    assert document["locale"]["language"] == "turkish"


def test_document_uses_english_section_titles() -> None:
    document = build_document(_resume(), language="en")
    assert list(document["cv"]["sections"]) == ["Summary", "Experience", "Education", "Skills"]
    assert document["locale"]["language"] == "english"


def test_theme_and_font_land_in_design() -> None:
    document = build_document(_resume(), theme="engineeringresumes", font="Lato")
    assert document["design"]["theme"] == "engineeringresumes"
    assert document["design"]["typography"]["font_family"]["body"] == "Lato"
    assert build_document(_resume())["design"]["theme"] == DEFAULT_THEME
    assert build_document(_resume())["design"]["typography"]["font_family"]["body"] == DEFAULT_FONT


def test_phone_becomes_a_tel_uri() -> None:
    assert build_document(_resume())["cv"]["phone"] == "tel:+90-535-950-4623"


def test_empty_fields_are_pruned() -> None:
    resume = Resume()
    resume.basics.name = "Ada"
    document = build_document(resume)

    assert document["cv"] == {"name": "Ada"}
    assert "sections" not in document["cv"]


def test_hidden_summary_is_dropped() -> None:
    resume = _resume()
    resume.summary.hidden = True
    assert "Özet" not in build_document(resume)["cv"]["sections"]


def test_certifications_are_mapped() -> None:
    resume = _resume()
    resume.sections.certifications.items = [
        CertificationItem(id="cert-aws", title="AWS SAA", issuer="Amazon", date="2025")
    ]
    section = build_document(resume)["cv"]["sections"]["Sertifikalar"]
    assert section == [{"name": "AWS SAA", "summary": "Amazon", "date": "2025"}]


def test_yaml_round_trip() -> None:
    import yaml

    document = build_document(_resume())
    assert yaml.safe_load(to_yaml(document)) == document


def test_yaml_keeps_turkish_characters_readable() -> None:
    text = to_yaml(build_document(_resume()))
    assert "Fırat Üniversitesi" in text
    assert "\\u" not in text


def test_write_yaml(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, build_document(_resume()))
    assert path.name == "cv.yaml"
    assert "Emirhan Kuru" in path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Sayfa bütçesi ve opsiyonel bağımlılık
# --------------------------------------------------------------------------- #


def test_page_count(tmp_path: Path) -> None:
    assert page_count(write_pdf(tmp_path / "cv.pdf", ["Tek sayfa"])) == 1


def test_overflow_advice_names_what_to_cut() -> None:
    resume = _resume()
    resume.sections.projects.items = [
        ProjectItem(id=f"proj-{index}", name=f"P{index}") for index in range(4)
    ]
    advice = " ".join(overflow_advice(resume))
    assert "4 proje" in advice


def test_overflow_advice_always_says_something() -> None:
    assert overflow_advice(_resume())


def test_missing_rendercv_explains_the_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Render opsiyonel — kurulu değilken ne yapılacağı söylenmeli, çakılmamalı."""
    monkeypatch.setitem(sys.modules, "rendercv.renderer.pdf_png", None)

    with pytest.raises(RenderCVMissingError, match="uv sync --extra render"):
        render(tmp_path / "cv.yaml", tmp_path / "cv.pdf")


# --------------------------------------------------------------------------- #
# Uçtan uca (RenderCV kuruluysa)
# --------------------------------------------------------------------------- #


def test_render_produces_an_ats_clean_pdf(tmp_path: Path) -> None:
    """Issue 11 kabul kriteri: üretilen PDF'te ligatür hatası **yok**."""
    pytest.importorskip("rendercv")
    from cv_updater.steps.ats_check import check_pdf

    resume = _resume()
    resume.sections.experience.items[
        0
    ].description = (
        "<ul><li>Modele özel ince ayar (fine-tuning) ve çevrimdışı (offline) çalışma.</li></ul>"
    )
    yaml_path = write_yaml(tmp_path, build_document(resume))
    pdf_path = render(yaml_path, tmp_path / "cv.pdf")

    report = check_pdf(pdf_path)
    ligatures = next(check for check in report.checks if check.rule == "ligatures")

    assert ligatures.ratio == 1.0
    assert ligatures.findings == ()
    assert page_count(pdf_path) == 1


def test_rendered_pdf_shows_links_as_text(tmp_path: Path) -> None:
    """Regresyon: `social_networks` sadece kullanıcı adını basıyordu, ATS URL'i göremiyordu."""
    pytest.importorskip("rendercv")
    from cv_updater.steps.ats_check import extract_text

    yaml_path = write_yaml(tmp_path, build_document(_resume()))
    text = extract_text(render(yaml_path, tmp_path / "cv.pdf")).text

    assert "github.com/Emirhan-K" in text
    assert "fa brands" not in text
