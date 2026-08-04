"""CLI duman testleri — ağ ve API anahtarı olmadan çalışmalı."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from cv_updater import __version__
from cv_updater.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Testler anahtarsız bir ortamı taklit eder (issue 01 kabul kriteri)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_help_lists_all_three_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("validate", "tailor", "check"):
        assert command in result.output


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


@pytest.mark.parametrize("command", ["validate", "tailor", "check"])
def test_subcommand_help_works_without_api_key(command: str) -> None:
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code != 0
    assert "validate" in result.output
