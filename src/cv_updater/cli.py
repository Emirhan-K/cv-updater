"""`cv-updater` komut satırı arayüzü."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from cv_updater import __version__
from cv_updater.lint import Finding, LintReport, fix_ligatures, lint
from cv_updater.llm import (
    MissingAPIKeyError,
    StructuredOutputError,
    has_api_key,
    load_dotenv_once,
)
from cv_updater.models.candidates import CandidatePool
from cv_updater.models.job import JobAnalysis, Language
from cv_updater.models.resume import Resume
from cv_updater.models.tailor import TailorPlan, TailorResult
from cv_updater.paths import MASTER_JSON
from cv_updater.schema import rxresume_schema
from cv_updater.steps.analyze_job import (
    JOB_FILENAME,
    analyze,
    latest_analysis,
    read_analysis,
    write_analysis,
)
from cv_updater.steps.ats_check import (
    ATS_REPORT_FILENAME,
    RISKY_SCORE,
    AtsReport,
    NoTextLayerError,
    build_markdown,
    check_pdf,
)
from cv_updater.steps.intake import (
    CLEAN_FILENAME,
    IntakeError,
    new_run_dir,
    read_source,
    slug_hint,
    slugify,
    write_run_files,
)
from cv_updater.steps.normalize import MIN_MEANINGFUL_CHARS, normalize
from cv_updater.steps.render_json import (
    DEFAULT_FONT,
    LIGATURE_FREE_FONTS,
    has_ligatures,
    render,
    write_tailored,
)
from cv_updater.steps.report import STRONG_FIT, WEAK_FIT, fit_score
from cv_updater.steps.report import build as build_report
from cv_updater.steps.report import write_report as write_match_report
from cv_updater.steps.score import CANDIDATES_FILENAME, build_pool, find_run, read_pool, write_pool
from cv_updater.steps.tailor import (
    PLAN_FILENAME,
    latest_plan,
    plan_summary,
    read_plan,
    write_plan,
)
from cv_updater.steps.tailor import tailor as tailor_step
from cv_updater.steps.verify import (
    VIOLATIONS_FILENAME,
    VerificationReport,
    read_report,
    verify,
    write_report,
)

app = typer.Typer(
    name="cv-updater",
    help="İş ilanına göre CV uyarlayan araç. master.json süper setinden seçer, "
    "asla yeni içerik uydurmaz.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

_LEVEL_STYLE = {"error": "bold red", "warning": "yellow", "info": "dim cyan"}
_LEVEL_LABEL = {"error": "HATA", "warning": "UYARI", "info": "bilgi"}


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"cv-updater {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Sürümü yazdır ve çık.",
        ),
    ] = False,
) -> None:
    """Ortak seçenekler."""
    # `.env` her komut için yüklenir; `validate` yine anahtarsız çalışır, sadece
    # tanımlıysa ortama girer.
    load_dotenv_once()


def _load(path: Path) -> Resume:
    """master.json'ı okur; şema hatasında okunur bir mesajla çıkar."""
    if not path.exists():
        console.print(f"[bold red]Dosya yok:[/bold red] {path}")
        console.print(
            "[dim]data/master.example.json'ı kopyalayarak başlayabilirsin "
            "(master.json gitignore'da).[/dim]"
        )
        raise typer.Exit(code=2)

    try:
        return Resume.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        console.print(f"[bold red]Şema hatası:[/bold red] {path}")
        for error in exc.errors()[:20]:
            where = ".".join(str(part) for part in error["loc"])
            console.print(f"  [red]•[/red] {where}: {error['msg']}")
        raise typer.Exit(code=2) from exc


def _print_report(report: LintReport, path: Path) -> None:
    stats = Table(title=f"{path.name} — süper set", title_style="bold", show_header=False)
    stats.add_column(style="dim")
    stats.add_column(justify="right")
    for key, value in report.stats.items():
        stats.add_row(key, str(value))
    console.print(stats)

    if not report.findings:
        return

    console.print()
    for level in ("error", "warning", "info"):
        group: list[Finding] = [f for f in report.findings if f.level == level]
        for finding in group:
            style = _LEVEL_STYLE[level]
            # Bulgu metinleri köşeli parantez içerebiliyor ("[terfi tarihi]") ve rich
            # bunu biçim etiketi sanıp yutuyor — kaçırmadan yazdır.
            console.print(
                f"[{style}]{_LEVEL_LABEL[level]}[/{style}] "
                f"[dim]{escape(finding.rule)}[/dim] {escape(finding.where)}"
            )
            console.print(f"    {escape(finding.message)}")
            if finding.fix_hint:
                console.print(f"    [dim]→ {escape(finding.fix_hint)}[/dim]")


