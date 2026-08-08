# `master.json` şeması

`master.json` bir Reactive Resume v2 CV'sidir — **artı** iki uzantı. Reactive Resume'a
doğrudan import edilebilir; uzantı alanları yok sayılır.

- Modeller: [`src/cv_updater/models/resume.py`](../src/cv_updater/models/resume.py)
- Canlı şema: `https://rxresu.me/schema.json`
- Anlık görüntü: [`tests/fixtures/rxresume.schema.json`](../tests/fixtures/rxresume.schema.json)
  (testler ağa çıkmaz — şema değişirse bu dosya yeniden indirilip commit'lenmeli)

> **Modeller dokümantasyondan değil, gerçek export'tan türetildi.** Barındırılan rxresu.me
> sürümü issue metnindeki şemadan farklı; farklar aşağıdaki tabloda.

## Üst düzey

| Alan | Tip | Not |
|---|---|---|
| `picture` | nesne | Görsel ayarları. `hidden: true` ise render edilmez. |
| `basics` | nesne | Ad, başlık, e-posta, telefon, konum, website, `customFields[]`. |
| `summary` | nesne | `content` **HTML string**'dir. |
| `sections` | nesne | 12 bölümün tamamı **zorunlu** (boş olabilir). |
| `customSections` | dizi | Serbest bölümler; MVP'de kullanılmıyor. |
| `metadata` | nesne | `template`, `layout`, `page`, `design`, `typography`, `notes`, `styleRules`. |

Bölümler: `profiles` · `experience` · `education` · `projects` · `skills` · `languages` ·
`interests` · `awards` · `certifications` · `publications` · `volunteer` · `references`.

Her bölüm `{title, icon, columns, hidden, keepTogether, startOnNewPage, items[]}` şeklindedir.
Her öğe bir `id` ve bir `hidden` bayrağı taşır.

### Öğe şekilleri

| Bölüm | Zorunlu alanlar (id/hidden dışında) |
|---|---|
| `experience` | `company`, `position`, `location`, `period`, `description` · ops. `website`, `roles[]` |
| `education` | `school`, `degree`, `area`, `grade`, `location`, `period`, `description` |
| `skills` | `icon`, `name`, `proficiency` · ops. `level` (0-5), `keywords[]`, `iconColor` |
| `projects` | `name`, `period`, `description` · ops. `website` |
| `languages` | `language`, `fluency` · ops. `level` |
| `profiles` | `icon`, `network`, `username` · ops. `website`, `iconColor` |
| `interests` | `icon`, `name` · ops. `keywords[]` |
| `awards` | `title`, `awarder`, `date`, `description` |
| `certifications` | `title`, `issuer`, `date`, `description` |
| `publications` | `title`, `publisher`, `date`, `description` |
| `volunteer` | `organization`, `location`, `period`, `description` |
| `references` | `name`, `position`, `phone`, `description` |

`description` alanlarının hepsi **HTML string**'dir (`<ul><li><p>…</p></li></ul>`).

## Dokümante edilen şemadan farklar

Issue 02'nin metni eski/yaklaşık bir şema anlatıyordu. Gerçekte:

| Konu | Issue metni | Gerçek export ve canlı şema |
|---|---|---|
| Tema bloğu | `metadata.theme{colors, level}` | **`metadata.design{level, colors}`** — `theme` diye bir alan yok |
| Bölüm bayrakları | `{title, columns, hidden, items}` | ayrıca `icon`, **`keepTogether`**, **`startOnNewPage`** |
| Deneyim | tek `position` | ayrıca **`roles[]`** — aynı şirket altında birden fazla unvan satırı |
| İkonlu öğeler | `icon` | ayrıca **`iconColor`** |
| `website` | `{url, label}` | ayrıca **`inlineLink`** |
| `metadata` | — | ayrıca **`notes`**, **`styleRules`**, ops. `stylesheet` |
| `basics` | — | **`customFields[]`** (`{id, icon, text, link}`) — CV'deki GitHub/LinkedIn satırları burada |
| Zorunluluk | belirsiz | 12 bölümün **hepsi** ve `metadata.{layout,page,design,typography,notes}` zorunlu |

`metadata.template` bir enum: `azurill · bronzor · chikorita · ditgar · ditto · gengar · glalie ·
kakuna · lapras · leafish · meowth · onyx · pikachu · rhyhorn · scizor`.

## Uzantılar

Canlı şemada (tek istisna `metadata.stylesheet`) hiçbir yerde `additionalProperties: false`
yoktur — bu yüzden `x_` önekli alanlar import'u bozmaz. Bu varsayım
`test_schema_allows_unknown_fields` ile test altındadır; şema bir gün kapanırsa test kırılır.

### `x_meta` — seçim metası

Her bölüm öğesinde ve her `x_bullet`'ta opsiyonel.

```json
"x_meta": {
  "tags": ["python", "computer-vision", "fastapi"],
  "impact": "8 saatlik videoyu 12 dakikada işleyen pipeline",
  "seniority": "lead",
  "lang": "tr",
  "always_include": false
}
```

| Alan | Tip | Sözleşme |
|---|---|---|
| `tags` | `string[]` | **`data/tags.yaml` kontrollü sözlüğünden.** Serbest metin değil — yazım farkı sessizce puan kaybettirir. Boşsa öğe **hiçbir zaman seçilemez**. |
| `impact` | `string?` | Ölçülebilir etki, tek cümle, sayı içersin. TODO.md §1'in "maddelere ölçülebilir etki ekle" maddesi burada yaşar. |
| `seniority` | enum? | `intern · junior · mid · senior · lead` |
| `lang` | enum? | `tr · en`. TR ve EN varyantları **aynı** süper sette yaşar; uyarlama ilanın diline göre seçer. |
| `always_include` | `bool` | `true` ise puanlamadan bağımsız olarak her zaman girer. Az kullan — her zorunlu madde bir seçim alanı harcar. |

### `x_bullets` — madde granülerliği

`experience` ve `projects` öğelerinde. Uyarlama adımı HTML `description` üzerinde değil,
**madde id'leri üzerinden** konuşur; seçilen id'lerden `<ul><li>…</li></ul>` issue 08'de derlenir.

```json
"x_bullets": [
  { "id": "exp-preunec.cv-pipeline.tr", "text": "…", "x_meta": { "tags": ["computer-vision"] } },
  { "id": "exp-preunec.cv-pipeline.en", "text": "…", "x_meta": { "tags": ["computer-vision"] } }
]
```

**İd'ler kararlı olmalı.** Uyarlama çıktısı ve `match_report.md` bu id'lere atıf yapar; id
değişirse geçmiş başvuru kayıtları anlamsızlaşır. Kullanılan kalıp:

```
<öğe-id>.<konu>.<dil>          örn. exp-preunec.lead.tr
```

UUID olmak zorunda değil — insan okuyabilir olması, `match_report.md`'yi okunur kılıyor.

**Aynı deneyim birden fazla madde varyantı taşır** (biri CV-pipeline odaklı, biri backend
odaklı); uyarlama ilana göre birini seçer. Süper setin anlamı bu.

#### `role` — madde hangi unvana ait?

Terfi taşıyan bir deneyimde (`roles[]`) her madde kendi rolünün altına yazılır:

```json
{ "id": "exp-preunec.agri-ml.tr", "text": "…", "role": "exp-preunec.role.engineer" }
```

Boş bırakılabilir — o zaman madde **en güncel role** düşer, ki rol taşımayan deneyimlerin
tamamı için doğru davranış. Alan, ikinci gerçek PDF'in bulgusu üzerine eklendi: bütün
maddeler en güncel role yığıldığı için terfi öncesi unvan sayfada *başlığı olup gövdesi
olmayan* bir satır olarak duruyordu. Var olmayan bir role atıf maddeyi kaybettirmez (güncel
role düşer) ama `validate` bunu `bullets.unknown_role` ile hata olarak raporlar.

### `x_i18n` — tek örnekli alanların çevirisi

Her bölüm öğesinde, her rolde ve `basics`'te opsiyonel. Yapı: **dil kodu → alan adı → değer.**

```json
"x_i18n": {
  "en": { "position": "Software Engineering Intern" }
}
```

Maddeler ve özet iki dilde yaşıyor (`x_meta.lang` ile ayrı varyantlar), ama **unvan, derece,
dil seviyesi, headline** gibi alanlar öyle yaşayamıyor: bir deneyimin bir `position`'ı var.
İlk İngilizce CV'de maddeler İngilizceydi, üstlerindeki "Stajyer Yazılım Mühendisi" Türkçe.

| Kural | Neden |
|---|---|
| Yalnızca **modelde var olan metin alanları** yazılır | Yazım hatası (`possition`) yeni bir alan uydurmasın. `validate` bunu `content.unknown_i18n_field` ile yakalar — yoksa sessizce hiçbir şey olmuyordu. |
| **Boş alan çevrilmez** | Boşluk derlemenin bir kararı olabilir: rol taşıyan deneyimde öğe seviyesindeki unvan bilerek siliniyor. Çeviri onu geri yazsaydı unvan tekrarı geri gelirdi. |
| Modele **hiç gösterilmez** | Unvan guardrail'in dokunulmaz tuttuğu alanlardan (`models/tailor.py`). Burada tutulan şey çeviri, veri değil. |

Tarihler bu tabloya girmez: onlar `cv_updater.periods` ile sözlükten çevriliyor. Yine de
`x_i18n`'e `period` yazılırsa **o kazanır** — sözlük genel kural, elle yazılan çeviri istisna.

## Değişmezler

- `data/master.json` **kişisel veri** tutar → `.gitignore`'da. Depoya yalnızca anonim
  `data/master.example.json` girer (`test_example_holds_no_personal_data` bunu kontrol eder).
- Bölüm öğesi id'leri ve madde id'leri dosya genelinde **tekil**.
- `Resume.model_dump(mode="json", exclude_none=True)` çıktısı canlı şemaya **uymalı** —
  `test_dump_validates_against_rxresume_schema` bunu her koşuda doğrular.
- Modeller `extra="allow"` ile çalışır: rxresu.me yeni bir alan eklerse round-trip'te
  kaybolmaz.

Süper seti nasıl büyüteceğin ve madde yazım kuralları `docs/MASTER_DATA.md`'de — o dosya
`data/master.json` ile aynı gizlilik sınıfında olduğu için gitignore'da, yalnızca yerelde.
