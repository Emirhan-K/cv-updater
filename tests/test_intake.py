"""Issue 04 — üç girdi yolu, slug üretimi ve çalıştırma dizini."""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import pytest

from cv_updater.steps.intake import (
    CLEAN_FILENAME,
    RAW_FILENAME,
    IntakeError,
    _editor_command,
    new_run_dir,
    read_source,
    slug_hint,
    slugify,
    write_run_files,
)

POSTING = "Senior Python Developer\nFastAPI ve Docker deneyimi aranıyor."


class TestFileSource:
    def test_reads_a_file(self, tmp_path: Path) -> None:
        path = tmp_path / "job.txt"
        path.write_text(POSTING, encoding="utf-8")

        source = read_source(str(path))

        assert source.kind == "file"
        assert source.path == path
        assert "FastAPI" in source.text

    def test_missing_file_raises_with_a_hint(self, tmp_path: Path) -> None:
        with pytest.raises(IntakeError) as exc:
            read_source(str(tmp_path / "yok.txt"))
        assert "-" in str(exc.value)  # stdin ipucu mesajda

    def test_directory_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(IntakeError, match="dizin"):
            read_source(str(tmp_path))

    def test_empty_file_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "bos.txt"
        path.write_text("   \n\n", encoding="utf-8")
        with pytest.raises(IntakeError, match="boş"):
            read_source(str(path))


class TestStdinSource:
    def test_reads_stdin_with_dash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(POSTING))

        source = read_source("-")

        assert source.kind == "stdin"
        assert source.path is None
        assert "Docker" in source.text

    def test_empty_stdin_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("\n  \n"))
        with pytest.raises(IntakeError, match="stdin"):
            read_source("-")


def _fake_editor(content: str):
    """Editör yerine geçer: geçici dosyaya `content` yazar. Gerçek editör açılmaz."""

    def write(path: Path) -> None:
        path.write_text(content, encoding="utf-8")

    return write


