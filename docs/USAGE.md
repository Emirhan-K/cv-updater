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
  tailored.json      ★ uyarlanmış CV — render'ın girdisi
  match_report.md    uyum raporu: skor, gerekçeler, eksikler

# `render` çalıştırıldıktan sonra aynı dizine eklenenler:
  cv.yaml            RenderCV girdisi (ara adım)
  <Ad>_CV.typ        Typst kaynağı (ara adım — tipografiyi elle kurcalamak istersen)
  cv.pdf             ★ gönderilecek dosya
  ats_report.md      ATS öz-kontrolü
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

## PDF üretmek — `render`

Asıl akış bu. `tailor` bittikten sonra tek komut:

```bash
uv run cv-updater render <slug>              # ilanın dilinde
uv run cv-updater render <slug> --lang en    # İngilizce başlıklar ve tarihler
```

`tailored.json` → RenderCV YAML → Typst → `cv.pdf`, ve arkasından **ATS öz-kontrolü otomatik
çalışır** (atlamak için `--no-check`). Ekranda yedi kontrollük bir tablo ve 100 üzerinden puan
görürsün; ayrıntı [`ATS.md`](ATS.md), render kararları [`RENDER.md`](RENDER.md).

```bash
uv run cv-updater render <slug> --font "EB Garamond"   # 17 gömülü fonttan biri
uv run cv-updater render <slug> --theme engineeringresumes
uv run cv-updater render <slug> --out ~/Desktop/cv.pdf
```

Bilmen gerekenler:

- **CV tek renk.** `classic` teması ad, headline, iletişim satırı, bölüm başlıkları ve
  linkleri maviye boyuyordu; beşi de siyaha çevrildi (`render_pdf.COLORS`).
- **İletişim ikonları kapalı**, telefon `+90 …` biçiminde.
- **Terfi taşıyan deneyimler düzleşir**: RenderCV'de iç içe rol yok, rol başına bir girdi
  üretilir ve şirket adı tekrar eder. Maddeler `x_bullets[].role` ile kendi rollerine gider.
- **Tarihler yeniden biçimlenir.** RenderCV `period`'u ayrıştırıp kendi yereline göre yazar
  ("Sept 2024 – present") ve süre rozeti ekler ("2 years"). `2020-2025` gibi yalın yıl
  aralıkları "Jan 2020 – Jan 2025" olarak çıkıyor — bilinen bir pürüz.
- **İki sayfaya taşarsa uyarır** ve neyin kırpılabileceğini söyler; akış durmaz. Kırpmak
  istersen `tailored.json`'da ilgili öğeye `"hidden": true` yazıp tekrar `render` çalıştır.

Render **opsiyonel bir bağımlılık**: `uv sync --extra render` kurulu değilse ne yapılacağını
söyleyen bir hata alırsın.

### Elde PDF varsa: yalnızca ATS kontrolü

```bash
uv run cv-updater check cv.pdf
uv run cv-updater check cv.pdf --job out/<slug>/job.json   # anahtar kelime kapsaması da ölçülsün
```

## Alternatif: `tailored.json`'ı Reactive Resume'a aktarmak

`render` çıktısını beğenmezsen ya da CV'yi web arayüzünde elle düzenlemek istersen v1 akışı
duruyor. Bu yolun görünümü `render`'ınkinden **belirgin biçimde farklı** (şablon, font, bölüm
sırası, kenar çubuğu) — sebepleri [`RENDER.md`](RENDER.md)'de.

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
- **Terfi taşıyan deneyimlerde maddeler kendi rollerinin altına yazılır** (`x_bullets[].role`),
  atanmamışlar en güncel role düşer. Reactive Resume bir deneyimde `roles[]` doluysa öğe
  seviyesindeki açıklamayı render etmiyor — ama unvanı **ediyor**, o yüzden roller varsa öğe
  seviyesindeki unvan boşaltılır (yoksa aynı unvan iki kez görünür).
- Dil `--lang` ile zorlanmadıysa ilanın dilinden gelir; `metadata.page.locale` ona göre
  `tr-TR` ya da `en-US` olur. Bölüm başlıkları, tarihler ve unvanlar da o dile çevrilir —
  ilk ikisi otomatik, unvanlar `master.json`'daki `x_i18n` tablosundan.

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

## Başvuru günlüğü — `log`

`tailor` her çalıştırmada `applications/` altına bir kayıt açar (kapatmak için `--no-log`).
Gönderdikten sonra durumunu takip et:

```bash
uv run cv-updater log list                    # hepsi
uv run cv-updater log list --status sent      # yalnızca gönderilenler
uv run cv-updater log list --since 2w         # son iki hafta
uv run cv-updater log status <id> applied --note "referans: Ünal"
uv run cv-updater log stats                   # dönüş oranları
```

`log stats` **20-30 başvurudan önce anlamlı değil** — o kadar veri birikince
[`SCORING.md`](SCORING.md)'deki uyum eşiklerini gerçek dönüş oranına göre ayarlayabilirsin.
Ayrıntı [`APPLICATIONS.md`](APPLICATIONS.md).

## Tam bir tur, baştan sona

```bash
Get-Clipboard | uv run cv-updater tailor - --slug acme-ai --lang en   # 1. uyarla
uv run cv-updater render acme-ai --lang en                            # 2. PDF + ATS
start out/acme-ai-*/cv.pdf                                            # 3. bak
uv run cv-updater log status acme-ai sent --note "LinkedIn üzerinden" # 4. kaydet
```

Uyum raporunu (`match_report.md`) göndermeden önce oku: hangi gereksinimin **dolaylı**
karşılandığını yazıyor, mülakat hazırlığının yarısı orada.
