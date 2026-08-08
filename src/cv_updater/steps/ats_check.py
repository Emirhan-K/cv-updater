"""ATS öz-kontrolü (issue 10) — PDF'i **makinenin gözünden** oku.

Ekranda güzel duran CV, bir ATS'in okuduğu metin değildir. Bu adım üretilen
PDF'ten metni geri çıkarır ve aynı soruları sorar: ligatür var mı, ilanın
zorunlu terimleri hayatta kaldı mı, e-posta bulunabiliyor mu, linkler **metin
olarak** görünüyor mu, başlıklar karışmış mı?

Boru hattının geri kalanı gibi bu modül de **deterministik**: LLM yok, ağ yok.
Girdi bir PDF, çıktı puanlanmış bir rapor.

İki ölçüm bu modülün tasarımını belirledi (2026-08-07, iki gerçek PDF):

* Ligatür bulgusu **kaynak metindeydi**, render'da değil — `validate`/
  `fix_ligatures` o katmanı kapatıyor. Yine de kontrol burada duruyor, çünkü
  render yolu değişebilir (issue 11) ve o zaman ölçen tek yer burası olur.
* Genel amaçlı metin çıkarıcılar ligatürü olmayan yerde bile bozuk gösterebiliyor.
  Bu yüzden asıl motor `pypdf`, yedeği `pdfplumber`, ve rapor **hangi motorla**
  ölçüldüğünü her zaman yazıyor.

Sınıflandırma tek bir ayrım üzerine kurulu: **`critical` = başvuru ölür**
(metin katmanı yok, e-posta okunamıyor, ligatür aramayı bozuyor), `warning` =
elenme riski artar, `info` = bilgi. Çıkış kodu yalnızca `critical`'a bakar
(`--strict` uyarıları da sayar), böylece kontrol ileride pre-commit ya da CI'ya
bağlanabilir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from cv_updater.lint import GITHUB_URL, LIGATURES, LINKEDIN_URL, fix_ligatures
from cv_updater.models.job import JobAnalysis
from cv_updater.steps.analyze_job import ascii_fold, fold
from cv_updater.steps.score import MONTH_WORDS, ONGOING_WORDS, parse_period_end

ATS_REPORT_FILENAME = "ats_report.md"

Level = Literal["critical", "warning", "info"]

#: Bu eşiğin altında metin çıktıysa `pypdf`in sonucuna güvenilmez ve yedek
#: motora düşülür. Tek sayfalık bir CV bile 1500+ karakter; 200'ün altı ya
#: taranmış bir sayfa ya da çıkarıcının çuvalladığı bir font demek.
MIN_TEXT_CHARS = 200

#: Harf oranı bunun altındaysa metin "bozuk" sayılır: çıkarıcı glifleri
#: çözememiş, elimizde noktalama ve kutu karakteri kalmış olabilir.
MIN_ALPHA_RATIO = 0.5

# --- Puanlama: burayı değiştirirsen `docs/ATS.md`'yi de güncelle ----------- #
#: Kontrol başına ağırlık. Toplam 100; ilan verilmediğinde `keywords` atlanır
#: ve payda kalanların toplamına iner — atlanmış bir kontrol puanı düşürmez.
WEIGHTS: dict[str, float] = {
    "ligatures": 20.0,
    "keywords": 25.0,
    "contact": 15.0,
    "links": 10.0,
    "headings": 15.0,
    "reading_order": 10.0,
    "dates": 5.0,
}
#: Bu puanın üstünde CV ATS'e hazır sayılır.
READY_SCORE = 85
#: Bu puanın altında düzeltmeden gönderme.
RISKY_SCORE = 65
# --------------------------------------------------------------------------- #

#: Bölüm başlıkları, ASCII'ye indirgenmiş halde (`ascii_fold`). Uzun alias önce
#: denenir: "is deneyimi" eşleşirse geriye "is" artığı kalmasın.
SECTION_HEADINGS: dict[str, tuple[str, ...]] = {
    "deneyim": (
        "profesyonel deneyim",
        "is deneyimi",
        "work experience",
        "professional experience",
        "employment history",
        "deneyim",
        "tecrube",
        "experience",
    ),
    "egitim": ("egitim bilgileri", "academic background", "egitim", "education"),
    "beceriler": (
        "teknik beceriler",
        "technical skills",
        "yetkinlikler",
        "yetenekler",
        "beceriler",
        "skills",
    ),
}

_NON_ALNUM = re.compile(r"[^\w]+", re.UNICODE)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
#: Yerel kısmı rakamla başlayan adres. Gerçek hayatta nadir, çıkarılan metinde
#: sık: sayfa numarası ya da yan sütundaki bir öğe adresle aynı satıra düşünce
#: "1emirhankuru@…" çıkıyor ve ATS adresi böyle kaydediyor. Gerçek bir CV'de
#: ölçüldü (2026-08-08).
_GLUED_LOCAL = re.compile(r"^\d")
#: Telefon: `+90 …` ya da `0…` ile başlayan, 10-14 haneli bir dizi. Baştaki
#: zorunlu `+`/`0` kasıtlı — yoksa "2018 - 2022 2024" gibi tarih yığınları
#: telefon sanılıyor.
_PHONE = re.compile(r"(?:\+\d{1,3}|0)[\s.\-()/]*\d(?:[\s.\-()/]*\d){8,12}")

_MONTHS = "|".join(sorted(MONTH_WORDS, key=len, reverse=True))
_ONGOING = "|".join(sorted(ONGOING_WORDS, key=len, reverse=True))

#: Tarih biçimleri. Hepsi `ascii_fold` edilmiş metin üzerinde aranıyor.
_DATE_STYLES: dict[str, re.Pattern[str]] = {
    "ay + yıl": re.compile(rf"\b(?:{_MONTHS})\s+(?:19|20)\d{{2}}\b"),
    "sayısal (aa.yyyy)": re.compile(r"\b\d{1,2}[./]\d{4}\b"),
    "sayısal (gg.aa.yyyy)": re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b"),
    "yalın yıl": re.compile(r"\b(?:19|20)\d{2}\b"),
}

#: Dönem: "Eylül 2024 – Devam Ediyor", "2018-2022", "Şubat 2026 – Mayıs 2026".
_PERIOD = re.compile(
    rf"(?:(?:{_MONTHS})\s+)?(?:19|20)\d{{2}}\s*(?:[-–—]+|\bto\b)\s*"
    rf"(?:(?:(?:{_MONTHS})\s+)?(?:19|20)\d{{2}}|(?:{_ONGOING})(?:\s+ediyor)?)"
)


class NoTextLayerError(RuntimeError):
    """PDF'te okunabilir bir metin katmanı yok — ATS için ölümcül."""


