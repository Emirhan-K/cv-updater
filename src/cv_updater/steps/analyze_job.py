"""İlan analizi — boru hattının tek LLM adımı (issue 05).

Modelden yapılandırılmış bir `JobPosting` alınır, ardından **deterministik** iki
işlem yapılır:

1. **Halüsinasyon süzgeci.** `tech_keywords` içindeki her terim ilan metninde
   gerçekten geçmek zorunda. Geçmeyen atılır ve `dropped`'a yazılır. Bu, issue
   07'nin dürüstlük guardrail'inin ilan tarafındaki karşılığı: ilanda olmayan
   bir teknoloji, CV'de o teknolojiyi öne çıkarmak için gerekçe olamaz.
2. **Etiket eşlemesi.** Kalan terimler `data/tags.yaml` kontrollü sözlüğüne
   indirgenir. Eşleşmeyenler `unmapped`'a düşer — sözlük zamanla oradan büyür.

İkisi de LLM'siz ve test edilebilir; ön filtre (issue 06) bu çıktıya yaslanıyor.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from cv_updater import llm
from cv_updater.models.job import JobAnalysis, JobPosting, KeywordMapping
from cv_updater.paths import OUT_DIR
from cv_updater.tags import TagVocabulary, default_vocabulary

JOB_FILENAME = "job.json"

#: Terim eşleştirmesi için metni sadeleştirirken tüm ayraçlar tek boşluğa iner:
#: "CI/CD" ile "ci-cd", "REST API" ile "rest-api" aynı şeye indirgensin.
_SEPARATORS = re.compile(r"[^0-9a-zçğıöşü]+", re.IGNORECASE)

SYSTEM_PROMPT = """\
Sen bir iş ilanı analiz asistanısın. Verilen ilan metnini yapılandırılmış hale getiriyorsun.

Kurallar:
1. YALNIZCA ilan metninde yazan bilgiyi çıkar. Metinde olmayan hiçbir şeyi tahmin etme,
   çıkarım yapma, tamamlama. Bir alan ilanda geçmiyorsa null bırak ya da "unknown" seç.
2. `tech_keywords` içindeki her terim ilan metninde AYNEN geçmelidir. Metinde geçmeyen
   bir teknoloji adı uydurma — "Python geliştirici" ilanına "Django" ekleme.
3. `language` ilanın yazıldığı dildir: ağırlıklı Türkçeyse "tr", ağırlıklı İngilizceyse "en".
   İngilizce teknik terim içeren Türkçe ilan yine "tr"dir.
4. `seniority`: ilan "kıdemli/senior" diyorsa "senior", "junior/yeni mezun" diyorsa "junior",
   "takım lideri/lead/yönetici" diyorsa "lead", "stajyer" diyorsa "intern", yıl sayısı
   veriyorsa (3-5 yıl → "mid", 5+ → "senior") ona göre; hiçbiri yoksa "unknown".
5. `requirements`: "Aranan nitelikler / zorunlu / olmazsa olmaz" başlığı altındakiler "must",
   "tercihen / artı olarak / nice to have" altındakiler "nice".
6. `tone_notes`: ilanın üslubunu bir iki cümleyle anlat (resmi mi samimi mi, kurumsal mı
   startup mı, hangi değerleri vurguluyor). Bu, CV'nin yeniden ifade edilmesinde kullanılacak.
7. Metnin dilinde yaz: Türkçe ilanda Türkçe, İngilizce ilanda İngilizce alan değerleri üret.
   `tech_keywords` her zaman ilandaki yazımıyla kalır.
"""

USER_TEMPLATE = """\
Aşağıdaki iş ilanını analiz et.

