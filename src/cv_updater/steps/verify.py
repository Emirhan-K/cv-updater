"""Dürüstlük guardrail'i (issue 07) — **modele güvenme, doğrula.**

Uyarlama adımı modelden bir `TailorPlan` alır. Bu modül o planı, modele hiç
sormadan, `master.json`'a karşı denetler. Bir madde ancak şu üç şeyi birden
sağlıyorsa CV'ye girebilir:

1. **Kaynağı var.** `source_id` süper sette ve ön filtrenin havuzunda olmalı.
2. **Sayı uydurulmamış.** `rewritten` içindeki her sayı kaynak maddede geçmeli.
   Halüsinasyonun en sık biçimi bu: "üç ekip" → "yedi ekip", "%20" → "%45".
3. **Varlık uydurulmamış.** Teknoloji, ürün ve şirket adları kaynak metinde,
   kaynağın etiketlerinde ya da deneyimin kendi bağlamında geçmeli.

Unvan, şirket ve tarih hiç denetlenmiyor çünkü **modelden hiç geçmiyorlar**:
`TailorPlan`'da böyle bir alan yok, `tailored.json` onları doğrudan
`master.json`'dan kopyalıyor (issue 08). Guardrail'in en güçlü kuralı, şemada
o alanın bulunmaması.

İhlal bulunduğunda iki davranış var:

* `--strict` (varsayılan): adım durur, ihlaller yazdırılır ve
  `out/<slug>/violations.json`'a yazılır. **Hiçbir şey üretilmez.**
* `--repair`: ihlalli madde kaynak metnine geri döndürülür ve akış devam eder.
  Onarım her zaman *geri* yönde: uydurulan metin atılır, süper setteki gerçek
  metin geri gelir.

Ayrıntılı gerekçe ve her kuralın yakaladığı hata biçimi `docs/GUARDRAILS.md`'de.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from cv_updater.models.candidates import CandidatePool
from cv_updater.models.resume import Resume
from cv_updater.models.tailor import TailorPlan
from cv_updater.steps.analyze_job import fold
from cv_updater.tags import TagVocabulary, default_vocabulary

VIOLATIONS_FILENAME = "violations.json"

Level = Literal["error", "warning"]

#: Yeniden ifade kaynağın kaç katına kadar uzayabilir? Üstü şişirme sinyali.
MAX_LENGTH_RATIO = 1.8

#: Sayı: "8", "12,5", "3.12", "1.500". Karşılaştırma rakamlar üzerinden yapılır,
#: böylece binlik ayracı ("1.500" ↔ "1500") yanlış alarm üretmez.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_DIGITS = re.compile(r"\d")

#: Cümle sonu — büyük harfli ilk kelime özel isim sayılmasın diye gerekiyor.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?:;])\s+|\n+")
_TOKEN_SPLIT = re.compile(r"[\s]+")
#: Kelimenin başındaki/sonundaki noktalama ve Türkçe ek kesmesi ("Docker'da").
_TRIM = "\"'’“”().,;:!?…-–—*[]{}"

_UPPER = "A-ZÇĞİÖŞÜ"
_LOWER = "a-zçğıöşü"

#: "FastAPI", "PostgreSQL", "LanceDB" — küçük harften sonra gelen büyük harf.
_CAMEL_CASE = re.compile(f"[{_LOWER}][{_UPPER}]")
#: Tireli/eğik çizgili bileşiği parçalarına ayırır: "LLM-based" → ["LLM", "based"].
_COMPOUND_SPLIT = re.compile(r"[-–—/]")
_STARTS_UPPER = re.compile(f"^[{_UPPER}]")
_HAS_LETTER = re.compile(f"[{_UPPER}{_LOWER}]")


class Violation(BaseModel):
    """Tek bir guardrail ihlali."""

    level: Level
    rule: str
    where: str
    message: str
    detail: str = ""
    #: `--repair` bu ihlali onardıysa ne yaptığı.
    repaired_with: str | None = None

    def __str__(self) -> str:
        tail = f" → {self.repaired_with}" if self.repaired_with else ""
        return f"[{self.level}] {self.rule} @ {self.where}: {self.message}{tail}"


class VerificationReport(BaseModel):
    """`out/<slug>/violations.json` — denetimin tam kaydı."""

    violations: list[Violation] = Field(default_factory=list)
    #: Kaç madde denetlendi (özet dahil).
    checked: int = 0
    repaired: int = 0

    @property
    def errors(self) -> list[Violation]:
        return [v for v in self.violations if v.level == "error"]

    @property
    def warnings(self) -> list[Violation]:
        return [v for v in self.violations if v.level == "warning"]

    @property
    def ok(self) -> bool:
        """Hata yoksa plan geçebilir. Uyarılar akışı durdurmaz."""
        return not self.errors


# --------------------------------------------------------------------------- #
# Kaynak tarafı
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SourceText:
    """Bir `source_id`'nin arkasındaki doğruluk kaynağı."""

    id: str
    text: str
    tags: tuple[str, ...] = ()
    #: Deneyimin şirket / unvan / dönem bilgisi. Madde metninde geçmese de
    #: doğrudur — "Preunec'te kurdu" yeniden ifadesi uydurma sayılmamalı.
    context: str = ""

    @property
    def grounded_text(self) -> str:
        return f"{self.text} {self.context} {' '.join(self.tags)}"


