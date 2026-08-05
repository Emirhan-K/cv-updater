"""İlanın yapılandırılmış hali — LLM adımı #1'in çıktı şeması (issue 05).

`JobPosting` doğrudan modele verilir (tool-use şeması ya da `output_format`),
`JobAnalysis` ise diske yazılan tam kayıt: ilan + deterministik anahtar kelime
eşlemesi + hangi model, ne zaman, hangi kaynaktan.

Ayrım kasıtlı: **modelden gelen** ile **bizim hesapladığımız** aynı nesnede
karışmasın. Etiket eşlemesi `data/tags.yaml` üzerinden yapılır ve LLM'e hiç
sorulmaz — ön filtre (issue 06) tekrarlanabilir olmak zorunda.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["must", "nice"]
Language = Literal["tr", "en"]
Seniority = Literal["intern", "junior", "mid", "senior", "lead", "unknown"]
WorkMode = Literal["remote", "hybrid", "onsite", "unknown"]


class Requirement(BaseModel):
    """İlandaki tek bir gereksinim."""

    text: str = Field(description="İlandaki ifadenin kısaltılmış hali.")
    kind: Kind = Field(description="'must' = zorunlu/aranan nitelik, 'nice' = tercih edilen/artı.")
    keywords: list[str] = Field(
        default_factory=list,
        description="Bu gereksinimdeki teknik terimler, ilanda geçtiği haliyle.",
    )


class JobPosting(BaseModel):
    """Bir iş ilanından çıkarılan yapılandırılmış bilgi.

    Alanları YALNIZCA ilan metninden doldur. Metinde olmayan bir şeyi tahmin
    etme; emin olmadığın alanı boş bırak ya da 'unknown' seç.
    """

    company: str | None = Field(default=None, description="Şirket adı; yoksa null.")
    title: str = Field(description="Pozisyon başlığı, ilanda yazdığı gibi.")
    language: Language = Field(description="İlanın yazıldığı dil — CV bu dilde üretilecek.")
    seniority: Seniority = Field(description="Aranan kıdem; ilan söylemiyorsa 'unknown'.")
    location: str | None = Field(default=None, description="Şehir/ülke; yoksa null.")
    work_mode: WorkMode = Field(description="Çalışma biçimi; ilan söylemiyorsa 'unknown'.")
    requirements: list[Requirement] = Field(
        default_factory=list, description="Zorunlu ve tercih edilen nitelikler."
    )
    tech_keywords: list[str] = Field(
        default_factory=list,
        description=(
            "İlandaki TÜM teknik terimler, tekrarsız. Her terim ilan metninde "
            "geçmek zorunda — yeni terim uydurma."
        ),
    )
    soft_signals: list[str] = Field(
        default_factory=list,
        description="Teknik olmayan sinyaller: 'takım liderliği', 'müşteriyle temas'.",
    )
    domain: str | None = Field(
        default=None, description="Sektör/alan: 'fintech', 'savunma', 'e-ticaret'; yoksa null."
    )
    tone_notes: str = Field(
        default="",
        description=(
            "İlanın üslubu (resmi/samimi, kurumsal/startup) — yeniden ifade adımı kullanacak."
        ),
    )


class KeywordMapping(BaseModel):
    """`tech_keywords`'ün kontrollü sözlüğe indirgenmiş hali.

    Tamamen deterministik: `data/tags.yaml` okunur, LLM'e sorulmaz.
    """

    tags: list[str] = Field(
        default_factory=list, description="Sözlükteki kanonik etiketler, tekrarsız."
    )
    unmapped: list[str] = Field(
        default_factory=list,
        description="Sözlükte karşılığı olmayan terimler — sözlük buradan büyür.",
    )
    dropped: list[str] = Field(
        default_factory=list,
        description="İlan metninde bulunamayan, bu yüzden atılan terimler (halüsinasyon).",
    )


class JobAnalysis(BaseModel):
    """`out/<slug>/job.json`'a yazılan tam kayıt."""

    posting: JobPosting
    keywords: KeywordMapping = Field(default_factory=KeywordMapping)

    #: İzlenebilirlik: hangi model, ne zaman, hangi girdi yoluyla.
    model: str = ""
    analyzed_at: datetime = Field(default_factory=datetime.now)
    source_kind: str = ""
    #: `--no-llm` ile başka bir çalıştırmadan kopyalandıysa nereden geldiği.
    reused_from: str | None = None

    @property
    def slug_hint(self) -> str:
        """Şirket + pozisyondan okunur bir slug parçası."""
        parts = [part for part in (self.posting.company, self.posting.title) if part]
        return " ".join(parts)
