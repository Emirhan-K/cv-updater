"""Uyarlama adımı (issue 07) — boru hattının ikinci ve son LLM çağrısı.

Modele **süper set değil, ön filtrenin havuzu** verilir; ve döndürebileceği tek
şey id'ler ile o id'lerin yeniden ifadesidir (`models/tailor.py`). Çıktı buradan
doğrudan CV'ye gitmez: önce `steps/verify.py` deterministik olarak denetler.

Sıcaklık ayarı yok, örnekleme ayarı yok — kararlılık `structured()` sözleşmesi ve
daralmış havuz üzerinden sağlanıyor. Aynı ilan üç kez çalıştırıldığında seçilen
`source_id` kümesinin büyük ölçüde aynı çıkması issue 07'nin kabul kriteri.
"""

from __future__ import annotations

import json
from pathlib import Path

from cv_updater import llm
from cv_updater.models.candidates import CandidatePool
from cv_updater.models.job import JobAnalysis
from cv_updater.models.tailor import TailorPlan, TailorResult

PLAN_FILENAME = "plan.json"

SYSTEM_PROMPT = """\
Sen bir CV uyarlama asistanısın. Elinde bir iş ilanının analizi ve bir adayın
**önceden filtrelenmiş** madde havuzu var. Görevin bu havuzdan seçim yapmak,
sıralamak ve maddeleri ilanın diline ve üslubuna yaklaştırmak.

DÜRÜSTLÜK KURALI — bu kuralın istisnası yoktur:
Sana verilen maddelerin dışına çıkma. Yeni bir teknoloji, şirket, sayı ya da
sorumluluk EKLEME. Yeniden ifade ederken maddenin ANLAMINI koru; yalnızca kelime
seçimini ilana yaklaştır. Bir madde ilana uymuyorsa seçme — asla zorlama.

Örnekler:

  Kaynak: "Modelleri FastAPI servisleri arkasına alarak üç ekibin kullandığı bir
           REST arayüzü çıkardı."
  DOĞRU:  "FastAPI ile üç ekibin ortak kullandığı bir REST API tasarladı."
  YANLIŞ: "FastAPI ve Django ile mikroservis mimarisi kurdu."   (Django yok)
  YANLIŞ: "Beş ekibin kullandığı bir REST API tasarladı."       (sayı değişti)
  YANLIŞ: "AWS üzerinde ölçeklenen bir REST API tasarladı."     (AWS yok)

  Kaynak: "Görüntü işleme modellerini bir veri hattına bağladı."
  DOĞRU (İngilizce ilan): "Connected computer-vision models into a data pipeline."
  YANLIŞ: "Led the computer vision team of five engineers."     (ikisi de yok)

Kurallar:
1. `source_id` alanına YALNIZCA sana verilen havuzdaki id'leri yaz, KARAKTERİ
   KARAKTERİNE aynı. Id'ler `[köşeli parantez]` içinde veriliyor ve **dil
   sonekiyle biter** (`.tr` / `.en`). Soneki düşürme, kısaltma, düzeltme:
   `exp-preunec.rag.en` id'sini `exp-preunec.rag` diye yazarsan çıktı reddedilir.
2. Her deneyim için en güçlü 3-5 maddeyi seç. Havuzdaki her maddeyi almak zorunda
   değilsin; ilanla ilgisi zayıf olanı bırak ve `omitted_reasons`'a yaz.
3. Sayılar kutsaldır. Kaynakta "üç ekip" yazıyorsa yeniden ifadede de üçtür.
   Kaynakta sayı yoksa sen sayı uyduramazsın.
4. `summary` 2-4 cümle, ilanın dilinde, ve YALNIZCA `summary_source_ids` ile
   gösterdiğin maddelerdeki bilgiye dayanmalı. Orada olmayan bir iddia yazma.
5. Metni **kullanıcı mesajındaki "Üretilecek CV'nin dili"** başlığında yazan dilde üret.
   Bu genelde ilanın dilidir ama zorlanmış olabilir; o satır bağlayıcıdır.
   Havuzdaki maddeler zaten o dilde seçildi.
6. `rationale` alanına maddenin ilandaki hangi gereksinimi karşıladığını yaz —
   bu metin uyum raporuna giriyor, aday okuyacak.
7. Unvan, şirket adı, tarih ve eğitim senin işin değil; onlar sana sorulmadan
   CV'ye kopyalanıyor. O alanlara dair bir şey yazma.
8. `project_ids`'te **sabit bir sayı yok**; kaç proje göstereceğine sen karar
   ver. Ölçü ilanla ilgi: ilana gerçekten hitap eden her projeyi koy, zayıf
   kalanı bırak. Sayıyı doldurmak için ilgisiz proje ekleme, yer kalmadığını
   düşünüp güçlü bir projeyi de atma — sayfa düzeni senin sorunun değil.
   Deneyim bölümü zayıfsa (staj ağırlıklı, kısa geçmiş) projeler o boşluğu
   doldurur, orada cömert ol. En güçlü proje başta olacak şekilde sırala.
"""

USER_TEMPLATE = """\
# İlan

{posting}

# Üretilecek CV'nin dili: {language}

`summary` ve tüm `rewritten` alanlarını **bu dilde** yaz. Bu, ilanın dilinden
farklı olabilir (`--lang` ile zorlanmış olabilir); bu satır bağlayıcıdır.

# Aday havuzu

Aşağıdaki maddelerin dışına çıkamazsın. Skorlar deterministik bir ön filtreden
geliyor; sıralama önerisidir, bağlayıcı değildir.

{pool}
"""