# --------------------------------------------------------------------------- #
# Çıkarma
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Extraction:
    """PDF'ten geri okunan metin ve nasıl okunduğu."""

    text: str
    pages: tuple[str, ...]
    #: "pypdf" ya da "pdfplumber" — rapor bunu yazar, iddia ölçüme bağlansın.
    engine: str
    #: Yedeğe neden düşüldüğü; asıl motor yettiyse None.
    fallback_reason: str | None = None

    @property
    def lines(self) -> list[str]:
        return [line.strip() for line in self.text.splitlines() if line.strip()]


def _looks_empty(text: str) -> str | None:
    """Metin kullanılamaz görünüyorsa sebebini döndürür, yoksa None."""
    stripped = text.strip()
    if len(stripped) < MIN_TEXT_CHARS:
        return f"{len(stripped)} karakter çıktı (eşik {MIN_TEXT_CHARS})"

    alpha = sum(1 for char in stripped if char.isalpha())
    ratio = alpha / len(stripped)
    if ratio < MIN_ALPHA_RATIO:
        return f"harf oranı %{ratio * 100:.0f} (eşik %{MIN_ALPHA_RATIO * 100:.0f})"
    return None


def _extract_pypdf(path: Path) -> tuple[str, ...]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return tuple(page.extract_text() or "" for page in reader.pages)


def _extract_pdfplumber(path: Path) -> tuple[str, ...]:
    import pdfplumber  # opsiyonel bağımlılık: `uv sync --extra pdf`

    with pdfplumber.open(str(path)) as pdf:
        return tuple(page.extract_text() or "" for page in pdf.pages)


def extract_text(path: Path) -> Extraction:
    """PDF'ten metni çıkarır; `pypdf` yetmezse `pdfplumber`a düşer.

    İkisi de boş dönerse `NoTextLayerError`: metin katmanı olmayan bir PDF
    (taranmış sayfa, düz görsel) ATS için okunamaz — düzeltilecek bir şey yok,
    baştan üretilmesi gerekiyor.
    """
    if not path.exists():
        raise FileNotFoundError(path)

    pages = _extract_pypdf(path)
    text = "\n".join(pages)
    reason = _looks_empty(text)
    if reason is None:
        return Extraction(text=text, pages=pages, engine="pypdf")

    try:
        backup = _extract_pdfplumber(path)
    except ImportError:
        raise NoTextLayerError(
            f"pypdf bu PDF'ten metin çıkaramadı ({reason}). Yedek motor kurulu değil: "
            "`uv sync --extra pdf` ile pdfplumber'ı kurup tekrar dene. Yedek de boş "
            "dönerse PDF'te metin katmanı yok demektir (taranmış sayfa) — bir ATS "
            "böyle bir CV'yi hiç okuyamaz."
        ) from None

    backup_text = "\n".join(backup)
    if (backup_reason := _looks_empty(backup_text)) is not None:
        raise NoTextLayerError(
            f"Bu PDF'te okunabilir metin katmanı yok — taranmış bir sayfa olabilir. "
            f"pypdf: {reason}; pdfplumber: {backup_reason}. Bir ATS bu dosyadan tek "
            "kelime okuyamaz; CV'yi metin katmanı üreten bir araçla yeniden üret."
        )

    return Extraction(
        text=backup_text,
        pages=backup,
        engine="pdfplumber",
        fallback_reason=reason,
    )


