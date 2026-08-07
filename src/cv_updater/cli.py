"""`cv-updater` komut satırı arayüzü."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
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
from cv_updater.paths import MASTER_JSON
from cv_updater.steps.analyze_job import (
    JOB_FILENAME,
    analyze,
    latest_analysis,
    read_analysis,
    write_analysis,
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
from cv_updater.steps.score import build_pool, find_run, write_pool

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
) -> None:
    """master.json'ı şemaya karşı doğrula ve içerik lint'i çalıştır.

    LLM kullanmaz, ağa çıkmaz, API anahtarı gerektirmez.
    """
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
) -> None:
    """İlana göre tailored.json + match_report.md üret (LLM adımı).

    Şu an issue 04-06 kapsamı çalışıyor: ilan okunur, normalize edilir,
    `JobPosting` olarak yapılandırılır, ardından deterministik ön filtre
    `candidates.json`'ı üretir. Uyarlama (07-09) eklenecek.
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

    pool = _score_step(run_dir, analysis, result.text, master=master, lang=_parse_lang(lang))
    _print_pool(pool)

    console.print("\n[yellow]Uyarlama adımı henüz uygulanmadı (issue 07-09).[/yellow]")


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
    analysis: JobAnalysis,
    posting_text: str,
    *,
    master: Path | None,
    lang: Language | None,
) -> CandidatePool:
    """Deterministik ön filtreyi çalıştırır ve `candidates.json`'ı yazar."""
    resume = _load(master or MASTER_JSON)
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
    pool = _score_step(run_dir, analysis, posting_text, master=master, lang=_parse_lang(lang))
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
def check(
    pdf: Annotated[
        Path | None, typer.Argument(help="ATS kontrolü yapılacak PDF (issue 10).")
    ] = None,
) -> None:
    """Üretilen PDF'ten metni geri çıkarıp ATS uyumunu puanla."""
    console.print("[yellow]check henüz uygulanmadı (issue 10).[/yellow]")
    if pdf is not None:
        console.print(f"PDF: {pdf}")
    raise typer.Exit(code=1)


if __name__ == "__main__":  # pragma: no cover
    app()
