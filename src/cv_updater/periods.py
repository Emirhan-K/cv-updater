"""Dönem metinlerinin dil çevirisi — `"Aralık 2025 – Devam Ediyor"` → `"December 2025 – Present"`.

Süper set tek dilde tarih tutuyor: `master.json`'daki `period` alanları Türkçe.
Maddeler ve özet iki dilde yaşıyor (`x_meta.lang`), tarihler yaşamıyor — ve
yaşamamalı da: tarih bir olgu, varyantı olmaz. Sonuç, ilk İngilizce CV'de
görüldü: içerik İngilizceydi, sağ sütundaki her tarih Türkçeydi.

Bu modül o boşluğu **deterministik** olarak kapatıyor. LLM'e gitmiyor, çünkü
tarih tam olarak modelin dokunmaması gereken alan (`models/tailor.py`): ay adı
bir sözlük araması, "Devam Ediyor" bir sabit. Uydurulacak bir şey yok.

Kural: **anlaşılmayan dönem olduğu gibi kalır.** `parse_period`'daki (issue 11)
mantığın aynısı — yarım çevrilmiş bir tarih, çevrilmemiş doğru bir tarihten
kötüdür. Bu yüzden çeviri kelime kelime değil, `_MONTHS` ve `_ONGOING`
tablolarındaki bilinen terimler üzerinden yapılıyor; geri kalan (ayraçlar,
yıllar, boşluk) dokunulmadan geçiyor.
"""

from __future__ import annotations

import re

from cv_updater.models.job import Language

#: Ay adları, ay numarasına göre hizalı. Dizin = ay - 1.
_MONTHS: dict[Language, tuple[str, ...]] = {
    "tr": (
        "Ocak",
        "Şubat",
        "Mart",
        "Nisan",
        "Mayıs",
        "Haziran",
        "Temmuz",
        "Ağustos",
        "Eylül",
        "Ekim",
        "Kasım",
        "Aralık",
    ),
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
}

#: "Devam eden dönem" ifadesinin her dildeki kanonik hali.
_ONGOING: dict[Language, str] = {"tr": "Devam Ediyor", "en": "Present"}

#: Devam eden dönem olarak tanınan yazımlar. Süper sette "Devam Ediyor" var ama
#: elle düzenlenen bir dosyada "halen" ya da "günümüz" de çıkabilir. Liste
#: bilerek dar: burada eşleşen her terim **değiştirilecek**, o yüzden bir
#: tarihin içinde tesadüfen geçebilecek kısa sözcükler ("now") alınmadı.
_ONGOING_FORMS: tuple[str, ...] = (
    "devam ediyor",
    "devam",
    "halen",
    "günümüz",
    "gunumuz",
    "present",
    "current",
    "ongoing",
)


def _fold(text: str) -> str:
    """`İ` → `i` dahil, tablo aramasına uygun küçük harf.

    CLAUDE.md'deki tuzak: düz `.lower()` Türkçe `İ`yi `i + U+0307` yapıyor ve
    eşleşmeyi sessizce bozuyor. Ay adlarında `İ` yok ama bu fonksiyon serbest
    metne uygulanıyor, o yüzden kural burada da geçerli.
    """
    return text.replace("İ", "i").replace("I", "ı").lower().replace("i̇", "i")


#: Ay adı → ay numarası, iki dil birden.
_LOOKUP: dict[str, int] = {
    _fold(name): index + 1 for names in _MONTHS.values() for index, name in enumerate(names)
}

#: Sözcük sınırı `\b` Türkçe harflerde güvenilir değil; bilinen terimleri
#: uzunluğa göre sıralayıp doğrudan arıyoruz ("Devam Ediyor" < "Devam" olmasın,
#: "Mayıs" < "May" olmasın).
_TERMS = sorted({*_LOOKUP, *_ONGOING_FORMS}, key=len, reverse=True)
_TERM_PATTERN = re.compile("|".join(re.escape(term) for term in _TERMS), re.IGNORECASE)


def translate_period(period: str, language: Language) -> str:
    """Bir `period` metnini hedef dile çevirir; tanımadığı parçaya dokunmaz.

    >>> translate_period("Aralık 2025 – Devam Ediyor", "en")
    'December 2025 – Present'
    >>> translate_period("2020-2025", "en")
    '2020-2025'
    """
    months = _MONTHS.get(language)
    if not period or months is None:
        return period

    ongoing = _ONGOING[language]

    def replace(match: re.Match[str]) -> str:
        folded = _fold(match.group())
        if (number := _LOOKUP.get(folded)) is not None:
            return months[number - 1]
        if folded in _ONGOING_FORMS:
            return ongoing
        return match.group()

    return _TERM_PATTERN.sub(replace, period)
