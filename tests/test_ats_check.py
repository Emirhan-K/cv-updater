"""Issue 10 — ATS öz-kontrolü. LLM yok, ağ yok; PDF'ler testte üretiliyor."""

from __future__ import annotations

from pathlib import Path

import pytest

from cv_updater.models.job import JobAnalysis, JobPosting, Requirement
from cv_updater.steps import ats_check
from cv_updater.steps.ats_check import (
    Extraction,
    NoTextLayerError,
    build_markdown,
    check_contact,
    check_dates,
    check_headings,
    check_keywords,
    check_ligatures,
    check_links,
    check_pdf,
    check_reading_order,
    contains_term,
    extract_text,
    find_headings,
    must_terms,
    run_checks,
)
from pdf_builder import write_pdf

# `pypdf`in metni yeterli bulması için eşiğin (MIN_TEXT_CHARS) üstünde bir CV
# gerekiyor; bu satırlar hem uzunluğu sağlıyor hem de her kontrolün gerçek bir
# karşılığını taşıyor.
CLEAN_CV = [
    "Emirhan Kuru",
    "Yazılım Mühendisi (AI & Görüntü İşleme)",
    "emirhankuru@example.com +90 535 950 4623 İstanbul, Türkiye",
    "github.com/Emirhan-K linkedin.com/in/emirhan-k",
    "Özet Yapay zeka ve otomasyon süreçleri odaklı yazılım mühendisi.",
    "Deneyim VidInsight Teknoloji",
    "Yazılım Mühendisi Eylül 2024 – Devam Ediyor",
    "Video ve ses işleme süreçleri için uçtan uca mikro servisler geliştirilmesi.",
    "Speech-to-Text ve video özetleme işlem hatlarının tasarlanması.",
    "Eterna Teknoloji",
    "Stajyer Yazılım Mühendisi Temmuz 2024 – Kasım 2024",
    "RAG sistemlerinin kurulması ve modele özel ince ayar süreçlerinin yönetilmesi.",
    "Eğitim Fırat Üniversitesi",
    "Yazılım Mühendisliği Eylül 2020 – Haziran 2025",
    "Yetenekler Python AWS (EC2) Docker Git RAG YOLO PyTorch FastAPI",
]

#: Aynı CV'nin ligatürlü hali — gerçek CV'de ölçülen bozulmanın aynısı.
LIGATURED_CV = [
    *CLEAN_CV[:-4],
    "modele özel ince ayar (ﬁne-tuning) ve ofﬂine çalışma süreçleri",
    *CLEAN_CV[-4:],
]


def _analysis(*must: tuple[str, list[str]], tech: list[str] | None = None) -> JobAnalysis:
    return JobAnalysis(
        posting=JobPosting(
            company="Acme",
            title="AI Mühendisi",
            language="tr",
            seniority="mid",
            work_mode="remote",
            requirements=[
                Requirement(text=text, kind="must", keywords=keywords) for text, keywords in must
            ],
            tech_keywords=tech or [],
        )
    )


def _extraction(lines: list[str]) -> Extraction:
    text = "\n".join(lines)
    return Extraction(text=text, pages=(text,), engine="pypdf")


# --------------------------------------------------------------------------- #
# Çıkarma
# --------------------------------------------------------------------------- #


def test_extract_reads_text_layer(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "cv.pdf", CLEAN_CV)
    extraction = extract_text(path)

    assert extraction.engine == "pypdf"
    assert extraction.fallback_reason is None
    assert "Emirhan Kuru" in extraction.text
    assert "github.com/Emirhan-K" in extraction.text


def test_extract_preserves_ligature_glyph(tmp_path: Path) -> None:
    """Ligatür metin katmanında duruyorsa çıkarıcı onu **açmadan** vermeli.

    Kontrolün tamamı buna dayanıyor: `pypdf` `ﬁ`yi U+FB01 olarak döndürdüğü
    için ATS'in "fine-tuning" araması kaçırıyor. Gerçek bir CV'de ölçüldü.
    """
    path = write_pdf(tmp_path / "cv.pdf", LIGATURED_CV)
    assert "ﬁne-tuning" in extract_text(path).text


def test_extract_without_text_layer_is_fatal(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "scan.pdf", CLEAN_CV, text_layer=False)

    with pytest.raises(NoTextLayerError) as excinfo:
        extract_text(path)

    message = str(excinfo.value)
    assert "metin katmanı yok" in message
    assert "taranmış" in message


