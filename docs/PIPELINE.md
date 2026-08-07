# Boru hattı — adım adım veri akışı

Her adımın girdisi, çıktısı ve hangi dosyaya yazdığı. Günlük kullanım
[`USAGE.md`](USAGE.md)'de; buradaki asıl mesele **hangi adımın deterministik, hangisinin
LLM olduğu**.

```
ilan metni
   │
   │  1. intake        dosya / stdin / --paste            [deterministik]
   ▼
Source(text, kind)
   │
   │  2. normalize     ligatür, boşluk, madde, arayüz     [deterministik]
   ▼
job_raw.txt + job_clean.txt
   │
   │  3. analyze_job   yapılandırılmış çıktı              [LLM]
   ▼                   + halüsinasyon süzgeci             [deterministik]
   │                   + tags.yaml eşlemesi               [deterministik]
job.json
   │
   │  4. ön filtre     etiket puanlama + geri çağırma ağı [deterministik]
   ▼
candidates.json
   │
   │  5. uyarlama      havuzdan seçim + yeniden ifade     [LLM]
   ▼
plan.json
   │
   │  6. guardrail     kaynak / sayı / varlık / dayanak   [deterministik]
   ▼
violations.json   ── ihlal varsa (ve --repair yoksa) akış BURADA durur
   │
   │  7. derleme      plan + master.json → CV             [deterministik]
   ▼                  + rxresu.me şema doğrulaması
tailored.json
   │
   │  8. uyum raporu   skor, gerekçe, eksik kelimeler     [deterministik]  ← issue 09
   ▼
match_report.md
   │
   │  9. ATS kontrolü  PDF'ten metni geri oku, puanla     [deterministik]  ← issue 10
   ▼
ats raporu
```

**Boru hattında ağa çıkan tek iki nokta 3 ve 5.** Diğer her şey API çağrısı olmadan
assert edilebilir; testler ağa çıkmaz (soket seviyesinde bloklanır, bkz. `tests/conftest.py`).
Her LLM adımının hemen ardından deterministik bir denetim geliyor — 3'ten sonra halüsinasyon
süzgeci, 5'ten sonra guardrail. **Modelin söylediği hiçbir şey doğrulanmadan bir sonraki
adıma geçmiyor.**

---

## 1. Girdi — `steps/intake.py`

| | |
|---|---|
| **Girdi** | dosya yolu · `-` (stdin) · `--paste` (editör) |
| **Çıktı** | `Source(text, kind, path)` |
| **Ağ** | yok |

Çalıştırma dizini burada açılır: `out/<slug>-<zaman damgası>/`. Zaman damgası saniye
çözünürlüğünde; aynı saniyede iki çalıştırma olursa sona sayaç eklenir. **Var olan bir dizin
asla yeniden kullanılmaz.**

Slug'ın okunur kısmı `--slug` → dosya adı → ilanın ilk anlamlı satırı sırasıyla belirlenir.
Şirket/pozisyon adı 3. adımdan önce bilinmediği için slug LLM'e bağlı değil.

## 2. Normalizasyon — `steps/normalize.py`

| | |
|---|---|
| **Girdi** | ham ilan metni |
| **Çıktı** | `Normalized(text, ligatures_fixed, dropped, bullets_rewritten)` |
| **Dosya** | `job_raw.txt` (dokunulmamış) · `job_clean.txt` (temiz) |
| **Ağ** | yok |

Sıra önemli — önce karakter düzeyi, sonra satır düzeyi, en son boşluk sıkıştırma:

1. Unicode NFC
2. görünmez karakterler silinir (`U+200B`–`U+200F`, yumuşak tire, BOM, yönlü işaretler)
3. kırılmayan boşluklar düz boşluğa iner
4. **ligatürler açılır** (`ﬁ`→`fi`, `ﬂ`→`fl`, …) — tablo `lint.LIGATURES`, CV tarafıyla ortak
5. satır sonları `\n`'e iner
6. arayüz artığı satırları atılır
7. madde işaretleri `- ` olur
8. ikiden fazla boş satır tek boş satıra iner

**Arayüz artığı kuralı iki ayaklı:**

- **Kalıp eşleşmesi** — satırın *tamamı* bir kalıba uyuyorsa atılır ("Kaydet", "142 başvuran",
  "3 gün önce yayınlandı", çerez/gizlilik metni, navigasyon). Cümle içinde geçen aynı kelime
  korunur: "adayların özgeçmişini kaydet butonuyla saklayan" satırı silinmez.
- **Tekrar eşiği** — aynı **kısa** satır (≤40 karakter) 3+ kez geçiyorsa arayüz artığıdır.
  Uzun satırlar bu kuralın dışında; üç kez tekrarlanan uzun bir cümle içerik olabilir.