@app.command()
def validate(
    master: Annotated[
        Path | None,
        typer.Option(
            "--master",
            "-m",
            help="Doğrulanacak master.json (varsayılan: data/master.json).",
        ),
    ] = None,
    fix: Annotated[
        bool,
        typer.Option("--fix", help="Otomatik düzeltilebilenleri düzelt (ligatürler)."),
    ] = False,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Uyarıları da hata say."),
    ] = False,
    refresh_schema_flag: Annotated[
        bool,
        typer.Option(
            "--refresh-schema",
            help="rxresu.me şemasının depodaki anlık görüntüsünü yenile (ağa çıkar).",
        ),
    ] = False,
) -> None:
    """master.json'ı şemaya karşı doğrula ve içerik lint'i çalıştır.

    LLM kullanmaz, API anahtarı gerektirmez ve `--refresh-schema` verilmedikçe
    ağa çıkmaz.
    """
    if refresh_schema_flag:
        _refresh_schema()

    path = master or MASTER_JSON

    if fix and path.exists():
        raw = path.read_text(encoding="utf-8")
        fixed, count = fix_ligatures(raw)
        if count:
            path.write_text(fixed, encoding="utf-8")
            console.print(f"[green]✓[/green] {count} ligatür karakteri düzeltildi.")

    resume = _load(path)
    report = lint(resume)
    _print_report(report, path)

    error_count = len(report.errors)
    warning_count = len(report.warnings)
    console.print()

    if error_count == 0 and warning_count == 0:
        console.print("[bold green]✓ Sıfır hata, sıfır uyarı.[/bold green]")
        return

    summary = f"{error_count} hata, {warning_count} uyarı"
    if error_count or (strict and warning_count):
        console.print(f"[bold red]✗ {summary}[/bold red]")
        raise typer.Exit(code=1)
    console.print(f"[yellow]⚠ {summary}[/yellow] [dim](--strict ile hata sayılır)[/dim]")


def _refresh_schema() -> None:
    """Canlı şemayı çekip anlık görüntüyü günceller.

    Anlık görüntü elle yenileniyor (`TODO.md` teknik borç kalemi); bu bayrak o
    işi tek komuta indiriyor. Değişiklik çıkarsa **commit'lenmesi** gerekiyor.
    """
    from cv_updater.schema import SCHEMA_URL, refresh_schema

    try:
        path, changed = refresh_schema()
    except OSError as exc:
        console.print(f"[bold red]✗ Şema çekilemedi:[/bold red] {escape(str(exc))}")
        console.print(f"[dim]{SCHEMA_URL}[/dim]")
        raise typer.Exit(code=2) from exc

    if changed:
        console.print(f"[yellow]⟳ Şema güncellendi:[/yellow] {escape(str(path))}")
        console.print("[dim]Değişikliği commit'lemeyi unutma — testler bu dosyaya bakıyor.[/dim]")
    else:
        console.print("[green]✓[/green] Şema zaten güncel.")


