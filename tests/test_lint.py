"""Issue 03 lint kuralları. Her kural TODO.md §1'deki bir ATS bulgusuna karşılık gelir."""

from __future__ import annotations

import pytest

from cv_updater.lint import (
    LIGATURES,
    check_education_degree,
    check_email,
    check_ids,
    check_language_coverage,
    check_ligatures,
    check_paren_spacing,
    check_placeholders,
    check_superset_size,
    check_tags,
    check_visible_profile_urls,
    fix_ligatures,
    iter_prose,
    lint,
    strip_html,
)
from cv_updater.models.resume import (
    CustomField,
    EducationItem,
    ExperienceItem,
    Resume,
    SkillItem,
    XBullet,
    XMeta,
)
from cv_updater.tags import default_vocabulary


def rules(findings) -> list[str]:
    return [f.rule for f in findings]


# --- e-posta ---------------------------------------------------------------- #


def test_email_missing_is_an_error() -> None:
    resume = Resume()
    assert "email.present" in rules(check_email(resume))


def test_single_email_passes() -> None:
    resume = Resume()
    resume.basics.email = "ada@example.com"
    assert not list(check_email(resume))


def test_two_emails_in_one_field_is_an_error() -> None:
    resume = Resume()
    resume.basics.email = "ada@example.com, ada2@example.com"
    assert "email.single" in rules(check_email(resume))


def test_second_email_elsewhere_is_an_error() -> None:
    resume = Resume()
    resume.basics.email = "ada@example.com"
    resume.basics.customFields.append(
        CustomField(id="cf", icon="", text="eski: old@example.com")
    )
    assert "email.single" in rules(check_email(resume))


# --- görünür GitHub / LinkedIn URL'leri ------------------------------------- #


def test_icon_plus_username_is_not_enough() -> None:
    """TODO.md §1: çoğu ATS gömülü linki okumaz, görünür metin gerekir."""
    resume = Resume()
    resume.basics.customFields = [
        CustomField(id="a", icon="github-logo", text="Ada-K", link="https://github.com/ada-k"),
        CustomField(
            id="b", icon="linkedin-logo", text="Ada-K", link="https://linkedin.com/in/ada-k"
        ),
    ]
    found = rules(check_visible_profile_urls(resume))
    assert "profiles.github" in found
    assert "profiles.linkedin" in found


def test_visible_full_urls_pass() -> None:
    resume = Resume()
    resume.basics.customFields = [
        CustomField(id="a", icon="github-logo", text="github.com/ada-k"),
        CustomField(id="b", icon="linkedin-logo", text="linkedin.com/in/ada-k"),
    ]
    assert not list(check_visible_profile_urls(resume))


def test_link_field_alone_does_not_satisfy_the_rule() -> None:
    """Kural kasıtlı olarak `link` alanına bakmaz — ATS onu okumuyor."""
    resume = Resume()
    resume.basics.customFields = [
        CustomField(id="a", icon="", text="GitHub", link="https://github.com/ada-k"),
        CustomField(id="b", icon="", text="LinkedIn", link="https://linkedin.com/in/ada-k"),
    ]
    assert len(list(check_visible_profile_urls(resume))) == 2


# --- eğitimde derece seviyesi ----------------------------------------------- #


def test_education_without_degree_is_an_error() -> None:
    resume = Resume()
    resume.sections.education.items = [
        EducationItem(id="e1", school="Örnek Üniversitesi", area="Yazılım Mühendisliği")
    ]
    assert "education.degree" in rules(check_education_degree(resume))


@pytest.mark.parametrize("degree", ["Lisans", "Bachelor's", "Yüksek Lisans", "PhD"])
def test_education_with_degree_passes(degree: str) -> None:
    resume = Resume()
    resume.sections.education.items = [
        EducationItem(id="e1", school="Örnek Üniversitesi", degree=degree)
    ]
    assert not list(check_education_degree(resume))


# --- ligatürler (kritik) ---------------------------------------------------- #


def test_ligature_is_detected() -> None:
    resume = Resume()
    resume.summary.content = "<p>ﬁne-tuning ve oﬄine çalışma</p>"
    findings = list(check_ligatures(resume))
    assert findings and findings[0].rule == "text.ligature"


