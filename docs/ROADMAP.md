# Yol haritası ve issue listesi

`TODO.md` §5 ("İlana göre CV uyarlayıcı araç") ve §9'daki "İş ilanına göre CV güncelleyici"
maddesinin arkasındaki uygulanabilir plan.

## Teknik kararlar

| Konu | Karar | Neden |
|---|---|---|
| Dil / paketleme | Python 3.12 + `uv` | doqqy ile aynı ekosistem |
| CLI | `typer` | doqqy'de zaten kullanılıyor |
| Şemalar | `pydantic` v2 | yapılandırılmış çıktıya birebir oturuyor |
| LLM | Anthropic SDK, `client.messages.parse(output_format=...)`, model `claude-opus-5` | doğrulanmış JSON çıktısı |
| CV şeması | [Reactive Resume](https://github.com/AmruthPillai/Reactive-Resume) JSON — canlı şema `https://rxresu.me/schema.json` | üretilen dosya doğrudan import edilir |
| PDF metni | `pypdf` (`pdfplumber` yedeğiyle) | ATS öz-kontrolü |
| Render v1 | tailored.json → Reactive Resume'a elle import | MVP'yi hızlı çıkar |
| Render v2 | RenderCV (YAML → Typst → PDF) | tam otomasyon, ligatür kontrolü bizde |
| Testler | `pytest` | guardrail'ler test edilebilir olmalı |

> **Not — model kimliği.** Issue 01 `claude-opus-4-8` diyordu; issue'lar yazıldığından beri
> `claude-opus-5` çıktı — aynı fiyat, daha yetenekli. `src/cv_updater/llm.py` tek kaynak, orada.

**Reactive Resume şeması, kuşbakışı** (v2 formatı, ayrıntısı `docs/SCHEMA.md`'de):
`picture`, `basics`, `summary`, `sections{experience, education, skills, projects, languages,
certifications, awards, publications, volunteer, interests, references, profiles}`,
`customSections[]`, `metadata{template, layout, page, design, typography}`.
Bölüm öğeleri HTML-string bir `description` taşır; her öğenin bir `id`'si ve `hidden` bayrağı vardır.

## Issue listesi

| # | Dosya | Faz | Bağımlı |
|---|---|---|---|
| 01 | Proje iskeleti: uv + typer + pydantic + pytest | 0 | — |
| 02 | `master.json` şeması: Reactive Resume uyumlu + `x_meta` uzantısı | 0 | 01 |
| 03 | Kendi CV'ni `master.json`'a taşı + `validate` komutu | 0 | 02 |
| 04 | İlan girdisi: dosya / stdin / yapıştırma + normalizasyon | 1 | 01 |
| 05 | İlan analizi: `JobPosting` yapılandırılmış çıktı (LLM adımı #1) | 1 | 04 |
| 06 | Deterministik ön filtre: etiket + anahtar kelime puanlama (LLM yok) | 2 | 03, 05 |
| 07 | LLM uyarlama adımı + **dürüstlük guardrail'i** (asıl mühendislik problemi) | 2 | 06 |
| 08 | `tailored.json` üretimi + Reactive Resume şema doğrulaması | 2 | 07 |
| 09 | `match_report.md`: uyum skoru, seçim gerekçesi, eksik anahtar kelimeler | 2 | 07 |
| 10 | ATS öz-kontrolü: PDF'ten metni geri çıkar, doğrula, puanla | 3 | 08 |
| 11 | Render v2: RenderCV, YAML → Typst → PDF (ligatürsüz) | 3 | 08 |
| 12 | Başvuru günlüğü: `applications/` altında başvuru başına bir kayıt | 3 | 08, 09 |
| 13 | MVP sonrası yol haritası (takip issue'su) | 4 | — |

**MVP çizgisi: 01–10.** 11–12 yaşam kalitesi, 13 takip issue'su.

Her issue bir **📄 Dokümantasyon** bölümüyle biter: ilgili doküman kapanmadan önce yazılır ya da
güncellenir (`docs/INDEX.md` her seferinde güncel tutulur).
