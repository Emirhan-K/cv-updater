"""Reactive Resume v2 JSON şeması + `master.json` uzantıları.

Modeller **gerçek rxresu.me export'undan** türetildi, dokümante edilen şemadan değil
(bkz. `docs/SCHEMA.md`). Canlı şema: https://rxresu.me/schema.json — anlık görüntüsü
`tests/fixtures/rxresume.schema.json` altında; testler ağa çıkmaz.

İki uzantı var, ikisi de `x_` önekli:

* `x_meta`  — her öğede opsiyonel; ön filtrenin (issue 06) puanladığı etiketler ve meta.
* `x_bullets` — deneyim öğelerinde; madde granülerliğinde seçim yapabilmek için.

Reactive Resume bilinmeyen alanları yok sayar (canlı şemada hiçbir yerde
`additionalProperties: false` yok), bu yüzden `x_*` alanları import'u bozmaz.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# `extra="allow"`: rxresu.me yeni alan eklediğinde export'u kaybetmeden round-trip
# yapabilelim diye. Bilinmeyen alanlar korunur ve tekrar dump edilir.
_Base = ConfigDict(extra="allow")


# --------------------------------------------------------------------------- #
# Uzantılar (master.json'a özel — Reactive Resume bunları yok sayar)
# --------------------------------------------------------------------------- #

Seniority = Literal["intern", "junior", "mid", "senior", "lead"]
Lang = Literal["tr", "en"]


class XMeta(BaseModel):
    """Ön filtrenin (issue 06) okuduğu seçim metası."""

    model_config = _Base

    tags: list[str] = Field(
        default_factory=list,
        description="`data/tags.yaml` içindeki kontrollü sözlükten etiketler. "
        "Boşsa bu öğe hiçbir zaman seçilemez.",
    )
    impact: str | None = Field(
        default=None,
        description="Ölçülebilir etki, tek cümle. Örn: '8 saatlik videoyu 12 dakikada "
        "işleyen pipeline'.",
    )
    seniority: Seniority | None = None
    lang: Lang | None = Field(
        default=None,
        description="Bu varyantın dili. TR ve EN varyantları aynı süper sette yaşar.",
    )
    always_include: bool = Field(
        default=False,
        description="True ise puanlamadan bağımsız olarak her zaman seçilir.",
    )


class XBullet(BaseModel):
    """Deneyim maddesi — uyarlama adımı bu `id`'ler üzerinden konuşur."""

    model_config = _Base

    id: str = Field(description="Kararlı, insan tarafından yazılan id. UUID olmak zorunda değil.")
    text: str = Field(min_length=1)
    role: str = Field(
        default="",
        description="Maddenin ait olduğu rolün id'si (`ExperienceItem.roles[].id`). "
        "Boş bırakılırsa madde en güncel role yazılır — terfi taşımayan "
        "deneyimlerde alan zaten anlamsız.",
    )
    x_meta: XMeta | None = None


# --------------------------------------------------------------------------- #
# Ortak parçalar
# --------------------------------------------------------------------------- #


class Website(BaseModel):
    model_config = _Base

    url: str = ""
    label: str = ""
    inlineLink: bool = False


class CustomField(BaseModel):
    model_config = _Base

    id: str
    icon: str = ""
    text: str = ""
    link: str = ""


#: Dile göre değişen alanların çevirisi: dil kodu → alan adı → değer.
#:
#: Maddeler ve özet iki dilde yaşıyor (`x_meta.lang` ile ayrı varyantlar), ama
#: **unvan, ünvan derecesi ve dil seviyesi gibi tek örnekli alanlar** öyle
#: yaşayamıyor: bir deneyimin bir `position`'ı var. İlk İngilizce CV'de sonuç
#: buydu — maddeler İngilizce, üstlerindeki "Stajyer Yazılım Mühendisi" Türkçe.
#:
#: Burada tutulan şey **çeviri, veri değil**: aynı olgunun başka dildeki
#: yazımı. Bu yüzden `x_meta`ya değil ayrı bir alana konuyor ve modele hiç
#: gösterilmiyor — LLM'in unvan alanına dokunmaması kuralı (`models/tailor.py`)
#: aynen geçerli. Çeviriyi süper seti yazan kişi yapar.
#:
#: Örnek: `{"en": {"position": "Software Engineer (AI & Computer Vision)"}}`
I18n = dict[str, dict[str, str]]


class Item(BaseModel):
    """Her bölüm öğesinin taşıdığı ortak alanlar."""

    model_config = _Base

    id: str
    hidden: bool = False
    x_meta: XMeta | None = None
    x_i18n: I18n = Field(default_factory=dict, description="Dil kodu → alan adı → çeviri.")


# --------------------------------------------------------------------------- #
# Bölüm öğeleri
# --------------------------------------------------------------------------- #