def resolve_id(source_id: str, offered: set[str]) -> str | None:
    """Sonek düşürülmüş bir id'yi havuzdaki karşılığına eşler.

    Madde id'leri dil sonekiyle bitiyor (`exp-preunec.rag.tr`). İngilizce
    çalıştırmada model soneki ısrarla düşürüyor ve `exp-preunec.rag` yazıyor —
    doğru maddeyi seçmiş, sadece ön filtrenin zaten karara bağladığı bir
    muhasebe ekini atlamış oluyor.

    **Yalnızca tek bir aday varsa** çözülür ve bu bir uyarı olarak raporlanır;
    sessiz bir düzeltme değil. Belirsizlikte (iki dil varyantı da havuzdaysa)
    çözüm yapılmaz, ihlal olarak kalır — tahmin etmek guardrail'i delerdi.
    """
    if source_id in offered:
        return source_id
    prefix = f"{source_id}."
    matches = [item for item in offered if item.startswith(prefix)]
    return matches[0] if len(matches) == 1 else None


def index_sources(resume: Resume) -> dict[str, SourceText]:
    """Süper setteki her madde ve yeteneği id'siyle indeksler."""
    sources: dict[str, SourceText] = {}

    for item in resume.sections.experience.items:
        context = " ".join(
            [item.company, item.position, item.period, *(role.position for role in item.roles)]
        )
        for bullet in item.x_bullets:
            sources[bullet.id] = SourceText(
                id=bullet.id,
                text=bullet.text,
                tags=tuple(bullet.x_meta.tags) if bullet.x_meta else (),
                context=context,
            )

    for item in resume.sections.projects.items:
        context = f"{item.name} {item.period}"
        for bullet in item.x_bullets:
            sources[bullet.id] = SourceText(
                id=bullet.id,
                text=bullet.text,
                tags=tuple(bullet.x_meta.tags) if bullet.x_meta else (),
                context=context,
            )

    for skill in resume.sections.skills.items:
        sources[skill.id] = SourceText(
            id=skill.id,
            text=skill.name,
            tags=tuple(skill.x_meta.tags) if skill.x_meta else (),
        )

    return sources


# --------------------------------------------------------------------------- #
# Kural 1: sayı
# --------------------------------------------------------------------------- #


def numbers(text: str) -> set[str]:
    """Metindeki sayılar, ayraçlarından arındırılmış halde.

    "1.500" ile "1500" aynı sayıdır; Türkçe binlik ayracı yanlış alarm
    üretmesin diye karşılaştırma yalnızca rakamlar üzerinden yapılıyor.
    """
    return {"".join(_DIGITS.findall(match.group())) for match in _NUMBER.finditer(text)}


