# Dokümantasyon dizini

| Doküman | Ne anlatır | İlgili issue |
|---|---|---|
| [`../README.md`](../README.md) | Proje ne yapar, kurulum, komutlar, örnek akış | 01 |
| [`ROADMAP.md`](ROADMAP.md) | Teknik kararlar, 13 issue'luk plan ve faz dağılımı | 01 |
| [`SCHEMA.md`](SCHEMA.md) | `master.json` alan alan; `x_meta` sözleşmesi; Reactive Resume'dan farklar | 02 |
| `MASTER_DATA.md` **(yerel)** | Süper set nasıl büyütülür; madde yazım kuralları; `validate` lint kuralları | 03 |
| [`USAGE.md`](USAGE.md) | Günlük akış: üç girdi yolu, çıktı dizinleri, `.env`, sık hatalar | 04 |
| [`PIPELINE.md`](PIPELINE.md) | Adım adım veri akışı; hangi adım deterministik, hangisi LLM; `job.json` şeması | 05 |
| [`SCORING.md`](SCORING.md) | Ön filtre formülü, ağırlıkların gerekçesi, geri çağırma ağı, `candidates.json` | 06 |
| [`GUARDRAILS.md`](GUARDRAILS.md) | Dürüstlük guardrail'i: kural listesi, `--strict` / `--repair`, tasarım gerekçesi | 07 |

`MASTER_DATA.md` gitignore'dadır: süper setin durumunu (terfi tarihi, ekip büyüklüğü, tarih
aralıkları) taşıdığı için `data/master.json` ile aynı gizlilik sınıfındadır. `CLAUDE.md` gibi
yalnızca yerelde yaşar.

## Mimari, kuşbakışı

```
cli.py                 typer uygulaması: validate · tailor · score · check
llm.py                 Anthropic istemcisi — model, endpoint ve yapılandırılmış
                       çıktı stratejisinin yaşadığı tek yer
paths.py               data/ ve out/ konumları
lint.py                validate'in içerik lint kuralları
tags.py                data/tags.yaml kontrollü sözlüğü
models/resume.py       master.json şeması (Reactive Resume + x_meta)
models/job.py          JobPosting · KeywordMapping · JobAnalysis
models/candidates.py   CandidatePool · ScoredItem · Weights (ön filtre çıktısı)
models/tailor.py       TailorPlan · SelectedBullet (uyarlamanın çıktı sözleşmesi)
steps/intake.py        ilanı oku (dosya / stdin / editör), çalıştırma dizini
steps/normalize.py     ligatür, boşluk, madde işareti, arayüz artığı
steps/analyze_job.py   LLM analizi + halüsinasyon süzgeci + etiket eşlemesi
steps/score.py         deterministik ön filtre + geri çağırma ağı (LLM yok)
steps/tailor.py        LLM uyarlama adımı — havuzdan seçim + yeniden ifade
steps/verify.py        dürüstlük guardrail'i: planı master.json'a karşı denetler
data/                  master.json (gitignore) · master.example.json · tags.yaml
out/                   çalıştırma başına bir dizin (gitignore)
```

Akışın tamamı ve hangi adımın ağa çıktığı [`PIPELINE.md`](PIPELINE.md)'de.

## Değişmezler

- **Dürüstlük guardrail'i pazarlanabilir özellik değil, ürünün kendisi.** LLM `master.json`'daki
  malzemeyi seçer, sıralar ve yeniden ifade eder; deneyim/yetenek/tarih/metrik icat edemez.
  Bu testlerle kapsanır (issue 07).
- **Deterministik ve LLM adımları ayrı durur.** Ön filtre hiçbir API çağrısı olmadan
  assert edilebilir olmalı. `job.json` bu ayrımı taşır: `posting` modelden gelir,
  `keywords` bizim hesabımızdır.
- **Testler ağa çıkmaz.** `tests/conftest.py` bunu soket seviyesinde zorlar; LLM adımları
  sahte istemciyle test edilir.
- **Kişisel veri depoya girmez** — `data/master.json` gitignore'da; yalnızca
  `data/master.example.json` commit'lenir.
