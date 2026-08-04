# cv-updater

İş ilanına göre CV uyarlayan bir CLI aracı. `master.json` her deneyimin, her maddenin ve her
yeteneğin **süper setini** tutar; araç bir ilanı yapılandırılmış olarak analiz eder, deterministik
bir ön filtre ile adayları puanlar, ve LLM adımı bu setten **seçer ve yeniden ifade eder** —
asla yeni içerik uydurmaz.

> **Dürüstlük guardrail'i projenin kalbi.** LLM mevcut malzemeyi sıralayabilir, seçebilir ve
> yeniden ifade edebilir; ama bir deneyimi, bir yeteneği, bir tarihi ya da bir metriği icat edemez.

```
job.txt ──▶ [1] ilan analizi (LLM, yapılandırılmış çıktı)
                     │
master.json ─────────┼──▶ [2] deterministik ön filtre (etiket / anahtar kelime puanlama)
 (her deneyimin,     │
  maddenin ve      └──▶ [3] LLM seçimi + yeniden ifade (dürüstlük guardrail'i)
  yeteneğin süper seti)     │
                             ├──▶ tailored.json  (Reactive Resume'a doğrudan import edilir)
                             └──▶ match_report.md (neden bu maddeler, hangi kelimeler eksik)
                                          │
                       PDF ──▶ [4] ATS öz-kontrolü (metni geri çıkar, kelime + ligatür doğrula)
```

## Kurulum

Python 3.12 + [uv](https://docs.astral.sh/uv/) gerekir.

```bash
git clone https://github.com/Emirhan-K/cv-updater
cd cv-updater
uv sync
```

LLM adımları için `ANTHROPIC_API_KEY` gerekir; `.env.example`'ı `.env` olarak kopyala.
`--help` ve `validate` anahtarsız çalışır.

## Komutlar

| Komut | Ne yapar | LLM? |
|---|---|---|
| `cv-updater validate` | `master.json`'ı şemaya karşı doğrular + içerik lint'i çalıştırır | hayır |
| `cv-updater tailor <ilan>` | `tailored.json` + `match_report.md` üretir | evet |
| `cv-updater check <pdf>` | PDF'ten metni geri çıkarıp ATS uyumunu puanlar | hayır |

`python -m cv_updater` de aynı arayüzü verir.

## Örnek akış

```bash
uv run cv-updater validate                  # süper set sağlıklı mı?
uv run cv-updater tailor ilanlar/acme.txt   # out/tailored.json + out/match_report.md
uv run cv-updater check out/cv.pdf          # PDF'te anahtar kelimeler hayatta kaldı mı?
```

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
