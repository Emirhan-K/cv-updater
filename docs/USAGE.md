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
  job_raw.txt      ilanın diskteki hali — hiç dokunulmamış
  job_clean.txt    normalize edilmiş metin (LLM bunu görür)
  job.json         yapılandırılmış analiz + etiket eşlemesi
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

## LLM'e gitmeden çalıştırmak

```bash
uv run cv-updater tailor ilan.txt --no-llm
```

En son üretilen `job.json`'ı yeniden kullanır, API'ye gitmez. Sonraki adımlar (ön filtre,
uyarlama) üzerinde çalışırken her denemede model ücreti/kotası harcamamak için.

Uyarı: araç hangi çalıştırmadan kopyaladığını yazar. **Aynı ilan olduğundan emin ol** —
başka bir ilanın analizini yeniden kullanmak sessiz bir hata olurdu.

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
| `Editör bulunamadı` | `CV_UPDATER_EDITOR` ya da `EDITOR` ayarla. |

## Sonraki adım

`job.json` üretildikten sonrası henüz uygulanmadı: ön filtre (issue 06), LLM uyarlaması ve
dürüstlük guardrail'i (07), `tailored.json` (08), uyum raporu (09), ATS öz-kontrolü (10).
Plan [`ROADMAP.md`](ROADMAP.md)'de.
