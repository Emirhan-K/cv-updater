"""`cv-updater validate` içerik lint'i.

Kurallar doğrudan `TODO.md` §1'deki ATS bulgularından geliyor: ligatürler, görünür
GitHub/LinkedIn URL'leri, eğitimde derece seviyesi, parantez öncesi boşluk hatası,
tekil e-posta. Buna `master.json`'a özgü değişmezler ekleniyor: kontrollü sözlük dışı
etiket yok, tekil id'ler, etiketsiz madde yok.

Bu modül **LLM kullanmaz ve ağa çıkmaz** — deterministik ve test edilebilir kalmalı.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

from jsonschema import Draft202012Validator

from cv_updater.models.resume import Resume, Sections
from cv_updater.schema import rxresume_schema
from cv_updater.tags import TagVocabulary, default_vocabulary

Level = Literal["error", "warning", "info"]

#: PDF'te "fine-tuning" kelimesini "ﬁne-tuning" yapan ve ATS'in aramayı kaçırmasına
#: yol açan tipografik ligatürler. TODO.md §1'in ilk (kritik) maddesi.
LIGATURES: dict[str, str] = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",
    "ﬆ": "st",
}

_HTML_TAG = re.compile(r"<[^>]+>")
_CODE_BLOCK = re.compile(r"<code>.*?</code>", re.DOTALL)
#: "Yazılım Mühendisi(AI" — harften hemen sonra gelen açılış parantezi.
_TIGHT_PAREN = re.compile(r"(?<=[^\W\d_])\(")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

DEGREE_WORDS = (
    "lisans",
    "önlisans",
    "onlisans",
    "yüksek lisans",
    "yuksek lisans",
    "doktora",
    "bachelor",
    "master",
    "associate",
    "phd",
    "doctorate",
)

#: TODO.md §1: gömülü linki çoğu ATS okumaz, görünür metin gerekiyor.
GITHUB_URL = re.compile(r"github\.com/[\w.-]+", re.IGNORECASE)
LINKEDIN_URL = re.compile(r"linkedin\.com/in/[\w.-]+", re.IGNORECASE)

#: `TODO.md` doldurulmamış olguları köşeli parantezle işaretliyor ("[terfi tarihi]",
#: "[X] kişilik ekip"). Bunlar CV'ye asla gitmemeli — ve **uydurulmamalı** da:
#: kural, eksik olguyu sessizce geçmek yerine gürültülü şekilde durduruyor.
_PLACEHOLDER = re.compile(r"\[[^\]]{1,60}\]")

MIN_TAGGED_SKILLS = 20
MIN_EXPERIENCES = 3
MIN_BULLETS_PER_EXPERIENCE = 4


@dataclass(frozen=True)
class Finding:
    """Tek bir lint bulgusu."""

    level: Level
    rule: str
    where: str
    message: str
    fix_hint: str | None = None

    def __str__(self) -> str:
        tail = f"  → {self.fix_hint}" if self.fix_hint else ""
        return f"[{self.level}] {self.rule} @ {self.where}: {self.message}{tail}"


@dataclass
class LintReport:
    findings: list[Finding]
    stats: dict[str, int]

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    @property
    def infos(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "info"]

    @property
    def ok(self) -> bool:
        """Issue 03 kabul kriteri: sıfır hata **ve** sıfır uyarı."""
        return not self.errors and not self.warnings


# --------------------------------------------------------------------------- #
# Metin gezinmesi
# --------------------------------------------------------------------------- #


def strip_html(value: str) -> str:
    """HTML etiketlerini ve `<code>` bloklarını atar.

    `<code>` içindeki `func(x)` gibi ifadeler parantez kuralını yanlış tetiklemesin diye
    tamamen çıkarılır.
    """
    return _HTML_TAG.sub(" ", _CODE_BLOCK.sub(" ", value))


#: Üst düzey stil blokları — prose değil, taranmaz.
_NON_PROSE_BLOCKS = frozenset({"picture", "metadata"})

#: Prose olmayan yaprak alanlar. `rgba(0, 0, 0, 0.5)` gibi renk değerleri parantez
#: kuralını, ikon adları da ligatür kuralını yanlış tetikliyordu.
_NON_PROSE_LEAVES = frozenset(
    {
        "id",
        "icon",
        "iconColor",
        "borderColor",
        "shadowColor",
        "url",
        "link",
        "fontFamily",
    }
)


def iter_texts(payload: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """CV içindeki tüm metin alanlarını `(yol, metin)` olarak dolaşır.

    Ham ve filtresiz — e-posta taraması gibi "her yerde ara" kuralları bunu kullanır.
    """
    if isinstance(payload, str):
        if payload.strip():
            yield path, payload
    elif isinstance(payload, dict):
        for key, value in payload.items():
            yield from iter_texts(value, f"{path}.{key}" if path else str(key))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            yield from iter_texts(value, f"{path}[{index}]")


def iter_prose(resume: Resume) -> Iterator[tuple[str, str]]:
    """Yalnızca **CV'de görünen düz metin** alanları.

    Stil blokları (`picture`, `metadata`) ve renk/ikon/URL yaprakları dışarıda kalır:
    ligatür ve parantez kuralları yalnızca ATS'in PDF'ten okuyacağı metinle ilgilenir.
    """
    payload = resume.model_dump(mode="json")
    for block, value in payload.items():
        if block in _NON_PROSE_BLOCKS:
            continue
        for path, text in iter_texts(value, block):
            if path.rsplit(".", 1)[-1] in _NON_PROSE_LEAVES:
                continue
            yield path, text


def visible_link_texts(resume: Resume) -> list[str]:
    """ATS'in gerçekten okuyacağı **görünür** metinler.

    Kasıtlı olarak `website.url` ve `customFields[].link` hariç: gömülü hyperlink
    ATS tarafından okunmuyor, TODO.md §1'in bulgusu tam olarak bu.
    """
    texts = [field.text for field in resume.basics.customFields]
    texts += [profile.username for profile in resume.sections.profiles.items]
    texts += [
        profile.website.label for profile in resume.sections.profiles.items if not profile.hidden
    ]
    texts.append(resume.basics.website.label)
    return [t for t in texts if t]


# --------------------------------------------------------------------------- #
# Kurallar
# --------------------------------------------------------------------------- #


def check_email(resume: Resume) -> Iterator[Finding]:
    """E-posta alanı var ve tekil mi? (TODO.md §1)"""
    email = resume.basics.email.strip()
    if not email:
        yield Finding("error", "email.present", "basics.email", "E-posta alanı boş.")
        return

    found = _EMAIL.findall(email)
    if len(found) != 1 or found[0] != email:
        yield Finding(
            "error",
            "email.single",
            "basics.email",
            f"Tek bir e-posta adresi bekleniyordu, şu bulundu: {email!r}",
            "ATS derece/iletişim alanını tek değer olarak okur; ikinci adresi sil.",
        )

    others = {
        text
        for _, text in iter_texts(resume.model_dump(mode="json"))
        for text in _EMAIL.findall(text)
    } - {email}
    if others:
        yield Finding(
            "error",
            "email.single",
            "basics",
            f"CV'de ikinci bir e-posta geçiyor: {sorted(others)}",
            "Tek adres bırak; ATS hangisini alacağını bilemez.",
        )


def check_visible_profile_urls(resume: Resume) -> Iterator[Finding]:
    """GitHub ve LinkedIn **tam görünür URL metni** olarak var mı? (TODO.md §1)"""
    texts = visible_link_texts(resume)
    joined = " ".join(texts)

    for name, pattern, example in (
        ("github", GITHUB_URL, "github.com/Emirhan-K"),
        ("linkedin", LINKEDIN_URL, "linkedin.com/in/emirhan-k"),
    ):
        if not pattern.search(joined):
            yield Finding(
                "error",
                f"profiles.{name}",
                "basics.customFields",
                f"Görünür {name} URL metni yok (sadece ikon/kullanıcı adı var).",
                f"Görünen metni tam URL yap: {example} — çoğu ATS gömülü linki okumaz.",
            )


def check_education_degree(resume: Resume) -> Iterator[Finding]:
    """Eğitim maddesi derece seviyesi belirtiyor mu? (TODO.md §1)"""
    items = [item for item in resume.sections.education.items if not item.hidden]
    if not items:
        yield Finding("warning", "education.missing", "sections.education", "Eğitim bölümü boş.")
        return

    for item in items:
        haystack = f"{item.degree} {item.area} {item.description}".lower()
        if not any(word in haystack for word in DEGREE_WORDS):
            yield Finding(
                "error",
                "education.degree",
                f"education/{item.id}",
                f"{item.school!r} için derece seviyesi yok.",
                "ATS'in derece alanı için `degree` alanına 'Lisans' yaz.",
            )


def check_ligatures(resume: Resume) -> Iterator[Finding]:
    """Ligatür karakteri hiçbir metinde geçmemeli. (TODO.md §1, kritik)"""
    for path, text in iter_prose(resume):
        hits = sorted({char for char in text if char in LIGATURES})
        if hits:
            replacements = ", ".join(f"{char!r}→{LIGATURES[char]!r}" for char in hits)
            yield Finding(
                "error",
                "text.ligature",
                path,
                f"Ligatür karakteri bulundu: {replacements}",
                "`validate --fix` ile otomatik düzeltilir; PDF'te fontun ligatürünü de kapat.",
            )


def check_paren_spacing(resume: Resume) -> Iterator[Finding]:
    """`Yazılım Mühendisi(AI` gibi eksik boşluklar. (TODO.md §1)"""
    for path, text in iter_prose(resume):
        plain = strip_html(text)
        for match in _TIGHT_PAREN.finditer(plain):
            start = max(0, match.start() - 20)
            yield Finding(
                "error",
                "text.paren_spacing",
                path,
                f"Parantez öncesi boşluk yok: …{plain[start : match.end() + 12]}…",
                "Açılış parantezinden önce boşluk bırak.",
            )


def check_placeholders(resume: Resume) -> Iterator[Finding]:
    """Doldurulmamış `[köşeli parantez]` yer tutucuları.

    Bilinmeyen bir olguyu (terfi tarihi, ekip büyüklüğü) uydurmak dürüstlük
    guardrail'ini veri katmanında delerdi. Yer tutucu bırakmak doğru davranış;
    bu kural onu CV'ye sızmadan yakalar.
    """
    for path, text in iter_prose(resume):
        for match in _PLACEHOLDER.finditer(strip_html(text)):
            yield Finding(
                "error",
                "content.placeholder",
                path,
                f"Doldurulmamış yer tutucu: {match.group()}",
                "Gerçek değeri yaz — tahmin etme, süper set doğruluk kaynağı.",
            )


def check_tags(resume: Resume, vocabulary: TagVocabulary) -> Iterator[Finding]:
    """Etiketler kontrollü sözlükten mi, ve etiketsiz madde var mı?"""
    for section_name in Sections.model_fields:
        for item in getattr(resume.sections, section_name).items:
            if item.x_meta and (unknown := vocabulary.unknown(item.x_meta.tags)):
                yield Finding(
                    "error",
                    "tags.unknown",
                    f"{section_name}/{item.id}",
                    f"Sözlükte olmayan etiket(ler): {unknown}",
                    "`data/tags.yaml`'a ekle ya da kanonik yazımı kullan.",
                )

    for owner_id, bullet in resume.iter_bullets():
        where = f"{owner_id}/{bullet.id}"
        if bullet.x_meta is None or not bullet.x_meta.tags:
            yield Finding(
                "warning",
                "bullets.untagged",
                where,
                "Maddenin etiketi yok — ön filtre bunu asla seçemez.",
                "`x_meta.tags` ekle; boş etiket = ölü madde.",
            )
            continue
        if unknown := vocabulary.unknown(bullet.x_meta.tags):
            yield Finding(
                "error",
                "tags.unknown",
                where,
                f"Sözlükte olmayan etiket(ler): {unknown}",
                "`data/tags.yaml`'a ekle ya da kanonik yazımı kullan.",
            )


def check_ids(resume: Resume) -> Iterator[Finding]:
    """Öğe ve madde id'leri tekil olmalı — uyarlama adımı bunlarla konuşuyor."""
    item_ids: list[str] = []
    for section_name in Sections.model_fields:
        item_ids += [item.id for item in getattr(resume.sections, section_name).items]

    for item_id, count in Counter(item_ids).items():
        if count > 1:
            yield Finding("error", "ids.duplicate", f"item/{item_id}", f"{count} kez geçiyor.")

    bullet_ids = [bullet.id for _, bullet in resume.iter_bullets()]
    for bullet_id, count in Counter(bullet_ids).items():
        if count > 1:
            yield Finding("error", "ids.duplicate", f"bullet/{bullet_id}", f"{count} kez geçiyor.")

    for owner_id, bullet in resume.iter_bullets():
        if not bullet.id.strip():
            yield Finding(
                "error", "ids.missing", owner_id, "Id'siz madde — seçilemez ve raporlanamaz."
            )