def invented_numbers(rewritten: str, source: SourceText) -> set[str]:
    """`rewritten` içinde olup kaynakta olmayan sayılar."""
    return numbers(rewritten) - numbers(source.grounded_text)


# --------------------------------------------------------------------------- #
# Kural 2: varlık (teknoloji / ürün / şirket)
# --------------------------------------------------------------------------- #


@dataclass
class Entities:
    """Bir metinden çıkarılan adlar, güven seviyesine göre ayrılmış."""

    #: Teknoloji biçimli: "FastAPI", "REST", "S3", "CI/CD".
    technical: list[str] = field(default_factory=list)
    #: Cümle başında olmayan sıradan büyük harfli kelime: "Kubernetes", "Google".
    #: Ayrı tutuluyorlar çünkü yakalama güveni farklı, ama ikisi de hata seviyesinde —
    #: "Django" ve "Kubernetes" gibi en sık uydurulan adlar bu kategoriye düşüyor,
    #: uyarı seviyesine indirmek guardrail'in asıl deliğini açardı.
    proper: list[str] = field(default_factory=list)


def _clean(token: str) -> str:
    """Noktalama ve Türkçe eki atar: "Docker'da." → "Docker"."""
    token = token.strip(_TRIM)
    for apostrophe in ("'", "’"):
        if apostrophe in token:
            token = token.split(apostrophe, 1)[0]
    return token.strip(_TRIM)


def _is_technical(token: str) -> bool:
    """Teknoloji adına benzeyen belirteç.

    Üç biçim: içinde hem harf hem rakam ("S3", "GPT-4"), tamamı büyük harf
    ("REST", "AWS"), ya da içeride büyük harf ("FastAPI", "PostgreSQL").
    """
    if len(token) < 2 or not _HAS_LETTER.search(token):
        return False
    if _DIGITS.search(token):
        return True
    letters = [char for char in token if char.isalpha()]
    if len(letters) >= 2 and all(char.isupper() for char in letters):
        return True
    return bool(_CAMEL_CASE.search(token))


def extract_entities(text: str, vocabulary: TagVocabulary | None = None) -> Entities:
    """Metindeki teknoloji ve özel isimleri ayıklar.

    Cümlenin ilk kelimesi özel isim sayılmaz: Türkçede her cümle büyük harfle
    başlıyor, aksi halde her maddenin ilk kelimesi yanlış alarm üretirdi.
    Kontrollü sözlükteki bir terim ise cümlenin neresinde olursa olsun sayılır.
    """
    vocab = vocabulary or default_vocabulary()
    found = Entities()

    for sentence in _SENTENCE_SPLIT.split(text):
        tokens = [_clean(token) for token in _TOKEN_SPLIT.split(sentence)]
        tokens = [token for token in tokens if token]
        for index, token in enumerate(tokens):
            if _is_technical(token):
                if token not in found.technical:
                    found.technical.append(token)
            elif index > 0 and _STARTS_UPPER.match(token) and token not in found.proper:
                found.proper.append(token)

    # Sözlükteki çok kelimeli terimler ("Görüntü İşleme") belirteç bazlı taramaya
    # takılmıyor; onları ayrıca ara.
    haystack = fold(text)
    covered: set[str] = set()
    for term in sorted(vocab.aliases) + sorted(vocab.canonical):
        needle = fold(term).strip()
        if " " in needle and f" {needle} " in haystack:
            covered.update(needle.split())
            if term not in found.technical:
                found.technical.append(term)

    # "Görüntü İşleme" tek bir terim olarak yakalandıysa "Görüntü" ve "İşleme"
    # ayrıca özel isim diye raporlanmasın.
    found.proper = [token for token in found.proper if fold(token).strip() not in covered]
    return found


