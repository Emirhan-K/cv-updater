"""Uyarlama adımının çıktı sözleşmesi (issue 07).

Bu şemanın tasarımı guardrail'in kendisidir. Modelin **serbest metin yazabildiği
tek yer** `rewritten`, `rationale` ve `summary`; ve `rewritten` her zaman bir
`source_id`'ye bağlı, yani neyin yeniden ifadesi olduğu belli. "Yeni madde"
diye bir alan yok — model bir deneyimi, teknolojiyi ya da sayıyı eklemek
isteseydi, koyacak yer bulamazdı.

Şirket adı, unvan, tarih ve eğitim bu şemada **hiç geçmiyor**: `tailored.json`
üretilirken (issue 08) doğrudan `master.json`'dan kopyalanıyorlar. Modelden
geçmeyen alan bozulamaz.

Projeler de bilerek dışarıda: modelin yapabildiği tek şey `project_ids` ile
hangi projelerin görüneceğini seçmek. Proje maddeleri ön filtrenin seçtiği
haliyle, **kelimesi kelimesine** CV'ye giriyor.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SelectedBullet(BaseModel):
    """Süper setteki bir maddenin, ilana göre yeniden ifade edilmiş hali."""

    source_id: str = Field(
        description=(
            "Maddenin sana verilen havuzdaki id'si. ZORUNLU ve birebir aynı olmalı — "
            "havuzda olmayan bir id yazma."
        )
    )
    rewritten: str = Field(
        description=(
            "Maddenin ilanın diline ve üslubuna yaklaştırılmış hali. Anlamı "
            "korunmalı; yeni teknoloji, sayı, şirket ya da sorumluluk eklenemez."
        )
    )
    rationale: str = Field(
        description=(
            "Bu maddenin bu ilana neden uyduğu, tek cümle. Uyum raporuna giriyor; "
            "ilandaki hangi gereksinimi karşıladığını yaz."
        )
    )


class TailoredExperience(BaseModel):
    """Tek bir deneyimin seçilmiş maddeleri, CV'de görünecek sırayla."""

    source_id: str = Field(description="Deneyimin havuzdaki id'si.")
    bullets: list[SelectedBullet] = Field(
        default_factory=list,
        description="Seçilen maddeler, en güçlüsü başta olacak şekilde sıralı.",
    )


class OmittedBullet(BaseModel):
    """Havuza girdiği halde seçilmeyen dikkat çekici bir madde.

    Issue 07 bu alanı `dict[str, str]` olarak tarif ediyordu; nesne listesine
    çevrildi çünkü serbest anahtarlı sözlük (`additionalProperties`) zorunlu
    tool-use şemasında sağlayıcıdan sağlayıcıya farklı davranıyor — gateway
    zaten yapılandırılmış çıktıda hassas (bkz. `llm.py`). Taşıdığı bilgi aynı.
    """

    source_id: str = Field(description="Seçilmeyen maddenin id'si.")
    reason: str = Field(description="Neden seçilmedi, tek cümle.")


class TailorPlan(BaseModel):
    """Modelin döndürdüğü uyarlama planı.

    Yalnızca **id'ler ve yeniden ifadeler** içerir. Bu planın `tailored.json`'a
    çevrilmesi (issue 08) ve doğrulanması (`steps/verify.py`) ayrı adımlardır.
    """

    summary: str = Field(
        description=(
            "CV'nin özet paragrafı, ilanın dilinde, 2-4 cümle. Yalnızca "
            "`summary_source_ids` ile gösterdiğin maddelerdeki bilgiye dayanmalı."
        )
    )
    summary_source_ids: list[str] = Field(
        default_factory=list,
        description="Özetin dayandığı madde id'leri. Boş bırakma.",
    )
    experiences: list[TailoredExperience] = Field(
        default_factory=list,
        description="Deneyimler, CV'de görünecek sırayla.",
    )
    skill_ids: list[str] = Field(
        default_factory=list,
        description="Seçilen yetenek id'leri, ilana en uygun olan başta olacak şekilde sıralı.",
    )
    project_ids: list[str] = Field(
        default_factory=list,
        description="Gösterilecek proje id'leri, sıralı. Proje maddeleri olduğu gibi kalır.",
    )
    omitted_reasons: list[OmittedBullet] = Field(
        default_factory=list,
        description=(
            "Havuzdaki güçlü ama seçilmeyen maddeler ve gerekçeleri. En az birkaç "
            "tane yaz — hangi kartı neden oynamadığın raporun bir parçası."
        ),
    )

    # -- kolaylık erişimleri ------------------------------------------------ #

    def all_bullets(self) -> list[SelectedBullet]:
        return [bullet for experience in self.experiences for bullet in experience.bullets]

    def source_ids(self) -> list[str]:
        return [bullet.source_id for bullet in self.all_bullets()]


class TailorResult(BaseModel):
    """`out/<slug>/plan.json` — plan + izlenebilirlik.

    Planın kendisi modelden gelir; buradaki diğer alanlar bizim kaydımızdır.
    `job.json`'daki ayrımın aynısı.
    """

    plan: TailorPlan
    model: str = ""
    tailored_at: datetime = Field(default_factory=datetime.now)
    #: Guardrail sonrası onarım yapıldı mı (`--repair`).
    repaired: bool = False
