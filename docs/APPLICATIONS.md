# Başvuru günlüğü — günlük rutin

> Issue 12. Kod: `src/cv_updater/steps/applications.py`, komutlar: `cv-updater log …`.
> **LLM yok, ağ yok.** `applications/` gitignore'da: kişisel veri ve şirket bilgisi taşıyor.

## Neden var

Plan haftada 10-15 hedefli başvuru. Üçüncü haftada şu soru kaçınılmaz: *buraya başvurmuş muydum,
hangi CV'yi göndermiştim, ne demiştim?* Araç zaten her başvuruda çalışıyor; kaydı da o tutuyor.

`out/` ile `applications/` farklı işler yapıyor:

| | `out/<slug>-<damga>/` | `applications/<tarih>-<şirket>-<pozisyon>/` |
|---|---|---|
| Ne | deneme alanı | **arşiv** |
| Kaç tane | aynı ilana istediğin kadar | gönderilen sürüm |
| Ömür | silinebilir | kalıcı |
| İçerik | boru hattının tüm ara dosyaları | gönderilenler + `meta.json` |

`out/` bir ilanı üç kez uyarlamak için var; hangisinin **gönderildiği** bilgisi orada yok.
Başvuru günlüğü tam olarak onu tutuyor.

## Günlük akış

```bash
# 1. İlanı bul, uyarla — kayıt otomatik açılır
uv run cv-updater tailor ilan.txt --source LinkedIn --url https://…

# 2. PDF üret (arşive cv.pdf ve ats_report.md da eklenir)
uv run cv-updater render novatek-tr

# 3. Gönder, sonra durumu işaretle
uv run cv-updater log status novatek applied

# 4. Ara sıra bak
uv run cv-updater log list                 # takip bekleyenler ⚑ ile işaretli
uv run cv-updater log list --since 2w
uv run cv-updater log stats
```

Kayıt istemiyorsan `tailor --no-log`.

## Kayıt ne içeriyor

```
applications/20260808-novatek-ai-muhendisi/
  job_raw.txt        ilanın ham hali — sonradan "ne yazıyordu?" diye bakılır
  job.json           yapılandırılmış analiz
  tailored.json      gönderilen CV'nin kaynağı
  match_report.md    uyum raporu (mülakat hazırlığı burada)
  cv.pdf             `render` çalıştırıldıysa
  ats_report.md      `render` çalıştırıldıysa
  meta.json          aşağıdaki alanlar
```

`meta.json`: `id`, `company`, `position`, `source`, `url`, `fit_score`, `run_slug`, `created_at`,
`applied_at`, `status`, `history[]`, `notes`.

`run_slug` izlenebilirlik için: kaydın hangi `out/` çalıştırmasından geldiği. `render` bu alanı
kullanarak PDF'i sonradan doğru arşive ekliyor.

## Durumlar

| Değer | Etiket | Anlamı |
|---|---|---|
| `prepared` | hazırlandı | CV üretildi, henüz gönderilmedi |
| `applied` | başvuruldu | gönderildi, cevap bekleniyor |
| `response` | ilk dönüş | insan yazdı (otomatik alındı maili değil) |
| `interview` | teknik mülakat | süreç ilerledi |
| `offer` | teklif | |
| `rejected` | red | cevap geldi, olumsuz |
| `silent` | sessiz | makul süre geçti, hiç dönüş yok |

Değerler dosyada İngilizce, ekranda Türkçe. Ayrım kasıtlı: kayıtlar taşınabilir kalıyor, arayüz
okunur kalıyor.

`log status` her değişikliği `history[]`'ye bir satır olarak ekliyor — üstüne yazmıyor.
`--note` ile not düşebilirsin ("Salı 14:00 teknik mülakat").

### `applied_at` bir kez damgalanır

Durum ilk kez `applied` olduğunda zaman damgası düşer ve **bir daha değişmez**. Takip
hatırlatıcısı buna bakıyor: `applied` durumunda 14+ gün bekleyen başvurular `log list`'te `⚑` ile
işaretleniyor ve altta sayılıyor.

Mülakata geçmiş bir başvuru "cevapsız" sayılmaz — bekleme yalnızca `applied` durumunda işler.

## `log stats` — eşiği veriyle ayarlamak

