"""Deterministik ön filtrenin çıktı şeması (issue 06).

`out/<slug>/candidates.json` bu modelle yazılır. Amacı iki türlü:

1. **LLM adımının girdisi.** Uyarlama adımı (issue 07) süper setin tamamını değil,
   burada kalan daraltılmış havuzu görür — hem ucuz hem de daha az serbestlik.
2. **Gerekçe kaydı.** Her maddenin skoru bileşenlerine ayrılmış halde duruyor;
   uyum raporu (issue 09) "bu madde neden seçildi" sorusunu LLM'e sormadan
   cevaplayabiliyor.

Buradaki hiçbir alan modelden gelmez. Tamamı `job.json` + `master.json` +
`tags.yaml` üzerinden hesaplanır, dolayısıyla aynı girdi her zaman aynı çıktıyı
verir (issue 06 kabul kriteri).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, computed_field

from cv_updater.models.job import Kind, Language

ItemKind = Literal["bullet", "skill", "project"]
CoverageStatus = Literal["covered", "indirect", "missing"]


class Weights(BaseModel):
    """Puanlama ağırlıkları — `docs/SCORING.md` bunların gerekçesini anlatıyor.

    Çalıştırma başına `candidates.json`'a yazılıyor: ağırlık değişince eski bir
    çalıştırmanın skoru neden farklıydı sorusu dosyadan cevaplanabilsin.
    """

    must: float = 3.0
    nice: float = 1.0
    #: İlanın etiketiyle aynı `tags.yaml` kategorisinde olmanın değeri.
    category: float = 0.25
    domain: float = 1.0
    seniority: float = 0.5
    recency: float = 1.0
    impact: float = 0.5


class ScoreBreakdown(BaseModel):
    """Tek bir maddenin skoru, bileşenlerine ayrılmış hali."""

    must: float = 0.0
    nice: float = 0.0
    category: float = 0.0
    domain: float = 0.0
    seniority: float = 0.0
    recency: float = 0.0
    impact: float = 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> float:
        return round(
            self.must
            + self.nice
            + self.category
            + self.domain
            + self.seniority
            + self.recency
            + self.impact,
            3,
        )


class ScoredItem(BaseModel):
    """Puanlanmış tek bir madde, yetenek ya da proje."""

    id: str
    kind: ItemKind
    #: Maddeyi taşıyan deneyim/proje id'si; yetenekte boş.
    owner_id: str | None = None
    text: str
    tags: list[str] = Field(default_factory=list)
    lang: str | None = None
    always_include: bool = False
    #: Hangi zorunlu / tercih edilen etiketler eşleşti — rapor bunu gösteriyor.
    matched_must: list[str] = Field(default_factory=list)
    matched_nice: list[str] = Field(default_factory=list)
    breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    #: Havuza girdi mi? (üst sınırlar ve eşik uygulandıktan sonra)
    selected: bool = False

    @property
    def score(self) -> float:
        return self.breakdown.total


class ExperienceCandidates(BaseModel):
    """Bir deneyimin (ya da projenin) hayatta kalan maddeleri.

    Unvan, şirket ve tarih burada **yalnızca bilgi olsun diye** duruyor; bu alanlar
    LLM'den geçmez, `tailored.json`'a doğrudan `master.json`'dan kopyalanır (issue 08).
    """

    id: str
    title: str
    subtitle: str = ""
    period: str = ""
    always_include: bool = False
    bullets: list[ScoredItem] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def score(self) -> float:
        """Deneyimin sıralama skoru: seçilen maddelerinin toplamı."""
        return round(sum(b.breakdown.total for b in self.bullets if b.selected), 3)

    @property
    def selected_bullets(self) -> list[ScoredItem]:
        return [b for b in self.bullets if b.selected]


class RequirementCoverage(BaseModel):
    """İlandaki tek bir gereksinimin CV'de karşılığı var mı?

    `covered` = seçilen bir madde bu gereksinimin etiketini taşıyor.
    `indirect` = madde yok ama bir yetenek ya da seçilmemiş bir madde taşıyor.
    `missing`  = süper sette hiçbir karşılığı yok.
    """

    index: int
    text: str
    kind: Kind
    tags: list[str] = Field(default_factory=list)
    status: CoverageStatus = "missing"
    bullet_ids: list[str] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list)


class RecallCheck(BaseModel):
    """Geri çağırma ağı — modelin **bildirmediği** ama ilanda geçen etiketler.

    İlan analizi (issue 05) fazladan terim uydurmayı yakalıyordu; bu kontrol ters
    yönü kapatıyor. `tags.yaml`'daki her kanonik terim ve alias ilan metninde
    aranır, modelin raporladıklarıyla farkı alınır. Ucuz bir modelle çalışırken
    "ne kaçırdım" sorusunun deterministik cevabı bu.
    """

    missed: list[str] = Field(default_factory=list)
    #: Etiket → ilan metninde onu tetikleyen terim.
    evidence: dict[str, str] = Field(default_factory=dict)


class TokenBudget(BaseModel):
    """Ön filtrenin LLM adımından ne kadar metin eksilttiği (issue 06 kabul kriteri)."""

    superset_chars: int = 0
    candidate_chars: int = 0
    superset_items: int = 0
    candidate_items: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def saved_ratio(self) -> float:
        """0.0 – 1.0 arası: süper sete kıyasla kısılan metin oranı."""
        if self.superset_chars <= 0:
            return 0.0
        return round(1 - self.candidate_chars / self.superset_chars, 3)


class CandidatePool(BaseModel):
    """`out/<slug>/candidates.json` — ön filtrenin tam çıktısı."""

    slug: str = ""
    #: CV'nin üretileceği dil. İlanın dili, `--lang` ile ezilebilir (issue 08).
    language: Language = "tr"
    must_tags: list[str] = Field(default_factory=list)
    nice_tags: list[str] = Field(default_factory=list)
    #: İlanda geçen ama süper sette hiç karşılığı olmayan etiketler.
    missing_tags: list[str] = Field(default_factory=list)

    experiences: list[ExperienceCandidates] = Field(default_factory=list)
    projects: list[ExperienceCandidates] = Field(default_factory=list)
    skills: list[ScoredItem] = Field(default_factory=list)

    coverage: list[RequirementCoverage] = Field(default_factory=list)
    recall: RecallCheck = Field(default_factory=RecallCheck)
    budget: TokenBudget = Field(default_factory=TokenBudget)
    weights: Weights = Field(default_factory=Weights)

    scored_at: datetime = Field(default_factory=datetime.now)

    # -- kolaylık erişimleri ------------------------------------------------ #

    @property
    def selected_skills(self) -> list[ScoredItem]:
        return [s for s in self.skills if s.selected]

    def all_selected(self) -> list[ScoredItem]:
        """Havuza giren her şey — guardrail ve rapor bunu dolaşıyor."""
        items: list[ScoredItem] = []
        for group in (self.experiences, self.projects):
            for entry in group:
                items.extend(entry.selected_bullets)
        items.extend(self.selected_skills)
        return items

    def by_id(self, item_id: str) -> ScoredItem | None:
        for group in (self.experiences, self.projects):
            for entry in group:
                for bullet in entry.bullets:
                    if bullet.id == item_id:
                        return bullet
        for skill in self.skills:
            if skill.id == item_id:
                return skill
        return None
