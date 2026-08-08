# Render — `tailored.json`'dan PDF'e

> Issue 11. Kod: `src/cv_updater/steps/render_pdf.py`, komut: `cv-updater render`.
> **Bu adımda LLM yok, ağ yok.** Girdi `tailored.json`, çıktı bir PDF ve onun ATS raporu.

İki yol var ve **ikisi de destekleniyor**:

| | v1 — elle import | v2 — `render` |
|---|---|---|
| Akış | `tailored.json` → rxresu.me → Import → PDF indir | `cv-updater render <slug>` |
| Tipografi | Reactive Resume'un | bizde (Typst) |
| Ligatür riski | fontla yönetilir (`docs/ATS.md`) | ölçümle yok |
| Bağımlılık | yok | `uv sync --extra render` |
| Ne zaman | görsel şablonu elle ayarlamak istiyorsan | günlük başvuru akışı |

v1 hâlâ çalışıyor ve kaldırılmadı: `tailored.json` rxresu.me şemasına karşı doğrulanmış halde
duruyor. v2 onu tek komuta indiriyor.

## Kullanım

```bash
uv run cv-updater render novatek-tr                     # cv.yaml + cv.pdf + ats_report.md
uv run cv-updater render novatek-tr --theme sb2nov      # başka tema
uv run cv-updater render novatek-tr --font "EB Garamond"
uv run cv-updater render novatek-tr --lang en           # bölüm başlıklarının dili
uv run cv-updater render novatek-tr --out ~/cv.pdf
uv run cv-updater render novatek-tr --no-check          # ATS kontrolünü atla
```

Çıktılar çalıştırma dizinine yazılır:

```
out/<slug>/
  cv.yaml         RenderCV girdisi — elle düzenleyip `rendercv render` ile de derlenebilir
  cv.pdf          üretilen CV
  ats_report.md   render sonrası otomatik çalışan ATS öz-kontrolü (issue 10)
```

**Üret ve doğrula tek adımda.** Render bittiğinde ATS kontrolü otomatik çalışır; kritik bir bulgu
çıkarsa çıkış kodu 1 olur. `--no-check` bunu kapatır.

Temalar: `classic` (varsayılan), `engineeringclassic`, `engineeringresumes`, `sb2nov`, `moderncv`,
`harvard`, `ember`, `ink`, `opal`.

## Alan eşleme tablosu

Reactive Resume (`tailored.json`) → RenderCV:

| Kaynak | Hedef | Not |
|---|---|---|
| `basics.name` | `cv.name` | |
| `basics.headline` | `cv.headline` | |
| `basics.location` | `cv.location` | |
| `basics.email` | `cv.email` | |
| `basics.phone` | `cv.phone` | `tel:` URI'ye çevrilir; RenderCV yereline göre biçimlendirir |
| `basics.website.url` | `cv.website` | şemasız verilmişse `https://` eklenir |
| `sections.profiles[]` | `cv.custom_connections[]` | **`social_networks` değil** — aşağıya bak |
| `summary.content` (HTML) | "Özet" / "Summary" bölümü | `<p>` → paragraf listesi |
| `sections.experience[]` | "Deneyim" / "Experience" | rol başına bir girdi |
| ↳ `company`, `position`, `location` | aynı adla | |
| ↳ `period` | `start_date` + `end_date`, ya da serbest `date` | |
| ↳ `description` (HTML) | `highlights[]` | `<li>` → madde |
| `sections.education[]` | "Eğitim" / "Education" | `school`→`institution`, `area`, `degree` |
| `sections.projects[]` | "Projeler" / "Projects" | `name`, `date`, `highlights` |
| `sections.skills[]` | "Yetenekler" / "Skills" | tek tip kuralı, aşağıya bak |
| `sections.certifications[]` | "Sertifikalar" / "Certifications" | `title`→`name`, `issuer`→`summary` |
| `sections.languages[]` | "Diller" / "Languages" | `language: fluency` satırları |
| `metadata.page.locale` | `locale.language` | `tr-TR` → `turkish` |

`hidden: true` olan her öğe atlanır — `render_json` seçilmeyenleri zaten böyle işaretliyor, yani
uyarlama kararı burada da geçerli. Boş alanlar (`None`, `""`, `[]`) çıktıya girmez.

### Bölüm başlıkları ATS'e göre seçildi

RenderCV bölüm başlığını sözlük anahtarından alıyor, yani başlık metnini biz belirliyoruz.
Seçilenler `docs/ATS.md`'deki başlık kontrolünün aradığı standart adlar: **Deneyim / Eğitim /
Yetenekler** (ya da Experience / Education / Skills). Yaratıcı başlık ATS'i şaşırtıyor, o yüzden
yaratıcılık burada yapılmıyor.

### Linkler `custom_connections` ile basılıyor

RenderCV'nin `social_networks` alanı sayfaya yalnızca **kullanıcı adını** yazıyor (`Emirhan-K`).
Mart 2026 CV'sinde ATS kontrolünü düşüren bulgu tam olarak buydu: metin katmanında URL yok, ikon
ve kullanıcı adı var. `custom_connections`'ın `placeholder` alanı görünen metni belirlediği için
tam adres (`github.com/Emirhan-K`) sayfaya yazılıyor ve `url` de gömülü link olarak kalıyor.

> **İkon adı yalın olmalı.** `fontawesome_icon` alanına `"fa brands fa-github"` gibi tam bir CSS
> sınıfı verilirse Typst çözemiyor ve sayfaya **düz metin olarak** "fa brands fa-github" basıyor.
> İlk render'da tam olarak bu oldu. Doğrusu `"github"`. Kanonik liste RenderCV'nin
> `renderer/templater/connections.py` dosyasındaki `fontawesome_icons` sözlüğü.

