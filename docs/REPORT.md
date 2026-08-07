# Uyum raporu nasıl okunur

> Issue 09. Kod: `src/cv_updater/steps/report.py`. Çıktı:
> `out/<slug>-<damga>/match_report.md`.

Rapor tek bir soruyu 30 saniyede cevaplamak için yazıldı: **bu ilana uyuyor muyum, uymuyorsam
ne eksik?** Cevap hem "başvurayım mı" kararını hem de ön yazı / mülakat hazırlığını besliyor.

Rapor **tamamen deterministik**. Uyum skoru bir modele sorulmuyor; gereksinim kapsamasından
hesaplanıyor. Modelden gelen tek şey madde gerekçeleri ve elenme sebepleri — ikisi de zaten
guardrail'den geçmiş metinler.

Rapor dili Türkçe, ilanın dili ne olursa olsun: bunu ilan sahibi değil, aday okuyor.

```bash
uv run cv-updater report <slug>    # raporu yeniden üret, LLM'e gitmez
```

## Bölüm bölüm

### 1. Başlık

```
> Uyum: %72 · zorunlu 8/11 · tercih 5/9 · karar: başvur
```

Formül ve eşikler [`SCORING.md`](SCORING.md#uyum-skoru)'de. Kısaca: zorunlu bir gereksinim
tercih edilenin üç katı ağırlıkta, dolaylı kapsama yarım puan.

| Karar | Ne yapmalı |
|---|---|
| **başvur** (≥%70) | Başvur. Raporun geri kalanını ön yazı için kullan. |
| **başvurulabilir** (%45–69) | Başvur, ama önce "mülakat hazırlığı" bölümünü oku. |
| **zorlama** (<%45) | ⛔ Başlıkta uyarı çıkar. Süper setteki malzeme bu ilanı karşılamıyor. |

Son satır önemli: bu araç "her ilana CV uydur" aracı değil. Uymayan bir ilana uydurulmuş bir
CV, guardrail sayesinde yalan söylemiyor ama **zayıf** oluyor — ve o zaman başka bir ilana
harcanabilirdi.

### 2. Gereksinim tablosu

İlandaki her `must` / `nice` gereksinim, durumu ve onu karşılayan madde:

| Durum | Anlamı | Ne yapmalı |
|---|---|---|
| ✅ kapsanıyor | Seçilen bir madde bu gereksinimin etiketini taşıyor | — |
| ⚠️ dolaylı | Madde yok, ama bir yetenek ya da seçilmemiş bir madde taşıyor | Mülakatta somut örnek hazırla |
| ❌ eksik | Süper sette hiçbir karşılığı yok | Aşağıdaki bölüme bak |

Zorunlu gereksinimler üstte. Bir gereksinim "dolaylı" görünüyorsa CV'de o konu var ama öne
çıkmıyor demektir — çoğu zaman `master.json`'a o konuyla ilgili bir madde eklemek doğru
hamle.

### 3. Eksik anahtar kelimeler

İlanda geçen ama süper sette **hiçbir** maddede olmayan etiketler, ilandaki geçtiği haliyle
birlikte. Her biri için tek soru: **bunu gerçekten biliyor musun?**

- **Biliyorsan:** `data/master.json`'da ilgili maddeye/yeteneğe `x_meta.tags` ekle,
  `validate` ile doğrula, `score <slug>` ile yeniden puanla. Bu bir eksiklik değil, kayıt
  boşluğuydu.
- **Bilmiyorsan:** **ekleme.** Süper set doğruluk kaynağı; buraya giren yanlış bir etiket
  guardrail'i baştan anlamsız kılar. Bu etiketler ayrıca senin öğrenme listen.

Alt kısımda `tags.yaml`'da karşılığı olmayan terimler ayrı listeleniyor — sözlük buradan
büyüyor.

### 4. Seçilen maddeler ve gerekçeleri

Modelin her madde için yazdığı `rationale`: bu maddenin ilandaki hangi gereksinimi
karşıladığı. Ön yazının iskeleti burada; cümleleri doğrudan kullanabilirsin çünkü hepsi
guardrail'den geçti.

### 5. Elenen güçlü maddeler

Havuza girip de seçilmeyen maddeler ve modelin gerekçesi. Bir tanesini gerçekten istiyorsan
Reactive Resume'a import ettikten sonra elle eklemek en kolayı — madde metni burada duruyor.

### 6. Mülakat hazırlığı

Yalnızca **dolaylı** kapsanan ve **eksik** olan zorunlu gereksinimler. Mülakatta sorulacak
şeyler bunlar; CV'nin zayıf noktalarını mülakattan önce görmek raporun en pratik faydası.

### 7. Guardrail

Kaç madde denetlendi, kaç ihlal bulundu, ne yapıldı. Temiz çalıştırmada tek satır:

```
✅ 14 madde denetlendi, sıfır ihlal. Modelin yazdığı her cümle süper setteki
   bir kaynağa dayanıyor.
```

İhlal varsa kural, yer, bulgu ve onarımı tablo halinde. Kural listesi
[`GUARDRAILS.md`](GUARDRAILS.md)'de.

### Çalıştırma ayrıntısı (katlanmış)

Hangi model, ne zaman, süper setin kaçta kaçı LLM'e gitti, hangi etiketler zorunlu sayıldı,
geri çağırma ağı ne yakaladı. Bir sonuç şaşırtıcı geldiğinde ilk bakılacak yer burası.

## Raporu ne zaman yeniden üretmeli

`report <slug>` `job.json` + `candidates.json` + `plan.json`'dan yeniden kuruyor, LLM'e
gitmiyor. İşe yaradığı durumlar:

- `master.json`'a eksik bir etiket ekledin → önce `score <slug>`, sonra `report <slug>`.
  Uyum yüzdesi güncellenir.
- Raporu elle düzenledin, orijinali geri istiyorsun.
- Ağırlıkları ya da eşikleri kurcaladın ve etkisini görmek istiyorsun.

Planı değiştirmek (farklı madde seçimi) LLM adımını tekrar gerektiriyor — `tailor`'ı baştan
çalıştır.