`job_raw.txt` bilerek `newline=""` ile yazılır: Windows satır sonlarını çevirseydi dosya
"kanıt" olma işlevini kaybederdi (ilanda gerçekten `\r\n` mi vardı, Python mu ekledi?).

## 3. İlan analizi — `steps/analyze_job.py`

| | |
|---|---|
| **Girdi** | `job_clean.txt` |
| **Çıktı** | `JobAnalysis` |
| **Dosya** | `job.json` |
| **Ağ** | **var** — tek LLM çağrısı |

### 3a. Yapılandırılmış çıktı [LLM]

Model `JobPosting` şemasını doldurur: `company`, `title`, `language`, `seniority`,
`location`, `work_mode`, `requirements[]`, `tech_keywords[]`, `soft_signals[]`, `domain`,
`tone_notes`. Alan sözleşmesi `models/job.py`'de.

Sistem promptunun ilk kuralı: *yalnızca ilan metninde yazanı çıkar, tahmin etme, uydurma*.
Bilinmeyen alan `null` ya da `"unknown"` kalır.

> **Endpoint notu.** `llm.py` iki yapılandırılmış çıktı stratejisi tanır. Yerel gateway
> `messages.parse(output_format=…)` **desteklemiyor** — parametreyi sessizce yok sayıp düz
> metin döndürüyor. `auto` modunda `LLM_BASE_URL` varsa **zorunlu tool-use**, yoksa `parse`
> seçilir. `LLM_STRUCTURED_MODE=parse|tool` ile zorlanabilir.

### 3b. Halüsinasyon süzgeci [deterministik]

`tech_keywords` içindeki **her terim ilan metninde gerçekten geçmek zorunda**. Geçmeyen
atılır ve `keywords.dropped`'a yazılır.

Karşılaştırma `fold()` üzerinden: küçük harf + tüm ayraçlar tek boşluk. Böylece `CI/CD` ile
`ci-cd` aynı forma iner ve yanlış alarm üretmez. Türkçe `İ` özel olarak ele alınır —
Python'un `.lower()`'ı `i + U+0307` (birleşik nokta) üretiyor, o nokta ayraç sayılıp
"İşleme"yi "i şleme" yapıyordu.

Bu, issue 07'deki dürüstlük guardrail'inin ilan tarafındaki karşılığı: ilanda olmayan bir
teknoloji, CV'de o teknolojiyi öne çıkarmak için gerekçe olamaz.

### 3c. Etiket eşlemesi [deterministik]

Kalan terimler `data/tags.yaml` kontrollü sözlüğüne indirgenir:

- eşleşenler → `keywords.tags` (kanonik, tekrarsız)
- eşleşmeyenler → `keywords.unmapped` — **sözlük buradan büyür**

Eşleme üç aday dener: ham terim, tireli hali, ayraçsız hali. Yani `Fast API` / `FastAPI` /
`fastapi` hepsi `fastapi`'ye iner. Bu adım LLM'e hiç sorulmaz; ön filtre tekrarlanabilir
olmak zorunda.

`unmapped` listesi bir iş listesidir: terimin gerçekten bir karşılığı varsa `tags.yaml`'a
alias olarak eklenir. (İlk üç gerçek ilan denemesi `vektör-veritabanları`,
`prompt-mühendisliği`, `iyzico`/`paytr`, `object-detection`, `segmentation` alias'larını
böyle üretti.)

### `--no-llm`

**İki LLM adımını da** atlar: en son yazılmış `job.json` ve `plan.json` yeniden kullanılır.
"En son", dizin adının sonundaki zaman damgasından bulunur — dizin adına göre alfabetik
sıralamak yanlış olurdu, çünkü slug adın başında. Plan yoksa akış ön filtreden sonra durur.

Plan aday havuzuna bağlı olduğu için başka bir ilanın planını yeniden kullanmak guardrail'i
`source.not_offered` ile tetikler. Bu istenen davranış: uyuşmazlık sessizce geçmez.

## 4. Deterministik ön filtre — `steps/score.py`

| | |
|---|---|
| **Girdi** | `job.json` + `master.json` + `tags.yaml` |
| **Çıktı** | `CandidatePool` |
| **Dosya** | `candidates.json` |
| **Ağ** | yok |

`master.json` maddeleri ilanın etiket profiline karşı puanlanır; dil süzgeci, üst sınırlar ve
`always_include` muafiyeti uygulanır. Ayrıca **geri çağırma ağı** çalışır: `tags.yaml`'ın
tamamı ilan metninde aranıp modelin bildirmedikleri raporlanır.

