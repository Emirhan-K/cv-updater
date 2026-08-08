"""Issue 12 — Başvuru günlüğü. Deterministik: LLM yok, ağ yok, sahte kayıtlar."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cv_updater.cli import app
from cv_updater.steps.applications import (
    FOLLOW_UP_DAYS,
    META_FILENAME,
    Application,
    compute_stats,
    filter_applications,
    find,
    find_all,
    find_by_run,
    load_all,
    make_id,
    parse_since,
    read,
    record,
    set_status,
    write,
)

runner = CliRunner()

NOW = datetime(2026, 8, 8, 12, 0)


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """`tailor`'ın bıraktığı gibi bir çalıştırma dizini."""
    target = tmp_path / "out" / "novatek-tr-20260808-090000"
    target.mkdir(parents=True)
    (target / "job_raw.txt").write_text("ham ilan", encoding="utf-8")
    (target / "job.json").write_text("{}", encoding="utf-8")
    (target / "tailored.json").write_text("{}", encoding="utf-8")
    (target / "match_report.md").write_text("# rapor", encoding="utf-8")
    return target


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    return tmp_path / "applications"


def _fake(
    archive: Path,
    identifier: str,
    *,
    status: str = "prepared",
    score: int = 70,
    created: datetime | None = None,
    applied: datetime | None = None,
) -> Application:
    directory = archive / identifier
    directory.mkdir(parents=True)
    application = Application(
        id=identifier,
        company=identifier.split("-", 1)[-1],
        position="AI Mühendisi",
        fit_score=score,
        status=status,  # type: ignore[arg-type]
        created_at=created or NOW,
        applied_at=applied,
    )
    write(directory, application)
    return application


# --------------------------------------------------------------------------- #
# Kimlik
# --------------------------------------------------------------------------- #


def test_id_is_date_prefixed_and_readable() -> None:
    assert make_id("Novatek", "AI Mühendisi", date(2026, 8, 8)) == "20260808-novatek-ai-muhendisi"


def test_id_sanitises_turkish_and_slashes() -> None:
    """Windows'ta `/` alt dizin açar, Türkçe harf kabuğa göre değişir — ikisi de inmeli."""
    identifier = make_id("Şirket A/B", "Yazılım Mühendisi (AI/ML)", date(2026, 8, 8))

    assert "/" not in identifier
    assert identifier.isascii()
    assert identifier == "20260808-sirket-a-b-yazilim-muhendisi-ai-ml"


def test_id_survives_an_empty_company() -> None:
    assert make_id("", "", date(2026, 8, 8)) == "20260808"


def test_id_stays_short_enough_for_windows() -> None:
    identifier = make_id("Ç" * 200, "M" * 200, date(2026, 8, 8))
    assert len(identifier) <= 69


# --------------------------------------------------------------------------- #
# Kayıt
# --------------------------------------------------------------------------- #


def test_record_archives_the_run(run_dir: Path, archive: Path) -> None:
    application = record(
        run_dir,
        company="Novatek",
        position="AI Mühendisi",
        fit_score=82,
        source="LinkedIn",
        url="https://example.com/ilan",
        root=archive,
        when=date(2026, 8, 8),
    )

    directory = archive / application.id
    assert (directory / "job_raw.txt").read_text(encoding="utf-8") == "ham ilan"
    assert (directory / "match_report.md").exists()
    assert (directory / META_FILENAME).exists()

    stored = read(directory / META_FILENAME)
    assert stored.company == "Novatek"
    assert stored.fit_score == 82
    assert stored.source == "LinkedIn"
    assert stored.run_slug == run_dir.name
    assert stored.status == "prepared"
    assert stored.history


def test_record_skips_files_that_do_not_exist(run_dir: Path, archive: Path) -> None:
    """`cv.pdf` yalnızca `render` çalıştıysa var — yokluğu hata değil."""
    application = record(run_dir, company="Novatek", position="AI", fit_score=70, root=archive)
    assert not (archive / application.id / "cv.pdf").exists()


