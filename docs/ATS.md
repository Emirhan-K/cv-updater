# ATS öz-kontrolü — PDF'i makinenin gözünden okumak

> Issue 10. Kod: `src/cv_updater/steps/ats_check.py`, komut: `cv-updater check`.
> **Bu adımda LLM yok, ağ yok.** Girdi bir PDF, çıktı puanlanmış bir rapor;
> aynı PDF her zaman aynı raporu verir ve testler bunu assert ediyor.

## Neden var

Boru hattının geri kalanı `tailored.json` üretiyor ve bu dosya **doğru**. Ama işveren
`tailored.json` okumuyor; bir PDF okuyor, üstelik çoğu zaman bir insan değil, bir ATS okuyor.
Aradaki her katman (şablon, font, sütun düzeni, PDF'in metin katmanı) sessizce bozabilir.

Bunun gerçekten olduğu ölçüldü. Aynı PDF, iki farklı çıkarıcı:

| | `pypdf` | `pdfplumber` |
|---|---|---|
| Mart 2026 CV'si | `ﬁne-tuning` (U+FB01) | `fine-tuning` |

Ligatür glifi **metin katmanında duruyor**. `pdfplumber` onu açtığı için sorunu göstermiyor,
`pypdf` göstermiyor değil — olduğu gibi veriyor. Bir ATS'in hangi sınıf çıkarıcı kullandığını
bilmiyoruz, dolayısıyla **katı olanı** varsayıyoruz: `ﬁne-tuning` yazan bir CV'de "fine-tuning"
araması sıfır sonuç döndürebilir.

> **Kural: PDF hakkında iddiada bulunmadan önce ölç.** Bu dokümandaki her sayı iki gerçek PDF
> üzerinde ölçüldü (2026-08-07 ve 2026-08-08). Genel amaçlı metin çıkarıcılar ligatürü olmayan
> yerde bile bozuk gösterebiliyor; tersi de doğru.

## Kullanım

```bash
uv run cv-updater check cv.pdf                      # genel kontroller
uv run cv-updater check cv.pdf --slug novatek-tr    # + ilana özgü anahtar kelime kapsaması
uv run cv-updater check cv.pdf --job out/x/job.json # ilanı doğrudan ver
uv run cv-updater check cv.pdf --strict             # uyarılar da çıkış kodunu bozsun
uv run cv-updater check cv.pdf --out /tmp/rapor.md  # raporu başka yere yaz
```

Rapor `out/<çalıştırma>/ats_report.md`'ye yazılır. `--slug`/`--job` verilmediyse araç kendi
çıktı dizininde yeni bir yer açar (`out/ats-<pdf>-<damga>/`) — kullanıcının PDF'inin yanına
dosya bırakmaz.

**Çıkış kodu:** kritik bulgu varsa `1`, PDF yoksa ya da ilan dosyası okunamıyorsa `2`, aksi
halde `0`. `--strict` uyarıları da hata sayar. Amaç kontrolü ileride pre-commit ya da CI'ya
bağlayabilmek.

## Metin nasıl çıkarılıyor

1. `pypdf` sayfa sayfa okur. Sonuç yeterliyse iş biter.
2. Yetmezse (`< 200` karakter ya da harf oranı `< %50`) `pdfplumber`a düşülür ve rapor
   **neden düşüldüğünü** yazar.
3. İkisi de boş dönerse `NoTextLayerError`: PDF'te metin katmanı yok, muhtemelen taranmış bir
   sayfa. Bu düzeltilebilir bir bulgu değil — bir ATS o dosyadan tek kelime okuyamaz, CV'nin
   yeniden üretilmesi gerekir.

`pdfplumber` **opsiyonel bağımlılık**: `uv sync --extra pdf`. Kurulu değilken yedeğe düşülmesi
gerekirse hata mesajı bunu söyler. Sıra kasıtlı — yukarıdaki ölçüm yüzünden katı motor asıl
motor.

## Kontroller

| Kontrol | Ağırlık | Ne soruyor | En kötü seviye |
|---|---|---|---|
| Ligatür | 20 | Metin katmanında `ﬁ ﬂ ﬀ ﬃ ﬄ` var mı? | 🔴 kritik |
| Anahtar kelime | 25 | İlanın **zorunlu** terimlerinden kaçı metinde? | 🟡 uyarı |
| İletişim | 15 | E-posta ve telefon bulunabiliyor mu? | 🔴 kritik (e-posta) |
| Link görünürlüğü | 10 | GitHub/LinkedIn **metin olarak** var mı? | 🟡 uyarı |
| Bölüm başlıkları | 15 | Deneyim / Eğitim / Beceriler var mı? | 🔴 kritik (hiçbiri yoksa) |
| Okuma sırası | 10 | Sütunlar iç içe geçmiş mi? | 🟡 uyarı |
| Tarih biçimi | 5 | Dönemler ayrıştırılabiliyor mu? | 🟡 uyarı |

Toplam 100. İlan verilmediğinde `keywords` atlanır ve **payda da küçülür** — ilan vermemek
puanı düşürmez.

Eşikler: **≥ 85** "ATS'e hazır", **≥ 65** "düzeltilebilir", altı "riskli". Kritik bulgu varsa
puan ne olursa olsun karar "bu haliyle gönderme".

### Seviyeler tek bir ayrım üzerine kurulu

* **🔴 kritik — başvuru ölür.** Metin katmanı yok, e-posta okunamıyor, ligatür aramayı bozuyor,
  hiçbir bölüm başlığı yok. Çıkış kodu yalnızca bunlara bakar.
* **🟡 uyarı — elenme riski artar.** Eksik anahtar kelime, görünmeyen link, sütun şüphesi.
* **ℹ️ bilgi — bilinsin.** Karışık tarih biçimi gibi, düzeltmesi zorunlu olmayan şeyler.

### Ligatür

`ﬁne-tuning` ekranda "fine-tuning" gibi görünür ama metin katmanındaki karakter U+FB01'dir;
`f`+`i` araması bulmaz. Rapor etkilenen kelimeleri tek tek listeler.

**Nasıl kapatılır:** ligatür glifi taşımadığı **ölçülmüş** bir font kullan — `Tinos`, `Inter`,
`Arimo`, `Cousine` (`render_json.LIGATURE_FREE_FONTS`, GSUB tabloları `fontTools` ile ölçüldü).
Kaynak metinde duruyorsa `cv-updater validate --fix` temizler. CSS ile kapatmak
(`font-variant-ligatures: none`) PDF tarafında işe yaramıyor: `@react-pdf/renderer` bu özelliği
tanımıyor.

**Not:** ligatür kontrolü ile anahtar kelime kontrolü aynı metne farklı bakıyor. Kapsama araması
**ligatürü açılmış** metinde yapılır: CV'de `ﬁne-tuning` yazıyorsa ilanın "fine-tuning" terimi
*bulunmuş* sayılır. Aksi halde tek hata iki kez raporlanırdı — ligatür sorunu zaten kendi
satırında duruyor.

### Anahtar kelime kapsaması

Terimler `job.json`'daki **`must`** gereksinimlerin `keywords` alanlarından geliyor; hiçbirinde
anahtar kelime yoksa ilanın tüm `tech_keywords`'üne düşülür. Karşılaştırma `fold` ile
sadeleştirilmiş metinde yapılır, yani `CI/CD` ≡ `ci-cd` ≡ `ci cd`, ve İngilizce çoğul iki yönde
de denenir (`LLM` ↔ `LLMs`).

Eksik terim **uyarıdır, kritik değildir**: terim gerçekten CV'de yoksa çare onu uydurmak değil.
Uyum raporu (issue 09) o terimi zaten "eksik" diye raporlayıp mülakat hazırlığına yazıyor.

### İletişim

E-posta kritik: ATS aday kaydını e-postayla açar, okuyamazsa başvuru hiç oluşmaz. Telefon uyarı.
İkinci bir adres varsa uyarı verilir — ATS hangisini alacağını bilemez.

**Elenen bir kural:** "adresin yerel kısmı rakamla başlıyorsa yanına bir şey yapışmıştır" diye bir
kontrol yazıldı. Kanıtı iki PDF'te birden görünen `1emirhankuru@gmail.com` idi ve sayfa
numarasının adrese yapıştığı sanıldı. **Kaynağa bakınca adresin gerçek hali çıktı**; kural her
çalıştırmada yanlış alarm veriyordu, kaldırıldı. Yapışmış metni doğru yakalamanın yolu sezgisel
değil karşılaştırmalı: PDF'ten okunan adresi `tailored.json`'daki adresle kıyaslamak. `check <pdf>`
CV'yi her zaman görmediği için o kontrol buraya yazılmadı.

> Buradaki ders `docs/GUARDRAILS.md`'dekiyle aynı: **bir bulgunun kanıtını kaynağa kadar takip
> etmeden kural yazma.** Ligatür bulgusunda da, burada da ilk teşhis yanlıştı.

### Link görünürlüğü

Gömülü hyperlink PDF'in annotation katmanında yaşıyor; ne metin çıkarıcı ne ATS oraya bakıyor.
Görünen metnin **tam URL** olması gerekiyor. `lint.check_visible_profile_urls` aynı kuralı
`master.json` tarafında uyguluyor; buradaki kontrol sonucun PDF'te de tuttuğunun kanıtı.

Mart 2026 CV'sinde metin katmanında `Emirhan-K` duruyordu: bir ikon glifi ve kullanıcı adı,
URL yok. İki uyarı da doğru çıktı.

### Bölüm başlıkları ve okuma sırası

Başlık tespiti **satır başı** kuralıyla çalışıyor, ve buraya gelene kadar iki kez düzeltildi:

| Sürüm | Kural | Neden elendi |
|---|---|---|
| 1 | Başlık satıra hâkim olmalı (geriye ≤ 24 karakter) | `Yetenekler Python AWS (EC2) Docker Git` kaçtı → "Beceriler bölümü yok" **yanlış eksiği** |
| 2 | Başlık satırın başında olmalı | `Eğitim platformunun dijital altyapısının…` maddesi başlık sanıldı |
| 3 | Satır başında **ve** sonraki kelime küçük harfle başlamıyor | — |

Üçüncü kural işe yarıyor çünkü gerçek bir başlıktan sonra ya hiçbir şey gelmez ya da büyük
harfle başlayan bir ad gelir (`Deneyim **V**idInsight`); cümle devam ediyorsa sonraki kelime
küçük harflidir.

Okuma sırası **sezgisel** ve bu yüzden hiçbir bulgusu kritik değil. İki sinyal kaldı:

1. `order.interleaved` — aynı satırda iki farklı bölüm başlığı. Tek sütunlu bir düzende olamaz.
2. `order.repeated_heading` — aynı başlık birden çok kez.

Üçüncü bir sinyal (`order.heading_noise`, "başlık satırında artık metin var") yazıldı ve
**ölçümle elendi**: Reactive Resume'un tek sütunlu çıktısında `pypdf` başlığı ilk öğeyle aynı
satıra indiriyor (`Deneyim VidInsight Teknoloji`), yani kural doğru bir CV'de sürekli yanlış
alarm veriyordu. Doğru bir CV'yi "bozuk" diye damgalamanın bedeli, bir sorunu kaçırmanın
bedelinden düşük değil.

Sütun karışması bulunursa çare tek: **tek sütunlu bir şablona geç.**

### Tarih biçimi

Ayrıştırılabilirlik `score.parse_period_end` ile ölçülüyor — tazelik bonusunu hesaplayan işlevin
ta kendisi. Onun okuyamadığı bir dönemi bir ATS'in okuyacağını varsaymak için sebep yok.

Biçim tutarlılığı yalnızca **bilgi**: eğitimde `2018-2022`, deneyimde `Eylül 2024 – Devam Ediyor`
kullanmak son derece yaygın ve hiçbir ATS'i bozmuyor. Raporlanır, puanı düşürmez.

## Ölçüm sonuçları

İki gerçek PDF, ilan verilmeden:

| PDF | Puan | Karar | Bulgular |
|---|---|---|---|
| Mart 2026 (elle yapılmış CV) | 60 | 🔴 kritik | 5 kelimede ligatür; GitHub ve LinkedIn metin olarak yok |
| 8 Ağustos 2026 (Reactive Resume'dan) | 100 | ATS'e hazır | yalnızca karışık tarih biçimi bilgisi |
| 8 Ağustos 2026 (`render` ile, RenderCV) | 98 | ATS'e hazır | bir zorunlu terim eksik; 2 sayfa uyarısı |

İkincisi bir ilana karşı ölçüldüğünde (`--slug novatek-tr2`) 95 puan: 11 zorunlu terimden 9'u
metinde, eksik olan ikisi (`nesne tespiti`, `vektör veritabanları`) gerçekten süper sette yok.

Bu tablo issue 10'un kabul kriterlerinin ta kendisi: ligatürlü CV yakalanıyor, düzeltilmiş CV
temiz geçiyor, kapsama ilan verilince ilana özgü oluyor, metin katmanı olmayan PDF açık bir
hata mesajı veriyor.

## Testler

`tests/test_ats_check.py`. PDF'ler **testin içinde üretiliyor** (`tests/pdf_builder.py`), üç
sebeple: `*.pdf` gitignore'da ve depoya kişisel CV giremez; PDF üreten bir kütüphane yalnızca
test için ağır bir bağımlılık; ve ligatürün metin katmanında gerçekten durduğu bir PDF ancak
`/Differences` ile `/fi` glifi kodlanarak kurgulanabiliyor — gerçek CV'de ölçülen davranışın
aynısı.

## Ne yapmıyor

* **Şablonu değiştirmiyor.** Bulguyu söyler, düzeltmeyi sen (ya da issue 11'in render yolu) yapar.
* **ATS taklidi etmiyor.** Workday'in, Taleo'nun, Kariyer.net'in ayrıştırıcıları farklı. Buradaki
  kontroller hepsinin ortak paydası: metin katmanı okunabilir mi, aranan kelime bulunabilir mi.
* **İçerik kalitesini değerlendirmiyor.** "Bu madde iyi yazılmış mı" sorusu uyum raporunun
  (issue 09) alanı.
