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
from cv_updater.models.resume import Resume
from cv_updater.paths import MASTER_JSON
from cv_updater.steps.intake import (
    IntakeError,
    new_run_dir,
    read_source,
    slug_hint,
    slugify,
    write_run_files,
)
from cv_updater.steps.normalize import MIN_MEANINGFUL_CHARS, normalize

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
) -> None:
    """İlana göre tailored.json + match_report.md üret (LLM adımı).

    Şu an issue 04 kapsamı çalışıyor: ilan okunur, normalize edilir ve
    `out/<slug>/` altına yazılır. Analiz (05) ve sonrası eklenecek.
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
    console.print("\n[yellow]Analiz adımı henüz uygulanmadı (issue 05).[/yellow]")


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