def test_second_application_same_day_does_not_overwrite(run_dir: Path, archive: Path) -> None:
    first = record(
        run_dir, company="Novatek", position="AI", fit_score=70, root=archive, when=date(2026, 8, 8)
    )
    second = record(
        run_dir, company="Novatek", position="AI", fit_score=71, root=archive, when=date(2026, 8, 8)
    )

    assert first.id != second.id
    assert read(archive / first.id / META_FILENAME).fit_score == 70


def test_attach_adds_a_late_file(run_dir: Path, archive: Path) -> None:
    from cv_updater.steps.applications import attach

    application = record(run_dir, company="Novatek", position="AI", fit_score=70, root=archive)
    pdf = run_dir / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4 sahte")

    assert attach(archive / application.id, pdf) is not None
    assert (archive / application.id / "cv.pdf").read_bytes() == b"%PDF-1.4 sahte"


def test_attach_refuses_unlisted_files(run_dir: Path, archive: Path) -> None:
    from cv_updater.steps.applications import attach

    application = record(run_dir, company="Novatek", position="AI", fit_score=70, root=archive)
    stray = run_dir / "plan.json"
    stray.write_text("{}", encoding="utf-8")

    assert attach(archive / application.id, stray) is None


def test_find_by_run_links_archive_to_run(run_dir: Path, archive: Path) -> None:
    record(run_dir, company="Novatek", position="AI", fit_score=70, root=archive)
    assert find_by_run(run_dir.name, root=archive) is not None
    assert find_by_run("baska-calistirma", root=archive) is None


# --------------------------------------------------------------------------- #
# Durum
# --------------------------------------------------------------------------- #


def test_status_change_stamps_applied_at(archive: Path) -> None:
    _fake(archive, "20260808-novatek")
    application = set_status(archive / "20260808-novatek", "applied", now=NOW)

    assert application.status == "applied"
    assert application.applied_at == NOW
    assert "başvuruldu" in application.history[-1]


def test_applied_at_is_not_rewritten(archive: Path) -> None:
    """İlk gönderim anı kayıt boyunca sabit — takip hatırlatıcısı ona bakıyor."""
    _fake(archive, "20260808-novatek")
    directory = archive / "20260808-novatek"
    set_status(directory, "applied", now=NOW)
    later = set_status(directory, "applied", now=NOW + timedelta(days=3))

    assert later.applied_at == NOW


def test_history_accumulates(archive: Path) -> None:
    _fake(archive, "20260808-novatek")
    directory = archive / "20260808-novatek"
    set_status(directory, "applied", now=NOW)
    application = set_status(directory, "interview", note="Salı 14:00", now=NOW)

    assert len(application.history) == 2
    assert "Salı 14:00" in application.history[-1]


def test_follow_up_flag(archive: Path) -> None:
    waiting = _fake(
        archive, "20260701-eski", status="applied", applied=NOW - timedelta(days=FOLLOW_UP_DAYS)
    )
    fresh = _fake(archive, "20260807-yeni", status="applied", applied=NOW - timedelta(days=2))

    assert waiting.needs_follow_up(NOW)
    assert waiting.days_waiting(NOW) == FOLLOW_UP_DAYS
    assert not fresh.needs_follow_up(NOW)


def test_only_applied_entries_wait(archive: Path) -> None:
    """Mülakata geçmiş bir başvuru "cevapsız" değildir."""
    moved = _fake(archive, "20260701-x", status="interview", applied=NOW - timedelta(days=30))
    assert moved.days_waiting(NOW) is None
    assert not moved.needs_follow_up(NOW)


# --------------------------------------------------------------------------- #
# Listeleme ve süzme
# --------------------------------------------------------------------------- #


def test_load_all_is_newest_first(archive: Path) -> None:
    _fake(archive, "20260801-a", created=NOW - timedelta(days=7))
    _fake(archive, "20260808-b", created=NOW)

    assert [item.id for item in load_all(archive)] == ["20260808-b", "20260801-a"]


def test_load_all_ignores_directories_without_meta(archive: Path) -> None:
    _fake(archive, "20260808-a")
    (archive / "yarim-kayit").mkdir()

    assert len(load_all(archive)) == 1


def test_load_all_on_missing_archive(tmp_path: Path) -> None:
    assert load_all(tmp_path / "yok") == []