def _is_grounded(entity: str, source: SourceText, vocabulary: TagVocabulary) -> bool:
    """Varlık kaynakta ya da kaynağın etiketlerinde karşılığı var mı?

    Etiket kontrolü kritik: İngilizce ilana Türkçe bir maddeden "computer
    vision" yazılması uydurma değil, çeviridir — kaynağın `x_meta.tags`
    listesinde `computer-vision` duruyor.
    """
    if _grounded_token(entity, source, vocabulary):
        return True

    # Tireli bileşik: "LLM-based", "RAG-based", "Python-first". Anlamlı olan
    # parçaların hepsi temelliyse bileşik de temellidir. İlk İngilizce
    # çalıştırmada iki yanlış alarm tam buradan geldi. "Django-powered" yine
    # yakalanır: "Django" parçası temelli değil.
    parts = [part for part in _COMPOUND_SPLIT.split(entity) if part]
    if len(parts) < 2:
        return False

    significant = [part for part in parts if _is_technical(part) or _STARTS_UPPER.match(part)]
    return bool(significant) and all(
        _grounded_token(part, source, vocabulary) for part in significant
    )


def _grounded_token(entity: str, source: SourceText, vocabulary: TagVocabulary) -> bool:
    """Tek bir adın kaynakta karşılığı var mı?

    İngilizce çoğul eki de denenir: kaynakta "LLM" varken yeniden ifadede "LLMs"
    yazmak uydurma değil. (Türkçe ekler zaten kesme işaretinden kesiliyor.)
    """
    from cv_updater.steps.analyze_job import _normalize_term

    haystack = fold(source.grounded_text)
    tag_parts = {part for tag in source.tags for part in tag.split("-")}

    candidates = [entity]
    if len(entity) > 2 and entity.lower().endswith("s"):
        candidates.append(entity[:-1])

    for candidate in candidates:
        needle = fold(candidate).strip()
        if not needle:
            return True
        if f" {needle} " in haystack or needle in tag_parts:
            return True
        # Etiket eşlemesi kritik: `machine-learning` etiketi "ML" kısaltmasını,
        # `computer-vision` etiketi "computer vision" çevirisini meşrulaştırır.
        canonical = _normalize_term(candidate, vocabulary)
        if canonical is not None and canonical in source.tags:
            return True

    return False


def invented_entities(
    rewritten: str, source: SourceText, vocabulary: TagVocabulary | None = None
) -> Entities:
    """`rewritten` içinde olup kaynakta karşılığı olmayan adlar."""
    vocab = vocabulary or default_vocabulary()
    found = extract_entities(rewritten, vocab)
    return Entities(
        technical=[e for e in found.technical if not _is_grounded(e, source, vocab)],
        proper=[e for e in found.proper if not _is_grounded(e, source, vocab)],
    )


# --------------------------------------------------------------------------- #
# Denetim
# --------------------------------------------------------------------------- #


def _check_text(
    where: str,
    rewritten: str,
    source: SourceText,
    vocabulary: TagVocabulary,
) -> list[Violation]:
    """Bir yeniden ifadeyi kaynağına karşı denetler."""
    found: list[Violation] = []

    if extra := invented_numbers(rewritten, source):
        found.append(
            Violation(
                level="error",
                rule="number.invented",
                where=where,
                message=f"Kaynakta olmayan sayı: {', '.join(sorted(extra))}",
                detail=f"kaynak: {source.text}",
            )
        )

    entities = invented_entities(rewritten, source, vocabulary)
    if entities.technical:
        found.append(
            Violation(
                level="error",
                rule="entity.invented",
                where=where,
                message=f"Kaynakta olmayan teknoloji/ürün adı: {', '.join(entities.technical)}",
                detail=f"kaynak: {source.text}",
            )
        )
    if entities.proper:
        found.append(
            Violation(
                level="error",
                rule="entity.unknown_name",
                where=where,
                message=f"Kaynakta geçmeyen özel isim: {', '.join(entities.proper)}",
                detail=f"kaynak: {source.text}",
            )
        )

    source_length = len(source.text.strip())
    if source_length and len(rewritten.strip()) / source_length > MAX_LENGTH_RATIO:
        found.append(
            Violation(
                level="warning",
                rule="text.drift",
                where=where,
                message=(
                    f"Yeniden ifade kaynağın {len(rewritten) / source_length:.1f} katı — "
                    "şişirme sinyali."
                ),
                detail=f"kaynak: {source.text}",
            )
        )

    return found