@app.command()
def tailor(
    source: Annotated[
        str | None,
        typer.Argument(
            help="İlan dosyası, stdin için '-', ya da --paste ile editör.",
            metavar="[KAYNAK]",
        ),
    ] = None,
    paste: Annotated[
        bool,
        typer.Option("--paste", help="$EDITOR aç, kaydedince devam et."),
    ] = False,
    slug: Annotated[
        str | None,
        typer.Option("--slug", help="Çıktı dizininin adı (zaman damgası yine eklenir)."),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Kısa ilan uyarısında soru sorma."),
    ] = False,
    no_llm: Annotated[
        bool,
        typer.Option(
            "--no-llm",
            help="LLM'e gitme; en son üretilen job.json'ı yeniden kullan.",
        ),
    ] = False,
    master: Annotated[
        Path | None,
        typer.Option("--master", "-m", help="Süper set (varsayılan: data/master.json)."),
    ] = None,
    lang: Annotated[
        str | None,
        typer.Option("--lang", help="CV dilini zorla: tr ya da en (varsayılan: ilanın dili)."),
    ] = None,
    repair: Annotated[
        bool,
        typer.Option(
            "--repair",
            help="Guardrail ihlalinde durma: ihlalli maddeyi kaynak metnine döndür ve devam et.",
        ),
    ] = False,
    font: Annotated[
        str,
        typer.Option(
            "--font",
            help=f"CV fontu. Varsayılan {DEFAULT_FONT} — ligatürsüz olduğu ölçülmüş.",
        ),
    ] = DEFAULT_FONT,
) -> None:
    """İlana göre tailored.json + match_report.md üret (LLM adımı).

    Şu an issue 04-07 kapsamı çalışıyor: ilan okunur, normalize edilir,
    `JobPosting` olarak yapılandırılır, deterministik ön filtre `candidates.json`'ı
    üretir, ve uyarlama adımı dürüstlük guardrail'i altında `plan.json` +
    `violations.json` yazar. `tailored.json` (08) ve rapor (09) eklenecek.

    Guardrail varsayılan olarak **katıdır**: bir ihlal bulunursa adım durur ve
    hiçbir şey üretilmez. `--repair` ihlalli metni kaynağına döndürüp sürdürür.
    """
    try:
        src = read_source(source, paste=paste)
    except IntakeError as exc:
        console.print(f"[bold red]✗[/bold red] {escape(str(exc))}")
        raise typer.Exit(code=2) from exc

    result = normalize(src.text)

    if result.is_short:
        console.print(
            f"[yellow]⚠ İlan çok kısa[/yellow] ({len(result.text)} karakter, "
            f"eşik {MIN_MEANINGFUL_CHARS}). Yanlış pano ya da eksik kopyalama olabilir."
        )
        if not yes and not typer.confirm("Yine de devam edilsin mi?", default=False):
            console.print("[dim]İptal edildi.[/dim]")
            raise typer.Exit(code=1)

    hint = slugify(slug) if slug else slug_hint(src, result.text)
    run_dir = new_run_dir(hint)
    raw_path, clean_path = write_run_files(run_dir, src.text, result.text)

    table = Table(title=f"ilan alındı — {src.kind}", title_style="bold", show_header=False)
    table.add_column(style="dim")
    table.add_column(justify="right")
    for key, value in result.stats.items():
        table.add_row(key, str(value))
    console.print(table)

    console.print(f"[green]✓[/green] {escape(str(raw_path))}")
    console.print(f"[green]✓[/green] {escape(str(clean_path))}")

    analysis = _analyze_step(result.text, source_kind=src.kind, no_llm=no_llm)
    job_path = write_analysis(run_dir, analysis)
    console.print(f"[green]✓[/green] {escape(str(job_path))}")

    _print_analysis(analysis)

    resume = _load(master or MASTER_JSON)
    pool = _score_step(run_dir, resume, analysis, result.text, lang=_parse_lang(lang))
    _print_pool(pool)

    outcome = _tailor_step(run_dir, resume, analysis, pool, repair=repair, no_llm=no_llm)
    if outcome is None:
        return
    result, verification = outcome

    _render_step(run_dir, resume, result.plan, pool, lang=_parse_lang(lang), font=font)
    _report_step(run_dir, analysis, pool, result.plan, verification)


def _report_step(
    run_dir: Path,
    analysis: JobAnalysis,
    pool: CandidatePool,
    plan: TailorPlan,
    verification: VerificationReport | None,
) -> Path:
    """Uyum raporunu üretir ve başlığı terminale basar."""
    markdown = build_report(analysis, pool, plan, verification)
    path = write_match_report(run_dir, markdown)
    console.print(f"[green]✓[/green] {escape(str(path))}")

    score = fit_score(pool.coverage)
    style = "bold green" if score.percent >= STRONG_FIT else "yellow"
    if score.weak:
        style = "bold red"
    console.print(
        f"[{style}]Uyum: %{score.percent}[/{style}] · "
        f"zorunlu {score.must_covered}/{score.must_total} · "
        f"tercih {score.nice_covered}/{score.nice_total} · "
        f"[bold]{escape(score.verdict)}[/bold]"
    )
    if score.weak:
        console.print(
            f"[dim]Uyum %{WEAK_FIT}'in altında — bu ilana CV uyarlamayı zorlama. "
            "Gerekçe raporda.[/dim]"
        )
    return path


