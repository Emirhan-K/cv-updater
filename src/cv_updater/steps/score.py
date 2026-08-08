"""Deterministik ön filtre (issue 06) — **bu dosyada LLM yok.**

Uyarlama adımına süper setin tamamını vermek üç şeyi birden kötüleştiriyor:
maliyet artıyor, çıktı çalıştırmadan çalıştırmaya oynuyor, ve "bu madde neden
seçildi" sorusunun cevabı modelin içinde kalıyor. Bu adım seçimi ikiye bölüyor:
**kod** hangi maddelerin konuşmaya değer olduğunu belirliyor, model yalnızca
daralmış havuzun içinden seçip yeniden ifade ediyor.

Puanlama:

    skor = 3 × (zorunlu etiket eşleşmesi)
         + 1 × (tercih edilen etiket eşleşmesi)
         + 0.25 × (ilanla aynı kategoride komşu etiket)
         + 1 × (alan eşleşmesi)
         + 0.5 × (kıdem uyumu)
         + son iki yıl için azalan tazelik bonusu
         + 0.5 × (ölçülebilir etki var mı)

Ağırlıkların gerekçesi ve nerelerinden tutulup değiştirileceği `docs/SCORING.md`'de.

Eşleştirme **serbest metinle değil `x_meta.tags` üzerinden** yapılıyor: "Python"
kelimesinin bir maddede geçiyor olması o maddeyi Python maddesi yapmıyor, gürültüyü
kesen şey tam olarak bu.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from cv_updater.models.candidates import (
    CandidatePool,
    ExperienceCandidates,
    ItemKind,
    RecallCheck,
    RequirementCoverage,
    ScoreBreakdown,
    ScoredItem,
    TokenBudget,
    Weights,
)
from cv_updater.models.job import JobAnalysis, Language
from cv_updater.models.resume import Resume, XBullet, XMeta
from cv_updater.steps.analyze_job import ascii_fold, casefold_tr, fold
from cv_updater.tags import TagVocabulary, default_vocabulary

CANDIDATES_FILENAME = "candidates.json"

# --- Havuz sınırları: burayı değiştir, formülü değil ------------------------ #
#: İlan başına deneyim başına en fazla kaç madde LLM'e gider (issue 06).
MAX_BULLETS_PER_EXPERIENCE = 8
#: Projeler daha kısa anlatılıyor; oradaki üst sınır daha dar.
MAX_BULLETS_PER_PROJECT = 4
MAX_SKILLS = 30
#: Kaç proje CV'ye girecek? **Sabit bir sayı yok.** Eskiden 5'ti; sınır iki
#: sebeple kaldırıldı. Birincisi eleme zaten madde seviyesinde yapılıyor:
#: `_select` yalnızca skoru sıfırdan büyük maddeleri seçiyor, dolayısıyla ilanla
#: hiç ilgisi olmayan bir proje havuza `selected_bullets` olmadan giriyor ve
#: `render_pool` onu modele hiç göstermiyor. İkincisi sayının doğrusu ilana
#: göre değişiyor: iki güçlü projesi olan bir ilanda üçüncüsü sayfayı doldurur,
#: proje ağırlıklı bir ilanda beşi de yerinde durur. Kararı model veriyor
#: (`steps/tailor.SYSTEM_PROMPT`, kural 8); burası yalnızca ilgili olan her
#: projeyi ona göstermekle yükümlü. `None` = sınırsız.
MAX_PROJECTS: int | None = None
#: Hiç eşleşmeyen bir deneyim bile CV'de boş görünmemeli — istihdam boşluğu,
#: konu dışı bir maddeden daha kötü okunur. Skoru sıfır olsa da bu kadarı kalır.
MIN_BULLETS_PER_EXPERIENCE = 2
#: Tazelik bonusunun sıfırlandığı yaş (yıl).
RECENCY_WINDOW_YEARS = 2.0
# --------------------------------------------------------------------------- #

#: Kıdem merdiveni — uyum, iki seviye arasındaki mesafeden hesaplanıyor.
_SENIORITY_LADDER = ("intern", "junior", "mid", "senior", "lead")

_TR_MONTHS = {
    "ocak": 1,
    "subat": 2,
    "mart": 3,
    "nisan": 4,
    "mayis": 5,
    "haziran": 6,
    "temmuz": 7,
    "agustos": 8,
    "eylul": 9,
    "ekim": 10,
    "kasim": 11,
    "aralik": 12,
}
_EN_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

#: Ay adları, ASCII'ye indirgenmiş halde. ATS tarih biçimi kontrolü (issue 10)
#: aynı listeyi kullanıyor — iki yerde ay adı listesi tutulmasın.
MONTH_WORDS: tuple[str, ...] = (*_TR_MONTHS, *_EN_MONTHS)

#: "Devam Ediyor", "Present", "Halen" — bitmemiş dönem bugünle puanlanır.
_ONGOING = ("devam", "present", "current", "halen", "now", "günümüz", "gunumuz")
#: `_ONGOING`'in ASCII hali — düzenli ifadeye gömülüyor (issue 10).
ONGOING_WORDS: tuple[str, ...] = ("devam", "present", "current", "halen", "now", "gunumuz")

#: Dönem ayracı: tire, en/em dash ve "to". `2018-2022` de `Eylül 2024 – Devam` de aynı.
_PERIOD_SPLIT = re.compile(r"\s*(?:[-–—]|\bto\b)\s*")
_YEAR = re.compile(r"(19|20)\d{2}")

#: `impact` alanının "ölçülebilir" sayılması için içinde bir sayı geçmeli.
_HAS_NUMBER = re.compile(r"\d")


# --------------------------------------------------------------------------- #
# İlan tarafı: hangi etiketler zorunlu, hangileri tercih
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PostingTags:
    """İlanın etiket profili — puanlamanın tek girdisi."""

    must: tuple[str, ...]
    nice: tuple[str, ...]
    #: Gereksinim indeksi → o gereksinimin etiketleri. Kapsama tablosu bunu kullanıyor.
    per_requirement: tuple[tuple[int, tuple[str, ...]], ...]
    recall: RecallCheck
    #: İlanın etiketlerinin ait olduğu `tags.yaml` kategorileri — komşuluk bonusu için.
    categories: frozenset[str] = frozenset()


def _normalize_terms(terms: list[str], vocabulary: TagVocabulary) -> list[str]:
    """Serbest terim listesini kanonik etiketlere indirger (tekrarsız, sıralı)."""
    from cv_updater.steps.analyze_job import _normalize_term

    tags: list[str] = []
    for term in terms:
        canonical = _normalize_term(term, vocabulary)
        if canonical is not None and canonical not in tags:
            tags.append(canonical)
    return tags


def recall_check(
    posting_text: str,
    reported: list[str],
    *,
    vocabulary: TagVocabulary | None = None,
) -> RecallCheck:
    """İlanda geçen ama modelin bildirmediği etiketler.

    Issue 05'in halüsinasyon süzgeci *fazladan* terimi yakalıyor; bu kontrol
    *eksik* terimi yakalıyor. İki ayrı modelle yapılan karşılaştırmada ucuz
    modelin kaçırdıkları tamamen Türkçeye özgüydü ("Görüntü İşleme",
    "Doğal Dil İşleme") — ve kayıp sessizdi, çıktı tertemiz görünüyordu.

    Sözlükteki her kanonik terim ve her alias ilan metninde aranır. Bulunan ama
    raporlanmayanlar `missed`'a düşer; puanlamada tercih edilen (nice) ağırlıkla
    hesaba katılırlar.
    """
    vocab = vocabulary or default_vocabulary()
    haystack = fold(posting_text)
    known = set(reported)

    missed: list[str] = []
    evidence: dict[str, str] = {}

    # Kanonik terimler önce: kanıt olarak alias yerine etiketin kendisi görünsün.
    candidates = [(tag, tag) for tag in sorted(vocab.canonical)]
    candidates += [(alias, tag) for alias, tag in sorted(vocab.aliases.items())]

    for term, tag in candidates:
        if tag in known or tag in evidence:
            continue
        needle = fold(term).strip()
        if needle and f" {needle} " in haystack:
            missed.append(tag)
            evidence[tag] = term

    return RecallCheck(missed=missed, evidence=evidence)


def posting_tags(
    analysis: JobAnalysis,
    posting_text: str = "",
    *,
    vocabulary: TagVocabulary | None = None,
) -> PostingTags:
    """İlanı zorunlu / tercih edilen etiket kümelerine ayırır.

    Gereksinimlerde adı geçmeyen ama ilanda duran etiketler (ve geri çağırma
    ağının yakaladıkları) `nice` tarafına düşer: ilanın gövdesinde geçen bir
    teknoloji puan değeri taşır, ama "aranan nitelikler" başlığı altındaki bir
    madde kadar değil.
    """
    vocab = vocabulary or default_vocabulary()

    must: list[str] = []
    nice: list[str] = []
    per_requirement: list[tuple[int, tuple[str, ...]]] = []

    for index, requirement in enumerate(analysis.posting.requirements):
        tags = _normalize_terms(requirement.keywords, vocab)
        # Gereksinim metninin kendisi de taranır: ucuz modeller gereksinimi doğru
        # yakalayıp `keywords`'ü boş bırakabiliyor (TODO.md, model karşılaştırması).
        for tag in recall_check(requirement.text, [], vocabulary=vocab).missed:
            if tag not in tags:
                tags.append(tag)

        per_requirement.append((index, tuple(tags)))
        target = must if requirement.kind == "must" else nice
        for tag in tags:
            if tag not in must and tag not in nice:
                target.append(tag)

    recall = recall_check(posting_text, analysis.keywords.tags, vocabulary=vocab)

    for tag in [*analysis.keywords.tags, *recall.missed]:
        if tag not in must and tag not in nice:
            nice.append(tag)

    categories = {
        category for tag in (*must, *nice) if (category := vocab.category_of(tag)) is not None
    }

    return PostingTags(
        must=tuple(must),
        nice=tuple(nice),
        per_requirement=tuple(per_requirement),
        recall=recall,
        categories=frozenset(categories),
    )


# --------------------------------------------------------------------------- #
# Dönem ayrıştırma (tazelik bonusu ve ters-kronolojik sıralama için)
# --------------------------------------------------------------------------- #


def parse_period_start(period: str) -> date | None:
    """Bir `period` metninin başlangıç tarihi. Anlaşılmazsa None.

    `parse_period_end`'in aynadaki hali; tek farkı ayı olmayan bir tarihte
    aralığın **başına** düşmesi ("2018-2022" → 2018-01-01). Yalnızca sıralamada
    eşitlik bozmak için kullanılıyor, puanlamada değil.
    """
    if not period.strip():
        return None

    parts = [part for part in _PERIOD_SPLIT.split(period.strip()) if part.strip()]
    if not parts:
        return None

    head = parts[0]
    folded = ascii_fold(head)
    year_match = _YEAR.search(head)
    if year_match is None:
        return None
    year = int(year_match.group())

    month = 1
    for name, number in (*_TR_MONTHS.items(), *_EN_MONTHS.items()):
        if name in folded or folded.startswith(name[:3]):
            month = number
            break

    try:
        return date(year, month, 1)
    except ValueError:  # pragma: no cover - year/month zaten sınırlar içinde
        return None


def reverse_chronological_key(period: str, *, today: date | None = None) -> tuple[date, date]:
    """Ters-kronolojik sıralama anahtarı: `(bitiş, başlangıç)`.

    `sorted(..., reverse=True)` ile kullanılır; en güncel deneyim en üste çıkar.
    Bitiş eşitse (iki devam eden iş) daha yeni başlayan öne geçer. Okunamayan bir
    dönem `date.min`e düşer, yani listenin sonuna — **tarih uydurulmuyor**, sadece
    sıralanamayan bir dönem en alta iniyor.

    Ayrı bir fonksiyon olmasının sebebi iki tüketicisinin olması: derleme
    (`render_json._render_experiences`) ve render (`render_pdf.build_document`).
    Sıra ikisinde de aynı kuralla oluşmalı.
    """
    return (
        parse_period_end(period, today=today) or date.min,
        parse_period_start(period) or date.min,
    )


def parse_period_end(period: str, *, today: date | None = None) -> date | None:
    """Bir `period` metninin bitiş tarihi. Anlaşılmazsa None.

    Süper sette üç biçim var: "Eylül 2024 – Devam Ediyor", "Şubat 2026 – Mayıs 2026"
    ve "2018-2022". Devam eden dönemler bugünle puanlanır.
    """
    if not period.strip():
        return None

    now = today or date.today()
    parts = [part for part in _PERIOD_SPLIT.split(period.strip()) if part.strip()]
    if not parts:
        return None

    tail = parts[-1]
    folded = ascii_fold(tail)
    if any(word in folded for word in _ONGOING):
        return now

    year_match = _YEAR.search(tail)
    if year_match is None:
        return None
    year = int(year_match.group())

    month = 12
    for name, number in (*_TR_MONTHS.items(), *_EN_MONTHS.items()):
        if name in folded or folded.startswith(name[:3]):
            month = number
            break

    try:
        return date(year, month, 28)
    except ValueError:  # pragma: no cover - year/month zaten sınırlar içinde
        return None


def recency_bonus(period: str, weight: float, *, today: date | None = None) -> float:
    """Son `RECENCY_WINDOW_YEARS` yıl içinde biten deneyime azalan bonus.

    Devam eden iş tam puan alır, iki yıl önce bitmiş iş sıfır. Amaç eski ama
    etiket olarak uyan bir deneyimin güncel bir deneyimin önüne geçmemesi.
    """
    end = parse_period_end(period, today=today)
    if end is None:
        return 0.0
    years = ((today or date.today()) - end).days / 365.25
    if years <= 0:
        return round(weight, 3)
    return round(weight * max(0.0, 1 - years / RECENCY_WINDOW_YEARS), 3)


def seniority_fit(posting: str, item: str | None, weight: float) -> float:
    """Kıdem uyumu: aynı seviye tam puan, bir kademe fark yarım, ötesi sıfır.

    İlan ya da madde kıdem belirtmiyorsa bonus da ceza da yok — bilinmeyen bir
    alan yüzünden sıralamayı bozmak istemiyoruz.
    """
    if item is None or posting not in _SENIORITY_LADDER or item not in _SENIORITY_LADDER:
        return 0.0
    distance = abs(_SENIORITY_LADDER.index(posting) - _SENIORITY_LADDER.index(item))
    if distance == 0:
        return round(weight, 3)
    if distance == 1:
        return round(weight / 2, 3)
    return 0.0


# --------------------------------------------------------------------------- #
# Madde puanlama
# --------------------------------------------------------------------------- #


def _domain_match(
    domain: str | None,
    tags: list[str],
    text: str,
    weight: float,
    vocabulary: TagVocabulary,
) -> float:
    """Alan eşleşmesi: ilanın alanı ya bir etikete iniyor ya da metinde geçiyor."""
    if not domain:
        return 0.0

    canonical = vocabulary.normalize(casefold_tr(domain))
    if canonical is not None and canonical in tags:
        return round(weight, 3)

    needle = fold(domain).strip()
    if needle and f" {needle} " in fold(text):
        return round(weight, 3)
    return 0.0


def _category_bonus(
    tags: list[str],
    matched: list[str],
    profile: PostingTags,
    weight: float,
    vocabulary: TagVocabulary,
) -> float:
    """İlanın etiketleriyle **aynı kategoride** olmanın küçük bonusu.

    Doğrudan eşleşme olmadan da komşuluk bilgi taşıyor: `nlp` arayan bir ilana
    `speech-to-text` yeteneği, `rest-api` arayan ilana `async` maddesi yakındır.
    Bonus ikili ve küçük (0.25) — sıralamayı ancak eşitlik bozacak kadar etkiler.

    Asıl işi CV'nin yetenek bölümünü çökertmemek: ilan sekiz etiket taşıyorsa
    50 yetenekten sadece o sekizi kalıyordu, ve "sekiz yetenek" bir CV'de
    uyarlanmış değil, eksik görünüyor.
    """
    if not profile.categories:
        return 0.0
    extra = [tag for tag in tags if tag not in matched]
    if any(vocabulary.category_of(tag) in profile.categories for tag in extra):
        return round(weight, 3)
    return 0.0


def score_bullet(
    meta: XMeta | None,
    text: str,
    profile: PostingTags,
    *,
    domain: str | None = None,
    posting_seniority: str = "unknown",
    period: str = "",
    weights: Weights | None = None,
    vocabulary: TagVocabulary | None = None,
    today: date | None = None,
) -> tuple[ScoreBreakdown, list[str], list[str]]:
    """Tek bir maddenin skorunu ve eşleşen etiketlerini döndürür."""
    active = weights or Weights()
    vocab = vocabulary or default_vocabulary()
    tags = list(meta.tags) if meta else []

    matched_must = [tag for tag in profile.must if tag in tags]
    matched_nice = [tag for tag in profile.nice if tag in tags]

    breakdown = ScoreBreakdown(
        must=round(active.must * len(matched_must), 3),
        nice=round(active.nice * len(matched_nice), 3),
        category=_category_bonus(
            tags, matched_must + matched_nice, profile, active.category, vocab
        ),
        domain=_domain_match(domain, tags, text, active.domain, vocab),
        seniority=seniority_fit(
            posting_seniority, meta.seniority if meta else None, active.seniority
        ),
        recency=recency_bonus(period, active.recency, today=today),
        impact=(
            round(active.impact, 3)
            if meta and meta.impact and _HAS_NUMBER.search(meta.impact)
            else 0.0
        ),
    )
    return breakdown, matched_must, matched_nice


@dataclass(frozen=True)
class _Context:
    """Bir çalıştırma boyunca sabit kalan puanlama girdileri.

    Her madde için tek tek taşımak yerine bir kez kurulur; `build_pool` içindeki
    üç döngü de aynı bağlamı kullanıyor.
    """

    domain: str | None
    posting_seniority: str
    weights: Weights
    vocabulary: TagVocabulary
    today: date | None

    def as_kwargs(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "posting_seniority": self.posting_seniority,
            "weights": self.weights,
            "vocabulary": self.vocabulary,
            "today": self.today,
        }


def _lang_fits(meta: XMeta | None, language: Language) -> bool:
    """Dil süzgeci: dili belirtilmemiş madde her dile uyar, belirtilmişse eşleşmeli."""
    if meta is None or meta.lang is None:
        return True
    return meta.lang == language


def _to_scored(
    bullet: XBullet,
    owner_id: str,
    kind: ItemKind,
    profile: PostingTags,
    *,
    period: str,
    context: _Context,
) -> ScoredItem:
    breakdown, matched_must, matched_nice = score_bullet(
        bullet.x_meta,
        bullet.text,
        profile,
        period=period,
        **context.as_kwargs(),
    )
    return ScoredItem(
        id=bullet.id,
        kind=kind,
        owner_id=owner_id,
        text=bullet.text,
        tags=list(bullet.x_meta.tags) if bullet.x_meta else [],
        lang=bullet.x_meta.lang if bullet.x_meta else None,
        always_include=bool(bullet.x_meta and bullet.x_meta.always_include),
        matched_must=matched_must,
        matched_nice=matched_nice,
        breakdown=breakdown,
    )


def _rank(items: list[ScoredItem]) -> list[ScoredItem]:
    """Skora göre sıralar; eşitlikte id — sıra çalıştırmadan çalıştırmaya oynamasın."""
    return sorted(items, key=lambda item: (-item.breakdown.total, item.id))


def _select(items: list[ScoredItem], limit: int, *, minimum: int = 0) -> None:
    """Üst sınırı ve `always_include` muafiyetini uygular; `selected` bayrağını yazar."""
    ranked = _rank(items)
    chosen: list[str] = [item.id for item in ranked if item.always_include]

    for index, item in enumerate(ranked):
        if item.id in chosen:
            continue
        if len(chosen) >= limit:
            break
        if item.breakdown.total > 0 or index < minimum:
            chosen.append(item.id)

    selected = set(chosen)
    for item in items:
        item.selected = item.id in selected


# --------------------------------------------------------------------------- #
# Havuzun kurulması
# --------------------------------------------------------------------------- #


def build_pool(
    resume: Resume,
    analysis: JobAnalysis,
    posting_text: str = "",
    *,
    slug: str = "",
    language: Language | None = None,
    vocabulary: TagVocabulary | None = None,
    weights: Weights | None = None,
    today: date | None = None,
) -> CandidatePool:
    """`master.json` × `job.json` → aday havuzu. Ağa çıkmaz, LLM kullanmaz."""
    vocab = vocabulary or default_vocabulary()
    active = weights or Weights()
    target = language or analysis.posting.language
    profile = posting_tags(analysis, posting_text, vocabulary=vocab)
    posting = analysis.posting

    context = _Context(
        domain=posting.domain,
        posting_seniority=posting.seniority,
        weights=active,
        vocabulary=vocab,
        today=today,
    )

    experiences: list[ExperienceCandidates] = []
    for item in resume.sections.experience.items:
        if item.hidden:
            continue
        bullets = [
            _to_scored(bullet, item.id, "bullet", profile, period=item.period, context=context)
            for bullet in item.x_bullets
            if _lang_fits(bullet.x_meta, target)
        ]
        _select(bullets, MAX_BULLETS_PER_EXPERIENCE, minimum=MIN_BULLETS_PER_EXPERIENCE)
        experiences.append(
            ExperienceCandidates(
                id=item.id,
                title=item.company,
                subtitle=item.position,
                period=item.period,
                always_include=bool(item.x_meta and item.x_meta.always_include),
                bullets=_rank(bullets),
            )
        )

    projects: list[ExperienceCandidates] = []
    for item in resume.sections.projects.items:
        if item.hidden:
            continue
        bullets = [
            _to_scored(bullet, item.id, "project", profile, period=item.period, context=context)
            for bullet in item.x_bullets
            if _lang_fits(bullet.x_meta, target)
        ]
        _select(bullets, MAX_BULLETS_PER_PROJECT)
        projects.append(
            ExperienceCandidates(
                id=item.id,
                title=item.name,
                period=item.period,
                always_include=bool(item.x_meta and item.x_meta.always_include),
                bullets=_rank(bullets),
            )
        )

    # Deneyimler kronolojik kalır (CV sırası), projeler skora göre sıralanır:
    # proje bölümü CV'nin esnek kısmı, deneyim bölümü değil. Sıralama modele
    # bir öneri.
    projects.sort(key=lambda entry: (-entry.score, entry.id))
    if MAX_PROJECTS is not None:
        for entry in projects[MAX_PROJECTS:]:
            if entry.always_include:
                continue
            for bullet in entry.bullets:
                bullet.selected = False

    skills: list[ScoredItem] = []
    for item in resume.sections.skills.items:
        if item.hidden or not _lang_fits(item.x_meta, target):
            continue
        breakdown, matched_must, matched_nice = score_bullet(
            item.x_meta, item.name, profile, **context.as_kwargs()
        )
        skills.append(
            ScoredItem(
                id=item.id,
                kind="skill",
                text=item.name,
                tags=list(item.x_meta.tags) if item.x_meta else [],
                lang=item.x_meta.lang if item.x_meta else None,
                always_include=bool(item.x_meta and item.x_meta.always_include),
                matched_must=matched_must,
                matched_nice=matched_nice,
                breakdown=breakdown,
            )
        )
    _select(skills, MAX_SKILLS)
    skills = _rank(skills)

    pool = CandidatePool(
        slug=slug,
        language=target,
        must_tags=list(profile.must),
        nice_tags=list(profile.nice),
        experiences=experiences,
        projects=projects,
        skills=skills,
        recall=profile.recall,
        weights=active,
    )
    pool.coverage = _coverage(pool, analysis, profile)
    pool.missing_tags = _missing_tags(resume, profile)
    pool.budget = _budget(resume, pool)
    return pool


def _coverage(
    pool: CandidatePool, analysis: JobAnalysis, profile: PostingTags
) -> list[RequirementCoverage]:
    """Her gereksinimin CV'de karşılığı var mı — issue 09'un gereksinim tablosu."""
    selected_bullets = [
        bullet
        for group in (pool.experiences, pool.projects)
        for entry in group
        for bullet in entry.selected_bullets
    ]
    other_bullets = [
        bullet
        for group in (pool.experiences, pool.projects)
        for entry in group
        for bullet in entry.bullets
        if not bullet.selected
    ]

    rows: list[RequirementCoverage] = []
    for index, tags in profile.per_requirement:
        requirement = analysis.posting.requirements[index]
        tag_set = set(tags)

        bullet_ids = [b.id for b in selected_bullets if tag_set & set(b.tags)]
        skill_ids = [s.id for s in pool.skills if s.selected and tag_set & set(s.tags)]
        fallback = [b.id for b in other_bullets if tag_set & set(b.tags)]

        if bullet_ids:
            status = "covered"
        elif skill_ids or fallback:
            status = "indirect"
        else:
            status = "missing"

        rows.append(
            RequirementCoverage(
                index=index,
                text=requirement.text,
                kind=requirement.kind,
                tags=list(tags),
                status=status,
                bullet_ids=bullet_ids or fallback,
                skill_ids=skill_ids,
            )
        )
    return rows


def _missing_tags(resume: Resume, profile: PostingTags) -> list[str]:
    """İlanda geçen ama süper sette hiç karşılığı olmayan etiketler.

    Rapordaki "eksik anahtar kelimeler" listesi bu — gerçekten bilmediğin şey mi,
    yoksa `master.json`'a eklemeyi mi unuttun sorusu buradan çıkıyor.
    """
    owned: set[str] = set()
    for section_name in ("experience", "projects", "skills"):
        for item in getattr(resume.sections, section_name).items:
            if item.x_meta:
                owned.update(item.x_meta.tags)
    for _, bullet in resume.iter_bullets():
        if bullet.x_meta:
            owned.update(bullet.x_meta.tags)

    return [tag for tag in (*profile.must, *profile.nice) if tag not in owned]


def _budget(resume: Resume, pool: CandidatePool) -> TokenBudget:
    """Ön filtrenin LLM adımından ne kadar metin eksilttiği."""
    superset = [bullet.text for _, bullet in resume.iter_bullets()]
    superset += [item.name for item in resume.sections.skills.items]

    selected = pool.all_selected()
    return TokenBudget(
        superset_chars=sum(len(text) for text in superset),
        candidate_chars=sum(len(item.text) for item in selected),
        superset_items=len(superset),
        candidate_items=len(selected),
    )


# --------------------------------------------------------------------------- #
# Kalıcılık
# --------------------------------------------------------------------------- #


def write_pool(run_dir: Path, pool: CandidatePool) -> Path:
    """`candidates.json`'ı çalıştırma dizinine yazar."""
    path = run_dir / CANDIDATES_FILENAME
    path.write_text(pool.model_dump_json(indent=2), encoding="utf-8", newline="")
    return path


def read_pool(path: Path) -> CandidatePool:
    """Var olan bir `candidates.json`'ı okur."""
    return CandidatePool.model_validate_json(path.read_text(encoding="utf-8"))


def find_run(slug: str, root: Path | None = None) -> Path | None:
    """Slug parçasından çalıştırma dizinini bulur — `score <slug>` bunu kullanıyor.

    Tam ad da, ayırt edici bir parça da kabul edilir; birden çok eşleşmede en
    yenisi seçilir (dizin adının sonundaki zaman damgası sıralamayı veriyor).
    """
    from cv_updater.paths import OUT_DIR
    from cv_updater.steps.analyze_job import _run_sort_key

    base = root or OUT_DIR
    if not base.is_dir():
        return None

    exact = base / slug
    if exact.is_dir():
        return exact

    needle = slug.lower()
    matches = [path for path in base.iterdir() if path.is_dir() and needle in path.name.lower()]
    if not matches:
        return None
    return sorted(matches, key=_run_sort_key)[-1]