def test_clean_text_has_no_ligature_finding() -> None:
    resume = Resume()
    resume.summary.content = "<p>fine-tuning ve offline çalışma</p>"
    assert not list(check_ligatures(resume))


def test_fix_ligatures_replaces_every_known_char() -> None:
    raw = "".join(LIGATURES) + " normal metin"
    fixed, count = fix_ligatures(raw)
    assert count == len(LIGATURES)
    assert not any(char in fixed for char in LIGATURES)
    assert "normal metin" in fixed


def test_fix_ligatures_is_a_noop_on_clean_text() -> None:
    fixed, count = fix_ligatures("fine-tuning offline")
    assert count == 0
    assert fixed == "fine-tuning offline"


def test_fix_then_lint_is_clean() -> None:
    """`--fix` gerçekten kuralı susturuyor mu?"""
    resume = Resume()
    resume.summary.content = "<p>ﬁne-tuning</p>"
    assert list(check_ligatures(resume))

    resume.summary.content = fix_ligatures(resume.summary.content)[0]
    assert not list(check_ligatures(resume))


# --- parantez öncesi boşluk ------------------------------------------------- #


def test_tight_paren_is_detected() -> None:
    resume = Resume()
    resume.sections.experience.items = [
        ExperienceItem(id="x", company="Örnek", position="Yazılım Mühendisi(AI & Otomasyon)")
    ]
    assert "text.paren_spacing" in rules(check_paren_spacing(resume))


def test_spaced_paren_passes() -> None:
    resume = Resume()
    resume.sections.experience.items = [
        ExperienceItem(id="x", company="Örnek", position="Yazılım Mühendisi (AI & Otomasyon)")
    ]
    assert not list(check_paren_spacing(resume))


def test_code_blocks_do_not_trigger_paren_rule() -> None:
    """`<code>with statement</code>` içindeki `func(x)` yanlış alarm vermemeli."""
    resume = Resume()
    resume.summary.content = "<p>Bağlam yöneticisi <code>open(path)</code> desteği.</p>"
    assert not list(check_paren_spacing(resume))


def test_strip_html_removes_tags_and_code() -> None:
    assert "<p>" not in strip_html("<p>merhaba</p>")
    assert "open(" not in strip_html("<code>open(x)</code>")


def test_placeholder_is_an_error() -> None:
    """Bilinmeyen olgu uydurulmamalı; yer tutucu bırakılmalı ve lint onu yakalamalı."""
    resume = Resume()
    resume.sections.experience.items = [
        ExperienceItem(id="x", company="Örnek", period="[terfi tarihi] – Devam Ediyor")
    ]
    findings = list(check_placeholders(resume))
    assert findings and findings[0].rule == "content.placeholder"
    assert "[terfi tarihi]" in findings[0].message


def test_no_placeholder_passes() -> None:
    resume = Resume()
    resume.sections.experience.items = [
        ExperienceItem(id="x", company="Örnek", period="Ocak 2026 – Devam Ediyor")
    ]
    assert not list(check_placeholders(resume))


def test_style_values_are_not_prose() -> None:
    """`rgba(0, 0, 0, 0.5)` parantez kuralını tetiklememeli — stil, prose değil."""
    resume = Resume()
    assert resume.picture.borderColor.startswith("rgba(")  # kuralın hedefi burada duruyor
    assert not list(check_paren_spacing(resume))


def test_prose_walk_skips_style_blocks_and_leaves() -> None:
    resume = Resume()
    resume.summary.content = "<p>görünür metin</p>"
    paths = [path for path, _ in iter_prose(resume)]
    assert any(p.startswith("summary") for p in paths)
    assert not any(p.startswith(("picture", "metadata")) for p in paths)
    assert not any(p.endswith((".iconColor", ".url", ".link", ".id")) for p in paths)


def test_prose_walk_still_covers_experience_text() -> None:
    resume = Resume()
    resume.sections.experience.items = [
        ExperienceItem(id="x", company="Örnek", position="Yazılım Mühendisi (AI)")
    ]
    texts = [text for _, text in iter_prose(resume)]
    assert "Yazılım Mühendisi (AI)" in texts
    assert "Örnek" in texts


