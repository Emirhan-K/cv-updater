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
from cv_updater.models.tailor import SelectedBullet, TailorPlan
from cv_updater.periods import translate_period
from cv_updater.steps.score import parse_period_end

TAILORED_FILENAME = "tailored.json"

#: `metadata.page.locale` — ATS ve PDF tarafı dili buradan okuyor.
LOCALES: dict[str, str] = {"tr": "tr-TR", "en": "en-US"}

#: Bölüm başlıkları CV'nin dilinde olmalı. Süper set tek dilde yazıldığı için
#: (`master.json` Türkçe) İngilizce bir çalıştırmada başlıklar Türkçe kalıyordu:
#: içerik İngilizce, üstündeki "Özet" Türkçe. `steps/render_pdf.SECTION_TITLES`
#: aynı işi RenderCV tarafında yapıyor — orada başlık sözlük anahtarından
#: geldiği için tablo ayrı duruyor, ama **isimler birebir aynı olmalı**.
SECTION_TITLES: dict[str, dict[Language, str]] = {
    "summary": {"tr": "Özet", "en": "Summary"},
    "profiles": {"tr": "Profiller", "en": "Profiles"},
    "experience": {"tr": "Deneyim", "en": "Experience"},
    "education": {"tr": "Eğitim", "en": "Education"},
    "projects": {"tr": "Projeler", "en": "Projects"},
    "skills": {"tr": "Yetenekler", "en": "Skills"},
    "languages": {"tr": "Diller", "en": "Languages"},
    "certifications": {"tr": "Sertifikalar", "en": "Certifications"},
    "awards": {"tr": "Ödüller", "en": "Awards"},
    "publications": {"tr": "Yayınlar", "en": "Publications"},
    "volunteer": {"tr": "Gönüllülük", "en": "Volunteering"},
    "interests": {"tr": "İlgi Alanları", "en": "Interests"},
    "references": {"tr": "Referanslar", "en": "References"},
}

#: `period` taşıyan bölümler — tarih çevirisi (issue 14) bunları dolaşıyor.
_PERIOD_SECTIONS = ("experience", "education", "projects", "volunteer")
#: `date` taşıyan bölümler; tek uçlu tarih ama çeviri kuralı aynı.
_DATE_SECTIONS = ("certifications", "awards", "publications")

# --- Ligatür savunması ----------------------------------------------------- #
# TODO.md §1'in kritik bulgusu: "fine-tuning" PDF'te "ﬁne-tuning" olup ATS'in
# aramasını kaçırıyor. **Bu bulgu kaynak metinle ilgiliydi** — CV'de düz `ﬁ`
# karakteri duruyordu. `lint.fix_ligatures` + `validate` o katmanı kapatıyor ve
# asıl iş orada bitiyor.
#
# Render katmanı ölçüldü (2026-08-07, iki gerçek PDF üzerinde): Reactive Resume
# PDF'i `@react-pdf/renderer` ile üretiyor ve **`liga` özelliğini uygulamıyor.**
# Ne IBM Plex Serif'le ne Tinos'la üretilen PDF'te tek bir ligatür glifi var
# (ToUnicode'da çok karakterli eşleme sıfır); `pypdf` ve `pdfplumber` ikisi de
# bütün f-kelimelerini eksiksiz çıkarıyor.
#
# Yani font seçimi **gözlenen bir hatayı düzeltmiyor, bir risk sınıfını
# kapatıyor**: react-pdf ileride `liga`'yı uygulamaya başlarsa ya da render
# yolu değişirse (issue 11, RenderCV → Typst) ligatür glifi olmayan bir fontta
# sorun doğamaz. Bedeli sıfır olduğu için varsayılan yapıldı.
#
#: Fontların GSUB tabloları `fontTools` ile ölçüldü (2026-08-07): `Tinos` ve
#: `Inter` ne `liga` özelliği ne de ligatür glifi taşıyor, `Arimo`'da özellik
#: yok. Üçü de Türkçe karakterlerin tamamını kapsıyor. Varsayılan Tinos: Times
#: New Roman metrik eşi, en konvansiyonel ATS fontu, ve süper setin serif
#: tercihini koruyor.
DEFAULT_FONT = "Tinos"