# --------------------------------------------------------------------------- #
# Bulgular ve sonuç
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Finding:
    """Tek bir ATS bulgusu."""

    level: Level
    rule: str
    message: str
    fix_hint: str | None = None
    #: Bulguyu somutlayan örnekler: etkilenen kelimeler, eksik terimler.
    evidence: tuple[str, ...] = ()

    def __str__(self) -> str:
        tail = f"  → {self.fix_hint}" if self.fix_hint else ""
        return f"[{self.level}] {self.rule}: {self.message}{tail}"


@dataclass(frozen=True)
class CheckResult:
    """Bir kontrolün sonucu: ne kadar geçti, ne buldu."""

    rule: str
    #: Rapordaki ve terminal tablosundaki Türkçe başlık.
    label: str
    #: 0.0–1.0. Ağırlıkla çarpılıp toplam puana giriyor.
    ratio: float
    #: "12/14 terim", "e-posta ✓ telefon ✗" gibi tek satırlık özet.
    detail: str
    findings: tuple[Finding, ...] = ()
    #: İlan verilmediği için atlanan kontrol puanlamaya hiç girmez.
    skipped: bool = False

    @property
    def weight(self) -> float:
        return 0.0 if self.skipped else WEIGHTS[self.rule]

    @property
    def points(self) -> float:
        return self.ratio * self.weight


