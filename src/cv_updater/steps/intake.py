"""İlanı araca sokan üç yol: dosya, stdin, `--paste`.

Issue 04'ün amacı sürtünmeyi sıfırlamak: günde 2-3 başvuruda bu adım beş
saniyeden uzun sürmemeli. Bu yüzden hiçbir yol soru sormaz — tek istisna,
metin anlamlı olamayacak kadar kısaysa (yanlışlıkla boş pano) verilen onay.

Çıktı dizini **her çalıştırmada yeni**: `out/<slug>/`, slug'ın sonunda zaman
damgası var. Aynı ilanı iki kez çalıştırmak öncekini ezmez — bir ilana iki
farklı uyarlama denemek, denemeleri yan yana karşılaştırabilmek demek.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cv_updater.paths import OUT_DIR

STDIN_SENTINEL = "-"

RAW_FILENAME = "job_raw.txt"
CLEAN_FILENAME = "job_clean.txt"

#: Slug'ın okunur kısmının üst sınırı — zaman damgası bunun dışında.
_SLUG_MAX_LEN = 48

#: Türkçe karakterler ASCII'ye iner: dosya adları her kabukta ve her dosya
#: sisteminde aynı görünsün.
_TR_MAP = str.maketrans(
    {
        "ı": "i",
        "İ": "i",
        "ğ": "g",
        "Ğ": "g",
        "ü": "u",
        "Ü": "u",
        "ş": "s",
        "Ş": "s",
        "ö": "o",
        "Ö": "o",
        "ç": "c",
        "Ç": "c",
    }
)

_NON_SLUG = re.compile(r"[^a-z0-9]+")


class IntakeError(RuntimeError):
    """İlan metni alınamadı."""


@dataclass(frozen=True)
class Source:
    """Nereden okuduğumuz ve ne okuduğumuz."""

    text: str
    #: "file" · "stdin" · "paste" — hata mesajlarında ve `job.json`'da izlenir.
    kind: str
    #: Dosya yolu (varsa); slug ipucu buradan da çıkabiliyor.
    path: Path | None = None


def read_source(source: str | None, *, paste: bool = False) -> Source:
    """İlan metnini üç yoldan biriyle okur.

    `source` bir dosya yolu ya da stdin için `-`. `paste=True` ise `$EDITOR`
    açılır ve kaydedince akış devam eder.
    """
    if paste:
        return Source(text=_read_from_editor(), kind="paste")

    if source is None:
        raise IntakeError(
            "İlan kaynağı verilmedi. Bir dosya yolu ver, stdin için '-' kullan "
            "(örn. `Get-Clipboard | cv-updater tailor -`) ya da --paste ile editörü aç."
        )

    if source == STDIN_SENTINEL:
        text = sys.stdin.read()
        if not text.strip():
            raise IntakeError("stdin boş geldi. Pano dolu mu?")
        return Source(text=text, kind="stdin")

    path = Path(source)
    if not path.exists():
        raise IntakeError(
            f"Dosya yok: {path}\n"
            "Stdin'den okumak istiyorsan kaynak olarak '-' yaz, panoyu editörde "
            "açmak için --paste kullan."
        )
    if path.is_dir():
        raise IntakeError(f"{path} bir dizin, ilan dosyası değil.")

    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise IntakeError(f"{path} boş.")
    return Source(text=text, kind="file", path=path)


#: `--paste` ile açılan geçici dosyanın başlığı.
_PASTE_TEMPLATE = (
    "# İlan metnini bu satırın ALTINA yapıştır, kaydet ve kapat.\n"
    "# '#' ile başlayan satırlar yok sayılır.\n\n"
)


def _editor_command() -> list[str]:
    """Hangi editör açılacak?

    `typer` 0.27 `click`'i artık dışa vermiyor (vendor'ladı), o yüzden editörü
    kendimiz açıyoruz — özel bir modüle (`typer._click`) yaslanmak, sürüm
    yükseltmesinde sessizce kırılacak bir bağımlılık olurdu.
    """
    for variable in ("CV_UPDATER_EDITOR", "VISUAL", "EDITOR"):
        value = os.environ.get(variable)
        if value:
            # `EDITOR="code --wait"` gibi bayraklı tanımlar yaygın.
            return shlex.split(value, posix=os.name != "nt")
    return ["notepad"] if os.name == "nt" else ["vi"]


def _launch_editor(path: Path) -> None:
    """Editörü açar ve kapanmasını bekler. Testler bunu değiştirir."""
    command = [*_editor_command(), str(path)]
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise IntakeError(
            f"Editör bulunamadı: {command[0]}. $EDITOR ya da CV_UPDATER_EDITOR ayarla."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise IntakeError(f"Editör hata koduyla kapandı: {exc.returncode}") from exc


def _read_from_editor() -> str:
    """Geçici bir dosya açar, editörden dönen metni döndürür."""
    handle, name = tempfile.mkstemp(prefix="cv-updater-ilan-", suffix=".txt")
    os.close(handle)
    path = Path(name)

    try:
        path.write_text(_PASTE_TEMPLATE, encoding="utf-8")
        _launch_editor(path)
        edited = path.read_text(encoding="utf-8", errors="replace")
    finally:
        path.unlink(missing_ok=True)

    body = "\n".join(line for line in edited.splitlines() if not line.startswith("#"))
    if not body.strip():
        raise IntakeError("Editörden boş metin geldi — kaydedilmeden kapatılmış olabilir.")
    return body


def slugify(value: str, *, max_len: int = _SLUG_MAX_LEN) -> str:
    """Serbest metni dosya sistemi güvenli bir slug'a indirger."""
    text = unicodedata.normalize("NFC", value).translate(_TR_MAP)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = _NON_SLUG.sub("-", text.lower()).strip("-")
    if len(text) > max_len:
        text = text[:max_len].rstrip("-")
    return text


