"""`tailored.json` derlemesi (issue 08) — aracın asıl ürünü.

`master.json` + doğrulanmış `TailorPlan` → Reactive Resume'a doğrudan import
edilebilen bir CV. **Bu dosyada LLM yok**; plan zaten doğrulandı (issue 07),
burada yapılan iş yalnızca derleme.

Derlemenin iki kuralı var:

1. **Modelden geçmeyen alan kopyalanır.** `basics`, eğitim, diller, şirket
   adları, unvanlar, tarihler, roller doğrudan `master.json`'dan gelir. Plan
   bu alanlara dokunamaz çünkü şemasında yoklar.
2. **Süper set çıktıya sızmaz.** `x_meta` ve `x_bullets` temizlenir: seçilmemiş
   72 maddenin tamamını taşıyan bir "uyarlanmış CV" ne uyarlanmış olurdu ne de
   paylaşılabilir.

Seçilmeyen öğeler silinmez, `hidden: true` yapılır — import'tan sonra elle geri
açmak, kaybolmuş bir maddeyi yeniden yazmaktan kolay.
"""

from __future__ import annotations

import html
import json
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from cv_updater.lint import LIGATURES, fix_ligatures
from cv_updater.models.candidates import CandidatePool
from cv_updater.models.job import Language
from cv_updater.models.resume import ExperienceItem, Item, Resume, Role, Sections
from cv_updater.models.tailor import TailorPlan
from cv_updater.steps.score import parse_period_end

TAILORED_FILENAME = "tailored.json"

#: `metadata.page.locale` — ATS ve PDF tarafı dili buradan okuyor.
LOCALES: dict[str, str] = {"tr": "tr-TR", "en": "en-US"}

# --- Ligatür savunması ----------------------------------------------------- #
# TODO.md §1'in kritik bulgusu: "fine-tuning" PDF'in metin katmanına tek bir
# ligatür glifi olarak gömülüyor, çıkarıldığında "fne-tuning" oluyor ve ATS
# aramayı kaçırıyor.
#
# Üç katman denendi, **yalnızca üçüncüsü işe yarıyor**:
#
# 1. Karakter temizliği (`fix_ligatures`) — kaynak metindeki ﬁ/ﬂ karakterlerini
#    açıyor. Gerekli ama yetersiz: sorun kaynakta değil, render'da.
# 2. `metadata.stylesheet` içinde `font-variant-ligatures: none` — **çalışmıyor.**
#    Reactive Resume PDF'i `@react-pdf/renderer` ile üretiyor, o da bu CSS
#    özelliğini tanımıyor. İlk gerçek PDF'te ligatürler aynen duruyordu.
#    Stil yine yazılıyor (web önizlemesinde işe yarar) ama PDF'i o kurtarmıyor.
# 3. **Ligatürü olmayan bir font.** Tek gerçek çözüm bu.
#
#: Fontların GSUB tabloları ölçüldü (2026-08-07): `Tinos` ve `Inter` ne `liga`
#: özelliği ne de ligatür glifi taşıyor, `Arimo`'da özellik yok. Üçü de Türkçe
#: karakterlerin tamamını kapsıyor. Varsayılan Tinos: Times New Roman metrik
#: eşi, en klasik ATS-güvenli font, ve süper setin serif tercihini koruyor.
DEFAULT_FONT = "Tinos"

#: Ligatür üretemediği ölçülmüş fontlar. Başka bir font seçilirse uyarı basılır.
LIGATURE_FREE_FONTS: frozenset[str] = frozenset({"Tinos", "Inter", "Arimo", "Cousine"})

#: Font başına gerçekten var olan ağırlıklar. Tinos yalnızca 400/700 taşıyor;
#: süper set 500 (gövde) ve 600 (başlık) istiyor, o yüzden eşleme gerekiyor —
#: yoksa Reactive Resume bilinmeyen ağırlıkta fontu bulamayıp geri düşüyor.
FONT_WEIGHTS: dict[str, tuple[str, ...]] = {
    "Tinos": ("400", "700"),
    "Cousine": ("400", "700"),
}