def _render_step(
    run_dir: Path,
    resume: Resume,
    plan: TailorPlan,
    pool: CandidatePool,
    *,
    lang: Language | None,
    font: str = DEFAULT_FONT,
) -> Path:
    """Planı `tailored.json`'a derler ve canlı şemaya karşı doğrular."""
    if font not in LIGATURE_FREE_FONTS:
        # Ligatürler PDF'in metin katmanında oluşuyor ve ATS aramasını bozuyor;
        # CSS ile kapatılamıyor (react-pdf tanımıyor), tek koruma fontun kendisi.
        console.print(
            f"[yellow]⚠ {escape(font)} ligatürsüz olduğu ölçülmüş fontlar arasında değil.[/yellow]"
        )
        console.print(
            f"[dim]Ölçülenler: {', '.join(sorted(LIGATURE_FREE_FONTS))}. "
            "PDF'te 'fine-tuning' → 'fne-tuning' olabilir.[/dim]"
        )

    doc = render(resume, plan, pool, language=lang, run_id=run_dir.name, font=font)
    path = write_tailored(run_dir, doc)
    payload = path.read_text(encoding="utf-8")

    console.print(f"[green]✓[/green] {escape(str(path))}")

    errors = sorted(
        Draft202012Validator(rxresume_schema()).iter_errors(json.loads(payload)), key=str
    )
    if errors:
        console.print(f"[bold red]✗ Şema hatası ({len(errors)}):[/bold red]")
        for error in errors[:10]:
            where = ".".join(str(part) for part in error.path) or "<root>"
            console.print(f"  [red]•[/red] {escape(where)}: {escape(error.message)}")
        raise typer.Exit(code=1)

    if ligatures := has_ligatures(payload):
        # Buraya düşmek bir hata: `to_json` ligatürleri zaten temizliyor.
        console.print(f"[bold red]✗ Ligatür karakteri kaldı:[/bold red] {ligatures}")
        raise typer.Exit(code=1)

    console.print(
        "[bold green]✓ Reactive Resume şemasına uyuyor.[/bold green] "
        "[dim]rxresu.me → Import → JSON ile içe aktar[/dim]"
    )
    return path


def _tailor_step(
    run_dir: Path,
    resume: Resume,
    analysis: JobAnalysis,
    pool: CandidatePool,
    *,
    repair: bool,
    no_llm: bool = False,
) -> tuple[TailorResult, VerificationReport] | None:
    """Uyarlama + guardrail. İhlal varsa `--repair` yoksa burada durur."""
    result = _plan_source(analysis, pool, no_llm=no_llm)
    if result is None:
        return None

    plan, report = verify(result.plan, pool, resume, repair=repair)
    result.plan = plan
    result.repaired = repair and report.repaired > 0

    plan_path = write_plan(run_dir, result)
    report_path = write_report(run_dir, report)
    console.print(f"[green]✓[/green] {escape(str(plan_path))}")
    console.print(f"[green]✓[/green] {escape(str(report_path))}")

    _print_plan(result, report)

    if report.errors and not repair:
        console.print(
            "\n[bold red]✗ Guardrail durdurdu.[/bold red] Uydurulmuş içerik CV'ye giremez.\n"
            "[dim]--repair ile ihlalli maddeler kaynak metnine döndürülüp akış sürdürülür.[/dim]"
        )
        raise typer.Exit(code=1)

    return result, report


def _plan_source(
    analysis: JobAnalysis, pool: CandidatePool, *, no_llm: bool
) -> TailorResult | None:
    """Planı ya modelden alır ya da `--no-llm` ile öncekini yeniden kullanır."""
    if no_llm:
        previous = latest_plan()
        if previous is None:
            console.print(
                "[dim]--no-llm: yeniden kullanılacak bir plan.json yok, "
                "uyarlama adımı atlandı.[/dim]"
            )
            return None
        result = read_plan(previous)
        console.print(
            f"[dim]--no-llm: {escape(previous.parent.name)} çalıştırmasındaki plan "
            "yeniden kullanılıyor.[/dim]"
        )
        return result

    if not has_api_key():
        console.print(f"[bold red]✗[/bold red] {escape(str(MissingAPIKeyError()))}")
        raise typer.Exit(code=2)

    with console.status("[dim]CV uyarlanıyor…[/dim]"):
        try:
            return tailor_step(analysis, pool, client=None)
        except StructuredOutputError as exc:
            console.print(f"[bold red]✗ Uyarlama başarısız:[/bold red] {escape(str(exc))}")
            raise typer.Exit(code=1) from exc


