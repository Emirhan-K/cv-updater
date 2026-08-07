"""Uyum raporu (issue 09) — "bu ilana uyuyor muyum, uymuyorsam ne eksik?"

`out/<slug>/match_report.md`. Otuz saniyede okunacak şekilde yazılıyor: başlıkta
tek satır karar, altında gereksinim tablosu, sonra eksikler ve mülakat hazırlığı.

Rapor **tamamen deterministik**. Uyum skoru bir LLM'e sorulmuyor; gereksinim
kapsamasından hesaplanıyor (`docs/SCORING.md`). Modelden gelen tek şey madde
gerekçeleri (`rationale`) ve elenme sebepleri — ikisi de zaten guardrail'den
geçmiş metinler.

Rapor dili Türkçe, ilanın dili ne olursa olsun: bunu ilan sahibi değil, aday
okuyor.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cv_updater.models.candidates import CandidatePool, RequirementCoverage
from cv_updater.models.job import JobAnalysis
from cv_updater.models.tailor import TailorPlan
from cv_updater.steps.verify import VerificationReport

REPORT_FILENAME = "match_report.md"

# --- Uyum skoru: burayı değiştir, `docs/SCORING.md`'yi de güncelle --------- #
#: Gereksinim ağırlıkları — puanlamadaki must/nice oranıyla aynı (3:1).
REQUIREMENT_WEIGHT = {"must": 3.0, "nice": 1.0}
#: Kapsama durumunun kaç puan getirdiği.
COVERAGE_CREDIT = {"covered": 1.0, "indirect": 0.5, "missing": 0.0}
#: Bu eşiğin altında "bu ilanı zorlama" uyarısı basılır.
WEAK_FIT = 45
#: Bu eşiğin üstünde karar net: başvur.
STRONG_FIT = 70
# --------------------------------------------------------------------------- #

_STATUS_LABEL = {
    "covered": "✅ kapsanıyor",
    "indirect": "⚠️ dolaylı",
    "missing": "❌ eksik",
}


@dataclass(frozen=True)
class FitScore:
    """Uyum skoru ve bileşenleri — başlık satırı bundan kuruluyor."""

    percent: int
    must_covered: int
    must_total: int
    nice_covered: int
    nice_total: int

    @property
    def verdict(self) -> str:
        if self.percent >= STRONG_FIT:
            return "başvur"
        if self.percent >= WEAK_FIT:
            return "başvurulabilir — eksikler için hazırlan"
        return "zorlama"

    @property
    def weak(self) -> bool:
        return self.percent < WEAK_FIT


def fit_score(coverage: list[RequirementCoverage]) -> FitScore:
    """Gereksinim kapsamasından deterministik uyum skoru.

    Zorunlu bir gereksinim tercih edilenin üç katı ağırlıkta (puanlamadaki
    oranın aynısı); dolaylı kapsama yarım puan alıyor — CV'de karşılığı olan
    ama madde düzeyinde öne çıkmayan bir yetkinlik ne tam sayılmalı ne de yok.
    """
    earned = 0.0
    possible = 0.0
    counts = {"must": [0, 0], "nice": [0, 0]}

    for row in coverage:
        weight = REQUIREMENT_WEIGHT[row.kind]
        earned += weight * COVERAGE_CREDIT[row.status]
        possible += weight
        counts[row.kind][1] += 1
        if row.status == "covered":
            counts[row.kind][0] += 1

    percent = round(100 * earned / possible) if possible else 0
    return FitScore(
        percent=percent,
        must_covered=counts["must"][0],
        must_total=counts["must"][1],
        nice_covered=counts["nice"][0],
        nice_total=counts["nice"][1],
    )


def _escape(text: str) -> str:
    """Markdown tablosunda `|` satırı bölmesin."""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def build(
    analysis: JobAnalysis,
    pool: CandidatePool,
    plan: TailorPlan,
    verification: VerificationReport | None = None,
) -> str:
    """Raporun tam metnini üretir."""
    score = fit_score(pool.coverage)
    posting = analysis.posting
    labels = {bullet.id: bullet for bullet in pool.all_selected()}

    lines: list[str] = [
        f"# Uyum raporu — {posting.title}",
        "",
        f"**{posting.company or 'şirket belirtilmemiş'}** · {posting.location or '—'} · "
        f"{posting.work_mode} · kıdem: {posting.seniority} · dil: {posting.language}",
        "",
        f"> **Uyum: %{score.percent}** · zorunlu {score.must_covered}/{score.must_total} · "
        f"tercih {score.nice_covered}/{score.nice_total} · **karar: {score.verdict}**",
        "",
    ]

    if score.weak:
        lines += [
            f"⛔ **Uyum %{WEAK_FIT}'in altında.** Bu ilana CV uyarlamayı zorlama; "
            "süper setteki malzeme ilanın istediğini karşılamıyor. Zaman, karşılığı "
            "olan bir ilana ya da eksik konuyu gerçekten öğrenmeye harcanır.",
            "",
        ]

    lines += _requirements_section(pool.coverage, labels)
    lines += _missing_section(pool, analysis)
    lines += _selection_section(plan, labels)
    lines += _omitted_section(plan, labels)
    lines += _interview_section(pool.coverage)
    lines += _guardrail_section(verification)
    lines += _footer(pool, analysis)

    return "\n".join(lines).rstrip() + "\n"


def _requirements_section(coverage: list[RequirementCoverage], labels: dict) -> list[str]:
    if not coverage:
        return ["## Gereksinimler", "", "_İlan analizi hiç gereksinim çıkarmadı._", ""]

    lines = [
        "## Gereksinimler",
        "",
        "| | Gereksinim | Durum | Karşılayan |",
        "|---|---|---|---|",
    ]
    for row in sorted(coverage, key=lambda r: (r.kind != "must", r.index)):
        kind = "zorunlu" if row.kind == "must" else "tercih"
        covering = ", ".join(
            _escape(labels[bullet_id].text[:60]) if bullet_id in labels else bullet_id
            for bullet_id in row.bullet_ids[:2]
        )
        if not covering and row.skill_ids:
            covering = ", ".join(row.skill_ids[:3]) + " _(yetenek)_"
        lines.append(
            f"| {kind} | {_escape(row.text)} | {_STATUS_LABEL[row.status]} | {covering or '—'} |"
        )
    lines.append("")
    return lines


def _missing_section(pool: CandidatePool, analysis: JobAnalysis) -> list[str]:
    """İlanın istediği ama süper sette hiç karşılığı olmayan konular."""
    if not pool.missing_tags and not analysis.keywords.unmapped:
        return [
            "## Eksik anahtar kelimeler",
            "",
            "_Yok — ilanın her konusunun CV'de karşılığı var._",
            "",
        ]

    lines = ["## Eksik anahtar kelimeler", ""]

    if pool.missing_tags:
        lines += [
            "İlanda geçiyor, süper sette **hiçbir** maddede yok. Her biri için tek soru:",
            "*bunu gerçekten biliyor musun?*",
            "",
        ]
        for tag in pool.missing_tags:
            evidence = pool.recall.evidence.get(tag)
            source = f" _(ilanda: “{evidence}”)_" if evidence else ""
            lines.append(f"- **`{tag}`**{source}")
        lines += [
            "",
            "Biliyorsan `data/master.json`'da ilgili maddeye ya da yeteneğe "
            "`x_meta.tags` içine ekle, sonra doğrula:",
            "",
            "```bash",
            "uv run cv-updater validate",
            "uv run cv-updater score <slug>   # yeniden puanla, LLM'e gitmez",
            "```",
            "",
            "**Bilmiyorsan ekleme.** Süper set doğruluk kaynağı; buraya giren yanlış bir "
            "etiket guardrail'i baştan anlamsız kılar.",
            "",
        ]

    if analysis.keywords.unmapped:
        lines += [
            "Kontrollü sözlükte karşılığı olmayan terimler (`data/tags.yaml` buradan büyür):",
            "",
            ", ".join(f"`{term}`" for term in analysis.keywords.unmapped),
            "",
        ]

    return lines


def _selection_section(plan: TailorPlan, labels: dict) -> list[str]:
    lines = ["## Seçilen maddeler ve gerekçeleri", ""]
    if not plan.experiences:
        lines += ["_Hiç madde seçilmemiş._", ""]
        return lines

    for experience in plan.experiences:
        if not experience.bullets:
            continue
        lines.append(f"### {experience.source_id}")
        lines.append("")
        for bullet in experience.bullets:
            lines.append(f"- {bullet.rewritten}")
            if bullet.rationale:
                lines.append(f"  - _{bullet.rationale}_")
        lines.append("")
    return lines


def _omitted_section(plan: TailorPlan, labels: dict) -> list[str]:
    if not plan.omitted_reasons:
        return []

    lines = [
        "## Elenen güçlü maddeler",
        "",
        "Havuza girdi ama seçilmedi. Biri gerçekten gerekliyse import'tan sonra elle ekle.",
        "",
    ]
    for omitted in plan.omitted_reasons:
        source = labels.get(omitted.source_id)
        text = f" — {source.text}" if source else ""
        lines.append(f"- **`{omitted.source_id}`**{text}")
        lines.append(f"  - _{omitted.reason}_")
    lines.append("")
    return lines


def _interview_section(coverage: list[RequirementCoverage]) -> list[str]:
    """Yalnızca dolaylı kapsanan zorunlu gereksinimler — mülakatta bunlar sorulur."""
    indirect = [row for row in coverage if row.kind == "must" and row.status == "indirect"]
    missing = [row for row in coverage if row.kind == "must" and row.status == "missing"]
    if not indirect and not missing:
        return []

    lines = [
        "## Mülakat hazırlığı",
        "",
        "Zorunlu gereksinimlerin CV'de doğrudan karşılığı yok. Sorulacaklar bunlar:",
        "",
    ]
    for row in indirect[:3]:
        covering = ", ".join(row.skill_ids or row.bullet_ids) or "—"
        lines.append(f"- **{row.text}** — dolaylı: `{covering}`. Somut bir örnek hazırla.")
    for row in missing[:3]:
        lines.append(f"- **{row.text}** — CV'de hiç yok. Nasıl kapatacağını düşün.")
    lines.append("")
    return lines


def _guardrail_section(verification: VerificationReport | None) -> list[str]:
    if verification is None:
        return []

    lines = ["## Guardrail", ""]
    if not verification.violations:
        lines += [
            f"✅ {verification.checked} madde denetlendi, **sıfır ihlal**. "
            "Modelin yazdığı her cümle süper setteki bir kaynağa dayanıyor.",
            "",
        ]
        return lines

    lines.append(
        f"{verification.checked} madde denetlendi: **{len(verification.errors)} ihlal**, "
        f"{len(verification.warnings)} uyarı"
        + (f", {verification.repaired} onarım." if verification.repaired else ".")
    )
    lines.append("")
    lines += ["| Kural | Nerede | Ne bulundu | Ne yapıldı |", "|---|---|---|---|"]
    for violation in [*verification.errors, *verification.warnings]:
        action = violation.repaired_with or "—"
        lines.append(
            f"| `{violation.rule}` | `{violation.where}` | "
            f"{_escape(violation.message)} | {_escape(action)} |"
        )
    lines.append("")
    return lines


def _footer(pool: CandidatePool, analysis: JobAnalysis) -> list[str]:
    """İzlenebilirlik: hangi model, ne kadar metin, hangi ağırlıklar."""
    saved = round(pool.budget.saved_ratio * 100)
    lines = [
        "---",
        "",
        "<details><summary>Çalıştırma ayrıntısı</summary>",
        "",
        f"- İlan analizi: `{analysis.model or 'bilinmiyor'}`, "
        f"{analysis.analyzed_at:%Y-%m-%d %H:%M}",
        f"- Ön filtre: {pool.budget.superset_items} maddelik süper setten "
        f"{pool.budget.candidate_items} aday; LLM'e giden metin **%{saved} daha az**",
        f"- Zorunlu etiketler: {', '.join(f'`{t}`' for t in pool.must_tags) or '—'}",
        f"- Tercih edilen etiketler: {', '.join(f'`{t}`' for t in pool.nice_tags) or '—'}",
    ]
    if pool.recall.missed:
        lines.append(
            "- Geri çağırma ağının yakaladıkları (model bildirmemişti): "
            + ", ".join(f"`{tag}`" for tag in pool.recall.missed)
        )
    lines += [
        "- Uyum skoru formülü: `docs/SCORING.md`, guardrail kuralları: `docs/GUARDRAILS.md`",
        "",
        "</details>",
    ]
    return lines


# --------------------------------------------------------------------------- #
# Kalıcılık
# --------------------------------------------------------------------------- #


def write_report(run_dir: Path, markdown: str) -> Path:
    path = run_dir / REPORT_FILENAME
    path.write_text(markdown, encoding="utf-8", newline="")
    return path