LIGATURE_CSS = """\
/* cv-updater: ATS'in "fine-tuning" araması "ﬁne-tuning"e takılmasın.
   Not: @react-pdf/renderer bu kuralı yok sayıyor — PDF'teki asıl koruma
   ligatürsüz font seçimi. Bu stil yalnızca web önizlemesi için. */
* {
  font-variant-ligatures: none;
  font-feature-settings: "liga" 0, "clig" 0, "dlig" 0, "hlig" 0;
}
"""

#: `metadata.stylesheet` şemadaki tek `additionalProperties: false` bloğu —
#: alan adları birebir bu üçü olmak zorunda.
_STYLESHEET_LANGUAGE_VERSION = 1

#: Çalıştırma başına kararlı ama benzersiz id üretmek için sabit ad alanı.
_ID_NAMESPACE = uuid.UUID("6f2a1f5e-3f6b-4a3a-9a2e-1c9d0b7e5a11")

_WHITESPACE = re.compile(r"\s+")


def new_id(run_id: str, source_id: str) -> str:
    """Çalıştırmaya özgü, kararlı bir öğe id'si.

    Reactive Resume import'ta id çakışmasını sevmiyor, o yüzden süper setin
    okunur id'leri ("exp-preunec") çıktıya taşınmıyor. UUID5 kullanılıyor:
    aynı çalıştırma iki kez derlenirse aynı id'ler çıkar, farklı çalıştırmalar
    çakışmaz — rastgele UUID testleri de belirsiz yapardı.
    """
    return str(uuid.uuid5(_ID_NAMESPACE, f"{run_id}:{source_id}"))


def bullet_list(texts: list[str]) -> str:
    """Madde listesini Reactive Resume'un beklediği HTML'e çevirir."""
    items = "".join(f"<li>{html.escape(_WHITESPACE.sub(' ', text).strip())}</li>" for text in texts)
    return f"<ul>{items}</ul>" if items else ""


def paragraph(text: str) -> str:
    """Özet paragrafı."""
    cleaned = _WHITESPACE.sub(" ", text).strip()
    return f"<p>{html.escape(cleaned)}</p>" if cleaned else ""


def _strip_extensions(item: Item) -> None:
    """`x_meta` / `x_bullets` çıktıya girmez — süper set gizli kalmalı."""
    item.x_meta = None
    if hasattr(item, "x_bullets"):
        item.x_bullets = []  # type: ignore[attr-defined]


def render(
    resume: Resume,
    plan: TailorPlan,
    pool: CandidatePool,
    *,
    language: Language | None = None,
    run_id: str = "",
    font: str = DEFAULT_FONT,
) -> Resume:
    """`master.json` + plan → import edilebilir `Resume`.

    Girdi `resume` değiştirilmez; derin kopya üzerinde çalışılır.
    `font` süper setin tipografisini ezer — varsayılanı ligatürsüz olduğu
    **ölçülmüş** bir font (bkz. `DEFAULT_FONT`).
    """
    doc = resume.model_copy(deep=True)
    target = language or pool.language
    stamp = run_id or pool.slug or "cv-updater"

    doc.summary.content = paragraph(plan.summary) or doc.summary.content
    doc.summary.hidden = False

    _render_experiences(doc, plan, stamp)
    _render_projects(doc, plan, pool, stamp)
    _render_skills(doc, plan, stamp)
    _render_profiles(doc, stamp)
    _renumber_remaining(doc, stamp)
    _render_metadata(doc, target, font)

    return doc