def _print_plan(result: TailorResult, report: VerificationReport) -> None:
    """Planın ve guardrail sonucunun özeti."""
    table = Table(title="uyarlama", title_style="bold", show_header=False)
    table.add_column(style="dim")
    table.add_column(justify="right")
    for key, value in plan_summary(result).items():
        table.add_row(key, str(value))
    table.add_row("denetlenen", str(report.checked))
    table.add_row("ihlal", f"{len(report.errors)} hata, {len(report.warnings)} uyarı")
    if result.repaired:
        table.add_row("onarılan", str(report.repaired))
    console.print(table)

    for violation in [*report.errors, *report.warnings]:
        style = "bold red" if violation.level == "error" else "yellow"
        label = "İHLAL" if violation.level == "error" else "uyarı"
        console.print(
            f"[{style}]{label}[/{style}] [dim]{escape(violation.rule)}[/dim] "
            f"{escape(violation.where)}"
        )
        console.print(f"    {escape(violation.message)}")
        if violation.repaired_with:
            console.print(f"    [green]→ {escape(violation.repaired_with)}[/green]")

    if not report.violations:
        console.print("[bold green]✓ Guardrail temiz: sıfır ihlal.[/bold green]")


def _parse_lang(value: str | None) -> Language | None:
    """`--lang` değerini doğrular. Şema iki dil tanıyor; üçüncüsü sessizce geçmemeli."""
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized not in ("tr", "en"):
        console.print(f"[bold red]✗[/bold red] --lang yalnızca 'tr' ya da 'en' olabilir: {value!r}")
        raise typer.Exit(code=2)
    return normalized  # type: ignore[return-value]


def _score_step(
    run_dir: Path,
    resume: Resume,
    analysis: JobAnalysis,
    posting_text: str,
    *,
    lang: Language | None,
) -> CandidatePool:
    """Deterministik ön filtreyi çalıştırır ve `candidates.json`'ı yazar."""
    pool = build_pool(
        resume,
        analysis,
        posting_text,
        slug=run_dir.name,
        language=lang,
    )
    path = write_pool(run_dir, pool)
    console.print(f"[green]✓[/green] {escape(str(path))}")
    return pool


def _analyze_step(clean_text: str, *, source_kind: str, no_llm: bool) -> JobAnalysis:
    """İlanı yapılandırır — ya LLM'e sorarak ya da öncekini yeniden kullanarak."""
    if no_llm:
        previous = latest_analysis()
        if previous is None:
            console.print(
                "[bold red]✗[/bold red] --no-llm için yeniden kullanılacak bir job.json yok. "
                "Önce bir kez LLM ile çalıştır."
            )
            raise typer.Exit(code=2)

        analysis = read_analysis(previous)
        analysis.reused_from = previous.parent.name
        console.print(
            f"[dim]--no-llm: {escape(previous.parent.name)} çalıştırmasındaki analiz "
            "yeniden kullanılıyor. Aynı ilan olduğundan emin ol.[/dim]"
        )
        return analysis

    if not has_api_key():
        console.print(f"[bold red]✗[/bold red] {escape(str(MissingAPIKeyError()))}")
        raise typer.Exit(code=2)

    with console.status("[dim]ilan analiz ediliyor…[/dim]"):
        try:
            return analyze(clean_text, source_kind=source_kind)
        except StructuredOutputError as exc:
            console.print(f"[bold red]✗ Analiz başarısız:[/bold red] {escape(str(exc))}")
            raise typer.Exit(code=1) from exc