def check_superset_size(resume: Resume) -> Iterator[Finding]:
    """Issue 03 kabul kriterleri: yeterince deneyim, madde ve etiketli yetenek."""
    experiences = resume.sections.experience.items
    if len(experiences) < MIN_EXPERIENCES:
        yield Finding(
            "warning",
            "superset.experiences",
            "sections.experience",
            f"{len(experiences)} deneyim var, en az {MIN_EXPERIENCES} bekleniyor.",
        )

    for item in experiences:
        if len(item.x_bullets) < MIN_BULLETS_PER_EXPERIENCE:
            yield Finding(
                "warning",
                "superset.bullets",
                f"experience/{item.id}",
                f"{len(item.x_bullets)} madde varyantı var, en az "
                f"{MIN_BULLETS_PER_EXPERIENCE} bekleniyor.",
                "Süper setin anlamı bu: aynı iş için farklı vitrinler "
                "(AI odaklı / backend odaklı).",
            )

    tagged_skills = [
        item for item in resume.sections.skills.items if item.x_meta and item.x_meta.tags
    ]
    if len(tagged_skills) < MIN_TAGGED_SKILLS:
        yield Finding(
            "warning",
            "superset.skills",
            "sections.skills",
            f"{len(tagged_skills)} etiketli yetenek var, en az {MIN_TAGGED_SKILLS} bekleniyor.",
        )