<ilan>
{posting}
</ilan>
"""


def casefold_tr(text: str) -> str:
    """Türkçe farkında küçük harfe indirger.

    Python `"İ".lower()` için `"i" + U+0307` (birleşik nokta) üretiyor. O nokta
    harf sayılmadığı için ayraç muamelesi görüyor ve "İşleme" → "i şleme" oluyor:
    "Görüntü İşleme" ne ilan metninde bulunabiliyor ne de `tags.yaml`'daki
    aliasla eşleşiyordu. Türkçeye özgü `İ` doğrudan `i`'ye indiriliyor.

    `I` bilerek dokunulmuyor: Türkçe kuralı `ı` derdi ama "AI" gibi İngilizce
    kısaltmaları bozardı.
    """
    return unicodedata.normalize("NFC", text.replace("İ", "i")).lower()


def fold(text: str) -> str:
    """Terim karşılaştırması için metni sadeleştirir.

    Küçük harfe indirir ve tüm ayraçları tek boşluğa çevirir; böylece "CI/CD",
    "ci-cd" ve "ci cd" aynı forma iner. Türkçe harfler korunur — "görüntü işleme"
    ASCII'ye zorlanırsa ilandaki yazımıyla eşleşmez.
    """
    return f" {_SEPARATORS.sub(' ', casefold_tr(text)).strip()} "


def find_hallucinated(posting_text: str, keywords: list[str]) -> list[str]:
    """İlan metninde geçmeyen terimler.

    Kabul kriteri: `tech_keywords` içindeki hiçbir terim ilanda eksik olmamalı.
    Kontrol sadeleştirilmiş metin üzerinde yapılır ki "CI/CD" ile "ci-cd" farkı
    yanlış alarm üretmesin.
    """
    haystack = fold(posting_text)
    missing = []
    for keyword in keywords:
        needle = fold(keyword).strip()
        if not needle or f" {needle} " not in haystack:
            missing.append(keyword)
    return missing


def map_keywords(
    posting_text: str,
    keywords: list[str],
    *,
    vocabulary: TagVocabulary | None = None,
) -> KeywordMapping:
    """Terimleri süzer ve kontrollü sözlüğe indirger."""
    vocab = vocabulary or default_vocabulary()
    hallucinated = set(find_hallucinated(posting_text, keywords))

    tags: list[str] = []
    unmapped: list[str] = []
    dropped: list[str] = []

    for keyword in keywords:
        if keyword in hallucinated:
            if keyword not in dropped:
                dropped.append(keyword)
            continue

        canonical = _normalize_term(keyword, vocab)
        if canonical is None:
            if keyword not in unmapped:
                unmapped.append(keyword)
        elif canonical not in tags:
            tags.append(canonical)

    return KeywordMapping(tags=tags, unmapped=unmapped, dropped=dropped)


def _normalize_term(raw: str, vocabulary: TagVocabulary) -> str | None:
    """Serbest terimi kanonik etikete indirger.

    `TagVocabulary.normalize` birebir yazıma bakıyor; ilanlarda terimler
    "Fast API", "FastAPI", "fastapi" gibi geldiği için önce ayraçları tireye
    çeviren birkaç aday üretilip sırayla denenir.
    """
    stripped = casefold_tr(raw.strip())
    hyphenated = _SEPARATORS.sub("-", stripped).strip("-")

    for candidate in (stripped, hyphenated, hyphenated.replace("-", "")):
        if not candidate:
            continue
        mapped = vocabulary.normalize(candidate)
        if mapped is not None:
            return mapped
    return None


def analyze(
    posting_text: str,
    *,
    source_kind: str = "",
    vocabulary: TagVocabulary | None = None,
    client=None,  # noqa: ANN001 - Anthropic; tembel içe aktarım için tiplenmedi
) -> JobAnalysis:
    """İlan metnini `JobAnalysis`'e çevirir. **Ağa çıkan tek fonksiyon.**"""
    active = client or llm.get_client()

    posting = llm.structured(
        active,
        JobPosting,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": USER_TEMPLATE.format(posting=posting_text)}],
    )

    mapping = map_keywords(posting_text, posting.tech_keywords, vocabulary=vocabulary)
    # Süzgeç sadece rapor etmiyor, çıktıyı da temizliyor: sonraki adımlar
    # `tech_keywords`'ü doğrudan kullanacak.
    posting.tech_keywords = [kw for kw in posting.tech_keywords if kw not in set(mapping.dropped)]

    return JobAnalysis(
        posting=posting,
        keywords=mapping,
        model=llm.model_name(),
        source_kind=source_kind,
    )


def write_analysis(run_dir: Path, analysis: JobAnalysis) -> Path:
    """`job.json`'ı çalıştırma dizinine yazar."""
    path = run_dir / JOB_FILENAME
    path.write_text(
        analysis.model_dump_json(indent=2, exclude_none=False),
        encoding="utf-8",
        newline="",
    )
    return path


def read_analysis(path: Path) -> JobAnalysis:
    """Var olan bir `job.json`'ı okur."""
    return JobAnalysis.model_validate_json(path.read_text(encoding="utf-8"))


def latest_analysis(root: Path | None = None) -> Path | None:
    """En son yazılmış `job.json` — `--no-llm` bunu yeniden kullanır.

    Çalıştırma dizinleri zaman damgalı olduğu için ada göre sıralamak yeterli;
    dosya sistemi zaman damgalarına güvenmeye gerek yok.
    """
    base = root or OUT_DIR
    if not base.is_dir():
        return None

    candidates = sorted(
        (path for path in base.glob(f"*/{JOB_FILENAME}") if path.is_file()),
        key=lambda path: path.parent.name,
    )
    return candidates[-1] if candidates else None