class Role(BaseModel):
    """Aynı şirket altındaki tek bir unvan satırı (Preunec terfisi bunu kullanır)."""

    model_config = _Base

    id: str
    position: str = ""
    period: str = ""
    description: str = ""
    x_meta: XMeta | None = None
    x_i18n: I18n = Field(default_factory=dict, description="Dil kodu → alan adı → çeviri.")


class ExperienceItem(Item):
    company: str
    position: str = ""
    location: str = ""
    period: str = ""
    website: Website = Field(default_factory=Website)
    description: str = ""
    roles: list[Role] = Field(default_factory=list)
    x_bullets: list[XBullet] = Field(
        default_factory=list,
        description="Madde varyantları. HTML `description`, seçilen id'lerden issue 08'de "
        "derlenir.",
    )


class EducationItem(Item):
    school: str
    degree: str = ""
    area: str = ""
    grade: str = ""
    location: str = ""
    period: str = ""
    website: Website = Field(default_factory=Website)
    description: str = ""


class SkillItem(Item):
    name: str
    icon: str = ""
    iconColor: str = ""
    proficiency: str = ""
    level: int = 0
    keywords: list[str] = Field(default_factory=list)


class ProjectItem(Item):
    name: str
    period: str = ""
    website: Website = Field(default_factory=Website)
    description: str = ""
    x_bullets: list[XBullet] = Field(default_factory=list)


class LanguageItem(Item):
    language: str
    fluency: str = ""
    level: int = 0


class ProfileItem(Item):
    network: str
    username: str = ""
    icon: str = ""
    iconColor: str = ""
    website: Website = Field(default_factory=Website)


class InterestItem(Item):
    name: str
    icon: str = ""
    iconColor: str = ""
    keywords: list[str] = Field(default_factory=list)


class AwardItem(Item):
    title: str
    awarder: str = ""
    date: str = ""
    website: Website = Field(default_factory=Website)
    description: str = ""


class CertificationItem(Item):
    title: str
    issuer: str = ""
    date: str = ""
    website: Website = Field(default_factory=Website)
    description: str = ""


class PublicationItem(Item):
    title: str
    publisher: str = ""
    date: str = ""
    website: Website = Field(default_factory=Website)
    description: str = ""


class VolunteerItem(Item):
    organization: str
    location: str = ""
    period: str = ""
    website: Website = Field(default_factory=Website)
    description: str = ""


class ReferenceItem(Item):
    name: str
    position: str = ""
    phone: str = ""
    website: Website = Field(default_factory=Website)
    description: str = ""


# --------------------------------------------------------------------------- #
# Bölümler
# --------------------------------------------------------------------------- #


class Section[ItemT: Item](BaseModel):
    model_config = _Base

    title: str = ""
    icon: str = ""
    columns: int = 1
    hidden: bool = False
    keepTogether: bool = False
    startOnNewPage: bool = False
    items: list[ItemT] = Field(default_factory=list)


class Sections(BaseModel):
    model_config = _Base

    profiles: Section[ProfileItem] = Field(default_factory=Section[ProfileItem])
    experience: Section[ExperienceItem] = Field(default_factory=Section[ExperienceItem])
    education: Section[EducationItem] = Field(default_factory=Section[EducationItem])
    projects: Section[ProjectItem] = Field(default_factory=Section[ProjectItem])
    skills: Section[SkillItem] = Field(default_factory=Section[SkillItem])
    languages: Section[LanguageItem] = Field(default_factory=Section[LanguageItem])
    interests: Section[InterestItem] = Field(default_factory=Section[InterestItem])
    awards: Section[AwardItem] = Field(default_factory=Section[AwardItem])
    certifications: Section[CertificationItem] = Field(default_factory=Section[CertificationItem])
    publications: Section[PublicationItem] = Field(default_factory=Section[PublicationItem])
    volunteer: Section[VolunteerItem] = Field(default_factory=Section[VolunteerItem])
    references: Section[ReferenceItem] = Field(default_factory=Section[ReferenceItem])


# --------------------------------------------------------------------------- #
# Üst düzey bloklar
# --------------------------------------------------------------------------- #


class Picture(BaseModel):
    model_config = _Base

    hidden: bool = True
    url: str = ""
    size: float = 64
    rotation: float = 0
    aspectRatio: float = 1
    borderRadius: float = 0
    borderColor: str = "rgba(0, 0, 0, 0.5)"
    borderWidth: float = 0
    shadowColor: str = "rgba(0, 0, 0, 0.5)"
    shadowWidth: float = 0


class Basics(BaseModel):
    model_config = _Base

    name: str = ""
    headline: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    website: Website = Field(default_factory=Website)
    customFields: list[CustomField] = Field(default_factory=list)
    x_i18n: I18n = Field(default_factory=dict, description="Dil kodu → alan adı → çeviri.")


