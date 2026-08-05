"""İlan metnini normalize eder — boru hattının 1. adımının deterministik yarısı.

LinkedIn ve Kariyer.net'ten kopyalanan ilan metni, ilanın kendisi kadar arayüz
artığı taşır: "Bu ilanı paylaş", "142 başvuran", çerez metni, tekrarlanan buton
etiketleri. Bunlar LLM adımına (issue 05) girdi olarak gitse token yakmaktan
öte zarar verir — `tech_keywords` içine "Kaydet" gibi terimler sızar.

**LLM kullanmaz, ağa çıkmaz.** Aynı girdi her zaman aynı çıktıyı verir; issue
05'in halüsinasyon testi bu belirlenimciliğe yaslanıyor.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from cv_updater.lint import LIGATURES

#: Satır başındaki madde işaretleri — hepsi `- ` olur ki LLM ve ön filtre tek
#: bir madde biçimi görsün.
_BULLET_PREFIX = re.compile(r"^[\s]*[•▪◦‣∙·※◆■□●○*+–—-]+[\s]+")

#: "1." / "2)" gibi numaralı maddeler de aynı biçime iner.
_NUMBERED_PREFIX = re.compile(r"^[\s]*\(?\d{1,2}[.)][\s]+")

#: Görünmez biçimlendirme karakterleri: sıfır genişlikli boşluk, yumuşak tire,
#: BOM, yönlü işaretler. Kopyala-yapıştır metninde sık — kaynakta da görünmesinler
#: diye kaçış dizisiyle yazıldı.
_INVISIBLE = re.compile("[\u200b-\u200f\u00ad\ufeff\u2060\u202a-\u202e]")

#: Kırılmayan boşluk ve akrabaları — düz boşluğa iner.
_NBSP = re.compile("[\u00a0\u2002\u2003\u2007\u2009\u202f\u3000]")

#: Satır içi tekrarlı boşluk.
_MULTISPACE = re.compile(r"[ \t]{2,}")

#: İkiden fazla ardışık boş satır tek boş satıra iner.
_BLANK_RUN = re.compile(r"\n{3,}")

#: LinkedIn / Kariyer.net arayüz artıkları. Satırın **tamamı** buna uyuyorsa
#: atılır — ilan metninin içinde geçen aynı kelime korunur.
_CHROME_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # Butonlar / bağlantılar
        r"^(kaydet|kaydedildi|paylaş|paylas|başvur|basvur|takip et|rapor et|şikayet et)$",
        r"^(save|saved|share|apply|easy apply|follow|report|report this job)$",
        r"^(hemen başvur|şimdi başvur|başvuru yap|başvuruyu tamamla)$",
        r"^(bu ilanı paylaş|bu ilani paylas|ilanı paylaş|share this posting|share this job)$",
        r"^(daha fazla göster|devamını oku|show more|see more|read more|less)$",
        r"^(geri|ileri|kapat|back|next|close|dismiss)$",
        # Meta satırları
        r"^\d+\s*(başvuran|basvuran|applicants?|kişi başvurdu)$",
        r"^\d+\s*(gün|hafta|ay|saat|dakika)\s*önce.*$",
        r"^(promoted|sponsorlu|öne çıkan ilan)$",
        r"^(\d+[\d.,]*)\s*(takipçi|followers?|çalışan|employees?)$",
        r"^(linkedin|kariyer\.net|indeed|glassdoor|secretcv)( corporation)?$",
        r"^ilan (no|numarası)\s*[:#]?\s*\w+$",
        r"^(job id|req id|requisition id)\s*[:#]?\s*\w+$",
        # Çerez / yasal
        r"^.*(çerez|cookie|kvkk|gizlilik politikası|privacy policy|kullanım koşulları"
        r"|terms of (use|service)|tüm hakları saklıdır|all rights reserved).*$",
        r"^(kabul et|tümünü kabul et|accept|accept all|reject|reddet)$",
        # Navigasyon
        r"^(ana sayfa|iş ilanları|is ilanlari|home|jobs|my network|messaging|notifications)$",
        r"^(bildirimler|mesajlar|ağım|agim|profilim|profil|arama|iş ara|is ara)$",
        r"^(giriş yap|üye ol|sign in|join now|sign up|log in)$",
    )
)

#: Aynı kısa satır bu kadar kez tekrarlıyorsa arayüz artığıdır. İlan metninde
#: aynı cümle üç kez geçmez; "Kaydet" geçer.
_REPEAT_THRESHOLD = 3
#: Tekrar kuralı yalnızca kısa satırlara uygulanır — uzun bir cümlenin üç kez
#: geçmesi tuhaf ama içerik olabilir, riske girme.
_REPEAT_MAX_LEN = 40

#: İlanın anlamlı sayılması için gereken alt sınır (issue 04 kabul kriteri).
MIN_MEANINGFUL_CHARS = 200


@dataclass(frozen=True)
class Normalized:
    """Normalizasyonun sonucu ve ne yaptığının dökümü."""

    text: str
    ligatures_fixed: int = 0
    dropped: tuple[str, ...] = field(default=())
    bullets_rewritten: int = 0

    @property
    def is_short(self) -> bool:
        """İlan, anlamlı bir analiz için fazla kısa mı?"""
        return len(self.text) < MIN_MEANINGFUL_CHARS

    @property
    def stats(self) -> dict[str, int]:
        return {
            "karakter": len(self.text),
            "satır": len([line for line in self.text.splitlines() if line.strip()]),
            "ligatür düzeltildi": self.ligatures_fixed,
            "madde işareti": self.bullets_rewritten,
            "atılan satır": len(self.dropped),
        }


def expand_ligatures(text: str) -> tuple[str, int]:
    """Tipografik ligatürleri harflerine açar; kaç tane düzeltildiğini de döndürür.

    `lint.LIGATURES` ile aynı tabloyu kullanır: CV tarafında ATS'in aramayı
    kaçırmasına yol açan sorun, ilan tarafında anahtar kelimeyi kaçırmaya yol
    açar (`ﬁne-tuning` ilanda geçiyor ama `fine-tuning` aramasıyla bulunmaz).
    """
    count = 0
    for ligature, replacement in LIGATURES.items():
        found = text.count(ligature)
        if found:
            count += found
            text = text.replace(ligature, replacement)
    return text, count


def _is_chrome(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return any(
        pattern.fullmatch(stripped) or pattern.match(stripped) for pattern in _CHROME_PATTERNS
    )


def _repeated_boilerplate(lines: list[str]) -> set[str]:
    """Sık tekrarlayan kısa satırlar — buton etiketleri ve tekrarlı başlıklar."""
    counts = Counter(line.strip() for line in lines if 0 < len(line.strip()) <= _REPEAT_MAX_LEN)
    return {line for line, count in counts.items() if count >= _REPEAT_THRESHOLD}


def normalize(raw: str) -> Normalized:
    """Ham ilan metnini temiz, tek biçimli metne indirger.

    Sıra önemli: önce karakter düzeyi (NFC, ligatür, görünmez karakterler),
    sonra satır düzeyi (arayüz artığı, madde işareti), en son boşluk sıkıştırma.
    """
    text = unicodedata.normalize("NFC", raw)
    text = _INVISIBLE.sub("", text)
    text = _NBSP.sub(" ", text)
    text, ligatures_fixed = expand_ligatures(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    lines = text.split("\n")
    boilerplate = _repeated_boilerplate(lines)

    kept: list[str] = []
    dropped: list[str] = []
    bullets = 0

    for line in lines:
        stripped = _MULTISPACE.sub(" ", line).strip()

        if not stripped:
            kept.append("")
            continue

        if _is_chrome(stripped) or stripped in boilerplate:
            dropped.append(stripped)
            continue

        bulleted = _BULLET_PREFIX.sub("- ", stripped)
        if bulleted == stripped:
            bulleted = _NUMBERED_PREFIX.sub("- ", stripped)
        if bulleted != stripped:
            bullets += 1
        kept.append(bulleted)

    cleaned = _BLANK_RUN.sub("\n\n", "\n".join(kept)).strip()

    return Normalized(
        text=cleaned,
        ligatures_fixed=ligatures_fixed,
        dropped=tuple(dropped),
        bullets_rewritten=bullets,
    )