def _print_analysis(analysis: JobAnalysis) -> None:
    """Analizin özetini yazdırır — doğru okunduğu bir bakışta görülsün."""
    posting = analysis.posting

    table = Table(title="ilan analizi", title_style="bold", show_header=False)
    table.add_column(style="dim")
    table.add_column()
    table.add_row("şirket", escape(posting.company or "—"))
    table.add_row("pozisyon", escape(posting.title))
    table.add_row("dil", posting.language)
    table.add_row("kıdem", posting.seniority)
    table.add_row("konum", escape(posting.location or "—"))
    table.add_row("çalışma biçimi", posting.work_mode)
    table.add_row("alan", escape(posting.domain or "—"))
    must = sum(1 for requirement in posting.requirements if requirement.kind == "must")
    table.add_row("gereksinim", f"{must} zorunlu, {len(posting.requirements) - must} tercih")
    table.add_row("etiket", str(len(analysis.keywords.tags)))
    console.print(table)

    if analysis.keywords.tags:
        console.print(f"[dim]etiketler:[/dim] {escape(', '.join(analysis.keywords.tags))}")
    if analysis.keywords.unmapped:
        console.print(
            f"[yellow]sözlükte yok:[/yellow] {escape(', '.join(analysis.keywords.unmapped))}"
        )
        console.print("[dim]→ karşılığı varsa data/tags.yaml'a alias olarak ekle.[/dim]")
    if analysis.keywords.dropped:
        console.print(
            f"[bold red]ilanda bulunamadı, atıldı:[/bold red] "
            f"{escape(', '.join(analysis.keywords.dropped))}"
        )


@app.command()
def score(
    slug: Annotated[
        str,
        typer.Argument(help="Çalıştırma dizini: out/ altındaki ad ya da ayırt edici bir parçası."),
    ],
    master: Annotated[
        Path | None,
        typer.Option("--master", "-m", help="Süper set (varsayılan: data/master.json)."),
    ] = None,
    lang: Annotated[
        str | None,
        typer.Option("--lang", help="CV dilini zorla: tr ya da en (varsayılan: ilanın dili)."),
    ] = None,
) -> None:
    """Var olan bir çalıştırma için ön filtreyi (yeniden) çalıştır.

    LLM kullanmaz, ağa çıkmaz: puanlamayı gözle görmek ve ağırlıkları
    kurcalamak için `tailor`'ı baştan çalıştırmaya gerek yok.
    """
    run_dir = find_run(slug)
    if run_dir is None:
        console.print(f"[bold red]✗[/bold red] Çalıştırma bulunamadı: {escape(slug)}")
        console.print("[dim]out/ altındaki dizin adının bir parçasını ver.[/dim]")
        raise typer.Exit(code=2)

    job_path = run_dir / JOB_FILENAME
    if not job_path.exists():
        console.print(
            f"[bold red]✗[/bold red] {escape(str(job_path))} yok — önce `tailor` çalıştır."
        )
        raise typer.Exit(code=2)

    analysis = read_analysis(job_path)
    clean_path = run_dir / CLEAN_FILENAME
    posting_text = clean_path.read_text(encoding="utf-8") if clean_path.exists() else ""
    if not posting_text:
        console.print(
            f"[yellow]⚠[/yellow] {CLEAN_FILENAME} yok; geri çağırma ağı bu çalıştırmada çalışmaz."
        )

    console.print(f"[dim]çalıştırma:[/dim] {escape(run_dir.name)}")
    resume = _load(master or MASTER_JSON)
    pool = _score_step(run_dir, resume, analysis, posting_text, lang=_parse_lang(lang))
    _print_pool(pool, verbose=True)


