# Dürüstlük guardrail'i

> Issue 07. Kod: `src/cv_updater/steps/verify.py`, sözleşme:
> `src/cv_updater/models/tailor.py`. Testler: `tests/test_verify.py`.

Bu araç bir CV'yi ilana göre uyarlıyor. Bir LLM'e "CV'mi bu ilana göre güncelle" demek beş
saniye sürer ve **çalışır gibi görünür**. Sonuç genelde şudur: model bilmediğiniz bir teknolojiyi
ekler, bir sayıyı yuvarlar, bir sorumluluğu büyütür. Mülakatta o cümleyi savunmak zorunda kalan
siz olursunuz.

Bu dosya, o cümlenin CV'ye **hiçbir bayrak altında** giremeyeceğini nasıl garanti ettiğimizi
anlatıyor.

## Temel fikir: modele daha az yetki ver, çıktısını doğrula

İki katman var, ikisi de kod tarafında:

```
┌──────────────────────────────────────────────────────────────┐
│ 1. ŞEMA — model neyi YAZABİLİR                               │
│    Sadece id + yeniden ifade + gerekçe. "Yeni madde" alanı   │
│    diye bir şey yok. Şirket, unvan, tarih şemada geçmiyor.   │
└──────────────────────────────────────────────────────────────┘
                              ↓  TailorPlan
┌──────────────────────────────────────────────────────────────┐
│ 2. DOĞRULAMA — model ne yazdı, gerçekten kaynağa uyuyor mu   │
│    Sayı, varlık, kaynak ve dayanak kontrolleri. Deterministik│
│    kod; modele "emin misin?" diye sorulmuyor.                │
└──────────────────────────────────────────────────────────────┘
                              ↓  ihlal yoksa
                        tailored.json
```

Sıralama önemli: doğrulama katmanı iyi de olsa, **yazılamayan bir alan doğrulanmak zorunda
değildir.** Şirket adının, unvanın ve tarihin `TailorPlan`'da hiç bulunmaması guardrail'in en
güçlü kuralı — o alanlar `tailored.json`'a doğrudan `master.json`'dan kopyalanıyor (issue 08).
Bir test bunu doğrudan assert ediyor: `TailorPlan` şemasında `company`, `position`, `date`,
`period`, `education` alanları yok.

Aynı mantıkla proje maddeleri de modelden geçmiyor: model yalnızca hangi projelerin
görüneceğini seçiyor (`project_ids`), maddelerin metni ön filtrenin seçtiği haliyle kalıyor.

## Kurallar

Her ihlal `out/<slug>/violations.json`'a yazılır — **temiz çalıştırmada da**, boş listeyle.
Dosyanın yokluğu "denetlenmedi" demek olurdu.

| Kural | Seviye | Yakaladığı hata biçimi |
|---|---|---|
| `source.unknown` | hata | `source_id` süper sette yok — madde tamamen uydurulmuş |
| `source.not_offered` | hata | id süper sette var ama aday havuzunda yoktu; model kendi eklemiş |
| `source.suffix_dropped` | uyarı | Dil soneki (`.tr`/`.en`) düşürülmüş; **tek aday varsa** çözüldü |
| `source.duplicate` | uyarı | Aynı madde iki kez seçilmiş |
| `number.invented` | hata | `rewritten` içinde kaynakta olmayan bir sayı var |
| `entity.invented` | hata | Kaynakta olmayan teknoloji/ürün adı (`FastAPI`, `S3`, `CI/CD` biçimli) |
| `entity.unknown_name` | hata | Kaynakta olmayan özel isim (`Kubernetes`, `Google`, `Trendyol`) |
| `text.drift` | uyarı | Yeniden ifade kaynağın 1.8 katından uzun — şişirme sinyali |
| `summary.ungrounded` | hata | Özet hiçbir maddeye dayandırılmamış |
| `summary.unknown_source` | hata | Özetin dayanağı olarak var olmayan bir id verilmiş |