@dataclass
class AtsReport:
    """`check` komutunun tam çıktısı."""

    pdf: Path
    engine: str
    checks: list[CheckResult] = field(default_factory=list)
    fallback_reason: str | None = None
    #: Hangi ilana karşı ölçüldü (varsa) — rapor başlığına giriyor.
    job_title: str | None = None

    @property
    def findings(self) -> list[Finding]:
        return [finding for check in self.checks for finding in check.findings]

    @property
    def critical(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "critical"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    @property
    def infos(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "info"]

    @property
    def score(self) -> int:
        """Ağırlıklı yüzde. Atlanan kontroller paydadan da düşer."""
        possible = sum(check.weight for check in self.checks)
        if not possible:
            return 0
        return round(100 * sum(check.points for check in self.checks) / possible)

    @property
    def verdict(self) -> str:
        if self.critical:
            return "kritik hata — bu haliyle gönderme"
        if self.score >= READY_SCORE:
            return "ATS'e hazır"
        if self.score >= RISKY_SCORE:
            return "düzeltilebilir"
        return "riskli"

    def ok(self, *, strict: bool = False) -> bool:
        """Çıkış kodu buna bakıyor: kritik yoksa geçer, `--strict` uyarıyı da sayar."""
        if self.critical:
            return False
        return not (strict and self.warnings)


# --------------------------------------------------------------------------- #
# Kontrol 1: ligatürler
# --------------------------------------------------------------------------- #


def check_ligatures(text: str) -> CheckResult:
    """Metin katmanında ligatür glifi var mı? (issue 10'un var olma sebebi)

    `ﬁne-tuning` PDF'te doğru görünür ama ATS "fine-tuning" araması hiçbir şey
    bulmaz: metin katmanındaki karakter U+FB01, aranan `f`+`i` değil.
    """
    hits = sorted({char for char in text if char in LIGATURES})
    if not hits:
        return CheckResult(
            rule="ligatures",
            label="Ligatür",
            ratio=1.0,
            detail="yok",
        )

    affected = sorted(
        {token.strip(".,;:()[]") for token in text.split() if any(c in token for c in hits)}
    )
    replacements = ", ".join(f"{char!r}→{LIGATURES[char]!r}" for char in hits)
    return CheckResult(
        rule="ligatures",
        label="Ligatür",
        ratio=0.0,
        detail=f"{len(affected)} kelime etkilendi",
        findings=(
            Finding(
                level="critical",
                rule="text.ligature",
                message=f"Metin katmanında ligatür karakteri var: {replacements}",
                fix_hint=(
                    "Ligatür üretmeyen bir font seç (Tinos, Inter, Arimo, Cousine) ya da "
                    "kaynak metni `validate --fix` ile temizle. ATS bu kelimeleri "
                    "aradığında bulamaz."
                ),
                evidence=tuple(affected[:12]),
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# Kontrol 2: anahtar kelime kapsaması
# --------------------------------------------------------------------------- #


def must_terms(analysis: JobAnalysis) -> list[str]:
    """İlanın **zorunlu** gereksinimlerindeki teknik terimler, tekrarsız.

    Zorunlu gereksinimlerin hiçbirinde anahtar kelime yoksa ilanın tüm teknik
    terimlerine düşülür — analiz her ilanda `requirements[].keywords` doldurmuyor.
    """
    terms = [
        keyword
        for requirement in analysis.posting.requirements
        if requirement.kind == "must"
        for keyword in requirement.keywords
    ]
    if not terms:
        terms = list(analysis.posting.tech_keywords)

    seen: set[str] = set()
    unique: list[str] = []
    for term in terms:
        key = fold(term).strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(term)
    return unique


def contains_term(haystack: str, term: str) -> bool:
    """`term` metinde geçiyor mu? `haystack` `fold` edilmiş olmalı.

    İngilizce çoğul iki yönde de denenir: ilan "LLMs" derken CV "LLM" yazıyorsa
    terim kayıp değildir. Türkçe ekler `fold` sırasında kesme işaretiyle birlikte
    ayraca dönüştüğü için ("Python'da" → " python da ") ayrıca ele alınmıyor.
    """
    needle = fold(term).strip()
    if not needle:
        return False

    variants = {needle}
    if len(needle) > 2 and needle.endswith("s"):
        variants.add(needle[:-1])
    else:
        variants.add(f"{needle}s")
    return any(f" {variant} " in haystack for variant in variants)


def check_keywords(text: str, analysis: JobAnalysis | None) -> CheckResult:
    """İlanın zorunlu terimlerinden kaçı PDF metninde hayatta kaldı?

    Arama ligatürü açılmış ve sadeleştirilmiş metinde yapılıyor: burada aranan
    "CV'de yazıyor mu" değil, **"ATS'in araması bulur mu"**.
    """
    if analysis is None:
        return CheckResult(
            rule="keywords",
            label="Anahtar kelime",
            ratio=0.0,
            detail="ilan verilmedi — atlandı",
            skipped=True,
        )

    terms = must_terms(analysis)
    if not terms:
        return CheckResult(
            rule="keywords",
            label="Anahtar kelime",
            ratio=0.0,
            detail="ilanda zorunlu terim yok — atlandı",
            skipped=True,
        )

    haystack = fold(expand_ligatures(text))
    missing = [term for term in terms if not contains_term(haystack, term)]
    found = len(terms) - len(missing)
    ratio = found / len(terms)

    findings: tuple[Finding, ...] = ()
    if missing:
        findings = (
            Finding(
                # Eksik terim başvuruyu öldürmez, elenme olasılığını artırır:
                # kelime gerçekten CV'de yoksa çare uydurmak değil, ilanı
                # yeniden değerlendirmek. Bu yüzden `warning`.
                level="warning",
                rule="keywords.missing",
                message=(f"İlanın zorunlu terimlerinden {len(missing)} tanesi PDF metninde yok."),
                fix_hint=(
                    "Terim gerçekten sende varsa `master.json`'a ekleyip yeniden uyarla; "
                    "yoksa ekleme — uyum raporu bunu zaten mülakat hazırlığına yazıyor."
                ),
                evidence=tuple(missing[:12]),
            ),
        )

    return CheckResult(
        rule="keywords",
        label="Anahtar kelime",
        ratio=ratio,
        detail=f"{found}/{len(terms)} terim bulundu",
        findings=findings,
    )


def expand_ligatures(text: str) -> str:
    """Ligatür karakterlerini açar — arama `fi` ile `ﬁ`yi aynı görsün."""
    return fix_ligatures(text)[0]


# --------------------------------------------------------------------------- #
# Kontrol 3: iletişim
# --------------------------------------------------------------------------- #


def check_contact(text: str) -> CheckResult:
    """E-posta ve telefon metinden bulunabiliyor mu?

    E-posta `critical`: ATS aday kaydını e-postayla açar, okuyamazsa başvuru
    hiç oluşmaz. Telefon `warning` — eksikliği zararlı ama ölümcül değil.
    """
    emails = sorted(set(_EMAIL.findall(text)))
    phones = [match.group() for match in _PHONE.finditer(text)]

    findings: list[Finding] = []
    if glued := [address for address in emails if _GLUED_LOCAL.match(address)]:
        findings.append(
            Finding(
                level="warning",
                rule="contact.email_glued",
                message=(
                    "E-posta adresinin başına komşu bir metin yapışmış görünüyor: "
                    f"{', '.join(glued)}"
                ),
                fix_hint=(
                    "Genellikle sayfa numarası ya da yan sütundaki bir öğe adresle aynı "
                    "satıra düşüyor. ATS adresi olduğu gibi alır ve gönderdiği yanıt "
                    "geri döner — adresin çevresine boşluk bırak ya da konumunu değiştir."
                ),
                evidence=tuple(glued),
            )
        )

    if not emails:
        findings.append(
            Finding(
                level="critical",
                rule="contact.email",
                message="PDF metninde e-posta adresi bulunamadı.",
                fix_hint=(
                    "Adresi düz metin olarak yaz; ikon ya da gömülü `mailto:` linki "
                    "ATS tarafından okunmuyor."
                ),
            )
        )
    elif len(emails) > 1:
        findings.append(
            Finding(
                level="warning",
                rule="contact.email_multiple",
                message=f"Metinde {len(emails)} farklı e-posta geçiyor.",
                fix_hint="Tek adres bırak; ATS hangisini alacağını bilemez.",
                evidence=tuple(emails),
            )
        )

    if not phones:
        findings.append(
            Finding(
                level="warning",
                rule="contact.phone",
                message="PDF metninde telefon numarası bulunamadı.",
                fix_hint="Numarayı `+90 5xx xxx xx xx` gibi düz metin olarak yaz.",
            )
        )

    ratio = (1.0 if emails else 0.0) * 0.7 + (1.0 if phones else 0.0) * 0.3
    detail = f"e-posta {'var' if emails else 'yok'}, telefon {'var' if phones else 'yok'}"
    return CheckResult(
        rule="contact",
        label="İletişim",
        ratio=ratio,
        detail=detail,
        findings=tuple(findings),
    )


# --------------------------------------------------------------------------- #
# Kontrol 4: link görünürlüğü
# --------------------------------------------------------------------------- #


def check_links(text: str) -> CheckResult:
    """GitHub ve LinkedIn **metin olarak** görünüyor mu?

    Gömülü hyperlink PDF'in annotation katmanında yaşıyor; metin çıkarıcı da
    ATS de oraya bakmıyor. `lint.check_visible_profile_urls` aynı kuralı
    `master.json` tarafında uyguluyor — bu, sonucun PDF'te de tuttuğunun kanıtı.
    """
    haystack = expand_ligatures(text)
    found: list[str] = []
    findings: list[Finding] = []

    for name, pattern, example in (
        ("github", GITHUB_URL, "github.com/kullanici-adi"),
        ("linkedin", LINKEDIN_URL, "linkedin.com/in/kullanici-adi"),
    ):
        if pattern.search(haystack):
            found.append(name)
            continue
        findings.append(
            Finding(
                level="warning",
                rule=f"links.{name}",
                message=f"{name} adresi PDF metninde görünmüyor.",
                fix_hint=(
                    f"Görünen metni tam URL yap: {example}. Gömülü link ATS için yok hükmünde."
                ),
            )
        )

    return CheckResult(
        rule="links",
        label="Link görünürlüğü",
        ratio=len(found) / 2,
        detail=", ".join(found) if found else "hiçbiri metin olarak yok",
        findings=tuple(findings),
    )


# --------------------------------------------------------------------------- #
# Kontrol 5 ve 6: bölüm başlıkları ve okuma sırası
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HeadingHit:
    """Bir satırın başında bulunan bölüm başlığı."""

    line: int
    section: str
    alias: str
    #: Satırda başlıktan sonra gelen metin. Boş olması gerekmiyor — bkz.
    #: `find_headings`.
    rest: str


def _flatten(line: str) -> str:
    """Satırı başlık aramasına hazırlar: ASCII, küçük harf, tek boşluk."""
    return _NON_ALNUM.sub(" ", ascii_fold(line)).strip()


def find_headings(lines: list[str]) -> list[HeadingHit]:
    """Satır **başındaki** bölüm başlıklarını bulur.

    Kural kasıtlı olarak tek maddelik: başlık satırın ilk kelimesi (ya da ilk
    kelimeleri) olmalı. Satırın geri kalanına bakılmıyor.

    Bu ölçümle karara bağlandı (2026-08-08, iki gerçek PDF). İlk sürüm başlığın
    satıra *hâkim* olmasını arıyordu — geriye 24 karakterden fazla kalırsa
    başlık saymıyordu. Reactive Resume'un tek sütunlu şablonunda `pypdf`
    başlığı ilk öğeyle aynı satıra indiriyor:

        Deneyim VidInsight Teknoloji
        Yetenekler Python AWS (EC2) Docker Git

    İkincisi eşiği aştığı için "Beceriler bölümü yok" diye **yanlış bir eksik**
    raporlanıyordu. Satır başı kuralı ikisini de doğru yakalıyor ve gövde
    cümlesindeki "…5 yıllık deneyim ile…" başlık sanılmıyor: orada kelime
    satırın başında değil.

    Tek başına satır başı da yetmiyor — aynı ölçümde şu madde başlık sanıldı:

        Eğitim platformunun dijital altyapısının … tasarlanması.

    Ayırt eden şey `_continues_sentence`: gerçek bir başlıktan sonra ya hiçbir
    şey gelmez ya da büyük harfle başlayan bir ad ("Deneyim **V**idInsight");
    cümle devam ediyorsa sonraki kelime küçük harflidir.
    """
    hits: list[HeadingHit] = []
    for index, line in enumerate(lines):
        flat = _flatten(line)
        if not flat:
            continue

        for section, aliases in SECTION_HEADINGS.items():
            for alias in aliases:
                if flat != alias and not flat.startswith(f"{alias} "):
                    continue
                if _continues_sentence(line, alias):
                    continue
                hits.append(
                    HeadingHit(
                        line=index,
                        section=section,
                        alias=alias,
                        rest=flat[len(alias) :].strip(),
                    )
                )
                break
    return hits


def _continues_sentence(line: str, alias: str) -> bool:
    """Başlıktan sonra gelen kelime cümleyi sürdürüyor mu?

    Yalnızca **küçük harfle başlayan** bir sonraki kelime "sürdürüyor" sayılır;
    büyük harf, rakam ve satır sonu başlığa yakışır. Karşılaştırma kelime
    düzeyinde yapılıyor: `_flatten` uzunlukları değiştirdiği için ham metinde
    karakter ofseti güvenilir değil.
    """
    words = line.split()
    index = len(alias.split())
    if index >= len(words):
        return False
    return words[index][:1].islower()


def sections_on_line(line: str) -> set[str]:
    """Satırın **herhangi bir yerinde** geçen bölüm başlıkları.

    Yalnızca okuma sırası kontrolü kullanıyor: iki farklı bölüm başlığının aynı
    satıra düşmesi, tek sütunlu bir düzende olamayacak bir şey.
    """
    flat = f" {_flatten(line)} "
    return {
        section
        for section, aliases in SECTION_HEADINGS.items()
        if any(f" {alias} " in flat for alias in aliases)
    }


def check_headings(lines: list[str]) -> CheckResult:
    """Deneyim / Eğitim / Beceriler başlıkları var mı?

    ATS bölümleri başlıklardan ayırıyor; başlığı olmayan bir blok ya yanlış
    bölüme yazılıyor ya da hiç ayrıştırılamıyor.
    """
    hits = find_headings(lines)
    present = {hit.section for hit in hits}
    missing = [section for section in SECTION_HEADINGS if section not in present]

    findings: list[Finding] = []
    if not present:
        findings.append(
            Finding(
                level="critical",
                rule="headings.none",
                message="Hiçbir bölüm başlığı bulunamadı (Deneyim / Eğitim / Beceriler).",
                fix_hint=(
                    "ATS bölümleri başlıktan ayırır; başlıksız bir CV tek blok metin "
                    "olarak okunur. Şablonun başlıkları metin katmanına yazdığından emin ol."
                ),
            )
        )
    elif missing:
        findings.append(
            Finding(
                level="warning",
                rule="headings.missing",
                message=f"Bulunamayan bölüm başlığı: {', '.join(missing)}",
                fix_hint="Standart başlıklar kullan — yaratıcı başlıklar ATS'i şaşırtıyor.",
                evidence=tuple(missing),
            )
        )

    return CheckResult(
        rule="headings",
        label="Bölüm başlıkları",
        ratio=len(present) / len(SECTION_HEADINGS),
        detail=f"{len(present)}/{len(SECTION_HEADINGS)} bulundu",
        findings=tuple(findings),
    )


def check_reading_order(lines: list[str]) -> CheckResult:
    """Metin çok sütunlu düzende iç içe geçmiş mi?

    Doğrudan ölçülemeyen bir şey, o yüzden iki **sezgisel** sinyale bakılıyor:

    1. Aynı satırda iki farklı bölüm başlığı — iki sütun aynı satıra düşmüş.
       Tek sütunlu bir düzende bu olamaz.
    2. Aynı başlığın birden çok kez çıkması — sütun tekrarı ya da her sayfada
       yinelenen bir üstbilgi.

    Üçüncü bir sinyal denendi ve **ölçümle elendi**: "başlık satırında başka
    içerik var". Reactive Resume'un tek sütunlu şablonunda `pypdf` başlığı ilk
    öğeyle aynı satıra indiriyor ("Deneyim VidInsight Teknoloji"), yani kural
    doğru bir CV'de sürekli yanlış alarm veriyordu. Kalan iki sinyal sütun
    karışmasına özgü.

    Sezgisel oldukları için hiçbiri `critical` değil: doğru bir CV'yi "bozuk"
    diye damgalamanın bedeli, kaçırmanın bedelinden düşük değil.
    """
    hits = find_headings(lines)
    findings: list[Finding] = []
    ratio = 1.0

    shared = {
        index: found for index, line in enumerate(lines) if len(found := sections_on_line(line)) > 1
    }
    if shared:
        ratio = 0.0
        findings.append(
            Finding(
                level="warning",
                rule="order.interleaved",
                message=(
                    f"{len(shared)} satırda iki farklı bölüm başlığı yan yana — metin çok "
                    "sütunlu düzende iç içe geçmiş görünüyor."
                ),
                fix_hint=(
                    "Tek sütunlu bir şablona geç. ATS sütunları soldan sağa okur ve "
                    "deneyim satırlarını beceri satırlarıyla birbirine karıştırır."
                ),
                evidence=tuple(lines[index][:80] for index in sorted(shared)),
            )
        )

    repeated = sorted(
        {hit.section for hit in hits if sum(1 for h in hits if h.section == hit.section) > 1}
    )
    if repeated:
        ratio = min(ratio, 0.5)
        findings.append(
            Finding(
                level="warning",
                rule="order.repeated_heading",
                message=f"Aynı bölüm başlığı birden çok kez geçiyor: {', '.join(repeated)}",
                fix_hint=(
                    "Sütun tekrarı ya da her sayfada yinelenen bir üstbilgi olabilir; "
                    "ATS ikinci bölümü ayrı bir blok sanabilir."
                ),
                evidence=tuple(repeated),
            )
        )

    if not hits:
        # Başlık yoksa sıra hakkında söylenecek bir şey de yok; ceza
        # `headings` kontrolünde zaten kesildi, burada iki kez kesilmesin.
        return CheckResult(
            rule="reading_order",
            label="Okuma sırası",
            ratio=1.0,
            detail="başlık bulunamadı — değerlendirilemedi",
        )

    detail = "temiz" if ratio == 1.0 else "sütun karışması şüphesi"
    return CheckResult(
        rule="reading_order",
        label="Okuma sırası",
        ratio=ratio,
        detail=detail,
        findings=tuple(findings),
    )


# --------------------------------------------------------------------------- #
# Kontrol 7: tarih biçimi
# --------------------------------------------------------------------------- #


def find_periods(text: str) -> list[str]:
    """Metindeki dönem ifadeleri ("Eylül 2024 – Devam Ediyor", "2018-2022")."""
    return [match.group() for match in _PERIOD.finditer(ascii_fold(text))]


def date_styles(text: str) -> list[str]:
    """Metinde kullanılan tarih biçimleri.

    Eşleşen aralıklar maskeleniyor: "Eylül 2024" içindeki yıl ayrıca "yalın yıl"
    sayılmasın diye — yoksa her CV karışık biçim kullanıyor görünürdü.
    """
    haystack = ascii_fold(text)
    mask = [False] * len(haystack)
    styles: list[str] = []

    for name, pattern in _DATE_STYLES.items():
        hit = False
        for match in pattern.finditer(haystack):
            if any(mask[match.start() : match.end()]):
                continue
            for index in range(match.start(), match.end()):
                mask[index] = True
            hit = True
        if hit:
            styles.append(name)
    return styles


def check_dates(text: str) -> CheckResult:
    """Tarihler ayrıştırılabilir ve tutarlı mı?

    Ayrıştırılabilirlik `score.parse_period_end` ile ölçülüyor — boru hattının
    tazelik bonusunu hesaplayan işlevin ta kendisi. Bir dönemi o okuyamıyorsa
    bir ATS'in okuyacağını varsaymak için sebep yok.

    Biçim tutarlılığı **`info`**: eğitimde "2018-2022", deneyimde "Eylül 2024 –
    Devam Ediyor" kullanmak son derece yaygın ve hiçbir ATS'i bozmuyor. Bilgi
    olarak raporlanıyor ama puanı düşürmüyor.
    """
    periods = find_periods(text)
    styles = date_styles(text)

    findings: list[Finding] = []
    if not periods:
        return CheckResult(
            rule="dates",
            label="Tarih biçimi",
            ratio=0.0,
            detail="dönem bulunamadı",
            findings=(
                Finding(
                    level="warning",
                    rule="dates.missing",
                    message="Metinde ayrıştırılabilir bir tarih aralığı yok.",
                    fix_hint=(
                        "Her deneyime 'Eylül 2024 – Devam Ediyor' gibi bir dönem yaz; "
                        "ATS iş geçmişini bu aralıklardan kuruyor."
                    ),
                ),
            ),
        )

    unparsed = [period for period in periods if parse_period_end(period) is None]
    ratio = (len(periods) - len(unparsed)) / len(periods)
    if unparsed:
        findings.append(
            Finding(
                level="warning",
                rule="dates.unparsed",
                message=f"{len(unparsed)} dönem ifadesi ayrıştırılamadı.",
                fix_hint="Ay adı + yıl ya da yıl–yıl biçimini kullan.",
                evidence=tuple(unparsed[:6]),
            )
        )

    if len(styles) > 1:
        findings.append(
            Finding(
                level="info",
                rule="dates.mixed",
                message=f"Birden çok tarih biçimi kullanılmış: {', '.join(styles)}",
                fix_hint=(
                    "Sorun değil — eğitimde yıl, deneyimde ay+yıl yaygın bir tercih. "
                    "Yine de tek biçim daha derli toplu görünür."
                ),
                evidence=tuple(styles),
            )
        )

    return CheckResult(
        rule="dates",
        label="Tarih biçimi",
        ratio=ratio,
        detail=f"{len(periods)} dönem, {len(styles)} biçim",
        findings=tuple(findings),
    )


# --------------------------------------------------------------------------- #
# Giriş noktası
# --------------------------------------------------------------------------- #


def run_checks(
    extraction: Extraction,
    analysis: JobAnalysis | None = None,
    *,
    pdf: Path | None = None,
) -> AtsReport:
    """Tüm kontrolleri çalıştırır ve puanlanmış raporu döndürür."""
    lines = extraction.lines
    checks = [
        check_ligatures(extraction.text),
        check_keywords(extraction.text, analysis),
        check_contact(extraction.text),
        check_links(extraction.text),
        check_headings(lines),
        check_reading_order(lines),
        check_dates(extraction.text),
    ]
    return AtsReport(
        pdf=pdf or Path("-"),
        engine=extraction.engine,
        checks=checks,
        fallback_reason=extraction.fallback_reason,
        job_title=analysis.posting.title if analysis else None,
    )


def check_pdf(path: Path, analysis: JobAnalysis | None = None) -> AtsReport:
    """PDF'ten metni çıkarıp tüm kontrolleri çalıştırır."""
    return run_checks(extract_text(path), analysis, pdf=path)


# --------------------------------------------------------------------------- #
# Rapor
# --------------------------------------------------------------------------- #

_LEVEL_LABEL: dict[Level, str] = {
    "critical": "🔴 KRİTİK",
    "warning": "🟡 uyarı",
    "info": "ℹ️ bilgi",
}


def build_markdown(report: AtsReport) -> str:
    """`ats_report.md`'nin tam metni."""
    lines: list[str] = [
        f"# ATS öz-kontrolü — {report.pdf.name}",
        "",
        f"> **Puan: {report.score}/100** · karar: **{report.verdict}**",
        "",
        f"Metin `{report.engine}` ile çıkarıldı"
        + (f" (pypdf yetmedi: {report.fallback_reason})" if report.fallback_reason else "")
        + ".",
        "",
    ]
    if report.job_title:
        lines += [f"İlan: **{report.job_title}**", ""]

    lines += ["## Kontroller", "", "| Kontrol | Sonuç | Puan |", "|---|---|---|"]
    for check in report.checks:
        if check.skipped:
            lines.append(f"| {check.label} | {check.detail} | — |")
            continue
        lines.append(f"| {check.label} | {check.detail} | {check.points:.0f}/{check.weight:.0f} |")
    lines += ["", f"**Toplam: {report.score}/100**", ""]

    lines += _findings_section(report)
    lines += _footer(report)
    return "\n".join(lines).rstrip() + "\n"


def _findings_section(report: AtsReport) -> list[str]:
    if not report.findings:
        return [
            "## Bulgular",
            "",
            "_Yok — bütün kontroller temiz geçti._",
            "",
        ]

    lines = ["## Bulgular", ""]
    for level in ("critical", "warning", "info"):
        group = [f for f in report.findings if f.level == level]
        for finding in group:
            lines.append(f"### {_LEVEL_LABEL[level]} `{finding.rule}`")
            lines.append("")
            lines.append(finding.message)
            if finding.evidence:
                lines.append("")
                lines.append(", ".join(f"`{item}`" for item in finding.evidence))
            if finding.fix_hint:
                lines.append("")
                lines.append(f"**Ne yapmalı:** {finding.fix_hint}")
            lines.append("")
    return lines


def _footer(report: AtsReport) -> list[str]:
    return [
        "---",
        "",
        "<details><summary>Kontrol nasıl çalışıyor</summary>",
        "",
        "- Metin PDF'ten `pypdf` ile geri okunur; boş ya da bozuk çıkarsa `pdfplumber`a düşülür.",
        "- Anahtar kelime araması **ligatürü açılmış** ve sadeleştirilmiş metinde yapılır: "
        "burada sorulan soru 'CV'de yazıyor mu' değil, 'ATS'in araması bulur mu'.",
        "- Okuma sırası kontrolü sezgiseldir; her bulgusu `warning` seviyesindedir.",
        f"- Çıkış kodu yalnızca kritik bulguya bakar ({len(report.critical)} tane); "
        "`--strict` uyarıları da hata sayar.",
        "- Kontrollerin ağırlıkları ve gerekçeleri: `docs/ATS.md`",
        "",
        "</details>",
    ]


def write_ats_report(run_dir: Path, markdown: str) -> Path:
    path = run_dir / ATS_REPORT_FILENAME
    path.write_text(markdown, encoding="utf-8", newline="")
    return path