def _attach_bullets(item: ExperienceItem, texts: list[str]) -> None:
    """Maddeleri deneyimin doğru yerine yazar.

    **Roller varsa maddeler öğe seviyesine yazılamaz.** Reactive Resume bir
    deneyimde `roles[]` doluysa öğenin kendi `description`'ını render etmiyor;
    her rolün kendi `description`'ına bakıyor (şema: "bu spesifik rolün
    açıklaması"). İlk üretilen CV'de Preunec — tek rol taşıyan deneyim —
    PDF'te maddesiz çıktı: `tailored.json`'da 4 madde duruyordu ama sayfada
    yalnızca unvan satırları görünüyordu.

    Maddeler **en güncel role** yazılıyor. Terfi çizgisi CV'de değerli bir
    sinyal, onu düzleştirmek yerine korunuyor; hangi maddenin hangi role ait
    olduğu bilgisi süper sette olmadığı için hepsi güncel rolün altında
    toplanıyor.
    """
    markup = bullet_list(texts)
    if not item.roles:
        item.description = markup
        return

    latest = max(item.roles, key=_role_sort_key)
    latest.description = markup
    # Öğe seviyesi bilerek boşaltılıyor: Reactive Resume bir gün ikisini birden
    # render ederse maddeler iki kez görünürdü.
    item.description = ""


def _role_sort_key(role: Role) -> tuple[int, date]:
    """Rolleri bitiş tarihine göre sıralar; ayrıştırılamayan dönem en eskiye düşer."""
    end = parse_period_end(role.period)
    return (1, end) if end is not None else (0, date.min)


def _render_experiences(doc: Resume, plan: TailorPlan, stamp: str) -> None:
    """Deneyimleri plandaki sırayla derler; unvan ve tarihe dokunmaz."""
    chosen = {entry.source_id: entry for entry in plan.experiences}
    ordered: list = []

    for entry in plan.experiences:
        item = next((i for i in doc.sections.experience.items if i.id == entry.source_id), None)
        if item is None:
            continue
        _attach_bullets(item, [bullet.rewritten for bullet in entry.bullets])
        item.hidden = False
        ordered.append(item)

    # Planda geçmeyen deneyim silinmez, gizlenir: sonradan elle açılabilsin.
    for item in doc.sections.experience.items:
        if item.id not in chosen:
            item.hidden = True
            ordered.append(item)

    for item in ordered:
        for role in item.roles:
            role.id = new_id(stamp, role.id)
            role.x_meta = None
        item.id = new_id(stamp, item.id)
        _strip_extensions(item)

    doc.sections.experience.items = ordered


def _render_projects(doc: Resume, plan: TailorPlan, pool: CandidatePool, stamp: str) -> None:
    """Projeleri plandaki sırayla derler.

    Proje maddeleri **modelden geçmiyor**: ön filtrenin seçtiği metin kelimesi
    kelimesine giriyor. Guardrail'in en ucuz biçimi — yeniden ifade yoksa
    uydurma da yok.
    """
    selected = {entry.id: entry for entry in pool.projects}
    ordered: list = []

    for project_id in plan.project_ids:
        item = next((i for i in doc.sections.projects.items if i.id == project_id), None)
        if item is None:
            continue
        entry = selected.get(project_id)
        texts = [bullet.text for bullet in entry.selected_bullets] if entry else []
        item.description = bullet_list(texts) or item.description
        item.hidden = False
        ordered.append(item)

    for item in doc.sections.projects.items:
        if item.id not in set(plan.project_ids):
            item.hidden = True
            ordered.append(item)

    for item in ordered:
        item.id = new_id(stamp, item.id)
        _strip_extensions(item)

    doc.sections.projects.items = ordered


def _render_skills(doc: Resume, plan: TailorPlan, stamp: str) -> None:
    """Yetenekleri plandaki sırayla dizer; seçilmeyenler gizlenir."""
    chosen = list(plan.skill_ids)
    ordered: list = []

    for skill_id in chosen:
        item = next((i for i in doc.sections.skills.items if i.id == skill_id), None)
        if item is not None:
            item.hidden = False
            ordered.append(item)

    for item in doc.sections.skills.items:
        if item.id not in set(chosen):
            item.hidden = True
            ordered.append(item)

    for item in ordered:
        item.id = new_id(stamp, item.id)
        _strip_extensions(item)

    doc.sections.skills.items = ordered