class TestPasteSource:
    def test_editor_content_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "cv_updater.steps.intake._launch_editor",
            _fake_editor(f"# yorum satırı\n{POSTING}"),
        )

        source = read_source(None, paste=True)

        assert source.kind == "paste"
        assert "Senior Python Developer" in source.text
        assert "# yorum" not in source.text

    def test_unsaved_editor_leaves_only_the_template(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Kaydetmeden çıkıldığında dosyada yalnızca yorum başlığı kalır."""
        monkeypatch.setattr("cv_updater.steps.intake._launch_editor", lambda path: None)

        with pytest.raises(IntakeError, match="boş"):
            read_source(None, paste=True)

    def test_only_comments_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "cv_updater.steps.intake._launch_editor",
            _fake_editor("# sadece yorum\n#\n"),
        )
        with pytest.raises(IntakeError, match="boş"):
            read_source(None, paste=True)

    def test_temp_file_is_cleaned_up(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[Path] = []

        def capture(path: Path) -> None:
            seen.append(path)
            path.write_text(POSTING, encoding="utf-8")

        monkeypatch.setattr("cv_updater.steps.intake._launch_editor", capture)
        read_source(None, paste=True)

        assert seen and not seen[0].exists()

    def test_missing_editor_binary_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CV_UPDATER_EDITOR", "kesinlikle-olmayan-editor-9x")

        with pytest.raises(IntakeError, match="Editör bulunamadı"):
            read_source(None, paste=True)


class TestEditorCommand:
    def test_env_variable_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CV_UPDATER_EDITOR", "nano")
        assert _editor_command() == ["nano"]

    def test_flags_are_supported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.setenv("CV_UPDATER_EDITOR", "code --wait")
        assert _editor_command() == ["code", "--wait"]

    def test_falls_back_to_a_platform_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for variable in ("CV_UPDATER_EDITOR", "VISUAL", "EDITOR"):
            monkeypatch.delenv(variable, raising=False)
        assert _editor_command() in (["notepad"], ["vi"])


class TestNoSource:
    def test_missing_source_explains_all_three_paths(self) -> None:
        with pytest.raises(IntakeError) as exc:
            read_source(None)
        message = str(exc.value)
        assert "-" in message
        assert "--paste" in message


class TestSlugify:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Yazılım Mühendisi", "yazilim-muhendisi"),
            ("Şirket A.Ş. — Kıdemli Geliştirici", "sirket-a-s-kidemli-gelistirici"),
            ("FastAPI/Docker", "fastapi-docker"),
            ("  ---  ", ""),
            ("İSTANBUL", "istanbul"),
        ],
    )
    def test_turkish_characters_become_ascii(self, raw: str, expected: str) -> None:
        assert slugify(raw) == expected

    def test_result_is_filesystem_safe(self) -> None:
        slug = slugify('acme: "senior" <dev> | 2026/08?')
        assert all(ch.isalnum() or ch == "-" for ch in slug)

    def test_长_input_is_truncated(self) -> None:
        assert len(slugify("a" * 200)) <= 48


class TestSlugHint:
    def test_file_stem_wins(self, tmp_path: Path) -> None:
        path = tmp_path / "acme-senior-python.txt"
        path.write_text(POSTING, encoding="utf-8")
        source = read_source(str(path))

        assert slug_hint(source, POSTING) == "acme-senior-python"

    def test_falls_back_to_first_meaningful_line(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(POSTING))
        source = read_source("-")

        assert slug_hint(source, POSTING) == "senior-python-developer"

    def test_falls_back_to_placeholder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("--"))
        source = read_source("-")

        assert slug_hint(source, "--\n!!") == "ilan"


class TestRunDir:
    def test_directory_is_created_with_timestamp(self, tmp_path: Path) -> None:
        run_dir = new_run_dir("acme", root=tmp_path, now=datetime(2026, 8, 6, 14, 30, 5))

        assert run_dir.is_dir()
        assert run_dir.name == "acme-20260806-143005"

    def test_same_posting_twice_does_not_overwrite(self, tmp_path: Path) -> None:
        """Kabul kriteri: önceki çıktı asla ezilmez."""
        now = datetime(2026, 8, 6, 14, 30, 5)
        first = new_run_dir("acme", root=tmp_path, now=now)
        write_run_files(first, "ham", "temiz")

        second = new_run_dir("acme", root=tmp_path, now=now)

        assert first != second
        assert (first / RAW_FILENAME).read_text(encoding="utf-8") == "ham"
        assert list(second.iterdir()) == []

    def test_run_dirs_are_distinct_across_seconds(self, tmp_path: Path) -> None:
        first = new_run_dir("acme", root=tmp_path, now=datetime(2026, 8, 6, 14, 30, 5))
        second = new_run_dir("acme", root=tmp_path, now=datetime(2026, 8, 6, 14, 30, 6))
        assert first != second

    def test_empty_hint_gets_a_placeholder(self, tmp_path: Path) -> None:
        run_dir = new_run_dir("", root=tmp_path, now=datetime(2026, 8, 6, 14, 30, 5))
        assert run_dir.name.startswith("ilan-")


class TestWriteRunFiles:
    def test_both_files_are_written(self, tmp_path: Path) -> None:
        run_dir = new_run_dir("acme", root=tmp_path)

        raw_path, clean_path = write_run_files(run_dir, "• ﬁne", "- fine")

        assert raw_path.name == RAW_FILENAME
        assert clean_path.name == CLEAN_FILENAME
        assert raw_path.read_text(encoding="utf-8") == "• ﬁne"
        assert clean_path.read_text(encoding="utf-8") == "- fine"

    def test_raw_keeps_the_original_untouched(self, tmp_path: Path) -> None:
        """Ham dosya kanıt: normalizasyon bir şeyi yanlış attıysa geri dönülebilsin."""
        run_dir = new_run_dir("acme", root=tmp_path)
        raw = "Kaydet\nŞirket\r\nÇerez politikası"

        raw_path, _ = write_run_files(run_dir, raw, "Şirket")

        # `read_text` satır sonlarını çevirir; diskteki baytlara bakmak lazım.
        assert raw_path.read_bytes().decode("utf-8") == raw