# --- etiketler -------------------------------------------------------------- #


def test_unknown_tag_is_an_error() -> None:
    resume = Resume()
    resume.sections.skills.items = [
        SkillItem(id="s1", name="Kobol", x_meta=XMeta(tags=["cobol"]))
    ]
    assert "tags.unknown" in rules(check_tags(resume, default_vocabulary()))


def test_untagged_bullet_is_a_warning() -> None:
    resume = Resume()
    resume.sections.experience.items = [
        ExperienceItem(
            id="x", company="Örnek", x_bullets=[XBullet(id="x.1", text="Bir şey yaptı.")]
        )
    ]
    findings = list(check_tags(resume, default_vocabulary()))
    assert [f.level for f in findings] == ["warning"]
    assert findings[0].rule == "bullets.untagged"


def test_tagged_bullet_passes() -> None:
    resume = Resume()
    resume.sections.experience.items = [
        ExperienceItem(
            id="x",
            company="Örnek",
            x_bullets=[
                XBullet(id="x.1", text="Bir şey yaptı.", x_meta=XMeta(tags=["python"]))
            ],
        )
    ]
    assert not list(check_tags(resume, default_vocabulary()))


# --- id'ler ----------------------------------------------------------------- #


def test_duplicate_item_ids_are_an_error() -> None:
    resume = Resume()
    resume.sections.skills.items = [
        SkillItem(id="dup", name="A"),
        SkillItem(id="dup", name="B"),
    ]
    assert "ids.duplicate" in rules(check_ids(resume))


def test_duplicate_bullet_ids_are_an_error() -> None:
    resume = Resume()
    resume.sections.experience.items = [
        ExperienceItem(
            id="x",
            company="Örnek",
            x_bullets=[XBullet(id="same", text="a"), XBullet(id="same", text="b")],
        )
    ]
    assert "ids.duplicate" in rules(check_ids(resume))


# --- süper set boyutu ve dil kapsaması -------------------------------------- #


def test_too_few_experiences_warns() -> None:
    resume = Resume()
    resume.sections.experience.items = [ExperienceItem(id="x", company="Örnek")]
    assert "superset.experiences" in rules(check_superset_size(resume))


def test_missing_english_variant_warns() -> None:
    resume = Resume()
    resume.sections.experience.items = [
        ExperienceItem(
            id="x",
            company="Örnek",
            x_bullets=[XBullet(id="x.tr", text="a", x_meta=XMeta(tags=["python"], lang="tr"))],
        )
    ]
    findings = [f for f in check_language_coverage(resume) if "en" in f.message.lower()]
    assert findings


def test_both_languages_present_passes() -> None:
    resume = Resume()
    resume.sections.experience.items = [
        ExperienceItem(
            id="x",
            company="Örnek",
            x_bullets=[
                XBullet(id="x.tr", text="a", x_meta=XMeta(tags=["python"], lang="tr")),
                XBullet(id="x.en", text="b", x_meta=XMeta(tags=["python"], lang="en")),
            ],
        )
    ]
    assert not list(check_language_coverage(resume))


# --- rapor ------------------------------------------------------------------ #


def test_report_counts_and_ok_flag(master_example_raw: str) -> None:
    report = lint(Resume.model_validate_json(master_example_raw))
    assert report.stats["deneyim"] == 2
    assert report.stats["madde"] > 0
    # Örnek dosya kasıtlı olarak küçük: 2 deneyim < 3, yani uyarı bekliyoruz.
    assert not report.ok
    assert "superset.experiences" in rules(report.warnings)


def test_report_ok_requires_zero_errors_and_zero_warnings() -> None:
    from cv_updater.lint import Finding, LintReport

    assert LintReport([], {}).ok
    assert not LintReport([Finding("warning", "r", "w", "m")], {}).ok
    assert not LintReport([Finding("error", "r", "w", "m")], {}).ok
    assert LintReport([Finding("info", "r", "w", "m")], {}).ok


def test_lint_does_not_touch_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lint deterministik olmalı — hiçbir kural HTTP yapmamalı."""
    import socket

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("lint ağa çıktı")

    monkeypatch.setattr(socket, "socket", explode)
    lint(Resume())