def _render_profiles(doc: Resume, stamp: str) -> None:
    """Profil linklerinin **görünen metni** tam URL olmalı (TODO.md §1).

    Çoğu ATS gömülü hyperlink'i okumuyor; okuduğu şey `website.label`.
    """
    for item in doc.sections.profiles.items:
        if not item.website.label and item.website.url:
            item.website.label = item.website.url.removeprefix("https://").removeprefix("http://")
            item.website.label = item.website.label.removeprefix("www.").rstrip("/")
        item.id = new_id(stamp, item.id)
        _strip_extensions(item)


def _renumber_remaining(doc: Resume, stamp: str) -> None:
    """Kalan bölümler olduğu gibi kopyalanır, yalnızca id'leri tazelenir."""
    handled = {"experience", "projects", "skills", "profiles"}
    for section_name in Sections.model_fields:
        if section_name in handled:
            continue
        for item in getattr(doc.sections, section_name).items:
            item.id = new_id(stamp, item.id)
            _strip_extensions(item)

    for field in doc.basics.customFields:
        field.id = new_id(stamp, field.id)


def nearest_weight(weight: str, available: tuple[str, ...]) -> str:
    """İstenen ağırlığa en yakın **gerçekten var olan** ağırlık.

    Eşitlikte kalın olan seçilir: 500 ile 400/700 arasında kalırsa 400'e iner
    (gövde metni), ama 600 için 700 gelir (başlık) — okunabilirlik yönünde.
    """
    target = int(weight)
    return min(available, key=lambda value: (abs(int(value) - target), -int(value)))


def _render_typography(doc: Resume, font: str) -> None:
    """Fontu ligatürsüz olanla değiştirir ve ağırlıkları fontun taşıdıklarına indirger."""
    available = FONT_WEIGHTS.get(font)
    for block in (doc.metadata.typography.body, doc.metadata.typography.heading):
        block.fontFamily = font
        if available:
            block.fontWeights = list(
                dict.fromkeys(nearest_weight(weight, available) for weight in block.fontWeights)
            ) or [available[0]]


def _render_metadata(doc: Resume, language: Language, font: str) -> None:
    """Dil, tipografi ve ligatür ayarları."""
    doc.metadata.page.locale = LOCALES.get(language, LOCALES["tr"])
    _render_typography(doc, font)
    doc.metadata.stylesheet = {  # type: ignore[attr-defined]
        "mode": "legacy",
        "source": {"languageVersion": _STYLESHEET_LANGUAGE_VERSION, "text": LIGATURE_CSS},
        "applied": {"languageVersion": _STYLESHEET_LANGUAGE_VERSION, "text": LIGATURE_CSS},
    }


# --------------------------------------------------------------------------- #
# Kalıcılık
# --------------------------------------------------------------------------- #


def drop_extensions(payload: Any) -> Any:
    """`x_` önekli tüm alanları çıktıdan siler.

    `_strip_extensions` içeriği boşaltıyor ama anahtar (`"x_bullets": []`)
    kalıyordu. Reactive Resume bunu yok sayardı, ama dosya paylaşılan bir
    ürün: içinde anlamı olmayan bir uzantı alanı durmasın.
    """
    if isinstance(payload, dict):
        return {
            key: drop_extensions(value)
            for key, value in payload.items()
            if not key.startswith("x_")
        }
    if isinstance(payload, list):
        return [drop_extensions(value) for value in payload]
    return payload


def to_json(doc: Resume) -> str:
    """İçe aktarılabilir JSON metni. Ligatür karakterleri burada da temizlenir."""
    payload = drop_extensions(doc.model_dump(mode="json", exclude_none=True))
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    fixed, _ = fix_ligatures(raw)
    return fixed


def write_tailored(run_dir: Path, doc: Resume) -> Path:
    path = run_dir / TAILORED_FILENAME
    path.write_text(to_json(doc), encoding="utf-8", newline="")
    return path


def read_tailored(path: Path) -> Resume:
    return Resume.model_validate_json(path.read_text(encoding="utf-8"))


def has_ligatures(payload: str) -> list[str]:
    """Metinde kalan ligatür karakterleri — issue 08 kabul kriteri."""
    return sorted({char for char in payload if char in LIGATURES})