Formül, ağırlıkların gerekçesi ve üst sınırlar: [`SCORING.md`](SCORING.md).

## 5. Uyarlama — `steps/tailor.py`

| | |
|---|---|
| **Girdi** | `job.json` özeti + `candidates.json` havuzu |
| **Çıktı** | `TailorPlan` |
| **Dosya** | `plan.json` |
| **Ağ** | **var** — ikinci ve son LLM çağrısı |

Modele **süper set değil havuz** gider; ham ilan metni ikinci kez gönderilmez (havuzda olmayan
bir teknolojiyi hatırlatmak uydurmayı davet ederdi). Modelin döndürebildiği tek şey id'ler ve
o id'lerin yeniden ifadesi.

## 6. Guardrail — `steps/verify.py`

| | |
|---|---|
| **Girdi** | `plan.json` + `candidates.json` + `master.json` |
| **Çıktı** | `VerificationReport` |
| **Dosya** | `violations.json` (temiz çalıştırmada da yazılır) |
| **Ağ** | yok |

Kaynak, sayı, varlık ve dayanak kontrolleri. Varsayılan katı: bir hata varsa akış durur ve
`tailored.json` **üretilmez**. `--repair` ihlalli metni kaynağına döndürüp sürdürür.

Kural listesi ve tasarım gerekçesi: [`GUARDRAILS.md`](GUARDRAILS.md).

## 7. Derleme — `steps/render_json.py`

| | |
|---|---|
| **Girdi** | `master.json` + doğrulanmış plan + havuz |
| **Çıktı** | `Resume` |
| **Dosya** | `tailored.json` |
| **Ağ** | yok |

- `basics`, eğitim, diller, şirket adı, unvan, tarih, roller **doğrudan `master.json`'dan**
- `summary.content` = `<p>…</p>`, deneyim `description` = `<ul><li>…</li></ul>`
- proje maddeleri ön filtrenin seçtiği haliyle, **yeniden ifade edilmeden**
- seçilmeyen öğe silinmez, `hidden: true` olur
- `metadata.page.locale` dile göre (`tr-TR` / `en-US`)
- `metadata.stylesheet` ligatürleri render seviyesinde kapatır; karakter seviyesi ayrıca
  temizlenir
- `x_meta` / `x_bullets` çıktıdan tamamen silinir — süper set paylaşılan dosyaya sızmaz
- her öğeye çalıştırmaya özgü yeni `id` (UUID5) verilir, import'ta çakışma olmasın

Çıktı, depodaki rxresu.me şema anlık görüntüsüne karşı doğrulanır; hata varsa çıkış kodu 1.
Anlık görüntü `cv-updater validate --refresh-schema` ile yenilenir (**ağa çıkan tek yardımcı
komut**) ve değişiklik commit'lenmelidir.

## 8-9. Henüz uygulanmadı

| Adım | Issue | Girdi | Çıktı |
|---|---|---|---|
| `match_report.md` | 09 | plan + havuz + `job.json` | uyum skoru, gerekçe, eksik anahtar kelimeler |
| ATS öz-kontrolü | 10 | üretilen PDF | metin geri okuma + puanlama |

## `job.json` şeması

```jsonc
{
  "posting": {           // modelden gelen
    "company": "Novatek Teknoloji A.Ş.",
    "title": "Kıdemli Yapay Zeka Mühendisi (LLM)",
    "language": "tr",                    // CV bu dilde üretilecek
    "seniority": "senior",               // intern|junior|mid|senior|lead|unknown
    "location": "İstanbul, Türkiye",
    "work_mode": "hybrid",               // remote|hybrid|onsite|unknown
    "requirements": [
      { "text": "...", "kind": "must", "keywords": ["Python"] }
    ],
    "tech_keywords": ["Python", "FastAPI"],
    "soft_signals": ["mentorluk"],
    "domain": "yapay zeka",
    "tone_notes": "Kurumsal ve resmi."   // yeniden ifade adımı kullanacak
  },
  "keywords": {          // bizim hesapladığımız
    "tags": ["python", "fastapi"],
    "unmapped": ["NVIDIA Jetson"],
    "dropped": []                        // ilanda bulunamayıp atılanlar
  },
  "model": "claude-opus-5",
  "analyzed_at": "2026-08-06T00:54:56",
  "source_kind": "file",
  "reused_from": null                    // --no-llm ile kopyalandıysa kaynak dizin
}
```

Ayrım kasıtlı: `posting` modelden gelir, `keywords` bizim deterministik hesabımızdır. Bir
hata olduğunda hangisinin suçlu olduğu belli olsun.