def test_find_accepts_a_fragment(archive: Path) -> None:
    _fake(archive, "20260808-novatek")

    assert find("novatek", root=archive) is not None
    assert find("20260808-novatek", root=archive) is not None
    assert find("helix", root=archive) is None


def test_ambiguous_fragment_is_not_resolved_silently(archive: Path) -> None:
    """Aynı şirkete iki uyarlama normal; yanlış kaydın durumunu değiştirmektense dur."""
    _fake(archive, "20260808-novatek-a")
    _fake(archive, "20260808-novatek-b")

    assert len(find_all("novatek", root=archive)) == 2
    assert find("novatek", root=archive) is None


def test_exact_name_beats_ambiguity(archive: Path) -> None:
    _fake(archive, "20260808-novatek-a")
    _fake(archive, "20260808-novatek-a-2")

    assert find_all("20260808-novatek-a", root=archive) == [archive / "20260808-novatek-a"]


@pytest.mark.parametrize(
    ("value", "days"),
    [("7d", 7), ("14g", 14), ("3w", 21), ("2h", 14)],
)
def test_parse_since_shortcuts(value: str, days: int) -> None:
    assert parse_since(value, today=NOW) == NOW - timedelta(days=days)


def test_parse_since_iso_date() -> None:
    assert parse_since("2026-08-01", today=NOW) == datetime(2026, 8, 1)


def test_parse_since_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="Anlaşılmayan tarih"):
        parse_since("geçen ay")


def test_filters(archive: Path) -> None:
    _fake(archive, "20260801-a", status="applied", created=NOW - timedelta(days=10))
    _fake(archive, "20260808-b", status="offer", created=NOW)
    applications = load_all(archive)

    assert [item.id for item in filter_applications(applications, status="offer")] == ["20260808-b"]
    since = filter_applications(applications, since=NOW - timedelta(days=3))
    assert [item.id for item in since] == ["20260808-b"]


# --------------------------------------------------------------------------- #
# İstatistik
# --------------------------------------------------------------------------- #


def test_prepared_entries_are_not_counted_as_failures(archive: Path) -> None:
    """Gönderilmemiş bir kayıt "dönüş alamadım" istatistiğini bozmamalı."""
    _fake(archive, "20260808-a", status="prepared")
    _fake(archive, "20260808-b", status="applied")

    stats = compute_stats(load_all(archive), today=NOW)
    assert stats.total == 2
    assert stats.sent == 1
    assert stats.answered_rate == 0.0


def test_rejection_counts_as_an_answer_but_not_progress(archive: Path) -> None:
    _fake(archive, "20260808-a", status="rejected")
    _fake(archive, "20260808-b", status="interview")

    stats = compute_stats(load_all(archive), today=NOW)
    assert stats.answered == 2
    assert stats.positive == 1
    assert stats.rejected == 1


def test_weekly_tempo(archive: Path) -> None:
    _fake(archive, "20260803-a", created=datetime(2026, 8, 3))
    _fake(archive, "20260804-b", created=datetime(2026, 8, 4))
    _fake(archive, "20260728-c", created=datetime(2026, 7, 28))

    stats = compute_stats(load_all(archive), today=NOW)
    assert list(stats.per_week.values()) == [1, 2]


def test_score_buckets_answer_the_threshold_question(archive: Path) -> None:
    """`stats`ın asıl sorusu: hangi uyum skorunun üstünde dönüş geliyor?"""
    for index in range(3):
        _fake(archive, f"20260808-dusuk{index}", status="silent", score=50)
    _fake(archive, "20260808-yuksek1", status="interview", score=88)
    _fake(archive, "20260808-yuksek2", status="response", score=90)

    stats = compute_stats(load_all(archive), today=NOW)
    best = stats.best_bucket

    assert best is not None
    assert best.label == "%85+"
    assert best.positive_rate == 1.0
    assert not stats.confident  # 5 gönderim eşik belirlemek için az


def test_stats_on_an_empty_archive(archive: Path) -> None:
    archive.mkdir()
    stats = compute_stats(load_all(archive), today=NOW)

    assert stats.sent == 0
    assert stats.answered_rate == 0.0
    assert stats.best_bucket is None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


