"""Başvuru günlüğü (issue 12) — başvuru başına bir kayıt.

Plan haftada 10-15 hedefli başvuru (`TODO.md` §8). Üçüncü haftada "buraya
başvurmuş muydum, hangi CV'yi göndermiştim, ne demiştim?" kaçınılmaz. Araç
zaten her başvuruda çalışıyor; kaydı da o tutsun.

`applications/<tarih>-<şirket>-<pozisyon>/` altında çalıştırmanın kopyası
duruyor — `out/` deneme alanı, burası **arşiv**. `out/` bir ilanı üç kez
uyarlamak için var; buraya yalnızca gönderilen sürüm giriyor.

Modül tamamen deterministik: LLM yok, ağ yok. `meta.json` dışındaki her şey
kopyalanan dosya.

## Neden `out/`den ayrı

`out/<slug>-<damga>/` her çalıştırmada yeniden doğuyor ve gitignore'da; bir
ilanı yeniden uyarlamak eskisini bırakıyor ama hangisinin **gönderildiği**
bilgisi hiçbir yerde yok. Başvuru günlüğü tam olarak o bilgiyi tutuyor:
gönderilen sürüm, ne zaman gönderildiği, ve sonrasında ne olduğu.
"""

from __future__ import annotations

import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from cv_updater.paths import APPLICATIONS_DIR
from cv_updater.steps.intake import slugify

META_FILENAME = "meta.json"

Status = Literal[
    "prepared",
    "applied",
    "response",
    "interview",
    "offer",
    "rejected",
    "silent",
]

#: Terminal ve rapor etiketleri. Anahtarlar makine tarafı (dosyada duran),
#: değerler insan tarafı — ikisini karıştırmamak kayıtları taşınabilir tutuyor.
STATUS_LABELS: dict[Status, str] = {
    "prepared": "hazırlandı",
    "applied": "başvuruldu",
    "response": "ilk dönüş",
    "interview": "teknik mülakat",
    "offer": "teklif",
    "rejected": "red",
    "silent": "sessiz",
}

#: Karşı taraf **herhangi bir şey** söyledi mi? Red de bir cevaptır.
ANSWERED: tuple[Status, ...] = ("response", "interview", "offer", "rejected")
#: Süreç ilerledi mi? Eşik ayarı bu orana bakıyor.
POSITIVE: tuple[Status, ...] = ("response", "interview", "offer")

#: `applied` durumunda bu kadar gün bekleyen başvuru takip ister.
FOLLOW_UP_DAYS = 14

#: `stats`'ın uyum skorunu böldüğü aralıklar. Sorunun cevabı burada okunuyor:
#: "hangi skorun üstünde gerçekten dönüş alıyorum?"
SCORE_BUCKETS: tuple[tuple[int, int], ...] = ((0, 45), (45, 60), (60, 70), (70, 85), (85, 101))

#: Dizin adının okunur kısmının üst sınırı. Windows'ta yol uzunluğu 260
#: karakterle sınırlı ve arşivin altında bir de dosya adı var.
_SLUG_MAX_LEN = 60


class Application(BaseModel):
    """`applications/<id>/meta.json` — başvurunun tam kaydı."""

    #: Dizin adı. Kayıt taşınırsa da geçerli kalsın diye dosyanın içinde de duruyor.
    id: str
    company: str = ""
    position: str = ""
    #: "LinkedIn", "Kariyer.net", "şirket sitesi" — serbest metin, elle girilir.
    source: str = ""
    url: str = ""
    #: Uyum raporundaki yüzde (issue 09). Eşik ayarının girdisi.
    fit_score: int = 0
    #: Hangi `out/` çalıştırmasından geldiği — izlenebilirlik.
    run_slug: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    #: Başvurunun gerçekten gönderildiği an; `status` ilk kez `applied`
    #: olduğunda doluyor. Takip hatırlatıcısı buna bakıyor.
    applied_at: datetime | None = None
    status: Status = "prepared"
    #: Durum geçmişi: ne zaman ne olduğu. Silinmiyor, üstüne yazılıyor değil.
    history: list[str] = Field(default_factory=list)
    notes: str = ""

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def answered(self) -> bool:
        return self.status in ANSWERED

    @property
    def positive(self) -> bool:
        return self.status in POSITIVE

    def days_waiting(self, today: datetime | None = None) -> int | None:
        """`applied` durumunda kaç gündür bekliyor? Başka durumda None."""
        if self.status != "applied" or self.applied_at is None:
            return None
        return ((today or datetime.now()) - self.applied_at).days

    def needs_follow_up(self, today: datetime | None = None) -> bool:
        waiting = self.days_waiting(today)
        return waiting is not None and waiting >= FOLLOW_UP_DAYS


# --------------------------------------------------------------------------- #
# Kimlik ve kalıcılık
# --------------------------------------------------------------------------- #