def check_language_coverage(resume: Resume) -> Iterator[Finding]:
    """Her deneyimin İngilizce madde varyantı var mı? (TODO.md §1 "İngilizce CV versiyonu")"""
    for item in resume.sections.experience.items:
        if not item.x_bullets:
            continue
        languages = {b.x_meta.lang for b in item.x_bullets if b.x_meta and b.x_meta.lang}
        for missing in ("tr", "en"):
            if missing not in languages:
                yield Finding(
                    "warning",
                    "lang.coverage",
                    f"experience/{item.id}",
                    f"{missing.upper()} madde varyantı yok.",
                    "Uzaktan/uluslararası ilanlar için EN varyantı gerekiyor.",
                )


def _i18n_owners(resume: Resume) -> Iterator[tuple[str, object]]:
    """`x_i18n` taşıyabilen her nesne, raporlanabilir yoluyla."""
    yield "basics", resume.basics
    for section_name in Sections.model_fields:
        for item in getattr(resume.sections, section_name).items:
            yield f"{section_name}/{item.id}", item
            for role in getattr(item, "roles", []):
                yield f"{section_name}/{item.id}/{role.id}", role


def check_i18n_fields(resume: Resume) -> Iterator[Finding]:
    """`x_i18n` içindeki alan adları modelde gerçekten var mı?

    Çeviri elle yazılıyor ve yanlış yazılmış bir alan adı (`possition`) hiçbir
    hata vermeden hiçbir şey yapmıyor — CV İngilizce çıkıyor, unvan Türkçe
    kalıyor ve sebebi görünmüyor. Kural o sessiz başarısızlığı sese çeviriyor.
    """
    for where, owner in _i18n_owners(resume):
        translations = getattr(owner, "x_i18n", None) or {}
        for language, fields in translations.items():
            for field in fields:
                if field not in type(owner).model_fields:
                    yield Finding(
                        "error",
                        "content.unknown_i18n_field",
                        f"{where}/x_i18n/{language}",
                        f"`{field}` bu öğede yok — çeviri uygulanmayacak.",
                        f"Alan adını düzelt; geçerli olanlar: {sorted(type(owner).model_fields)}.",
                    )
                elif not isinstance(getattr(owner, field, None), str):
                    yield Finding(
                        "error",
                        "content.unknown_i18n_field",
                        f"{where}/x_i18n/{language}",
                        f"`{field}` metin alanı değil — yalnızca metinler çevrilebilir.",
                        "Çeviri tablosundan çıkar.",
                    )