#: Ligatür üretemediği **ölçülmüş** fontlar. Başka bir font seçilirse uyarı
#: basılır — tahmine değil ölçüme dayanan bir liste.
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
   Not: PDF tarafında etkisi ölçülemedi — react-pdf zaten ligatür üretmiyor,
   dolayısıyla kapatılacak bir şey yoktu. Web önizlemesi için duruyor;
   PDF'teki koruma ligatürsüz font seçiminden geliyor. */
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
    _render_section_titles(doc, target)
    _render_periods(doc, target)
    _apply_i18n(doc, target)
    _render_metadata(doc, target, font)

    return doc


def _translate_fields(owner: Any, language: Language) -> None:
    """Bir öğenin `x_i18n` çevirilerini hedef dilde uygular.

    İki kısıt var:

    * Yalnızca **modelde gerçekten var olan** metin alanları yazılıyor. Yanlış
      yazılmış bir alan adı (`possition`) sessizce yeni bir alan uydurmasın
      diye; `validate` bunu ayrıca `content.unknown_i18n_field` ile raporluyor.
    * **Boş alan çevrilmiyor.** Boş bir alan "burada gösterilecek bir şey yok"
      demek ve derlemenin bir kararı olabilir: rol taşıyan deneyimlerde öğe
      seviyesindeki unvan bilerek siliniyor (`_attach_bullets`). Çeviri onu
      geri yazsaydı Preunec'te "AI Team Lead" yine iki kez görünürdü — ilk
      sürümde tam olarak bu oldu.
    """
    for field, value in (getattr(owner, "x_i18n", None) or {}).get(language, {}).items():
        if field in type(owner).model_fields and getattr(owner, field, None):
            if isinstance(getattr(owner, field), str):
                setattr(owner, field, value)


def _apply_i18n(doc: Resume, language: Language) -> None:
    """Tek örnekli alanların (unvan, derece, dil seviyesi) çevirisini uygular.

    Madde ve özet iki dilde yaşıyor, unvan yaşamıyor: bir deneyimin bir
    `position`'ı var. İlk İngilizce CV'de maddeler İngilizceydi ama üstlerinde
    "Stajyer Yazılım Mühendisi" yazıyordu. Çeviri süper sette elle yazılıyor
    (`x_i18n`); burada yapılan iş yalnızca doğru dili seçmek — **modele
    sorulmuyor**, unvan alanı guardrail'in dokunulmaz tuttuğu alanlardan.
    """
    _translate_fields(doc.basics, language)
    for section_name in Sections.model_fields:
        for item in getattr(doc.sections, section_name).items:
            _translate_fields(item, language)
            for role in getattr(item, "roles", []):
                _translate_fields(role, language)


def _render_section_titles(doc: Resume, language: Language) -> None:
    """Bölüm başlıklarını CV'nin diline çevirir.

    **Yalnızca dolu başlıklar çevriliyor.** Boş bir başlık Reactive Resume'da
    "bu bölümü gösterme" değil, "varsayılanı kullan" demek; oraya metin yazmak
    süper sette olmayan bir bölümü sayfaya çağırabilirdi.
    """
    if (title := SECTION_TITLES["summary"].get(language)) and doc.summary.title:
        doc.summary.title = title

    for name, titles in SECTION_TITLES.items():
        if name == "summary":
            continue
        section = getattr(doc.sections, name, None)
        if section is None or not section.title:
            continue
        if translated := titles.get(language):
            section.title = translated


def _render_periods(doc: Resume, language: Language) -> None:
    """Tarihleri CV'nin diline çevirir — `cv_updater.periods`.

    Süper set tarihleri tek dilde tutuyor (ve tutmalı: tarih bir olgu, varyantı
    yok). İlk İngilizce CV'de bütün sağ sütun Türkçe çıkmıştı. Çeviri
    deterministik ve sözlük tabanlı; tanınmayan bir dönem olduğu gibi kalıyor.
    """
    for name in _PERIOD_SECTIONS:
        for item in getattr(doc.sections, name).items:
            item.period = translate_period(item.period, language)
            for role in getattr(item, "roles", []):
                role.period = translate_period(role.period, language)

    for name in _DATE_SECTIONS:
        for item in getattr(doc.sections, name).items:
            item.date = translate_period(item.date, language)