def make_id(company: str, position: str, when: date | None = None) -> str:
    """`20260808-novatek-ai-muhendisi` — Windows'ta da güvenli bir dizin adı.

    `slugify` Türkçe harfleri ASCII'ye indiriyor ve `/` dahil her ayracı tireye
    çeviriyor, yani "Yazılım Mühendisi (AI/ML)" bir alt dizin açmıyor. Tarih
    öneki hem sıralamayı veriyor hem de adın Windows'un ayrılmış adlarına
    (`CON`, `PRN`, `AUX`…) denk gelmesini imkânsız kılıyor.
    """
    stamp = (when or date.today()).strftime("%Y%m%d")
    label = slugify(f"{company} {position}".strip(), max_len=_SLUG_MAX_LEN)
    return f"{stamp}-{label}" if label else stamp


def _unique_dir(base: Path, identifier: str) -> Path:
    """Aynı gün aynı şirkete ikinci başvuru öncekini ezmez."""
    candidate = base / identifier
    counter = 2
    while candidate.exists():
        candidate = base / f"{identifier}-{counter}"
        counter += 1
    return candidate


#: Arşive giren dosyalar. Eksik olan sessizce atlanır — `cv.pdf` yalnızca
#: `render` çalıştırıldıysa var, `ats_report.md` de öyle.
ARCHIVED_FILES: tuple[str, ...] = (
    "job_raw.txt",
    "job.json",
    "tailored.json",
    "match_report.md",
    "cv.pdf",
    "ats_report.md",
)


def record(
    run_dir: Path,
    *,
    company: str,
    position: str,
    fit_score: int,
    source: str = "",
    url: str = "",
    root: Path | None = None,
    when: date | None = None,
) -> Application:
    """Bir çalıştırmayı arşive alır ve `meta.json`'ı yazar."""
    base = root or APPLICATIONS_DIR
    base.mkdir(parents=True, exist_ok=True)

    target = _unique_dir(base, make_id(company, position, when))
    target.mkdir(parents=True)

    for name in ARCHIVED_FILES:
        source_file = run_dir / name
        if source_file.exists():
            shutil.copy2(source_file, target / name)

    application = Application(
        id=target.name,
        company=company,
        position=position,
        source=source,
        url=url,
        fit_score=fit_score,
        run_slug=run_dir.name,
        history=[f"{datetime.now():%Y-%m-%d %H:%M} hazırlandı"],
    )
    write(target, application)
    return application


def attach(application_dir: Path, path: Path) -> Path | None:
    """Sonradan üretilen bir dosyayı (tipik olarak `cv.pdf`) arşive ekler."""
    if not path.exists() or path.name not in ARCHIVED_FILES:
        return None
    target = application_dir / path.name
    shutil.copy2(path, target)
    return target


def write(application_dir: Path, application: Application) -> Path:
    path = application_dir / META_FILENAME
    path.write_text(application.model_dump_json(indent=2), encoding="utf-8", newline="")
    return path


def read(path: Path) -> Application:
    return Application.model_validate_json(path.read_text(encoding="utf-8"))


def load_all(root: Path | None = None) -> list[Application]:
    """Bütün başvurular, en yenisi başta."""
    base = root or APPLICATIONS_DIR
    if not base.is_dir():
        return []

    found: list[Application] = []
    for directory in sorted(base.iterdir()):
        meta = directory / META_FILENAME
        if meta.is_file():
            found.append(read(meta))
    return sorted(found, key=lambda item: item.created_at, reverse=True)


def find_all(identifier: str, root: Path | None = None) -> list[Path]:
    """Parçaya uyan bütün başvurular. Tam ad verildiyse yalnızca o.

    Liste döndürmesi kasıtlı: aynı şirkete birden çok kez uyarlama yapmak
    normal ve dizin adları birbirine çok benziyor. Belirsizliği sessizce
    çözmek yanlış kaydın durumunu değiştirirdi — çağıran taraf sorsun.
    """
    base = root or APPLICATIONS_DIR
    if not base.is_dir():
        return []

    exact = base / identifier
    if (exact / META_FILENAME).is_file():
        return [exact]

    needle = identifier.lower()
    return [
        directory
        for directory in sorted(base.iterdir())
        if (directory / META_FILENAME).is_file() and needle in directory.name.lower()
    ]


def find(identifier: str, root: Path | None = None) -> Path | None:
    """Tek bir eşleşme; belirsizlikte `None`. Ayrımı `find_all` ile yap."""
    matches = find_all(identifier, root)
    return matches[0] if len(matches) == 1 else None


def find_by_run(run_slug: str, root: Path | None = None) -> Path | None:
    """Bir `out/` çalıştırmasından açılmış başvuru kaydı.

    `render` bunu kullanıyor: PDF sonradan üretildiği için arşive de sonradan
    ekleniyor. Aynı çalıştırmadan birden çok kayıt varsa en yenisi seçilir.
    """
    base = root or APPLICATIONS_DIR
    if not base.is_dir():
        return None

    matches = [
        directory
        for directory in sorted(base.iterdir())
        if (directory / META_FILENAME).is_file()
        and read(directory / META_FILENAME).run_slug == run_slug
    ]
    return matches[-1] if matches else None