def _print_pool(pool: CandidatePool, *, verbose: bool = False) -> None:
    """Ön filtrenin sonucunu gözle denetlenebilir biçimde yazdırır."""
    summary = Table(title="ön filtre", title_style="bold", show_header=False)
    summary.add_column(style="dim")
    summary.add_column(justify="right")
    summary.add_row("dil", pool.language)
    summary.add_row("zorunlu etiket", str(len(pool.must_tags)))
    summary.add_row("tercih edilen etiket", str(len(pool.nice_tags)))
    summary.add_row("seçilen madde", str(pool.budget.candidate_items))
    summary.add_row("süper set", str(pool.budget.superset_items))
    summary.add_row("kısılan metin", f"%{pool.budget.saved_ratio * 100:.0f}")
    console.print(summary)

    for entry in (*pool.experiences, *pool.projects):
        selected = entry.selected_bullets
        if not selected:
            continue
        table = Table(
            title=f"{entry.title} — {entry.period}",
            title_style="bold",
            title_justify="left",
        )
        table.add_column("skor", justify="right", style="cyan", width=6)
        table.add_column("madde")
        if verbose:
            table.add_column("eşleşen", style="dim")
        for bullet in selected:
            row = [f"{bullet.breakdown.total:g}", escape(bullet.text[:88])]
            if verbose:
                row.append(escape(", ".join(bullet.matched_must + bullet.matched_nice)))
            table.add_row(*row)
        console.print(table)

    skills = pool.selected_skills
    if skills:
        console.print(
            f"[dim]yetenekler ({len(skills)}):[/dim] "
            f"{escape(', '.join(skill.text for skill in skills))}"
        )

    covered = sum(1 for row in pool.coverage if row.status == "covered")
    indirect = sum(1 for row in pool.coverage if row.status == "indirect")
    missing = [row for row in pool.coverage if row.status == "missing"]
    console.print(
        f"[dim]gereksinim:[/dim] {covered} kapsanıyor, {indirect} dolaylı, {len(missing)} eksik"
    )

    if pool.recall.missed:
        console.print(
            f"[yellow]model kaçırmış:[/yellow] {escape(', '.join(pool.recall.missed))} "
            "[dim](geri çağırma ağı ilan metninden buldu)[/dim]"
        )
    if pool.missing_tags:
        console.print(
            f"[yellow]süper sette yok:[/yellow] {escape(', '.join(pool.missing_tags))} "
            "[dim](gerçekten biliyorsan master.json'a ekle)[/dim]"
        )


@app.command()
def report(
    slug: Annotated[
        str,
        typer.Argument(help="Çalıştırma dizini: out/ altındaki ad ya da ayırt edici bir parçası."),
    ],
) -> None:
    """Var olan bir çalıştırma için uyum raporunu yeniden üret.

    LLM'e gitmez: `job.json` + `candidates.json` + `plan.json` yeter. Rapor
    metnini elle düzenledikten sonra geri almak ya da `master.json`'ı
    güncelledikten sonra skoru tazelemek için.
    """
    run_dir = find_run(slug)
    if run_dir is None:
        console.print(f"[bold red]✗[/bold red] Çalıştırma bulunamadı: {escape(slug)}")
        raise typer.Exit(code=2)

    missing = [
        name
        for name in (JOB_FILENAME, CANDIDATES_FILENAME, PLAN_FILENAME)
        if not (run_dir / name).exists()
    ]
    if missing:
        console.print(f"[bold red]✗[/bold red] Eksik dosya: {', '.join(missing)}")
        console.print("[dim]Önce `tailor` çalıştır.[/dim]")
        raise typer.Exit(code=2)

    console.print(f"[dim]çalıştırma:[/dim] {escape(run_dir.name)}")
    analysis = read_analysis(run_dir / JOB_FILENAME)
    pool = read_pool(run_dir / CANDIDATES_FILENAME)
    plan = read_plan(run_dir / PLAN_FILENAME).plan

    violations_path = run_dir / VIOLATIONS_FILENAME
    verification = read_report(violations_path) if violations_path.exists() else None

    _report_step(run_dir, analysis, pool, plan, verification)