def test_extract_falls_back_to_pdfplumber(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """pypdf boş dönerse yedek motor devreye girer ve rapor bunu söyler."""
    path = write_pdf(tmp_path / "cv.pdf", CLEAN_CV)
    monkeypatch.setattr(ats_check, "_extract_pypdf", lambda _: ("",))

    extraction = extract_text(path)

    assert extraction.engine == "pdfplumber"
    assert extraction.fallback_reason is not None
    assert "Emirhan Kuru" in extraction.text


def test_missing_backup_engine_explains_how_to_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_pdf(tmp_path / "cv.pdf", CLEAN_CV)
    monkeypatch.setattr(ats_check, "_extract_pypdf", lambda _: ("",))

    def _no_pdfplumber(_: Path) -> tuple[str, ...]:
        raise ImportError("pdfplumber")

    monkeypatch.setattr(ats_check, "_extract_pdfplumber", _no_pdfplumber)

    with pytest.raises(NoTextLayerError, match="uv sync --extra pdf"):
        extract_text(path)


def test_extract_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        extract_text(tmp_path / "yok.pdf")


# --------------------------------------------------------------------------- #
# Ligatür
# --------------------------------------------------------------------------- #


def test_clean_text_has_no_ligature_finding() -> None:
    result = check_ligatures("\n".join(CLEAN_CV))
    assert result.ratio == 1.0
    assert result.findings == ()


def test_ligature_is_critical_and_lists_words() -> None:
    result = check_ligatures("modele özel ince ayar (ﬁne-tuning) ve ofﬂine çalışma")

    assert result.ratio == 0.0
    (finding,) = result.findings
    assert finding.level == "critical"
    assert finding.rule == "text.ligature"
    assert "ﬁne-tuning" in finding.evidence
    assert "ofﬂine" in finding.evidence


# --------------------------------------------------------------------------- #
# Anahtar kelime kapsaması
# --------------------------------------------------------------------------- #


def test_must_terms_uses_must_requirements_only() -> None:
    analysis = _analysis(("Python", ["Python", "FastAPI"]))
    analysis.posting.requirements.append(
        Requirement(text="Kubernetes", kind="nice", keywords=["Kubernetes"])
    )
    assert must_terms(analysis) == ["Python", "FastAPI"]


def test_must_terms_falls_back_to_tech_keywords() -> None:
    """Analiz `requirements[].keywords`'ü boş bırakabiliyor; kontrol sessizce boşa düşmemeli."""
    analysis = _analysis(("Deneyim", []), tech=["Python", "Docker"])
    assert must_terms(analysis) == ["Python", "Docker"]


def test_must_terms_are_deduplicated_by_form() -> None:
    analysis = _analysis(("a", ["CI/CD"]), ("b", ["ci-cd"]), ("c", ["Python"]))
    assert must_terms(analysis) == ["CI/CD", "Python"]


@pytest.mark.parametrize(
    ("haystack", "term", "expected"),
    [
        ("python ile fastapi", "Python", True),
        ("ci cd hatti", "CI/CD", True),
        ("llm tabanli", "LLMs", True),
        ("llms tabanli", "LLM", True),
        ("google cloud", "Go", False),
        ("golang", "Go", False),
    ],
)
def test_contains_term(haystack: str, term: str, expected: bool) -> None:
    assert contains_term(f" {haystack} ", term) is expected


def test_keyword_coverage_counts_found_terms() -> None:
    analysis = _analysis(("a", ["Python", "Docker"]), ("b", ["Kubernetes"]))
    result = check_keywords("\n".join(CLEAN_CV), analysis)

    assert result.detail == "2/3 terim bulundu"
    (finding,) = result.findings
    assert finding.level == "warning"
    assert finding.evidence == ("Kubernetes",)


def test_keyword_search_expands_ligatures() -> None:
    """CV'de `ﬁne-tuning` yazıyorsa ilanın "fine-tuning" terimi **bulunmuş** sayılır.

    İki kontrol iki farklı soruya bakıyor: ligatür kontrolü "ATS'in araması
    bozulur mu", kapsama kontrolü "terim CV'de var mı". İkincisini ligatür
    yüzünden kaybetmek aynı hatayı iki kez raporlamak olurdu.
    """
    analysis = _analysis(("ince ayar", ["fine-tuning"]))
    result = check_keywords("modele özel ince ayar (ﬁne-tuning) süreçleri", analysis)

    assert result.ratio == 1.0
    assert result.findings == ()


def test_keyword_check_is_skipped_without_job() -> None:
    result = check_keywords("\n".join(CLEAN_CV), None)
    assert result.skipped
    assert result.weight == 0.0


def test_skipped_check_does_not_lower_the_score() -> None:
    """İlan verilmemesi puanı düşürmemeli — payda da küçülüyor."""
    with_job = run_checks(_extraction(CLEAN_CV), _analysis(("a", ["Python"])))
    without_job = run_checks(_extraction(CLEAN_CV))

    assert with_job.score == 100
    assert without_job.score == 100


# --------------------------------------------------------------------------- #
# İletişim
# --------------------------------------------------------------------------- #


def test_contact_found() -> None:
    result = check_contact("\n".join(CLEAN_CV))
    assert result.ratio == 1.0
    assert result.findings == ()


def test_missing_email_is_critical() -> None:
    result = check_contact("Emirhan Kuru\n+90 535 950 4623")
    (finding,) = result.findings
    assert finding.level == "critical"
    assert finding.rule == "contact.email"


def test_missing_phone_is_only_a_warning() -> None:
    result = check_contact("Emirhan Kuru\nemirhankuru@example.com")
    (finding,) = result.findings
    assert finding.level == "warning"
    assert finding.rule == "contact.phone"


def test_email_starting_with_a_digit_is_not_a_finding() -> None:
    """Rakamla başlayan yerel kısım normaldir — bunu "yapışmış metin" sayan kural elendi."""
    result = check_contact("1emirhankuru@example.com +90 535 950 4623")
    assert result.ratio == 1.0
    assert result.findings == ()


def test_second_email_is_reported() -> None:
    result = check_contact("a@example.com b@example.com +90 535 950 4623")
    (finding,) = result.findings
    assert finding.rule == "contact.email_multiple"


def test_year_range_is_not_a_phone_number() -> None:
    """`2018-2022 2024` gibi tarih yığınları telefon sanılmamalı."""
    result = check_contact("Eğitim 2018-2022 ve 2024 yılları\na@example.com")
    rules = {finding.rule for finding in result.findings}
    assert "contact.phone" in rules


# --------------------------------------------------------------------------- #
# Linkler
# --------------------------------------------------------------------------- #


def test_visible_links_pass() -> None:
    result = check_links("github.com/Emirhan-K linkedin.com/in/emirhan-k")
    assert result.ratio == 1.0
    assert result.findings == ()


def test_icon_only_links_are_reported() -> None:
    """Gerçek CV'deki bulgu: ikon glifi + kullanıcı adı, görünür URL yok."""
    result = check_links("Emirhan-K Emirhan-k")

    assert result.ratio == 0.0
    assert {finding.rule for finding in result.findings} == {"links.github", "links.linkedin"}


def test_one_visible_link_scores_half() -> None:
    result = check_links("github.com/Emirhan-K")
    assert result.ratio == 0.5


# --------------------------------------------------------------------------- #
# Başlıklar
# --------------------------------------------------------------------------- #


def test_headings_found_on_shared_lines() -> None:
    """Reactive Resume çıktısında başlık ilk öğeyle aynı satıra iniyor; yine de sayılmalı."""
    hits = find_headings(["Deneyim VidInsight Teknoloji", "Yetenekler Python AWS (EC2) Docker"])
    assert [hit.section for hit in hits] == ["deneyim", "beceriler"]


def test_body_sentence_starting_with_heading_word_is_not_a_heading() -> None:
    """`Eğitim platformunun…` bir madde, başlık değil — ayırt eden sonraki kelimenin küçük harfi."""
    assert find_headings(["Eğitim platformunun dijital altyapısının tasarlanması."]) == []


def test_heading_word_mid_sentence_is_not_a_heading() -> None:
    assert find_headings(["5 yıllık deneyim ile ekip yönetimi"]) == []


def test_all_sections_present() -> None:
    result = check_headings(CLEAN_CV)
    assert result.ratio == 1.0
    assert result.findings == ()


def test_missing_section_is_a_warning() -> None:
    result = check_headings([line for line in CLEAN_CV if not line.startswith("Yetenekler")])

    (finding,) = result.findings
    assert finding.level == "warning"
    assert finding.evidence == ("beceriler",)


def test_no_headings_at_all_is_critical() -> None:
    result = check_headings(["Emirhan Kuru", "emirhankuru@example.com"])

    (finding,) = result.findings
    assert finding.level == "critical"
    assert finding.rule == "headings.none"


# --------------------------------------------------------------------------- #
# Okuma sırası
# --------------------------------------------------------------------------- #


def test_single_column_reading_order_is_clean() -> None:
    result = check_reading_order(CLEAN_CV)
    assert result.ratio == 1.0
    assert result.findings == ()


def test_two_headings_on_one_line_signal_columns() -> None:
    """Tek sütunlu bir düzende iki bölüm başlığı aynı satıra düşemez."""
    result = check_reading_order(["Deneyim VidInsight Yetenekler Python", "Eğitim Fırat"])

    assert result.ratio == 0.0
    assert result.findings[0].rule == "order.interleaved"


def test_repeated_heading_is_reported() -> None:
    result = check_reading_order(["Deneyim VidInsight", "Deneyim Eterna", "Eğitim Fırat"])

    assert result.ratio == 0.5
    assert result.findings[0].rule == "order.repeated_heading"


def test_reading_order_is_not_scored_twice_when_no_headings() -> None:
    """Başlık yoksa ceza `headings` kontrolünde kesildi; burada tekrarlanmamalı."""
    result = check_reading_order(["Emirhan Kuru", "emirhankuru@example.com"])
    assert result.ratio == 1.0
    assert result.findings == ()


# --------------------------------------------------------------------------- #
# Tarihler
# --------------------------------------------------------------------------- #


def test_periods_are_parsed() -> None:
    result = check_dates("\n".join(CLEAN_CV))
    assert result.ratio == 1.0
    assert not [finding for finding in result.findings if finding.level == "warning"]


def test_no_dates_is_a_warning() -> None:
    result = check_dates("Emirhan Kuru\nYazılım Mühendisi")

    assert result.ratio == 0.0
    (finding,) = result.findings
    assert finding.rule == "dates.missing"


def test_mixed_date_styles_are_informational_only() -> None:
    """Eğitimde yıl, deneyimde ay+yıl yaygın; puanı düşürmemeli."""
    result = check_dates("Eylül 2024 – Devam Ediyor\n2018-2022")

    assert result.ratio == 1.0
    (finding,) = result.findings
    assert finding.level == "info"
    assert finding.rule == "dates.mixed"


def test_english_periods_are_parsed() -> None:
    result = check_dates("September 2024 - Present\nJanuary 2020 - June 2024")
    assert result.ratio == 1.0


# --------------------------------------------------------------------------- #
# Puanlama ve rapor
# --------------------------------------------------------------------------- #


def test_clean_cv_scores_full_marks() -> None:
    report = run_checks(_extraction(CLEAN_CV))

    assert report.score == 100
    assert report.critical == []
    assert report.verdict == "ATS'e hazır"
    assert report.ok()


def test_ligature_alone_fails_the_run() -> None:
    report = run_checks(_extraction(LIGATURED_CV))

    assert report.critical
    assert report.verdict == "kritik hata — bu haliyle gönderme"
    assert not report.ok()


def test_strict_turns_warnings_into_failure() -> None:
    report = run_checks(_extraction([line for line in CLEAN_CV if "github.com" not in line]))

    assert report.critical == []
    assert report.ok() is True
    assert report.ok(strict=True) is False


def test_markdown_contains_score_and_evidence() -> None:
    report = run_checks(_extraction(LIGATURED_CV), _analysis(("a", ["Kubernetes"])))
    markdown = build_markdown(report)

    assert f"Puan: {report.score}/100" in markdown
    assert "text.ligature" in markdown
    assert "`Kubernetes`" in markdown
    assert "ﬁne-tuning" in markdown


def test_markdown_says_which_engine_measured() -> None:
    """Rapor iddiayı ölçüme bağlamalı: hangi motor okudu?"""
    extraction = Extraction(
        text="\n".join(CLEAN_CV),
        pages=("",),
        engine="pdfplumber",
        fallback_reason="0 karakter çıktı (eşik 200)",
    )
    markdown = build_markdown(run_checks(extraction))

    assert "`pdfplumber`" in markdown
    assert "0 karakter çıktı" in markdown


# --------------------------------------------------------------------------- #
# Uçtan uca
# --------------------------------------------------------------------------- #


def test_check_pdf_end_to_end(tmp_path: Path) -> None:
    clean = check_pdf(write_pdf(tmp_path / "clean.pdf", CLEAN_CV))
    dirty = check_pdf(write_pdf(tmp_path / "dirty.pdf", LIGATURED_CV))

    assert clean.ok()
    assert not dirty.ok()
    assert clean.score > dirty.score