def set_status(
    application_dir: Path,
    status: Status,
    *,
    note: str = "",
    now: datetime | None = None,
) -> Application:
    """Durumu günceller, geçmişe bir satır ekler ve dosyayı yazar."""
    stamp = now or datetime.now()
    application = read(application_dir / META_FILENAME)

    application.status = status
    if status == "applied" and application.applied_at is None:
        application.applied_at = stamp

    entry = f"{stamp:%Y-%m-%d %H:%M} {STATUS_LABELS[status]}"
    application.history.append(f"{entry} — {note}" if note else entry)
    if note:
        application.notes = note

    write(application_dir, application)
    return application


# --------------------------------------------------------------------------- #
# Süzme
# --------------------------------------------------------------------------- #


def parse_since(value: str, *, today: datetime | None = None) -> datetime:
    """`--since` değeri: `7d`, `3w`, `2026-08-01`.

    Gün/hafta kısayolları kasıtlı: "son iki hafta ne yaptım" sorusu tarih
    yazmayı hak etmiyor.
    """
    text = value.strip().lower()
    now = today or datetime.now()

    if text.endswith(("d", "g")) and text[:-1].isdigit():
        return now - timedelta(days=int(text[:-1]))
    if text.endswith(("w", "h")) and text[:-1].isdigit():
        return now - timedelta(weeks=int(text[:-1]))

    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            f"Anlaşılmayan tarih: {value!r}. `7d`, `3w` ya da `2026-08-01` yaz."
        ) from exc


def filter_applications(
    applications: list[Application],
    *,
    status: str | None = None,
    since: datetime | None = None,
) -> list[Application]:
    found = applications
    if status:
        found = [item for item in found if item.status == status]
    if since:
        found = [item for item in found if item.created_at >= since]
    return found


# --------------------------------------------------------------------------- #
# İstatistik
# --------------------------------------------------------------------------- #


class Bucket(BaseModel):
    """Bir uyum skoru aralığının dönüş performansı."""

    low: int
    high: int
    total: int = 0
    answered: int = 0
    positive: int = 0

    @property
    def label(self) -> str:
        return f"%{self.low}–{self.high - 1}" if self.high <= 100 else f"%{self.low}+"

    @property
    def positive_rate(self) -> float:
        return self.positive / self.total if self.total else 0.0

    @property
    def answered_rate(self) -> float:
        return self.answered / self.total if self.total else 0.0


class Stats(BaseModel):
    """`log stats` çıktısı — hepsi sayılabilir, hiçbiri tahmin değil."""

    total: int = 0
    #: `prepared`da kalmamış, gerçekten gönderilmiş olanlar.
    sent: int = 0
    answered: int = 0
    positive: int = 0
    silent: int = 0
    rejected: int = 0
    follow_up: int = 0
    per_week: dict[str, int] = Field(default_factory=dict)
    buckets: list[Bucket] = Field(default_factory=list)
    #: Anlamlı bir eşik önerebilmek için gereken en az gönderilmiş başvuru.
    #: Issue 12: "20-30 başvurudan sonra anlamlı".
    confidence_threshold: int = 20

    @property
    def answered_rate(self) -> float:
        return self.answered / self.sent if self.sent else 0.0

    @property
    def positive_rate(self) -> float:
        return self.positive / self.sent if self.sent else 0.0

    @property
    def confident(self) -> bool:
        return self.sent >= self.confidence_threshold

    @property
    def best_bucket(self) -> Bucket | None:
        """Dönüş oranı en yüksek, en az bir gönderim taşıyan aralık."""
        candidates = [bucket for bucket in self.buckets if bucket.total]
        if not candidates:
            return None
        return max(candidates, key=lambda bucket: (bucket.positive_rate, bucket.total))


def compute_stats(
    applications: list[Application],
    *,
    today: datetime | None = None,
) -> Stats:
    """Başvurulardan istatistikleri çıkarır.

    Oranların paydası **gönderilmiş** başvurular: `prepared`da bekleyen bir
    kayıt henüz kimseye ulaşmadığı için "dönüş alamadım" sayılmamalı.
    """
    now = today or datetime.now()
    sent = [item for item in applications if item.status != "prepared"]

    per_week: dict[str, int] = {}
    for item in applications:
        year, week, _ = item.created_at.isocalendar()
        per_week[f"{year}-H{week:02d}"] = per_week.get(f"{year}-H{week:02d}", 0) + 1

    buckets = [Bucket(low=low, high=high) for low, high in SCORE_BUCKETS]
    for item in sent:
        for bucket in buckets:
            if bucket.low <= item.fit_score < bucket.high:
                bucket.total += 1
                bucket.answered += int(item.answered)
                bucket.positive += int(item.positive)
                break

    return Stats(
        total=len(applications),
        sent=len(sent),
        answered=sum(1 for item in sent if item.answered),
        positive=sum(1 for item in sent if item.positive),
        silent=sum(1 for item in sent if item.status == "silent"),
        rejected=sum(1 for item in sent if item.status == "rejected"),
        follow_up=sum(1 for item in applications if item.needs_follow_up(now)),
        per_week=dict(sorted(per_week.items())),
        buckets=buckets,
    )