def _attach_bullets(item: ExperienceItem, selected: list[SelectedBullet]) -> None:
    """Maddeleri deneyimin doğru rolüne yazar.

    **Roller varsa maddeler öğe seviyesine yazılamaz.** Reactive Resume bir
    deneyimde `roles[]` doluysa öğenin kendi `description`'ını render etmiyor;
    her rolün kendi `description`'ına bakıyor (şema: "bu spesifik rolün
    açıklaması"). İlk üretilen CV'de Preunec — tek rol taşıyan deneyim —
    PDF'te maddesiz çıktı: `tailored.json`'da 4 madde duruyordu ama sayfada
    yalnızca unvan satırları görünüyordu.

    Dağıtım süper setteki `x_bullets[].role` alanına bakıyor. İkinci PDF'in
    bulgusu buydu: bütün maddeler en güncel role yığıldığı için terfi öncesi
    "Yazılım Mühendisi" satırı sayfada **başlığı olup gövdesi olmayan** bir
    satır olarak duruyordu. Artık her madde kendi rolünün altına gidiyor;
    `role` yazılmamış maddeler (terfi taşımayan deneyimlerin tamamı) en güncel
    role düşüyor, yani eski davranış varsayılan olarak korunuyor.
    """
    if not item.roles:
        item.description = bullet_list([bullet.rewritten for bullet in selected])
        return

    owner = {bullet.id: bullet.role for bullet in item.x_bullets}
    known = {role.id for role in item.roles}
    latest = max(item.roles, key=_role_sort_key)

    grouped: dict[str, list[str]] = {role.id: [] for role in item.roles}
    for bullet in selected:
        role_id = owner.get(bullet.source_id, "")
        grouped[role_id if role_id in known else latest.id].append(bullet.rewritten)

    for role in item.roles:
        role.description = bullet_list(grouped[role.id])

    # Öğe seviyesi bilerek boşaltılıyor: Reactive Resume bir gün ikisini birden
    # render ederse maddeler iki kez görünürdü.
    item.description = ""
    # Aynı sebeple unvan da: Reactive Resume öğenin `position`'ını **ve** rol
    # satırlarını birden basıyor, o yüzden Preunec'te "AI Team Lead" arka arkaya
    # iki kez görünüyordu. Roller varsa unvanı roller taşır; öğe satırında
    # şirket adı ve toplam süre kalır.
    item.position = ""


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
        _attach_bullets(item, entry.bullets)
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


def _render_typography(doc: Resume, font: str) -> None:
    """Fontu ligatürsüz olanla değiştirir ve **gerçekten var olan** ağırlıkları yükler.

    `fontWeights` "metin bu ağırlıkta yazılsın" demiyor; **hangi yüzlerin
    gömüleceğini** söylüyor. Reactive Resume şablonu şirket ve proje adlarını
    yarı kalın basıyor, ama o yüz gömülü değilse tarayıcı sessizce normale
    düşüyor.

    İlk sürüm her istenen ağırlığı en yakınına indirgiyordu: süper set gövde
    için `["400", "500"]` istiyor, Tinos yalnızca 400/700 taşıyor, 500 → 400'e
    iniyor ve liste `["400"]`e çöküyordu. **Ölçüldü** (2026-08-08, üretilen
    PDF): dosyada `Tinos-Bold` yalnızca ad ve bölüm başlıklarında geçiyor,
    şirket adlarının hepsi `Tinos-Regular`. Kalın yüz sayfaya hiç girmemişti.

    Bu yüzden artık indirgeme yok: kısıtlı bir fontta **taşıdığı ağırlıkların
    tamamı** yükleniyor. Bedeli birkaç KB, karşılığı şablonun kalın istediği
    her yerin gerçekten kalın çıkması.
    """
    available = FONT_WEIGHTS.get(font)
    for block in (doc.metadata.typography.body, doc.metadata.typography.heading):
        block.fontFamily = font
        if available:
            block.fontWeights = list(available)


def _render_metadata(doc: Resume, language: Language, font: str) -> None:
    """Dil, tipografi, ikonlar ve ligatür ayarları."""
    doc.metadata.page.locale = LOCALES.get(language, LOCALES["tr"])
    # İletişim satırındaki zarf/telefon ikonları kapatılıyor. ATS için gürültü:
    # ikon metin katmanında ya hiç yok (Reactive Resume onları SVG basıyor) ya
    # da özel kullanım alanından bir glif olarak çıkıyor. Bölüm ikonları süper
    # sette zaten kapalı; iletişim ikonları da aynı sebeple kapanıyor.
    doc.metadata.page.hideIcons = True
    doc.metadata.page.hideSectionIcons = True
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