def slug_hint(source: Source, normalized_text: str) -> str:
    """Slug'ın okunur kısmı için en iyi tahmin.

    Şirket ve pozisyon LLM adımından (issue 05) önce bilinmiyor; dosya adı ya
    da ilanın ilk anlamlı satırı yeterince ayırt edici oluyor.
    """
    if source.path is not None:
        hint = slugify(source.path.stem)
        if hint:
            return hint

    for line in normalized_text.splitlines():
        candidate = slugify(line)
        if len(candidate) >= 3:
            return candidate

    return "ilan"


def new_run_dir(hint: str, *, root: Path | None = None, now: datetime | None = None) -> Path:
    """`out/<hint>-<zaman damgası>/` dizinini oluşturur ve döndürür.

    Zaman damgası saniye çözünürlüğünde; aynı saniyede iki çalıştırma olursa
    sonuna sayaç eklenir. Var olan bir dizin **asla** yeniden kullanılmaz.
    """
    base = root or OUT_DIR
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    slug = f"{hint or 'ilan'}-{stamp}"

    candidate = base / slug
    counter = 2
    while candidate.exists():
        candidate = base / f"{slug}-{counter}"
        counter += 1

    candidate.mkdir(parents=True)
    return candidate


def write_run_files(run_dir: Path, raw: str, clean: str) -> tuple[Path, Path]:
    """Ham ve normalize metni çalıştırma dizinine yazar.

    `newline=""` kritik: Windows'ta varsayılan yazma `\\n`'i `\\r\\n` yapar,
    okuma da geri çevirir — ham dosya "kanıt" olma işlevini kaybeder, çünkü
    ilanda gerçekten `\\r\\n` mi vardı yoksa Python mu ekledi ayırt edilemez.
    """
    raw_path = run_dir / RAW_FILENAME
    clean_path = run_dir / CLEAN_FILENAME
    raw_path.write_text(raw, encoding="utf-8", newline="")
    clean_path.write_text(clean, encoding="utf-8", newline="")
    return raw_path, clean_path
