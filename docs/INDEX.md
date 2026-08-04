# Dokümantasyon dizini

| Doküman | Ne anlatır | İlgili issue |
|---|---|---|
| [`../README.md`](../README.md) | Proje ne yapar, kurulum, komutlar, örnek akış | 01 |
| [`ROADMAP.md`](ROADMAP.md) | Teknik kararlar, 13 issue'luk plan ve faz dağılımı | 01 |
| [`SCHEMA.md`](SCHEMA.md) | `master.json` alan alan; `x_meta` sözleşmesi; Reactive Resume'dan farklar | 02 |
| `MASTER_DATA.md` **(yerel)** | Süper set nasıl büyütülür; madde yazım kuralları; `validate` lint kuralları | 03 |

`MASTER_DATA.md` gitignore'dadır: süper setin durumunu (terfi tarihi, ekip büyüklüğü, tarih
aralıkları) taşıdığı için `data/master.json` ile aynı gizlilik sınıfındadır. `CLAUDE.md` gibi
yalnızca yerelde yaşar.

## Mimari, kuşbakışı

```
cli.py            typer uygulaması: validate · tailor · check
llm.py            Anthropic istemcisi — model/effort ayarının yaşadığı tek yer
paths.py          data/ ve out/ konumları
models/           pydantic şemaları (resume.py: master.json; posting.py: JobPosting)
steps/            boru hattı adımları (ön filtre, uyarlama, rapor, ATS kontrolü)
data/             master.json (gitignore) · master.example.json · tags.yaml
out/              üretilen tailored.json / match_report.md (gitignore)
```

## Değişmezler

- **Dürüstlük guardrail'i pazarlanabilir özellik değil, ürünün kendisi.** LLM `master.json`'daki
  malzemeyi seçer, sıralar ve yeniden ifade eder; deneyim/yetenek/tarih/metrik icat edemez.
  Bu testlerle kapsanır (issue 07).
- **Deterministik ve LLM adımları ayrı durur.** Ön filtre hiçbir API çağrısı olmadan
  assert edilebilir olmalı.
- **Testler ağa çıkmaz.**
- **Kişisel veri depoya girmez** — `data/master.json` gitignore'da; yalnızca
  `data/master.example.json` commit'lenir.
