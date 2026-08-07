# Puanlama — deterministik ön filtre

> Issue 06. Kod: `src/cv_updater/steps/score.py`, şema: `src/cv_updater/models/candidates.py`.
> **Bu adımda LLM yok.** Aynı `master.json` + aynı `job.json` her zaman aynı `candidates.json`'ı
> üretir; testler bunu doğrudan assert ediyor.

## Neden var

Uyarlamanın tamamını modele bırakmak üç şeyi birden kötüleştiriyor:

| Sorun | Ön filtre olmadan | Ön filtreyle |
|---|---|---|
| Maliyet | 72 madde + 50 yetenek her çalıştırmada gider | ~%50 daha az metin (`budget.saved_ratio`) |
| Tekrarlanabilirlik | Aynı ilan iki farklı CV üretebilir | Aday havuzu bit bit aynı |
| Açıklanabilirlik | "Bu madde neden seçildi?" → model bilir | Skor bileşenlerine ayrılmış halde dosyada |

Seçim ikiye bölünüyor: **kod** neyin konuşmaya değer olduğunu belirliyor, **model** daralmış
havuzun içinden seçip yeniden ifade ediyor (issue 07).

## Formül

```
skor = 3.0  × (eşleşen zorunlu etiket sayısı)
     + 1.0  × (eşleşen tercih edilen etiket sayısı)
     + 0.25 × (ilanla aynı kategoride komşu etiket var mı)
     + 1.0  × (alan eşleşmesi)
     + 0.5  × (kıdem uyumu)
     + 1.0  × (tazelik, son 2 yılda doğrusal azalarak)
     + 0.5  × (ölçülebilir etki var mı)
```

Ağırlıklar `models/candidates.py:Weights` içinde ve **her çalıştırmada `candidates.json`'a
yazılır** — ağırlık değişince eski bir çalıştırmanın skoru neden farklıydı sorusu dosyadan
cevaplanabilsin diye. Değiştirmek için: `Weights` varsayılanlarını düzenle ya da
`build_pool(..., weights=Weights(must=4.0))` ver.

### Eşleştirme etiketten yapılır, metinden değil

`x_meta.tags` ile ilanın etiketleri karşılaştırılır; madde metninde "Python" kelimesinin geçiyor
olması o maddeyi Python maddesi yapmaz. Gürültüyü kesen tek şey bu. Bunun bedeli, etiketi olmayan
maddenin **hiçbir zaman seçilememesi** — `validate` bunu `bullets.untagged` uyarısıyla söylüyor.

### Ağırlıkların gerekçesi

| Bileşen | Ağırlık | Neden |
|---|---|---|
| `must` | 3.0 | "Aranan nitelikler" başlığı altındaki bir gereksinimi karşılamak, ilanın gövdesinde geçen bir teknolojiden üç kat değerli. Oran 3:1, çünkü üç tercih edilen etiket bir zorunlu etikete denk gelsin istiyoruz — daha yüksek bir oran tek bir `must` etiketini diktatör yapıyordu. |
| `nice` | 1.0 | Ölçek birimi. Diğer ağırlıkların hepsi buna göre okunur. |
| `category` | 0.25 | Doğrudan eşleşme olmadan da **komşuluk** bilgi taşıyor: `nlp` arayan ilana `speech-to-text`, `rest-api` arayan ilana `async` yakındır. `tags.yaml` kategorileri (`ai-ml`, `backend`, `veri`…) bu komşuluğu zaten tanımlıyor. İkili ve küçük — sıralamayı ancak eşitlik bozacak kadar etkiler. Asıl işi yetenek bölümünü çökertmemek: sekiz etiketli bir ilanda 50 yetenekten yalnızca o sekizi kalıyordu, ve sekiz yetenek bir CV'de "uyarlanmış" değil "eksik" görünüyor. |
| `domain` | 1.0 | Alan uyumu ("fintech", "savunma") bir tercih edilen etiket kadar değerli: aynı sektörde çalışmış olmak bir kütüphaneyi bilmekle aynı ağırlıkta. |
| `seniority` | 0.5 | Kıdem CV'nin tamamının özelliği, tek maddenin değil; sıralamayı ancak eşitlik bozacak kadar etkilemeli. |
| `recency` | 1.0 (azalarak) | Devam eden iş tam puan, iki yıl önce bitmiş iş sıfır. Amaç: etiket olarak uyan **eski** bir deneyimin güncel bir deneyimin önüne geçmemesi. |
| `impact` | 0.5 | Sayı taşıyan madde her zaman daha iyi okunur, ama ölçülemeyen iş ölçülemez olduğu için değersiz değil. **`x_meta.impact` serbest metin kalıyor**; "ölçülebilir" sayılması için içinde bir rakam geçmesi yeterli. Yapılandırmak (sayı + birim) tartışıldı ve ertelendi: şu an 58 maddede alan zaten boş, migrasyonun getirisi yok. |