def check_bullet_roles(resume: Resume) -> Iterator[Finding]:
    """Maddenin işaret ettiği rol gerçekten var mı?

    Yazım hatası ya da silinmiş bir rol maddeyi kaybettirmiyor (en güncel role
    düşüyor, `render_json._attach_bullets`) ama sessizce yanlış yere koyuyor.
    """
    for item in resume.sections.experience.items:
        known = {role.id for role in item.roles}
        for bullet in item.x_bullets:
            if bullet.role and bullet.role not in known:
                yield Finding(
                    "error",
                    "bullets.unknown_role",
                    f"experience/{item.id}/{bullet.id}",
                    f"`role: {bullet.role}` bu deneyimde yok.",
                    f"Geçerli rol id'leri: {sorted(known) or 'yok'}.",
                )


def check_impact(resume: Resume) -> Iterator[Finding]:
    """Ölçülebilir etki eksik olan maddeler. (TODO.md §1 son madde)

    `info` seviyesinde: her madde sayı taşıyamaz, ve bu kabul kriterini bloke etmemeli.
    """
    missing = [
        f"{owner}/{bullet.id}"
        for owner, bullet in resume.iter_bullets()
        if not (bullet.x_meta and bullet.x_meta.impact)
    ]
    if missing:
        yield Finding(
            "info",
            "impact.missing",
            "x_bullets",
            f"{len(missing)} maddede `x_meta.impact` yok.",
            "Mümkün olan her maddeye sayı ekle ('X saatlik videoyu Y dakikada işleyen pipeline').",
        )