def verify(
    plan: TailorPlan,
    pool: CandidatePool,
    resume: Resume,
    *,
    repair: bool = False,
    vocabulary: TagVocabulary | None = None,
) -> tuple[TailorPlan, VerificationReport]:
    """Planı denetler; `repair=True` ise ihlalli metinleri kaynağına döndürür.

    Dönen plan `repair` kapalıyken **girdiyle aynı nesnedir**: doğrulama saf,
    onarım açıkça istendiğinde yapılıyor.
    """
    vocab = vocabulary or default_vocabulary()
    sources = index_sources(resume)
    offered = {item.id for item in pool.all_selected()}

    violations: list[Violation] = []
    checked = 0
    repaired = 0
    seen: set[str] = set()

    # Özet dayanakları ve yetenek/proje seçimleri de aynı sonek düşmesinden
    # etkileniyor; onları da tek adaya çözülebiliyorsa çöz.
    plan.summary_source_ids = [
        resolve_id(item_id, offered) or item_id for item_id in plan.summary_source_ids
    ]
    plan.skill_ids = [resolve_id(item_id, offered) or item_id for item_id in plan.skill_ids]

    for experience in plan.experiences:
        kept = []
        for bullet in experience.bullets:
            checked += 1

            if (resolved := resolve_id(bullet.source_id, offered)) != bullet.source_id:
                if resolved is not None:
                    violations.append(
                        Violation(
                            level="warning",
                            rule="source.suffix_dropped",
                            where=bullet.source_id,
                            message=f"Dil soneki düşürülmüş; tek adaya çözüldü: {resolved}",
                        )
                    )
                    bullet.source_id = resolved

            source = sources.get(bullet.source_id)

            if source is None or bullet.source_id not in offered:
                unknown = source is None
                violations.append(
                    Violation(
                        level="error",
                        rule="source.unknown" if unknown else "source.not_offered",
                        where=bullet.source_id,
                        message=(
                            "Bu id süper sette yok — madde uydurulmuş."
                            if unknown
                            else "Bu madde aday havuzunda yoktu — model kendi eklemiş."
                        ),
                        detail=bullet.rewritten[:160],
                        repaired_with="madde atıldı" if repair else None,
                    )
                )
                if repair:
                    repaired += 1
                continue

            if bullet.source_id in seen:
                violations.append(
                    Violation(
                        level="warning",
                        rule="source.duplicate",
                        where=bullet.source_id,
                        message="Aynı madde birden fazla kez seçilmiş.",
                    )
                )
            seen.add(bullet.source_id)

            found = _check_text(bullet.source_id, bullet.rewritten, source, vocab)
            if repair and any(v.level == "error" for v in found):
                for violation in found:
                    violation.repaired_with = "kaynak metnine döndürüldü"
                bullet.rewritten = source.text
                repaired += 1
            violations.extend(found)
            kept.append(bullet)

        if repair:
            experience.bullets = kept

    summary_found, summary_repaired = _check_summary(
        plan, sources, vocab, repair=repair, resume=resume, selected=offered
    )
    violations.extend(summary_found)
    checked += 1
    repaired += summary_repaired

    known_skills = {item.id for item in resume.sections.skills.items}
    known_projects = {item.id for item in resume.sections.projects.items}
    offered_projects = {entry.id for entry in pool.projects if entry.selected_bullets}

    violations += _check_ids("skill_ids", plan.skill_ids, known_skills, offered)
    violations += _check_ids("project_ids", plan.project_ids, known_projects, offered_projects)

    if repair:
        before = len(plan.skill_ids) + len(plan.project_ids)
        plan.skill_ids = [item_id for item_id in plan.skill_ids if item_id in offered]
        plan.project_ids = [item_id for item_id in plan.project_ids if item_id in offered_projects]
        repaired += before - len(plan.skill_ids) - len(plan.project_ids)

    return plan, VerificationReport(violations=violations, checked=checked, repaired=repaired)