### `must` / `nice` ayrımı eleme yapmaz

Karar (2026-08-07): zorunlu bir gereksinimi karşılamayan madde **elenmez, düşük puanlanır.**
Eleme kuralı, ilan "must" dediği halde CV'de dolaylı karşılığı olan güçlü maddeleri sessizce
kaybettirirdi — oysa uyum raporu (issue 09) tam da onları "⚠️ dolaylı kapsanıyor" diye
raporlayıp mülakat hazırlığına yazacak. Eleme kararını puanlama değil, üst sınırlar veriyor.

### Kıdem uyumu

`intern < junior < mid < senior < lead` merdiveninde mesafeye bakılır: aynı seviye tam puan,
bir kademe fark yarım, iki kademe ve ötesi sıfır. İlan ya da madde kıdem belirtmiyorsa bonus da
ceza da yok — bilinmeyen bir alan yüzünden sıralama bozulmasın.

### Tazelik

`period` metninin **bitiş** tarihi ayrıştırılır. Üç biçim tanınır: `Eylül 2024 – Devam Ediyor`,
`Şubat 2026 – Mayıs 2026`, `2018-2022`. "Devam Ediyor" / "Present" / "Halen" bugün sayılır.
Ayrıştırılamayan dönem bonus almaz (ceza da yemez).

```
bonus = 1.0 × max(0, 1 − (bugün − bitiş) / 2 yıl)
```

Bu, puanlamayı **bugünün tarihine** bağımlı kılan tek bileşen; testler `today=` parametresiyle
tarihi sabitliyor.

## Süzgeçler ve üst sınırlar

Puanlama sıralamayı verir, seçimi bu kurallar verir:

- **Dil süzgeci.** `x_meta.lang` ilanın diliyle uyuşmayan madde havuza hiç girmez. Dili
  belirtilmemiş madde her iki dile de uyar. `--lang en` ilanın dilini ezer (issue 08).
- **`always_include: true`** — puanı ne olursa olsun havuza girer (eğitim, çekirdek yetenekler).
- **Gizli öğe** (`hidden: true`) hiç okunmaz.
- **Üst sınırlar:** deneyim başına 8 madde, proje başına 4 madde, en fazla 5 proje, 30 yetenek.
- **Alt sınır:** hiç eşleşmeyen bir deneyimden bile en az 2 madde kalır. İstihdam boşluğu,
  konu dışı bir maddeden daha kötü okunur.
- **Eşitlik bozma:** eşit skorda `id`'ye göre sıralanır. Sözlük sırası platformdan platforma
  değişebileceği için sıralama asla ona bırakılmıyor.

Sınırlar `steps/score.py`'nin başındaki `MAX_*` / `MIN_*` sabitlerinde, formülden ayrı duruyor.

## Geri çağırma ağı

İlan analizi (issue 05) modelin **uydurduğu** terimi yakalıyordu: her `tech_keyword` ilan
metninde birebir geçmek zorunda. Geri çağırma ağı ters yönü kapatıyor — modelin **kaçırdığı**
terimi bulur.

`tags.yaml`'daki her kanonik etiket ve her alias ilan metninde aranır; bulunup da model
tarafından raporlanmamış olanlar `recall.missed`'a düşer, kanıtıyla birlikte
(`recall.evidence[etiket] = ilanda geçen terim`). Bu etiketler puanlamaya `nice` ağırlığıyla
girer.

