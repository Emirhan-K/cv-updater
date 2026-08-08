# Günlük akış — kopyala, çalıştır, içe aktar

Bu doküman aracın **günlük kullanımını** anlatır. Kurulum `README.md`'de, boru hattının
içindeki adımlar [`PIPELINE.md`](PIPELINE.md)'de.

Tasarım hedefi: günde 2-3 başvuruda ilanı araca sokmak **beş saniyeden uzun sürmemeli**.
O yüzden hiçbir girdi yolu soru sormaz; tek istisna, metin anlamlı olamayacak kadar kısaysa
(yanlışlıkla boş pano) sorulan onay.

## Üç girdi yolu

### 1. Pano → stdin (en hızlı)

İlanı tarayıcıdan kopyala, sonra:

```powershell
Get-Clipboard | uv run cv-updater tailor -
```

```bash
# macOS / Linux
pbpaste | uv run cv-updater tailor -     # ya da: xclip -o | ...
```

`-` "stdin'den oku" demektir. Günlük kullanımın büyük kısmı bu.

### 2. Dosya

İlanı bir dosyaya kaydettiysen:

```bash
uv run cv-updater tailor ilanlar/novatek-ai-muhendisi.txt
```

Dosya adı çıktı dizininin adına da katkı verir (aşağıya bak), o yüzden `job.txt` yerine
`sirket-pozisyon.txt` gibi bir ad işini kolaylaştırır.

### 3. Editör (`--paste`)

Panoya sığmayan ya da elle düzeltmek istediğin ilanlar için:

```bash
uv run cv-updater tailor --paste
```

