"""Render v2 (issue 11) — `tailored.json` → RenderCV YAML → Typst → PDF.

v1'de `tailored.json` elle Reactive Resume'a import ediliyordu. v2 aynı işi tek
komutta yapıyor ve **tipografiyi bizim tarafımıza alıyor**: PDF'i Typst
üretiyor, dolayısıyla ligatür sorunu kaynağında çözülüyor.

Modül ikiye bölünmüş, kasıtlı olarak:

* `build_document()` — saf dönüşüm. `Resume` → RenderCV sözlüğü. **RenderCV
  kurulu olmadan çalışır ve test edilir**; boru hattının geri kalanındaki
  "deterministik adım ağa ve bağımlılığa muhtaç olmasın" kuralının aynısı.
* `render()` — asıl render. `rendercv`i tembel import eder; kurulu değilse
  ne yapılacağını söyleyen bir hata verir (`uv sync --extra render`).

## Ligatür: ölçüm sonucu

RenderCV'nin Typst şablonunda şu satır duruyor (`rendercv_typst/lib.typ`):

    // Disable ligatures for better ATS compatibility:
    ligatures: true,

Yorum "kapat" diyor, değer "aç" diyor ve ayar dışarıya açılmamış. Yani
tasarımdan kapatamıyoruz. Bunun yerine **ölçtük**: 17 gömülü fontun her biriyle
aynı CV render edilip PDF'ten metin geri okundu (2026-08-08).

**Hepsinde sıfır ligatür glifi.** EB Garamond ve Libertinus Serif gibi
gerçekten `fi` ligatürü taşıyan serif fontlarda bile "fine-tuning" metin
katmanından bozulmadan çıkıyor: Typst PDF'e doğru bir `ToUnicode` eşlemesi
yazıyor, ligatür glifi iki karaktere geri çözülüyor. Reactive Resume'un
`@react-pdf/renderer` çıktısında durum böyle değildi — Mart 2026 CV'sinde
`pypdf` `ﬁne-tuning` döndürüyordu.

Bu yüzden burada font kısıtı **yok**: `--font` ile 17 fonttan herhangi biri
seçilebiliyor. Ama iddiaya güvenmiyoruz — `render` her çalıştırmada issue
10'un ATS kontrolünü otomatik çalıştırıyor. Bir gün Typst'in davranışı
değişirse ilk haber veren o olur.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from cv_updater.lint import strip_html
from cv_updater.models.job import Language
from cv_updater.models.resume import ExperienceItem, Resume, Role
from cv_updater.steps.analyze_job import ascii_fold
from cv_updater.steps.score import (
    MONTH_WORDS,
    ONGOING_WORDS,
    parse_period_end,
    reverse_chronological_key,
)

YAML_FILENAME = "cv.yaml"
PDF_FILENAME = "cv.pdf"

DEFAULT_THEME = "classic"
#: RenderCV'nin gömülü fontları arasından varsayılan. Ölçümde hepsi ligatürsüz
#: çıktı (modül docstring'i); `Source Sans 3` RenderCV'nin de varsayılanı ve
#: Türkçe karakterlerin tamamını kapsıyor.
DEFAULT_FONT = "Source Sans 3"

#: CV tek renk. RenderCV'nin `classic` teması **beş** yeri birden maviye
#: (`rgb(0, 79, 144)`) boyuyor: ad, headline, iletişim satırı, bölüm başlıkları
#: ve linkler. Ölçüldü (2026-08-08, `pdfplumber` `non_stroking_color`): üretilen
#: PDF'te bu beş blok `(0.0, 0.31, 0.565)`, geri kalan her şey siyah.
#:
#: Siyaha çevrildi çünkü CV baskıda ve ATS önizlemesinde tek renk okunuyor;
#: renk, bölüm ayrımını taşıyan şey değil — punto ve boşluk taşıyor. Alt
#: bilgideki gri sayfa numarası (`footer`) ve üstteki tarih notu (`top_note`)
#: bilerek dışarıda: ikisi de içeriğin parçası değil, sayfa donanımı.
BLACK = "rgb(0, 0, 0)"
COLORS: dict[str, str] = {
    "name": BLACK,
    "headline": BLACK,
    "connections": BLACK,
    "section_titles": BLACK,
    "links": BLACK,
}

#: Bölüm başlıkları ATS'in aradığı standart adlar olmalı (`docs/ATS.md`).
#: RenderCV bölüm başlığını sözlük anahtarından alıyor, yani başlık metni
#: doğrudan burada belirleniyor.
SECTION_TITLES: dict[str, dict[Language, str]] = {
    "summary": {"tr": "Özet", "en": "Summary"},
    "experience": {"tr": "Deneyim", "en": "Experience"},
    "education": {"tr": "Eğitim", "en": "Education"},
    "projects": {"tr": "Projeler", "en": "Projects"},
    "skills": {"tr": "Yetenekler", "en": "Skills"},
    "certifications": {"tr": "Sertifikalar", "en": "Certifications"},
    "languages": {"tr": "Diller", "en": "Languages"},
}

#: RenderCV'nin `locale.language` değerleri.
LOCALES: dict[Language, str] = {"tr": "turkish", "en": "english"}

#: İletişim satırında ikon gösterilsin mi? Hayır: e-postanın yanındaki zarf,
#: telefonun yanındaki ahize ve konum iğnesi PDF'in metin katmanına özel
#: kullanım alanından birer glif olarak düşüyor (ölçüm: `pypdf` üretilen
#: PDF'ten `U+F0E0`, `U+F095`, `U+F3C5` çıkarıyor). ATS için gürültü, sayfa
#: için süs. Kapalıyken adres ve numara düz metin olarak duruyor.
SHOW_CONNECTION_ICONS = False

#: Telefon biçimi. RenderCV varsayılanı `national` — Türkiye yerelinde numara
#: `0535 950 46 23` olarak basılıyor ve ülke kodu kayboluyor. Uluslararası
#: ilanlara başvuruluyor, o yüzden `+90 535 …` (`international`).
PHONE_NUMBER_FORMAT = "international"

#: Metin hizası. `classic` temasının varsayılanı `justified` ve Typst iki yana
#: yaslarken **kelime bölüyor** — tire PDF'in metin katmanına giriyor, süse
#: değil. Ölçüldü (2026-08-08, `pypdf`, `out/novatek-en-final-…/cv.pdf`): sekiz
#: kırık kelime (`architec-tures`, `multispec-tral`, `concur-rency`,
#: `record-ings`, `archi-tecture`, `fine-tuning`, `text-based`,
#: `architec-ture`). ATS `architecture` aradığında `architec-ture` eşleşmiyor —
#: ligatür dersinin aynısı, başka bir kaynaktan.
#:
#: `justified-with-no-hyphenation` yaslamayı koruyor, bölmeyi bırakıyor.
#: Geçerli değerler: `rendercv/schema/models/design/classic_theme.py`
#: (`Typography.alignment`) — alan `design.typography` altında yaşıyor,
#: `design.text` diye bir blok yok.
TEXT_ALIGNMENT = "justified-with-no-hyphenation"

#: Profil ağı → FontAwesome ikon **adı**. RenderCV bunu Typst tarafında
#: `#connection-with-icon("linkedin")` olarak çözüyor; `"fa brands fa-linkedin"`
#: gibi tam bir CSS sınıfı verilirse çözemeyip **düz metin olarak basıyor**.
#: İlk render'da sayfaya "fa brands fa-linkedin" yazdı, ölçümle düzeltildi.
#: Kanonik liste: `rendercv/renderer/templater/connections.py:fontawesome_icons`.
_NETWORK_ICONS: dict[str, str] = {
    "github": "github",
    "linkedin": "linkedin",
    "gitlab": "gitlab",
    "x": "x-twitter",
    "twitter": "x-twitter",
    "stackoverflow": "stack-overflow",
    "instagram": "instagram",
    "youtube": "youtube",
    "telegram": "telegram",
    "mastodon": "mastodon",
    "orcid": "orcid",
    "researchgate": "researchgate",
}
_DEFAULT_ICON = "link"

_LIST_ITEM = re.compile(r"<li[^>]*>(.*?)</li>", re.DOTALL | re.IGNORECASE)
_PARAGRAPH = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")

_MONTHS = "|".join(sorted(MONTH_WORDS, key=len, reverse=True))
_MONTH_INDEX = {name: number % 12 + 1 for number, name in enumerate(MONTH_WORDS)}
#: Dönemin iki ucunu ayıran şey — `score._PERIOD_SPLIT` ile aynı kural.
_SPLIT = re.compile(r"\s*(?:[-–—]|\bto\b)\s*")
_YEAR = re.compile(r"(19|20)\d{2}")
_MONTH_YEAR = re.compile(rf"({_MONTHS})\s+((?:19|20)\d{{2}})")


class RenderCVMissingError(RuntimeError):
    """`rendercv` kurulu değil — render opsiyonel bir özellik."""


class RenderError(RuntimeError):
    """RenderCV girdiyi reddetti ya da derleme başarısız oldu."""


# --------------------------------------------------------------------------- #
# HTML → düz metin
# --------------------------------------------------------------------------- #


def _clean(fragment: str) -> str:
    """HTML parçasını tek satırlık düz metne indirir."""
    return _WHITESPACE.sub(" ", html.unescape(strip_html(fragment))).strip()


def bullets(markup: str) -> list[str]:
    """`<ul><li>…</li></ul>` → madde listesi.

    Liste yoksa metnin kendisi tek madde olarak döner: süper sette bazı
    açıklamalar düz paragraf.
    """
    items = [_clean(match.group(1)) for match in _LIST_ITEM.finditer(markup)]
    items = [item for item in items if item]
    if items:
        return items
    single = _clean(markup)
    return [single] if single else []


def paragraphs(markup: str) -> list[str]:
    """`<p>…</p>` → paragraf listesi; etiketsiz metin tek paragraf sayılır."""
    found = [_clean(match.group(1)) for match in _PARAGRAPH.finditer(markup)]
    found = [item for item in found if item]
    if found:
        return found
    single = _clean(markup)
    return [single] if single else []


# --------------------------------------------------------------------------- #
# Dönem → tarih
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Period:
    """Bir `period` metninin RenderCV karşılığı.

    `start`/`end` ayrıştırılabildiyse dolu (`2024-09`, `present`); ayrıştırılamadıysa
    `raw` doldurulup serbest metin `date` alanına yazılıyor. **Tarih asla
    uydurulmuyor** — okunamayan bir dönem olduğu gibi geçiyor, çünkü tarih
    modelden değil `master.json`'dan geliyor ve doğruluğu orada.
    """

    start: str | None = None
    end: str | None = None
    raw: str | None = None

    @property
    def fields(self) -> dict[str, Any]:
        if self.start or self.end:
            return {"start_date": self.start, "end_date": self.end}
        return {"date": self.raw} if self.raw else {}


def _endpoint(part: str) -> str | None:
    """Dönemin bir ucunu `YYYY-MM` / `YYYY` / `present`e çevirir."""
    folded = ascii_fold(part)
    if any(word in folded for word in ONGOING_WORDS):
        return "present"

    if match := _MONTH_YEAR.search(folded):
        return f"{match.group(2)}-{_MONTH_INDEX[match.group(1)]:02d}"

    if match := _YEAR.search(folded):
        return match.group()
    return None


def parse_period(period: str) -> Period:
    """ "Eylül 2024 – Devam Ediyor" → `Period(start="2024-09", end="present")`."""
    text = period.strip()
    if not text:
        return Period()

    parts = [part for part in _SPLIT.split(text) if part.strip()]
    start = _endpoint(parts[0]) if parts else None
    end = _endpoint(parts[-1]) if len(parts) > 1 else None

    if start is None or (len(parts) > 1 and end is None):
        # Bir ucu bile okunamıyorsa ikisini de kullanma: yarım bir tarih
        # aralığı, serbest metin olarak duran doğru bir dönemden kötüdür.
        return Period(raw=text)
    if len(parts) == 1:
        return Period(raw=text)
    return Period(start=start, end=end)


def _role_sort_key(role: Role) -> tuple[int, date]:
    """En güncel rol en üstte — `render_json._role_sort_key` ile aynı kural."""
    end = parse_period_end(role.period)
    return (1, end) if end is not None else (0, date.min)


# --------------------------------------------------------------------------- #
# Bölümler
# --------------------------------------------------------------------------- #


def _visible(items: list) -> list:
    return [item for item in items if not item.hidden]


def experiences_in_order(resume: Resume) -> list[ExperienceItem]:
    """Görünür deneyimler, **ters-kronolojik**.

    `render_json` sırayı zaten burada bırakıyor; bu ikinci sıralama elle yazılmış
    ya da eski bir `tailored.json` için ağ. Kural iki yerde de aynı olsun diye
    anahtar ortak (`score.reverse_chronological_key`) — sıra dosyadan dosyaya
    değişmemeli, sayfada en güncel iş her zaman en üstte.
    """
    return sorted(
        _visible(resume.sections.experience.items),
        key=lambda item: reverse_chronological_key(item.period),
        reverse=True,
    )


def experience_entries(item: ExperienceItem) -> list[dict[str, Any]]:
    """Bir deneyimi RenderCV girdilerine çevirir.

    **Rol başına bir girdi.** RenderCV'de iç içe rol yok; terfi çizgisini
    (`Yazılım Mühendisi` → `AI Ekip Lideri`) düzleştirmek yerine her rol kendi
    satırında duruyor — CV'de değerli bir sinyal ve `master.json` onu zaten
    ayrı tutuyor. Roller yoksa deneyimin kendisi tek girdi.
    """
    if not item.roles:
        return [
            {
                "company": item.company,
                "position": item.position,
                "location": item.location or None,
                **parse_period(item.period).fields,
                "highlights": bullets(item.description) or None,
            }
        ]

    entries = []
    for role in sorted(item.roles, key=_role_sort_key, reverse=True):
        entries.append(
            {
                "company": item.company,
                "position": role.position or item.position,
                "location": item.location or None,
                **parse_period(role.period or item.period).fields,
                # Maddeler `render_json._attach_bullets` tarafından en güncel
                # role yazılıyor; öğe seviyesi yedek olarak duruyor.
                "highlights": bullets(role.description or item.description) or None,
            }
        )
    return entries


def education_entries(resume: Resume) -> list[dict[str, Any]]:
    return [
        {
            "institution": item.school,
            "area": item.area,
            "degree": item.degree or None,
            "location": item.location or None,
            **parse_period(item.period).fields,
            "highlights": bullets(item.description) or None,
        }
        for item in _visible(resume.sections.education.items)
    ]


def project_entries(resume: Resume) -> list[dict[str, Any]]:
    return [
        {
            "name": item.name,
            **parse_period(item.period).fields,
            "highlights": bullets(item.description) or None,
        }
        for item in _visible(resume.sections.projects.items)
    ]


def skill_entries(resume: Resume) -> list[Any]:
    """Yetenekleri tek tip girdiye indirir.

    RenderCV bir bölümde **tek girdi tipi** kabul ediyor, o yüzden karar ikili:

    * Her yeteneğin `keywords`'ü doluysa → `label: details` satırları
      ("Diller: Python, SQL"). Kategorili bir yetenek listesi böyle okunur.
    * Değilse (süper setin bugünkü hali: 50 tane yalın ad) → hepsi tek bir
      virgüllü satır. Otuz tane "Python:" satırı ne okunur ne de ATS'e faydalı.
    """
    items = _visible(resume.sections.skills.items)
    if not items:
        return []

    if all(item.keywords for item in items):
        return [{"label": item.name, "details": ", ".join(item.keywords)} for item in items]

    names = []
    for item in items:
        names.append(item.name)
        names.extend(item.keywords)
    return [", ".join(dict.fromkeys(names))]


def certification_entries(resume: Resume) -> list[dict[str, Any]]:
    return [
        {
            "name": item.title,
            "summary": item.issuer or None,
            **(Period(raw=item.date).fields if item.date else {}),
        }
        for item in _visible(resume.sections.certifications.items)
    ]


def language_entries(resume: Resume) -> list[dict[str, str]]:
    return [
        {"label": item.language, "details": item.fluency or "—"}
        for item in _visible(resume.sections.languages.items)
    ]


def connections(resume: Resume) -> list[dict[str, Any]]:
    """Profil linkleri — **görünen metin tam URL**.

    `custom_connections` kullanılıyor çünkü RenderCV'nin `social_networks`'ü
    yalnızca kullanıcı adını basıyor (`Emirhan-K`). Mart 2026 CV'sinde ATS
    kontrolünü düşüren bulgu tam olarak buydu: metin katmanında URL yok, sadece
    ikon ve kullanıcı adı vardı. `placeholder` görünen metni belirlediği için
    tam adres sayfaya yazılıyor.

    `fontawesome_icon` **zorunlu alan** — RenderCV onsuz girdiyi reddediyor —
    o yüzden doğru ikon adı yazılmaya devam ediyor. İkonların görünüp
    görünmeyeceği ayrı bir karar ve `design.header.connections.show_icons`
    ile veriliyor (`SHOW_CONNECTION_ICONS`).
    """
    found = []
    for item in _visible(resume.sections.profiles.items):
        url = item.website.url.strip()
        label = item.website.label.strip() or _label_from_url(url) or item.username
        if not label:
            continue
        found.append(
            {
                "fontawesome_icon": _NETWORK_ICONS.get(
                    ascii_fold(item.network).replace(" ", ""), _DEFAULT_ICON
                ),
                "placeholder": label,
                "url": url or None,
            }
        )
    return found


def _label_from_url(url: str) -> str:
    """`https://github.com/x/` → `github.com/x`."""
    trimmed = url.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    return trimmed.rstrip("/")


# --------------------------------------------------------------------------- #
# Belge
# --------------------------------------------------------------------------- #


def _prune(payload: Any) -> Any:
    """`None` ve boş listeleri atar — RenderCV boş alanları sevmiyor."""
    if isinstance(payload, dict):
        cleaned = {key: _prune(value) for key, value in payload.items()}
        return {key: value for key, value in cleaned.items() if value not in (None, [], {}, "")}
    if isinstance(payload, list):
        return [_prune(value) for value in payload]
    return payload


def build_document(
    resume: Resume,
    *,
    language: Language = "tr",
    theme: str = DEFAULT_THEME,
    font: str = DEFAULT_FONT,
) -> dict[str, Any]:
    """`Resume` → RenderCV girdi sözlüğü. **Saf dönüşüm, RenderCV gerekmez.**"""
    titles = {key: value[language] for key, value in SECTION_TITLES.items()}

    sections: dict[str, Any] = {}
    if not resume.summary.hidden and (summary := paragraphs(resume.summary.content)):
        sections[titles["summary"]] = summary

    experience: list[dict[str, Any]] = []
    for item in experiences_in_order(resume):
        experience.extend(experience_entries(item))
    if experience:
        sections[titles["experience"]] = experience

    for key, entries in (
        ("education", education_entries(resume)),
        ("projects", project_entries(resume)),
        ("skills", skill_entries(resume)),
        ("certifications", certification_entries(resume)),
        ("languages", language_entries(resume)),
    ):
        if entries:
            sections[titles[key]] = entries

    document = {
        "cv": {
            "name": resume.basics.name,
            "headline": resume.basics.headline or None,
            "location": resume.basics.location or None,
            "email": resume.basics.email or None,
            "phone": _phone(resume.basics.phone),
            "website": _website(resume.basics.website.url),
            "custom_connections": connections(resume),
            "sections": sections,
        },
        "design": {
            "theme": theme,
            "colors": dict(COLORS),
            "typography": {
                "alignment": TEXT_ALIGNMENT,
                "font_family": {
                    "body": font,
                    "name": font,
                    "headline": font,
                    "connections": font,
                    "section_titles": font,
                },
            },
            # `connections` temanın **header**'ı altında yaşıyor, `design`in
            # doğrudan altında değil; oraya yazılırsa RenderCV "bu alan bu nesne
            # için bilinmiyor" deyip girdiyi reddediyor.
            "header": {
                "connections": {
                    "show_icons": SHOW_CONNECTION_ICONS,
                    "phone_number_format": PHONE_NUMBER_FORMAT,
                }
            },
        },
        "locale": {"language": LOCALES[language]},
    }
    return _prune(document)


def _phone(value: str) -> str | None:
    """RenderCV telefonu `tel:` URI olarak istiyor."""
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.startswith("tel:"):
        return cleaned
    return "tel:" + _WHITESPACE.sub("-", cleaned).replace("(", "").replace(")", "")


def _website(value: str) -> str | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned if cleaned.startswith("http") else f"https://{cleaned}"


def to_yaml(document: dict[str, Any]) -> str:
    """Belgeyi YAML metnine çevirir."""
    import yaml

    return yaml.safe_dump(document, allow_unicode=True, sort_keys=False, width=100)


def write_yaml(run_dir: Path, document: dict[str, Any]) -> Path:
    path = run_dir / YAML_FILENAME
    path.write_text(to_yaml(document), encoding="utf-8", newline="")
    return path


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #

INSTALL_HINT = (
    "RenderCV kurulu değil. PDF üretimi opsiyonel bir özellik:\n"
    "  uv sync --extra render\n"
    "Kurmadan da çalışabilirsin: `tailored.json`'ı rxresu.me'ye elle import et (v1 akışı)."
)


def render(yaml_path: Path, pdf_path: Path) -> Path:
    """RenderCV YAML'ını PDF'e derler ve üretilen dosyanın yolunu döndürür.

    `rendercv` tembel import ediliyor: kurulu olmadan `build_document` ve
    testlerin tamamı çalışmaya devam etsin.
    """
    try:
        from rendercv.renderer.pdf_png import generate_pdf
        from rendercv.renderer.typst import generate_typst
        from rendercv.schema.rendercv_model_builder import build_rendercv_dictionary_and_model
    except ImportError as exc:  # pragma: no cover - kurulum durumuna bağlı
        raise RenderCVMissingError(INSTALL_HINT) from exc

    try:
        _, model = build_rendercv_dictionary_and_model(
            yaml_path.read_text(encoding="utf-8"),
            input_file_path=yaml_path,
            output_folder=pdf_path.parent,
            pdf_path=pdf_path,
            dont_generate_png=True,
            dont_generate_html=True,
            dont_generate_markdown=True,
        )
        typst_path = generate_typst(model)
        produced = generate_pdf(model, typst_path)
    except Exception as exc:  # RenderCV kendi hata sınıflarını kullanıyor
        raise RenderError(str(exc)) from exc

    if produced is None or not Path(produced).exists():
        raise RenderError("RenderCV bir PDF üretmedi.")
    return Path(produced)


def page_count(pdf_path: Path) -> int:
    """PDF'in sayfa sayısı — tek sayfa kuralı bununla denetleniyor."""
    from pypdf import PdfReader

    return len(PdfReader(str(pdf_path)).pages)


#: Bir CV kaç sayfa olmalı? Bir. İkiye taşarsa uyarı basılıyor ama akış
#: durmuyor: kırpma kararı kullanıcının, aracın değil.
MAX_PAGES = 1


def overflow_advice(resume: Resume) -> list[str]:
    """Sayfa taşarsa neyin kırpılabileceğine dair somut öneriler."""
    advice: list[str] = []

    experiences = _visible(resume.sections.experience.items)
    total_bullets = sum(
        len(bullets(role.description or item.description))
        for item in experiences
        for role in (item.roles or [Role(id="", description=item.description)])
    )
    if total_bullets > 12:
        advice.append(f"{total_bullets} madde var — en eski deneyimden madde çıkar.")

    projects = _visible(resume.sections.projects.items)
    if len(projects) > 2:
        advice.append(f"{len(projects)} proje görünüyor — ilana en uzak olanı gizle.")

    skills = _visible(resume.sections.skills.items)
    if len(skills) > 24:
        advice.append(f"{len(skills)} yetenek listeleniyor — ilanla eşleşmeyenleri ele.")

    if not advice:
        advice.append("Kenar boşluklarını ya da yazı boyutunu küçültmeyi dene (`design`).")
    return advice