Özet ayrıca sayı ve varlık kontrollerinden geçer. `summary_source_ids` bir **izlenebilirlik
sözleşmesi**: boş bırakılamaz, var olmayan id gösteremez. Ama temellendirme yalnızca o
listeye değil, **havuzun seçilen tamamına** bakar — özet CV'yi özetler, ve CV'de duran
(dolayısıyla `master.json`'dan gelen) bir şeyi tekrar etmesi uydurma değildir.

### Sonek çözümü — sessiz düzeltme değil

Madde id'leri dil sonekiyle biter (`exp-preunec.rag.en`). İngilizce çalıştırmalarda model bu
soneki ısrarla düşürüyor: doğru maddeyi seçmiş, sadece ön filtrenin zaten karara bağladığı bir
muhasebe ekini atlamış oluyor. Havuzda **tek bir aday** varsa id çözülür ve bu bir uyarı
olarak raporlanır. İki dil varyantı da havuzdaysa çözüm yapılmaz — tahmin etmek guardrail'i
delerdi.

### Sayı kontrolü — en sık halüsinasyon biçimi

Sayılar rakamlarına indirgenerek karşılaştırılıyor, böylece "1.500" ile "1500" farkı yanlış
alarm üretmiyor. Kural tek yönlü: **sayı çıkarmak serbest, eklemek yasak.**

```
kaynak:    "8 saatlik kaydı 12 dakikada işledi"
geçer:     "8 saatlik kaydı 12 dakikada işleyen bir hat kurdu"
geçer:     "Görüntü işleme hattı kurdu"                     (sayı düştü)
İHLAL:     "8 saatlik kaydı 3 dakikada işledi"              (12 → 3)
İHLAL:     "... ve gecikmeyi %40 azalttı"                   (yeni sayı)
```

### Varlık kontrolü — ve neden iki kategori var

Bir ad üç yoldan biriyle "temellenmiş" sayılıyor:

1. Kaynak maddenin metninde geçiyor.
2. Kaynağın `x_meta.tags` listesindeki bir etikete iniyor (`Computer Vision` ↔
   `computer-vision`, `machine-learning` ↔ `ML`). **Bu şart çeviri için gerekli:** İngilizce
   ilana Türkçe bir maddeden "computer vision" yazmak uydurma değil, çeviridir.
3. Bir etiketin parçası (`rest-api` etiketi hem "REST"i hem "API"yi meşrulaştırır).
4. Deneyimin kendi bağlamında geçiyor — şirket adı, unvan, dönem. Madde metninde geçmese de
   doğrudur; "Örnek AI bünyesinde FastAPI servisleri çıkardı" uydurma sayılmamalı.
5. İngilizce çoğulunun tekili temelli (`LLM` → `LLMs`).
6. Tireli bileşiğin **anlamlı parçalarının hepsi** temelli (`LLM-based`, `RAG-based`).
   `Django-powered` yine yakalanır: `Django` parçası temelli değil.

3-6 arasındaki gevşetmelerin hepsi gerçek çalıştırmalardaki yanlış alarmlardan doğdu ve
hepsinin bir regresyon testi var. Kural şu: **biçim farkı uydurma değildir, yeni bilgi
uydurmadır.**

Ad çıkarımı iki kategoriye ayrılıyor çünkü **yakalama güveni farklı**:

- `entity.invented` — teknoloji biçimli belirteçler: içinde rakam olanlar (`S3`, `GPT-4`),
  tamamı büyük harf olanlar (`AWS`, `REST`), içinde büyük harf olanlar (`FastAPI`,
  `PostgreSQL`), ve sözlükteki çok kelimeli terimler (`Görüntü İşleme`).
- `entity.unknown_name` — cümle başında olmayan sıradan büyük harfli kelimeler:
  `Kubernetes`, `Django`, `Trendyol`. **Cümlenin ilk kelimesi sayılmaz** — Türkçede her cümle
  büyük harfle başlar, aksi halde her maddenin ilk kelimesi yanlış alarm üretirdi.

İkisi de **hata** seviyesinde. İkincisini uyarıya indirmek cazipti (yanlış alarm riski daha
yüksek), ama en sık uydurulan adlar — `Django`, `Kubernetes`, `Redis`, bir müşteri adı —
tam olarak o kategoriye düşüyor. Uyarıya indirmek guardrail'in asıl deliğini açardı.

## `--strict` ve `--repair`

```bash
uv run cv-updater tailor ilan.txt            # katı (varsayılan)
uv run cv-updater tailor ilan.txt --repair   # onararak devam et
```

**Katı mod (varsayılan):** bir hata bulunursa adım durur, ihlaller terminale ve
`violations.json`'a yazılır, `tailored.json` **üretilmez**. Çıkış kodu 1.

**`--repair`:** akış sürer, ama onarım her zaman *geri* yönde — uydurulan metin atılır, gerçek
metin geri gelir:

| İhlal | Onarım |
|---|---|
| `number.invented`, `entity.*`, `text.drift` | `rewritten` kaynak metnine döndürülür |
| `source.unknown`, `source.not_offered` | Madde plandan tamamen atılır |
| `summary.*` | Özet `master.json`'daki özete döndürülür |
| Uydurma yetenek / proje id'si | Listeden çıkarılır |

Kritik nokta: `--repair` bir **gevşetme değil**. Onarılmış plan ikinci kez doğrulandığında
sıfır hata veriyor — bunu bir test doğrudan assert ediyor
(`test_nothing_invented_survives_repair`). Yani issue 07'nin kabul kriteri harfiyen geçerli:
uydurulmuş içerik **hiçbir bayrak altında** `tailored.json`'a ulaşamaz.

## Neden modele serbest metin yazdırmıyoruz

Sık sorulan itiraz: "model zaten iyi yazıyor, bırak kendi cümlesini kursun."

Sorun kalite değil, **doğrulanabilirlik**. Serbest bir cümlenin doğru olup olmadığını kod
anlayamaz; anlayabilmesi için bir doğruluk kaynağına bağlı olması gerekir. `source_id` tam
olarak o bağ. Modelin ürettiği her cümle bir kaynağa işaret ettiği için, her cümle
karşılaştırılabilir hale geliyor — ve karşılaştırılabilir olan şey test edilebilir oluyor.

Bunun bedeli var: model gerçekten iyi olan bir sentezi ("iki farklı deneyimi tek maddede
birleştir") yapamıyor. Bu bilinçli bir takas. CV'de yanlış tek bir cümlenin maliyeti, kaçırılan
bir sentezin maliyetinden çok daha yüksek.

## İlan tarafındaki karşılığı

Aynı prensip boru hattının ilk LLM adımında da uygulanıyor (issue 05): modelin ilandan
çıkardığı her `tech_keyword` ilan metninde birebir geçmek zorunda, geçmeyen atılıyor. Ters
yönü de ön filtre kapatıyor (issue 06, geri çağırma ağı): ilanda geçen ama modelin bildirmediği
terimler `tags.yaml` üzerinden deterministik olarak bulunuyor.

Üç adımın ortak kuralı: **modelin söylediği hiçbir şey, kod doğrulamadan bir sonraki adıma
geçmiyor.**

## Bilinen sınırlar

- **Anlam kayması tam olarak yakalanamıyor.** "Bir ekipte çalıştı" → "bir ekibi yönetti"
  cümlesinde ne sayı ne de ad değişiyor; `text.drift` uzunluk oranına baktığı için bunu
  yalnızca uzama varsa görüyor. Tam çözüm anlamsal bir kontrol gerektirir ve o da bir model
  çağrısı demek — yani doğrulamayı yine modele emanet etmek.
- **Ad çıkarımı Türkçeye ayarlı.** Cümle başı istisnası ve `İ` katlaması Türkçe metne göre
  yazıldı; başka bir dil eklenirse `extract_entities` gözden geçirilmeli.
- **Kısaltma açılımı yanlış alarm üretebilir.** Kaynakta "LLM" varken "Large Language Model"
  yazılırsa `entity.unknown_name` tetiklenir. Ters yön (`machine-learning` → "ML") `tags.yaml`
  alias'ıyla çözülüyor, ama açılımın kendisi çözülmüyor. `--repair` bunu kaynağa döndürerek
  aşıyor.
- **Sonek çözümü yalnızca dil sonekini kapsıyor.** Model id'yi başka bir biçimde bozarsa
  (harf hatası, tire yerine alt çizgi) çözüm denenmez, ihlal olarak kalır.

## Gerçek çalıştırmalardan gözlem (2026-08-07)

İlk uçtan uca denemelerde guardrail'in yakaladıklarının dökümü — yanlış alarmların hepsi
düzeltildi, gerçek yakalamalar durdu:

| Bulgu | Tür | Sonuç |
|---|---|---|
| Özette "FastAPI", seçilmiş yetenek ama hiçbir maddede yok | yanlış alarm | Temellendirme havuzun tamamına genişletildi |
| "LLM-based", "RAG-based" | yanlış alarm | Tireli bileşik parçalarından temelleniyor |
| "LLMs" (çoğul), "ML" (kısaltma) | yanlış alarm | Çoğul eki + `tags.yaml` alias'ı |
| Tüm id'lerden `.en` soneki düşürülmüş | **gerçek** | Tek adaya çözülüyor, uyarı basılıyor |

Türkçe ve İngilizce çalıştırmaların ikisi de şu an **sıfır ihlalle** geçiyor; üretilen
`tailored.json` rxresu.me şemasını doğruluyor.