def _check_summary(
    plan: TailorPlan,
    sources: dict[str, SourceText],
    vocabulary: TagVocabulary,
    *,
    repair: bool,
    resume: Resume,
    selected: set[str],
) -> tuple[list[Violation], int]:
    """Özet, CV'ye giren malzemenin toplamına karşı denetlenir.

    Özet planın en serbest alanı, dolayısıyla en riskli yeri. Kural aynı: içindeki
    her sayı ve her ad, CV'de görünen bir kaynakta geçmeli.

    `summary_source_ids` **izlenebilirlik sözleşmesi**: boş bırakılamaz ve var
    olmayan id gösteremez. Ama temellendirme yalnızca o listeye bakmıyor, havuzun
    seçilen tamamına bakıyor. Sebebi ilk gerçek çalıştırmada görüldü: model
    özette "FastAPI ile mikro servis mimarileri" yazmıştı, FastAPI **seçilmiş bir
    yetenek** olduğu halde hiçbir maddenin metninde geçmediği için uydurma
    sayılıyordu. Özet CV'yi özetler; CV'de duran ve zaten `master.json`'dan gelen
    bir şeyi tekrar etmesi uydurma değildir.
    """
    found: list[Violation] = []
    if not plan.summary.strip():
        return found, 0

    unknown = [sid for sid in plan.summary_source_ids if sid not in sources]
    if unknown:
        found.append(
            Violation(
                level="error",
                rule="summary.unknown_source",
                where="summary",
                message=f"Özetin dayanağı olarak var olmayan id verilmiş: {', '.join(unknown)}",
            )
        )

    known = [sources[sid] for sid in plan.summary_source_ids if sid in sources]
    if not known:
        found.append(
            Violation(
                level="error",
                rule="summary.ungrounded",
                where="summary",
                message="Özet hiçbir maddeye dayandırılmamış (`summary_source_ids` boş).",
            )
        )
    else:
        grounding = list(known)
        grounding += [
            sources[item_id]
            for item_id in sorted(selected)
            if item_id in sources and item_id not in plan.summary_source_ids
        ]
        merged = SourceText(
            id="summary",
            text=" ".join(source.text for source in grounding),
            tags=tuple({tag for source in grounding for tag in source.tags}),
            context=" ".join(source.context for source in grounding),
        )
        found.extend(_check_text("summary", plan.summary, merged, vocabulary))

    if repair and any(violation.level == "error" for violation in found):
        # Özetin kaynak metni yok; süper setteki özete dönmek tek dürüst çare.
        from cv_updater.lint import strip_html

        plan.summary = " ".join(strip_html(resume.summary.content).split())
        for violation in found:
            violation.repaired_with = "master.json'daki özete döndürüldü"
        return found, 1

    return found, 0


def _check_ids(
    where: str,
    ids: list[str],
    known: set[str],
    offered: set[str],
) -> list[Violation]:
    """Yetenek / proje id listelerinde uydurma var mı?

    Model yalnızca aday havuzunu gördü; havuzda olmayan bir id ya süper sette
    hiç yok (uydurma) ya da modelin kendi eklediği bir şey. İkisi de hata.
    """
    found: list[Violation] = []
    for item_id in ids:
        if item_id not in known:
            found.append(
                Violation(
                    level="error",
                    rule="source.unknown",
                    where=f"{where}/{item_id}",
                    message="Bu id süper sette yok.",
                )
            )
        elif item_id not in offered:
            found.append(
                Violation(
                    level="error",
                    rule="source.not_offered",
                    where=f"{where}/{item_id}",
                    message="Aday havuzunda olmayan bir id seçilmiş.",
                )
            )
    return found


# --------------------------------------------------------------------------- #
# Kalıcılık
# --------------------------------------------------------------------------- #


def write_report(run_dir: Path, report: VerificationReport) -> Path:
    """`violations.json`'ı yazar — temiz çalıştırmada da yazılır (boş liste kanıttır)."""
    path = run_dir / VIOLATIONS_FILENAME
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8", newline="")
    return path


def read_report(path: Path) -> VerificationReport:
    return VerificationReport.model_validate_json(path.read_text(encoding="utf-8"))
