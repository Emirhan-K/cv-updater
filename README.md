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

LLM adımları için `.env.example`'ı `.env` olarak kopyala. İki seçenek var: yerel bir gateway
(`LLM_BASE_URL` + `LLM_API_KEY`) ya da doğrudan Anthropic (`ANTHROPIC_API_KEY`). `--help` ve
`validate` anahtarsız çalışır.

## Durum

Faz 0 ve Faz 1 tamam (issue 01-05): süper set doğrulanıyor, ilan üç yoldan alınıp normalize
ediliyor ve yapılandırılmış `job.json`'a çevriliyor. Sırada ön filtre (06) ve dürüstlük
guardrail'i (07). Günlük kullanım [`docs/USAGE.md`](docs/USAGE.md), akışın tamamı
[`docs/PIPELINE.md`](docs/PIPELINE.md).

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

# ilanı üç yoldan biriyle ver:
Get-Clipboard | uv run cv-updater tailor -  # panodan (PowerShell)
uv run cv-updater tailor ilanlar/acme.txt   # dosyadan
uv run cv-updater tailor --paste            # $EDITOR açılır

uv run cv-updater check out/cv.pdf          # PDF'te anahtar kelimeler hayatta kaldı mı?
```

Her çalıştırma `out/<slug>-<zaman damgası>/` altına yazar; önceki çıktı asla ezilmez.
Sonraki adımlar üzerinde çalışırken `--no-llm` en son analizi yeniden kullanır ve API'ye
gitmez.

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