class Summary(BaseModel):
    model_config = _Base

    title: str = ""
    icon: str = ""
    columns: int = 1
    hidden: bool = False
    keepTogether: bool = False
    startOnNewPage: bool = False
    content: str = Field(default="", description="HTML string.")
    #: Özetin başka dildeki hali. Şema tek bir `summary` tutuyor, o yüzden EN
    #: varyantı maddeler gibi `x_meta.lang` ile yan yana yaşayamıyor.
    #:
    #: Normalde uyarlanmış CV'nin özetini model yazar (`TailorPlan.summary`,
    #: ilanın dilinde) ve buradaki metin yalnızca **yedek**: plan bir özet
    #: üretemezse sayfaya bu düşüyor. Yedeğin tek dilde olması İngilizce bir
    #: çalıştırmada içeriği İngilizce, özeti Türkçe bir CV'ye yol açıyordu.
    x_i18n: I18n = Field(default_factory=dict, description="Dil kodu → alan adı → çeviri.")


class LayoutPage(BaseModel):
    model_config = _Base

    fullWidth: bool = False
    main: list[str] = Field(default_factory=list)
    sidebar: list[str] = Field(default_factory=list)


class Layout(BaseModel):
    model_config = _Base

    sidebarWidth: float = 35
    pages: list[LayoutPage] = Field(default_factory=lambda: [LayoutPage(fullWidth=True)])


class Page(BaseModel):
    model_config = _Base

    gapX: float = 4
    gapY: float = 6
    marginX: float = 14
    marginY: float = 12
    format: Literal["a4", "letter", "free-form"] = "a4"
    locale: str = "tr-TR"
    hideLinkUnderline: bool = False
    hideIcons: bool = False
    hideSectionIcons: bool = False


class LevelDesign(BaseModel):
    model_config = _Base

    icon: str = "star"
    type: Literal[
        "hidden", "circle", "square", "rectangle", "rectangle-full", "progress-bar", "icon"
    ] = "circle"


class Colors(BaseModel):
    model_config = _Base

    primary: str = "rgb(0, 0, 0)"
    text: str = "rgba(0, 0, 0, 1)"
    background: str = "rgba(255, 255, 255, 1)"


class Design(BaseModel):
    model_config = _Base

    level: LevelDesign = Field(default_factory=LevelDesign)
    colors: Colors = Field(default_factory=Colors)


class Font(BaseModel):
    model_config = _Base

    fontFamily: str = "IBM Plex Serif"
    fontWeights: list[str] = Field(default_factory=lambda: ["400"])
    fontSize: float = 11
    lineHeight: float = 1.5


class Typography(BaseModel):
    model_config = _Base

    body: Font = Field(default_factory=Font)
    heading: Font = Field(default_factory=lambda: Font(fontWeights=["600"], fontSize=14))


Template = Literal[
    "azurill",
    "bronzor",
    "chikorita",
    "ditgar",
    "ditto",
    "gengar",
    "glalie",
    "kakuna",
    "lapras",
    "leafish",
    "meowth",
    "onyx",
    "pikachu",
    "rhyhorn",
    "scizor",
]


class Metadata(BaseModel):
    model_config = _Base

    template: Template = "onyx"
    layout: Layout = Field(default_factory=Layout)
    page: Page = Field(default_factory=Page)
    design: Design = Field(default_factory=Design)
    typography: Typography = Field(default_factory=Typography)
    notes: str = ""
    styleRules: list[Any] = Field(default_factory=list)


class Resume(BaseModel):
    """Reactive Resume v2 CV'si. `master.json` bunun `x_*` alanlarıyla genişletilmiş hali."""

    model_config = _Base

    picture: Picture = Field(default_factory=Picture)
    basics: Basics = Field(default_factory=Basics)
    summary: Summary = Field(default_factory=Summary)
    sections: Sections = Field(default_factory=Sections)
    customSections: list[dict[str, Any]] = Field(default_factory=list)
    metadata: Metadata = Field(default_factory=Metadata)

    # -- kolaylık erişimleri ------------------------------------------------ #

    def iter_bullets(self) -> list[tuple[str, XBullet]]:
        """`(deneyim id'si, madde)` çiftleri — ön filtre ve lint bunu dolaşır."""
        pairs: list[tuple[str, XBullet]] = []
        for exp in self.sections.experience.items:
            pairs.extend((exp.id, bullet) for bullet in exp.x_bullets)
        for proj in self.sections.projects.items:
            pairs.extend((proj.id, bullet) for bullet in proj.x_bullets)
        return pairs

    def to_json_dict(self) -> dict[str, Any]:
        """Reactive Resume'a import edilebilir sözlük (None alanlar düşer)."""
        return self.model_dump(mode="json", exclude_none=True)