### Roller düzleştirilmiyor

RenderCV'de iç içe rol yok. Bir deneyimde `roles[]` doluysa **rol başına bir girdi** üretiliyor
(şirket adı tekrar eder), en güncel rol üstte. Terfi çizgisi (`Yazılım Mühendisi` → `AI Ekip
Lideri`) CV'de değerli bir sinyal; `master.json` onu ayrı tuttuğu için burada da ayrı duruyor.

Maddeler `render_json._attach_bullets` tarafından en güncel role yazılıyor, o yüzden rolün kendi
`description`'ı önce denenir, yoksa öğe seviyesine düşülür.

### Yetenekler: tek girdi tipi kuralı

RenderCV bir bölümde **tek girdi tipi** kabul ediyor, karışık liste doğrulamada patlıyor. Karar:

* Her yeteneğin `keywords`'ü doluysa → `label: details` satırları ("Diller: Python, SQL").
* Değilse (süper setin bugünkü hali: 50 tane yalın ad) → hepsi **tek virgüllü satır**.

İkincisi hem okunur hem ATS dostu. Otuz tane "Python:" satırı ne CV'de iyi durur ne de ayrıştırıcıya
bir şey katar.

### Tarihler uydurulmuyor

`period` serbest metin ("Eylül 2024 – Devam Ediyor", "2018-2022"). İki ucu da ayrıştırılabiliyorsa
`start_date` / `end_date` üretilir (`2024-09`, `present`); **bir ucu bile okunamıyorsa ikisi de
kullanılmaz** ve dönem olduğu gibi RenderCV'nin serbest `date` alanına yazılır. Yarım bir tarih
aralığı, doğru duran serbest bir metinden kötüdür — ve tarih zaten modelden değil `master.json`'dan
geliyor, doğruluğu orada.

## Ligatür: ölçüm

Issue 11'in vaadi "ligatür sorununu kaynağında çözmek"ti. RenderCV'nin Typst şablonunda şu satır
duruyor (`rendercv_typst/lib.typ`):

```typst
// Disable ligatures for better ATS compatibility:
ligatures: true,
```

Yorum "kapat" diyor, değer "aç" diyor, ve ayar tasarım seçeneği olarak dışarıya açılmamış. Yani
kapatamıyoruz. Bunun yerine **ölçtük** (2026-08-08): 17 gömülü fontun her biriyle aynı CV render
edilip PDF'ten metin `pypdf` ile geri okundu.

**Sonuç: hepsinde sıfır ligatür glifi.** EB Garamond, Libertinus Serif, XCharter gibi gerçekten
`fi` ligatürü taşıyan serif fontlarda bile `fine-tuning`, `offline`, `gamification`, `spesifik`
kelimelerinin hepsi bozulmadan çıkıyor — Typst PDF'e doğru bir `ToUnicode` eşlemesi yazdığı için
ligatür glifi iki karaktere geri çözülüyor.

Bu, Reactive Resume'dan **farklı** bir davranış: Mart 2026 CV'sinde `pypdf` `ﬁne-tuning` (U+FB01)
döndürüyordu (`docs/ATS.md`).

Dolayısıyla burada font kısıtı **yok** — `--font` ile 17 fonttan herhangi biri seçilebilir. Ama
iddiaya güvenmiyoruz: `render` her çalıştırmada ATS kontrolünü çalıştırıyor. Typst'in davranışı bir
gün değişirse ilk haber veren o olacak, ve `tests/test_render_pdf.py` bunu ayrıca assert ediyor.

## Sayfa bütçesi

Hedef bir sayfa. Taşarsa akış **durmuyor** — kırpma kararı kullanıcının, aracın değil — ama uyarı
neyin kırpılacağını somut olarak söylüyor:

```
⚠ CV 2 sayfa (hedef 1)
    → 13 madde var — en eski deneyimden madde çıkar.
    → 3 proje görünüyor — ilana en uzak olanı gizle.
```

Kırpmak için `tailored.json`'da ilgili öğeye `hidden: true` yazıp `render`'ı tekrar çalıştırmak
yeterli; ya da `tailor`'ı daha dar bir seçimle yeniden çalıştırmak.

## Opsiyonel bağımlılık

RenderCV (`rendercv[full]`, Typst derleyicisi ve fontlarıyla birlikte) varsayılan kuruluma
girmiyor. `build_document()` — `Resume`'dan RenderCV sözlüğü üreten saf dönüşüm — RenderCV kurulu
olmadan çalışır ve testlerin çoğu orada. Yalnızca `render()` gerçek pakete ihtiyaç duyar ve kurulu
değilse ne yapılacağını söyler:

```
RenderCV kurulu değil. PDF üretimi opsiyonel bir özellik:
  uv sync --extra render
Kurmadan da çalışabilirsin: `tailored.json`'ı rxresu.me'ye elle import et (v1 akışı).
```

## Ölçüm sonucu

Gerçek çalıştırma (`novatek-tr2`, `classic` + `Source Sans 3`):

| | |
|---|---|
| ATS puanı | **98/100** |
| Ligatür | yok |
| Link görünürlüğü | github + linkedin, metin olarak |
| Bölüm başlıkları | 3/3 |
| Sayfa | 2 (uyarı ve kırpma önerileri basıldı) |

Eksik tek zorunlu terim (`vektör veritabanları`) gerçekten süper sette yok — uyum raporu onu zaten
mülakat hazırlığına yazıyor.
