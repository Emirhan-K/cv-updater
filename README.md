# cv-updater

İş ilanına göre CV uyarlayan bir CLI aracı. `master.json` her deneyimin, her maddenin ve her
yeteneğin **süper setini** tutar; araç bir ilanı yapılandırılmış olarak analiz eder, deterministik
bir ön filtre ile adayları puanlar, ve LLM adımı bu setten **seçer ve yeniden ifade eder** —
asla yeni içerik uydurmaz.

> **Dürüstlük guardrail'i projenin kalbi.** LLM mevcut malzemeyi sıralayabilir, seçebilir ve
> yeniden ifade edebilir; ama bir deneyimi, bir yeteneği, bir tarihi ya da bir metriği icat edemez.

```
ilan.txt ──▶ [1] ilan analizi ····························· LLM
                  └── halüsinasyon süzgeci + etiket eşlemesi   deterministik
                                    │
master.json ────────▶ [2] ön filtre: etiket puanlama ········ deterministik
 (her deneyimin,                    │                          (LLM'e giden metni yarıya indirir)
  maddenin ve                       ▼
  yeteneğin süper seti)  [3] seçim + yeniden ifade ·········· LLM
                                    │
                         [4] DÜRÜSTLÜK GUARDRAIL'İ ·········· deterministik
                                    │                          ihlal varsa akış BURADA durur
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
             tailored.json                   match_report.md
   (Reactive Resume'a import edilir)   (uyum skoru, gerekçe, eksik kelimeler)
                    │
                    ▼
              [5] render: RenderCV → Typst → PDF ············· deterministik  (opsiyonel)
                    │
                    ▼
              [6] ATS ÖZ-KONTROLÜ ···························· deterministik
                    │  PDF'ten metni geri oku: ligatür, anahtar kelime, iletişim,
                    ▼  link görünürlüğü, başlık, okuma sırası, tarih
              cv.pdf + ats_report.md
```

**Modelin söylediği hiçbir şey doğrulanmadan bir sonraki adıma geçmiyor.** İki LLM adımının
ikisinin de ardından deterministik bir denetim var.

## Kurulum

Python 3.12 + [uv](https://docs.astral.sh/uv/) gerekir.

```bash
git clone https://github.com/Emirhan-K/cv-updater
cd cv-updater
uv sync
uv sync --extra render --extra pdf   # opsiyonel: PDF üretimi ve yedek metin çıkarıcı
```

İki opsiyonel ek var: `render` (RenderCV + Typst — `cv-updater render` için) ve `pdf`
(pdfplumber — ATS kontrolünün yedek metin çıkarıcısı). İkisi de olmadan boru hattının
tamamı çalışır.

LLM adımları için `.env.example`'ı `.env` olarak kopyala. İki seçenek var: yerel bir gateway
(`LLM_BASE_URL` + `LLM_API_KEY`) ya da doğrudan Anthropic (`ANTHROPIC_API_KEY`). `--help` ve
`validate` anahtarsız çalışır.

## Durum

**Faz 0–3 tamam (issue 01–12): araç ilandan PDF'e kadar uçtan uca çalışıyor.** İlan analizi,
ön filtre, guardrail'li uyarlama, `tailored.json` + `match_report.md`, tek komutla PDF ve
üretilen PDF'in ATS öz-kontrolü. Testler ağa çıkmıyor.

Günlük kullanım [`docs/USAGE.md`](docs/USAGE.md), akışın tamamı
[`docs/PIPELINE.md`](docs/PIPELINE.md), guardrail'in nasıl çalıştığı
[`docs/GUARDRAILS.md`](docs/GUARDRAILS.md).

## Komutlar

| Komut | Ne yapar | LLM? |
|---|---|---|
| `cv-updater validate` | `master.json`'ı şemaya karşı doğrular + içerik lint'i çalıştırır | hayır |
| `cv-updater tailor <ilan>` | Tam boru hattı: `tailored.json` + `match_report.md` üretir | evet |
| `cv-updater score <slug>` | Sadece ön filtreyi çalıştırır, puanlamayı tabloyla gösterir | hayır |
| `cv-updater report <slug>` | Uyum raporunu yeniden üretir | hayır |
| `cv-updater render <slug>` | `tailored.json` → PDF (RenderCV/Typst) + otomatik ATS kontrolü | hayır |
| `cv-updater check <pdf>` | PDF'ten metni geri çıkarıp ATS uyumunu puanlar; kritik bulguda çıkış kodu 1 | hayır |
| `cv-updater log list/status/stats` | Başvuru günlüğü: takip, durum, "hangi skorun üstünde dönüş geliyor" | hayır |

Guardrail varsayılan olarak **katı**: bir ihlal bulunursa akış durur ve `tailored.json`
üretilmez. `--repair` ihlalli metni kaynağına döndürüp sürdürür — bir gevşetme değil,
uydurulan metnin atılması.

`python -m cv_updater` de aynı arayüzü verir.

## Örnek akış

```bash
uv run cv-updater validate                  # süper set sağlıklı mı?

# ilanı üç yoldan biriyle ver:
Get-Clipboard | uv run cv-updater tailor -  # panodan (PowerShell)
uv run cv-updater tailor ilanlar/acme.txt   # dosyadan
uv run cv-updater tailor --paste            # $EDITOR açılır

uv run cv-updater score acme                # ön filtreyi gözle denetle (LLM'e gitmez)
uv run cv-updater tailor ilan.txt --lang en # Türkçe ilana İngilizce CV
uv run cv-updater tailor ilan.txt --repair  # guardrail ihlalinde durma, onararak devam et
```

Her çalıştırma `out/<slug>-<zaman damgası>/` altına yazar; önceki çıktı asla ezilmez.
Bir çalıştırma dizini şunları taşır: `job_raw.txt` · `job_clean.txt` · `job.json` ·
`candidates.json` · `plan.json` · `violations.json` · **`tailored.json`** ·
**`match_report.md`**.

`--no-llm` iki LLM adımını da atlar (en son analizi ve planı yeniden kullanır) — sonraki
adımlar üzerinde çalışırken kota harcamamak için.

## Geliştirme

```bash
uv run pytest                     # tüm testler (ağa çıkmaz)
uv run pytest tests/test_cli.py   # tek dosya
uv run ruff check .               # lint
uv run ruff format .              # biçimlendirme
```

## Dokümantasyon

Giriş noktası: [`docs/INDEX.md`](docs/INDEX.md). Yol haritası ve issue listesi:
[`docs/ROADMAP.md`](docs/ROADMAP.md) →
[github.com/Emirhan-K/cv-updater/issues](https://github.com/Emirhan-K/cv-updater/issues).

## Gizlilik

`data/master.json` kişisel veri içerir (telefon, e-posta, adres) ve `.gitignore`'dadır.
Depoya yalnızca anonimleştirilmiş `data/master.example.json` girer.