Neden gerekti: 2026-08-06'da aynı iki ilan `claude-opus-4.6-thinking` ve `gemini-3.6-flash-high`
ile çalıştırıldı. Türkçe kurumsal ilanda ucuz model 14 etiket yerine 10 buldu; kaçırdıklarının
hepsi Türkçeye özgüydü (`Görüntü İşleme`, `Doğal Dil İşleme`, `vektör veritabanları`,
`prompt mühendisliği`). **Kayıp sessizdi** — `dropped` ve `unmapped` boş geliyor, çıktı tertemiz
görünüyordu. Geri çağırma ağı dördünü de yakalıyor, ve aracı model seçiminden bağımsızlaştırıyor:
ucuz modelle çalışıp "ne kaçırdım" sorusunu deterministik cevaplayabiliyorsun.

Aynı tarama gereksinim metinlerine de uygulanıyor: ucuz modeller gereksinimi doğru yakalayıp
`keywords`'ü boş bırakabiliyor, o durumda gereksinim etiketsiz kalırdı.

## Gereksinim kapsaması

Her gereksinim için `candidates.json` bir satır taşır (`coverage[]`):

| Durum | Anlamı |
|---|---|
| `covered` | Seçilen bir madde bu gereksinimin etiketini taşıyor |
| `indirect` | Madde yok ama bir yetenek ya da seçilmemiş bir madde taşıyor |
| `missing` | Süper sette hiçbir karşılığı yok |

`missing_tags` ise ilanda geçen ama süper setin **hiçbir yerinde** olmayan etiketler: uyum
raporundaki "eksik anahtar kelimeler" listesi buradan çıkıyor. Bir etiket burada görünüyorsa
soru şu: gerçekten bilmiyor musun, yoksa `master.json`'a eklemeyi mi unuttun?

## Uyum skoru

> Issue 09. Kod: `src/cv_updater/steps/report.py`. **Bu skor bir LLM'e sorulmuyor** —
> yukarıdaki kapsama tablosundan hesaplanıyor, dolayısıyla aynı çalıştırma her zaman aynı
> yüzdeyi veriyor.

```
uyum % = Σ(ağırlık × kredi) / Σ(ağırlık) × 100

ağırlık:  zorunlu gereksinim 3.0  ·  tercih edilen 1.0
kredi:    ✅ kapsanıyor 1.0  ·  ⚠️ dolaylı 0.5  ·  ❌ eksik 0.0
```

Ağırlık oranı puanlamadaki `must`/`nice` oranının aynısı (3:1) — aynı ilanın iki farklı
yerde farklı önemde sayılması tutarsızlık olurdu. Dolaylı kapsama yarım puan alıyor: CV'de
karşılığı olan ama madde düzeyinde öne çıkmayan bir yetkinlik ne tam sayılmalı ne de yok.

| Eşik | Karar | Ne demek |
|---|---|---|
| ≥ %70 | **başvur** | Zorunlu gereksinimlerin çoğu doğrudan kapsanıyor |
| %45 – %69 | **başvurulabilir, hazırlan** | Boşluklar var; raporun "mülakat hazırlığı" bölümünü oku |
| < %45 | **zorlama** | Süper setteki malzeme ilanı karşılamıyor; zaman başka bir ilana harcanır |

Eşikler `report.py`'deki `WEAK_FIT` / `STRONG_FIT` sabitlerinde. %45'in altında rapor
başına ⛔ işaretli bir uyarı basıyor: bu araç "her ilana CV uydur" aracı değil, hangi ilana
**değmeyeceğini** de söylemesi gerekiyor.

## Ne kadar kısıldı

`budget` bloğu süper setin ve havuzun karakter/öğe sayılarını tutar; `saved_ratio` ikisi
arasındaki oran. Issue 06'nın kabul kriteri bu sayının anlamlı olması, issue 09 da bunu uyum
raporuna yazıyor.

## Elle bakmak

```bash
uv run cv-updater score <slug>              # var olan bir çalıştırmayı yeniden puanla
uv run cv-updater score novatek --lang en   # aynı ilan, İngilizce CV
```

`<slug>` `out/` altındaki dizin adının ayırt edici bir parçası olabilir; birden çok eşleşmede
en yenisi seçilir. Komut LLM'e gitmez, API anahtarı istemez — ağırlıkları kurcalarken `tailor`'ı
baştan çalıştırıp kota yakmaya gerek yok.