@app.command()
def check(
    pdf: Annotated[Path, typer.Argument(help="ATS kontrolü yapılacak PDF.")],
    job: Annotated[
        Path | None,
        typer.Option("--job", help="İlan analizi (job.json) — anahtar kelime kapsaması için."),
    ] = None,
    slug: Annotated[
        str | None,
        typer.Option(
            "--slug", help="Çalıştırma dizini; job.json oradan alınır, rapor oraya yazılır."
        ),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(
            "--out", help=f"Raporun yazılacağı dosya (varsayılan: {ATS_REPORT_FILENAME})."
        ),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Uyarıları da hata say (çıkış kodu)."),
    ] = False,
) -> None:
    """Üretilen PDF'ten metni geri çıkarıp ATS uyumunu puanla.

    LLM kullanmaz, ağa çıkmaz. `--job` ya da `--slug` verilirse anahtar kelime
    kapsaması ilana özgü ölçülür; verilmezse genel kontroller çalışır.

    Kritik bir bulguda çıkış kodu 1 olur — kontrol pre-commit ya da CI'ya
    bağlanabilsin diye.
    """
    run_dir, analysis = _check_context(job, slug)

    try:
        report = check_pdf(pdf, analysis)
    except FileNotFoundError:
        console.print(f"[bold red]✗[/bold red] PDF yok: {escape(str(pdf))}")
        raise typer.Exit(code=2) from None
    except NoTextLayerError as exc:
        console.print(f"[bold red]✗ Metin katmanı okunamadı.[/bold red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    _print_ats(report)

    target = out or (
        run_dir / ATS_REPORT_FILENAME if run_dir else _ats_run_dir(pdf) / ATS_REPORT_FILENAME
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_markdown(report), encoding="utf-8", newline="")
    console.print(f"[green]✓[/green] {escape(str(target))}")

    if not report.ok(strict=strict):
        raise typer.Exit(code=1)


def _check_context(job: Path | None, slug: str | None) -> tuple[Path | None, JobAnalysis | None]:
    """`--job` / `--slug`'dan ilan analizini ve rapor dizinini çözer."""
    run_dir: Path | None = None

    if slug is not None:
        run_dir = find_run(slug)
        if run_dir is None:
            console.print(f"[bold red]✗[/bold red] Çalıştırma bulunamadı: {escape(slug)}")
            raise typer.Exit(code=2)
        if job is None:
            job = run_dir / JOB_FILENAME

    if job is None:
        console.print(
            "[dim]İlan verilmedi; anahtar kelime kapsaması atlanacak "
            "(--job ya da --slug ile ilana özgü ölçülür).[/dim]"
        )
        return run_dir, None

    if not job.exists():
        console.print(f"[bold red]✗[/bold red] İlan analizi yok: {escape(str(job))}")
        raise typer.Exit(code=2)

    if run_dir is None:
        run_dir = job.parent
    return run_dir, read_analysis(job)


def _ats_run_dir(pdf: Path) -> Path:
    """İlansız çalıştırmada raporun gideceği yer: `out/ats-<pdf>-<damga>/`.

    Rapor kullanıcının PDF'inin yanına değil, aracın kendi çıktı dizinine
    yazılıyor — `out/` zaten gitignore'da ve boru hattının geri kalanıyla
    tutarlı.
    """
    return new_run_dir(f"ats-{slugify(pdf.stem) or 'pdf'}")


_ATS_LEVEL_STYLE: dict[str, str] = {
    "critical": "bold red",
    "warning": "yellow",
    "info": "dim cyan",
}
_ATS_LEVEL_LABEL: dict[str, str] = {
    "critical": "KRİTİK",
    "warning": "UYARI",
    "info": "bilgi",
}


def _print_ats(report: AtsReport) -> None:
    """Puan tablosunu ve bulguları terminale basar."""
    table = Table(title=f"ATS öz-kontrolü — {report.pdf.name}", title_style="bold")
    table.add_column("kontrol")
    table.add_column("sonuç", style="dim")
    table.add_column("puan", justify="right")
    for check_result in report.checks:
        if check_result.skipped:
            table.add_row(check_result.label, escape(check_result.detail), "—")
            continue
        table.add_row(
            check_result.label,
            escape(check_result.detail),
            f"{check_result.points:.0f}/{check_result.weight:.0f}",
        )
    console.print(table)

    engine = f"metin motoru: {report.engine}"
    if report.fallback_reason:
        engine += f" [dim](pypdf yetmedi: {escape(report.fallback_reason)})[/dim]"
    console.print(f"[dim]{engine}[/dim]")

    for finding in report.findings:
        style = _ATS_LEVEL_STYLE[finding.level]
        console.print(
            f"[{style}]{_ATS_LEVEL_LABEL[finding.level]}[/{style}] "
            f"[dim]{escape(finding.rule)}[/dim] {escape(finding.message)}"
        )
        if finding.evidence:
            console.print(f"    [dim]{escape(', '.join(finding.evidence))}[/dim]")
        if finding.fix_hint:
            console.print(f"    [dim]→ {escape(finding.fix_hint)}[/dim]")

    style = "bold green"
    if report.critical:
        style = "bold red"
    elif report.score < RISKY_SCORE:
        style = "yellow"
    console.print(
        f"\n[{style}]Puan: {report.score}/100[/{style}] · [bold]{escape(report.verdict)}[/bold]"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