```
┌────────────────┬─────────┐        ┌────────┬────────────┬──────────┬───────────┐
│ toplam kayıt   │       3 │        │ uyum   │ gönderilen │    dönüş │ ilerleyen │
│ gönderilmiş    │       3 │        ├────────┼────────────┼──────────┼───────────┤
│ dönüş alan     │ 1 (%33) │        │ %45–59 │          1 │   0 (%0) │    0 (%0) │
│ ilerleyen      │ 1 (%33) │        │ %70–84 │          1 │   0 (%0) │    0 (%0) │
│ red            │       0 │        │ %85+   │          1 │ 1 (%100) │  1 (%100) │
│ sessiz         │       0 │        └────────┴────────────┴──────────┴───────────┘
│ takip bekleyen │       1 │
└────────────────┴─────────┘        En iyi dönüş aralığı: %85+ (%100 ilerleme, 1 başvuru)
```

Sağdaki tablo issue 12'nin asıl sorusunu cevaplıyor: **hangi uyum skorunun üstünde gerçekten dönüş
alıyorum?** Cevap `docs/SCORING.md`'deki `WEAK_FIT` / `STRONG_FIT` eşiklerini tahminle değil veriyle
ayarlamayı mümkün kılıyor.

Üç tasarım kararı:

* **Oranların paydası gönderilmiş başvurular.** `prepared`da bekleyen bir kayıt henüz kimseye
  ulaşmadığı için "dönüş alamadım" istatistiğini bozmamalı.
* **Red bir cevaptır ama ilerleme değil.** İki oran ayrı: "dönüş alan" (`response`, `interview`,
  `offer`, `rejected`) ve "ilerleyen" (redsiz). Eşik ayarı ikincisine bakıyor.
* **20 gönderimden azken araç kendi rakamına güvenmiyor** ve bunu yazıyor. Üç başvurudan çıkan
  "%85+ en iyi aralık" sonucu istatistik değil gürültü.

## Dizin adları

`20260808-novatek-ai-muhendisi`. Tarih öneki sıralamayı veriyor; okunur kısım `slugify`'dan
geçiyor, yani Türkçe harfler ASCII'ye iniyor ve `/` dahil her ayraç tireye dönüyor:

```
"Küçük Şirket A/B" + "Yazılım Mühendisi (AI/ML)"
  → 20260807-kucuk-sirket-a-b-yazilim-muhendisi-ai-ml
```

`/` özellikle önemli: sanitize edilmeseydi bir alt dizin açardı. Tarih öneki ayrıca adın Windows'un
ayrılmış adlarına (`CON`, `PRN`, `AUX`, `NUL`) denk gelmesini imkânsız kılıyor. Ad 69 karakterle
sınırlı — Windows'un 260 karakterlik yol sınırı altında dosya adına da yer kalsın diye.

Aynı gün aynı şirkete ikinci başvuru öncekini ezmiyor, sonuna sayaç ekleniyor.

### Belirsiz id sessizce çözülmez

`log status novatek applied` iki kayda birden uyuyorsa komut **duruyor** ve adayları listeliyor:

```
✗ 2026 3 başvuruya uyuyor:
    20260720-novatek-ai-muhendisi
    20260801-helix-labs-ml-engineer
    20260807-kucuk-sirket-a-b-yazilim-muhendisi-ai-ml
Daha ayırt edici bir parça ya da tam adı ver.
```

Aynı şirkete birden çok uyarlama yapmak normal ve dizin adları birbirine çok benziyor; yanlış
kaydın durumunu değiştirmek geri alınması zahmetli bir hata. Tam ad verilirse belirsizlik olmaz.

## Gizlilik

`applications/` **gitignore'da** ve orada kalmalı: içinde kişisel iletişim bilgisi, üretilmiş
CV'lerin tamamı ve başvurulan şirketlerin ilanları var. Depo public olacak.

`data/master.json` ve `docs/MASTER_DATA.md` ile aynı sınıf. `tests/conftest.py` ayrıca bir güvenlik
ağı kuruyor: her testte arşiv geçici bir dizine yönleniyor — `tailor`'ı gerçekten çalıştıran testler
geliştiricinin arşivine sahte kayıt yazmasın diye. (Bu ağ eklenmeden önce tam olarak bu oldu.)