def check_schema(resume: Resume) -> Iterator[Finding]:
    """Dump canlı Reactive Resume şemasına uyuyor mu?"""
    payload = resume.model_dump(mode="json", exclude_none=True)
    validator = Draft202012Validator(rxresume_schema())
    for error in sorted(validator.iter_errors(payload), key=str):
        where = ".".join(str(part) for part in error.path) or "<root>"
        yield Finding("error", "schema", where, error.message)


# --------------------------------------------------------------------------- #
# Giriş noktası
# --------------------------------------------------------------------------- #

CHECKS = (
    check_schema,
    check_email,
    check_visible_profile_urls,
    check_education_degree,
    check_ligatures,
    check_paren_spacing,
    check_placeholders,
    check_ids,
    check_superset_size,
    check_language_coverage,
    check_i18n_fields,
    check_bullet_roles,
    check_impact,
)


def lint(resume: Resume, vocabulary: TagVocabulary | None = None) -> LintReport:
    """Tüm kuralları çalıştırır."""
    vocab = vocabulary or default_vocabulary()

    findings: list[Finding] = []
    for check in CHECKS:
        findings.extend(check(resume))
    findings.extend(check_tags(resume, vocab))

    bullets = resume.iter_bullets()
    stats = {
        "deneyim": len(resume.sections.experience.items),
        "proje": len(resume.sections.projects.items),
        "yetenek": len(resume.sections.skills.items),
        "etiketli yetenek": sum(
            1 for s in resume.sections.skills.items if s.x_meta and s.x_meta.tags
        ),
        "madde": len(bullets),
        "etiketsiz madde": sum(1 for _, b in bullets if not (b.x_meta and b.x_meta.tags)),
        "etkisi olan madde": sum(1 for _, b in bullets if b.x_meta and b.x_meta.impact),
        "EN madde": sum(1 for _, b in bullets if b.x_meta and b.x_meta.lang == "en"),
    }
    return LintReport(findings=findings, stats=stats)


# --------------------------------------------------------------------------- #
# Otomatik düzeltme
# --------------------------------------------------------------------------- #


def fix_ligatures(raw: str) -> tuple[str, int]:
    """Ham JSON metnindeki ligatürleri değiştirir; `(yeni metin, değişiklik sayısı)`.

    Ham metin üzerinde çalışır çünkü tek amacı karakter değişimi — modelden geçirmek
    biçimlendirmeyi ve alan sırasını gereksiz yere değiştirirdi.
    """
    fixed = raw
    count = 0
    for char, replacement in LIGATURES.items():
        occurrences = fixed.count(char)
        if occurrences:
            fixed = fixed.replace(char, replacement)
            count += occurrences
    return fixed, count