#: Dil kodunun modele okunur hali — "tr" demek yerine adını yazmak daha güvenilir.
_LANGUAGE_NAMES = {"tr": "Türkçe", "en": "İngilizce (English)"}


def render_posting(analysis: JobAnalysis) -> str:
    """İlanı modele verilecek kompakt biçime indirger.

    Ham ilan metni değil `job.json` gönderiliyor: analiz adımı zaten gürültüyü
    temizledi, ve ikinci kez ham metin göndermek modele "ilanda geçen ama
    havuzda olmayan" teknolojileri hatırlatarak uydurmayı davet ederdi.
    """
    posting = analysis.posting
    lines = [
        f"Pozisyon: {posting.title}",
        f"Şirket: {posting.company or '—'}",
        f"Dil: {posting.language}   Kıdem: {posting.seniority}   Çalışma: {posting.work_mode}",
    ]
    if posting.domain:
        lines.append(f"Alan: {posting.domain}")
    if posting.tone_notes:
        lines.append(f"Üslup: {posting.tone_notes}")

    must = [r for r in posting.requirements if r.kind == "must"]
    nice = [r for r in posting.requirements if r.kind == "nice"]
    if must:
        lines.append("\nZorunlu gereksinimler:")
        lines += [f"- {requirement.text}" for requirement in must]
    if nice:
        lines.append("\nTercih edilen nitelikler:")
        lines += [f"- {requirement.text}" for requirement in nice]
    if posting.soft_signals:
        lines.append(f"\nTeknik olmayan sinyaller: {', '.join(posting.soft_signals)}")

    return "\n".join(lines)


def render_pool(pool: CandidatePool) -> str:
    """Aday havuzunu modele verilecek biçime indirger.

    Sadece seçilenler gider. Puanın kendisi de gider: modelin ön filtreyle
    çelişmesi mümkün olsun ama bilmeden çelişmesin.
    """
    lines: list[str] = []

    for entry in pool.experiences:
        bullets = entry.selected_bullets
        if not bullets:
            continue
        lines.append(f"\n## Deneyim: {entry.title} — {entry.subtitle} ({entry.period})")
        lines.append(f"id: {entry.id}")
        for bullet in bullets:
            tags = ", ".join(bullet.tags)
            lines.append(f"- [{bullet.id}] (skor {bullet.breakdown.total:g}; {tags})")
            lines.append(f"  {bullet.text}")

    projects = [entry for entry in pool.projects if entry.selected_bullets]
    if projects:
        lines.append(
            "\n## Projeler (yalnızca id seç; maddeleri olduğu gibi kalacak)\n"
            "Kaç tane göstereceğine sen karar ver — sabit bir sınır yok, ölçü ilanla ilgi."
        )
        for entry in projects:
            lines.append(f"- [{entry.id}] {entry.title} ({entry.period})")
            for bullet in entry.selected_bullets:
                lines.append(f"    {bullet.text}")

    skills = pool.selected_skills
    if skills:
        lines.append("\n## Yetenekler (id ile seç ve sırala)")
        lines += [f"- [{skill.id}] {skill.text}" for skill in skills]

    if pool.missing_tags:
        lines.append(
            "\n## İlanda geçen ama havuzda karşılığı OLMAYAN konular\n"
            f"{', '.join(pool.missing_tags)}\n"
            "Bunlar için madde yazma; aday bunlara sahip değil."
        )

    return "\n".join(lines)


def tailor(
    analysis: JobAnalysis,
    pool: CandidatePool,
    *,
    client=None,  # noqa: ANN001 - Anthropic; tembel içe aktarım için tiplenmedi
) -> TailorResult:
    """Havuzdan bir `TailorPlan` üretir. **Ağa çıkan fonksiyon.**"""
    active = client or llm.get_client()

    content = USER_TEMPLATE.format(
        posting=render_posting(analysis),
        # Hedef dil ilanın dilinden farklı olabilir (`--lang`). Sistem promptu
        # "ilanın dilinde yaz" diyor; burası onu ezen tek yer.
        language=_LANGUAGE_NAMES.get(pool.language, pool.language),
        pool=render_pool(pool),
    )

    plan = llm.structured(
        active,
        TailorPlan,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )

    return TailorResult(plan=plan, model=llm.model_name())


def write_plan(run_dir: Path, result: TailorResult) -> Path:
    """`plan.json`'ı yazar."""
    path = run_dir / PLAN_FILENAME
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8", newline="")
    return path


def read_plan(path: Path) -> TailorResult:
    return TailorResult.model_validate_json(path.read_text(encoding="utf-8"))


def latest_plan(root: Path | None = None) -> Path | None:
    """En son yazılmış `plan.json` — `--no-llm` bunu yeniden kullanır.

    Plan aday havuzuna bağlı olduğu için başka bir ilanın planını yeniden
    kullanmak guardrail'i `source.not_offered` ile tetikler. Bu bir kaza değil,
    istenen davranış: uyuşmazlık sessizce geçmez.
    """
    from cv_updater.steps.analyze_job import latest_run_file

    return latest_run_file(PLAN_FILENAME, root)


def plan_summary(result: TailorResult) -> dict[str, int]:
    """Terminal özeti için sayılar."""
    plan = result.plan
    return {
        "deneyim": len(plan.experiences),
        "madde": len(plan.all_bullets()),
        "yetenek": len(plan.skill_ids),
        "proje": len(plan.project_ids),
        "elenen": len(plan.omitted_reasons),
    }


def as_json(result: TailorResult) -> str:  # pragma: no cover - hata ayıklama kolaylığı
    return json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
