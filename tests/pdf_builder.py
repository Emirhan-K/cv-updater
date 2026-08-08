"""Testler için elden PDF üretici.

ATS kontrolünün (issue 10) girdisi bir PDF; bunu üretmeden çıkarma katmanı
test edilemez. Hazır bir PDF dosyasını depoya koymak yerine byte'ları burada
kuruyoruz — üç sebeple:

* `*.pdf` gitignore'da ve depoya kişisel CV giremez;
* PDF üreten bir kütüphane (reportlab) yalnızca test için ağır bir bağımlılık;
* ligatürün metin katmanında **gerçekten** durduğu bir PDF ancak böyle
  kurgulanabiliyor — `/Differences` ile `/fi` glifi kodlanıyor ve `pypdf` onu
  U+FB01 olarak geri okuyor. Gerçek CV'de ölçülen davranışın aynısı.

Üretilen dosya kasten minimal: tek sayfa, tek Type1 font, düz bir içerik akışı.
Amaç güzel bir PDF değil, **doğru bir metin katmanı**.
"""

from __future__ import annotations

#: ASCII dışı karakterlerin Adobe glif adları. `pypdf` `/Differences`'taki glif
#: adını Unicode'a bu isimler üzerinden çeviriyor.
GLYPH_NAMES: dict[str, str] = {
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬀ": "ff",
    "ğ": "gbreve",
    "Ğ": "Gbreve",
    "ş": "scedilla",
    "Ş": "Scedilla",
    "ı": "dotlessi",
    "İ": "Idotaccent",
    "ü": "udieresis",
    "Ü": "Udieresis",
    "ö": "odieresis",
    "Ö": "Odieresis",
    "ç": "ccedilla",
    "Ç": "Ccedilla",
    "–": "endash",
    "—": "emdash",
}

#: `/Differences` kodları buradan başlıyor: 0-127 ASCII olarak duruyor.
_FIRST_CODE = 128


def _encode(lines: list[str]) -> tuple[list[bytes], dict[str, int]]:
    """Satırları PDF string'ine çevirir; ASCII dışı karakterlere kod atar."""
    assigned: dict[str, int] = {}
    encoded: list[bytes] = []

    for line in lines:
        out = bytearray()
        for char in line:
            if char in "\\()":
                out += f"\\{char}".encode("ascii")
            elif ord(char) < 128:
                out += char.encode("ascii")
            else:
                if char not in assigned:
                    if char not in GLYPH_NAMES:
                        raise ValueError(f"Glif adı bilinmiyor: {char!r} — GLYPH_NAMES'e ekle.")
                    assigned[char] = _FIRST_CODE + len(assigned)
                out += f"\\{assigned[char]:03o}".encode("ascii")
        encoded.append(bytes(out))

    return encoded, assigned


def _differences(assigned: dict[str, int]) -> bytes:
    if not assigned:
        return b""
    parts = [
        f"{code} /{GLYPH_NAMES[char]}"
        for char, code in sorted(assigned.items(), key=lambda item: item[1])
    ]
    return f"/Encoding << /Type /Encoding /Differences [{' '.join(parts)}] >>".encode("ascii")


def build_pdf(lines: list[str], *, text_layer: bool = True) -> bytes:
    """Verilen satırları taşıyan tek sayfalık bir PDF.

    `text_layer=False` ile içerik akışı boş kalır: metin katmanı olmayan
    (taranmış) bir PDF'in testteki karşılığı.
    """
    encoded, assigned = _encode(lines if text_layer else [])

    if encoded:
        body = b"\n".join(b"(" + line + b") Tj T*" for line in encoded)
        content = b"BT\n/F1 11 Tf\n14 TL\n56 760 Td\n" + body + b"\nET\n"
    else:
        content = b""

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length "
        + str(len(content)).encode("ascii")
        + b" >>\nstream\n"
        + content
        + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica " + _differences(assigned) + b" >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, payload in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + payload + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n".encode(
        "ascii"
    )
    out += b"%%EOF\n"
    return bytes(out)


def write_pdf(path, lines: list[str], *, text_layer: bool = True):
    """`build_pdf`ın diske yazan hali; testler yolu bekliyor."""
    path.write_bytes(build_pdf(lines, text_layer=text_layer))
    return path
