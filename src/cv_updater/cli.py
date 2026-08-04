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
    posting: Annotated[Path | None, typer.Argument(help="İlan metni dosyası (issue 04).")] = None,
) -> None:
    """İlana göre tailored.json + match_report.md üret (LLM adımı)."""
    console.print("[yellow]tailor henüz uygulanmadı (issue 04-09).[/yellow]")
    if posting is not None:
        console.print(f"İlan: {posting}")
    raise typer.Exit(code=1)


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
