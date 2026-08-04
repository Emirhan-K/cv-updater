"""`cv-updater` komut satırı arayüzü."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from cv_updater import __version__

app = typer.Typer(
    name="cv-updater",
    help="İş ilanına göre CV uyarlayan araç. master.json süper setinden seçer, "
    "asla yeni içerik uydurmaz.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


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
) -> None:
    """master.json'ı şemaya karşı doğrula ve içerik lint'i çalıştır.

    LLM kullanmaz, API anahtarı gerektirmez.
    """
    console.print("[yellow]validate henüz uygulanmadı (issue 03).[/yellow]")
    if master is not None:
        console.print(f"Hedef: {master}")
    raise typer.Exit(code=1)


@app.command()
def tailor(
    posting: Annotated[
        Path | None, typer.Argument(help="İlan metni dosyası (issue 04).")
    ] = None,
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