@pytest.fixture
def cli_archive(archive: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    archive.mkdir(parents=True)
    monkeypatch.setattr("cv_updater.steps.applications.APPLICATIONS_DIR", archive)
    return archive


def test_cli_list_empty(cli_archive: Path) -> None:
    result = runner.invoke(app, ["log", "list"])
    assert result.exit_code == 0
    assert "Henüz başvuru kaydı yok" in result.stdout


def test_cli_list_shows_entries(cli_archive: Path) -> None:
    _fake(cli_archive, "20260808-novatek", status="applied", applied=NOW)

    result = runner.invoke(app, ["log", "list"])
    assert result.exit_code == 0
    assert "novatek" in result.stdout
    assert "başvuruldu" in result.stdout


def test_cli_list_flags_stale_applications(cli_archive: Path) -> None:
    _fake(
        cli_archive,
        "20260701-eski",
        status="applied",
        applied=datetime.now() - timedelta(days=FOLLOW_UP_DAYS + 1),
    )

    result = runner.invoke(app, ["log", "list"])
    assert result.exit_code == 0
    assert "takip mesajı zamanı" in result.stdout


def test_cli_status_updates(cli_archive: Path) -> None:
    _fake(cli_archive, "20260808-novatek")

    result = runner.invoke(app, ["log", "status", "novatek", "applied"])
    assert result.exit_code == 0
    assert read(cli_archive / "20260808-novatek" / META_FILENAME).status == "applied"


def test_cli_status_rejects_unknown_value(cli_archive: Path) -> None:
    _fake(cli_archive, "20260808-novatek")

    result = runner.invoke(app, ["log", "status", "novatek", "kabul"])
    assert result.exit_code == 2
    assert "Bilinmeyen durum" in result.stdout


def test_cli_status_on_missing_application(cli_archive: Path) -> None:
    result = runner.invoke(app, ["log", "status", "yok", "applied"])
    assert result.exit_code == 2
    assert "bulunamadı" in result.stdout


def test_cli_status_refuses_an_ambiguous_id(cli_archive: Path) -> None:
    _fake(cli_archive, "20260808-novatek-a")
    _fake(cli_archive, "20260808-novatek-b")

    result = runner.invoke(app, ["log", "status", "novatek", "applied"])
    assert result.exit_code == 2
    assert "2 başvuruya uyuyor" in result.stdout
    assert read(cli_archive / "20260808-novatek-a" / META_FILENAME).status == "prepared"


def test_cli_list_does_not_wrap_long_positions(cli_archive: Path) -> None:
    """Uzun pozisyon başlığı tek kaydı beş satıra yayıp tabloyu okunmaz yapıyordu."""
    application = _fake(cli_archive, "20260808-novatek")
    application.position = "Kıdemli Yapay Zeka Mühendisi (Görüntü İşleme & LLM)"
    write(cli_archive / application.id, application)

    result = runner.invoke(app, ["log", "list"], env={"COLUMNS": "160"})
    body = [line for line in result.stdout.splitlines() if "novatek" in line]

    assert len(body) == 1
    assert "…" in body[0]  # sarmak yerine kısaltıldı


def test_cli_stats_reports_the_best_bucket(cli_archive: Path) -> None:
    _fake(cli_archive, "20260808-a", status="silent", score=40)
    _fake(cli_archive, "20260808-b", status="offer", score=90)

    result = runner.invoke(app, ["log", "stats"])
    assert result.exit_code == 0
    assert "En iyi dönüş aralığı" in result.stdout
    assert "%85+" in result.stdout


def test_cli_list_filters(cli_archive: Path) -> None:
    _fake(cli_archive, "20260808-a", status="offer")
    _fake(cli_archive, "20260808-b", status="silent")

    result = runner.invoke(app, ["log", "list", "--status", "offer"])
    assert result.exit_code == 0
    assert "20260808-a" in result.stdout
    assert "20260808-b" not in result.stdout


def test_cli_list_rejects_a_bad_since(cli_archive: Path) -> None:
    _fake(cli_archive, "20260808-a")

    result = runner.invoke(app, ["log", "list", "--since", "geçen ay"])
    assert result.exit_code == 2
    assert "Anlaşılmayan tarih" in result.stdout