`$EDITOR` (yoksa Windows'ta `notepad`, diğerlerinde `vi`) açılır, metni yapıştırırsın,
kaydedip kapatınca akış devam eder. `#` ile başlayan satırlar yok sayılır.

Editörü seçmek için `CV_UPDATER_EDITOR`, `VISUAL` ya da `EDITOR`; bayrak da yazılabilir:

```bash
CV_UPDATER_EDITOR="code --wait" uv run cv-updater tailor --paste
```

## Çıktı nereye gider

Her çalıştırma **yeni** bir dizin açar:

```
out/<slug>-<YYYYAAGG>-<SSDDss>/
  job_raw.txt        ilanın diskteki hali — hiç dokunulmamış
  job_clean.txt      normalize edilmiş metin (LLM bunu görür)
  job.json           yapılandırılmış analiz + etiket eşlemesi
  candidates.json    ön filtrenin puanladığı aday havuzu (LLM yok)
  plan.json          modelin seçimi + yeniden ifadeleri
  violations.json    guardrail denetimi — temiz çalıştırmada da yazılır
  tailored.json      ★ Reactive Resume'a import edilecek CV
```

**Önceki çıktı asla ezilmez.** Aynı ilanı iki kez çalıştırmak iki dizin üretir; bu kasıtlı,
bir ilana iki farklı uyarlama denemek ve ikisini yan yana karşılaştırmak isteyeceksin.

`slug` şuradan gelir, bu sırayla:

1. `--slug "Acme Kıdemli Geliştirici"` verdiysen ondan (Türkçe karakterler ASCII'ye iner),
2. dosya adından (`novatek-ai-muhendisi.txt` → `novatek-ai-muhendisi`),
3. ilanın ilk anlamlı satırından.

`job_raw.txt` bilerek tutulur: normalizasyon bir şeyi yanlış attıysa geri dönebilmek için.

## Normalizasyon ne yapar

`job_clean.txt` üretilirken (ayrıntı [`PIPELINE.md`](PIPELINE.md)):

- **Ligatürler açılır** — `ﬁne-tuning` → `fine-tuning`. LinkedIn kopyasında sık, ATS'in
  aramayı kaçırmasına yol açıyor.
- **Görünmez karakterler** (sıfır genişlikli boşluk, kırılmayan boşluk) temizlenir.
- **Madde işaretleri tek biçime iner** — `•`, `▪`, `1.`, `2)` hepsi `- ` olur.
- **Arayüz artığı ayıklanır** — "Kaydet", "Bu ilanı paylaş", "142 başvuran", çerez metni,
  navigasyon satırları. Kural satırın **tamamına** bakar; cümle içinde geçen aynı kelime
  korunur ("adayların özgeçmişini kaydet butonuyla saklayan" cümlesi silinmez).

Ekrandaki tabloda kaç ligatür düzeltildiği ve kaç satır atıldığı yazar. Beklenmedik bir sayı
görürsen `job_raw.txt` ile `job_clean.txt`'yi karşılaştır.

## İlan çok kısaysa

200 karakterin altındaki metinde araç durur ve sorar:

```
⚠ İlan çok kısa (38 karakter, eşik 200). Yanlış pano ya da eksik kopyalama olabilir.
Yine de devam edilsin mi? [y/N]:
```

Eşik **temizlenmiş** metin üzerinden ölçülür — 20 satır "Kaydet"/"Paylaş" eşiği şişirmez.
Soruyu atlamak için `--yes`.

## `tailored.json`'ı Reactive Resume'a aktarmak

1. [rxresu.me](https://rxresu.me) hesabına gir.
2. Dashboard'da **Create a new resume** yanındaki menüden **Import** → **JSON**.
3. `out/<slug>-<damga>/tailored.json` dosyasını seç, **Validate** de.
4. Doğrulama geçtiyse **Import**. Araç dosyayı zaten aynı şemaya karşı doğruluyor;
   ekranda "✓ Reactive Resume şemasına uyuyor" gördüysen bu adım sorun çıkarmaz.
5. İçe aktarılan CV'yi aç, sağ üstten **PDF** olarak indir.

İçe aktarma sonrası bilmen gerekenler:

- **Seçilmeyen hiçbir şey silinmedi**, `hidden: true` yapıldı. Bir yeteneği ya da deneyimi
  geri istiyorsan editörde göz ikonuna tıklaman yeterli — yeniden yazman gerekmiyor.
- **Unvan, şirket adı ve tarihler `master.json` ile birebir aynı.** Bu alanlar modele hiç
  gösterilmiyor (bkz. [`GUARDRAILS.md`](GUARDRAILS.md)).
- **Font `Tinos` olarak değiştirilir** (süper sette ne yazıyorsa ezilir). Bu bir hatayı
  düzeltmiyor, bir riski kapatıyor: ölçüldüğü kadarıyla Reactive Resume zaten ligatür
  üretmiyor, ama Tinos'ta ligatür glifi hiç olmadığı için render yolu değişse bile
  ("fine-tuning" → `fne-tuning`) sorun doğamaz. Times New Roman metrik eşi olduğu için
  ATS tarafında da en konvansiyonel seçim. Beğenmezsen `--font Inter` ya da `--font Arimo`
  (ikisi de ölçüldü, ligatürsüz); başka bir font verirsen uyarı basılır.
- **Terfi taşıyan deneyimlerde maddeler en güncel rolün altına yazılır.** Reactive Resume
  bir deneyimde `roles[]` doluysa öğe seviyesindeki açıklamayı render etmiyor.
- Dil `--lang` ile zorlanmadıysa ilanın dilinden gelir; `metadata.page.locale` ona göre
  `tr-TR` ya da `en-US` olur.

## Guardrail durdurursa

Uydurulmuş içerik varsayılan olarak akışı durdurur:

```
✗ Guardrail durdurdu. Uydurulmuş içerik CV'ye giremez.
```

`violations.json` hangi maddede ne bulunduğunu yazar. İki seçenek:

```bash
uv run cv-updater tailor ilan.txt --repair   # ihlalli metni kaynağına döndür, devam et
uv run cv-updater tailor ilan.txt            # baştan dene (model farklı bir plan üretebilir)
```

`--repair` bir gevşetme değil: uydurulan metin atılır, `master.json`'daki gerçek metin geri
gelir. Kural listesi [`GUARDRAILS.md`](GUARDRAILS.md)'de.

## LLM'e gitmeden çalıştırmak

```bash
uv run cv-updater tailor ilan.txt --no-llm
```

**İki LLM adımını da** atlar: en son üretilen `job.json` ve `plan.json` yeniden kullanılır.
Derleme ve raporlama üzerinde çalışırken her denemede kota harcamamak için.

Uyarı: araç hangi çalıştırmadan kopyaladığını yazar. **Aynı ilan olduğundan emin ol** —
başka bir ilanın analizini yeniden kullanmak sessiz bir hata olurdu. (Planda değil: farklı
bir ilanın planı guardrail'e `source.not_offered` olarak takılır.)

Yalnızca ön filtreyi tekrar çalıştırmak için ayrı bir komut var, o hiç LLM'e gitmez:

```bash
uv run cv-updater score <slug>              # puanlamayı tabloyla göster
uv run cv-updater score novatek --lang en   # aynı ilan, İngilizce havuz
```

## Yapılandırma

`.env.example`'ı `.env` olarak kopyala. İki seçenek var:

```bash
# Yerel gateway (varsayılan) — workspace'teki llm-gateway/
LLM_BASE_URL=http://127.0.0.1:8045    # /v1 YOK, SDK kendi ekler
LLM_API_KEY=<llm-gateway/.env içindeki AG_API_KEY>
LLM_MODEL=claude-opus-5

# ya da doğrudan Anthropic: üstteki üç satırı sil, bunu yaz
ANTHROPIC_API_KEY=sk-ant-...
```

Gateway kullanıyorsan **Docker Desktop açık olmalı**; kapalıysa `Connection refused` alırsın.

`validate` ve `--help` anahtar istemez.

## Sık karşılaşılanlar

| Belirti | Sebep / çözüm |
|---|---|
| `LLM_API_KEY ... tanımlı değil` | `.env` yok ya da eksik. `AG_WEB_PASSWORD` değil, `AG_API_KEY` kopyalanacak. |
| `Connection refused` | Gateway kapalı — `docker compose up -d` (llm-gateway dizininde). |
| `sözlükte yok: ...` satırı | İlandaki terimin `data/tags.yaml`'da karşılığı yok. Karşılığı varsa alias ekle; yoksa sorun değil, bilgi amaçlı. |
| `ilanda bulunamadı, atıldı: ...` | Model ilanda geçmeyen bir terim üretmiş; süzgeç attı. Beklenen davranış. |
| `model kaçırmış: ...` | Geri çağırma ağı ilanda olup modelin bildirmediği etiketleri buldu. Puanlamaya dahil edildiler; bilgi amaçlı. |
| `süper sette yok: ...` | İlanın istediği bir konu `master.json`'da yok. Gerçekten biliyorsan ekle, bilmiyorsan uyum raporu bunu "eksik" diye yazacak. |
| `Guardrail durdurdu` | Model uydurmuş. `violations.json`'a bak, `--repair` ile sürdür. |
| `Editör bulunamadı` | `CV_UPDATER_EDITOR` ya da `EDITOR` ayarla. |

## Sonraki adım

`tailored.json`'a kadar olan her şey çalışıyor. Kalanlar: uyum raporu (issue 09),
ATS öz-kontrolü (10), RenderCV ile otomatik PDF (11). Plan [`ROADMAP.md`](ROADMAP.md)'de.
