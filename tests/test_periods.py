"""`cv_updater.periods` — dönem metinlerinin dil çevirisi. Ağ yok, LLM yok."""

from __future__ import annotations

import pytest

from cv_updater.periods import translate_period


class TestTurkishToEnglish:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("Aralık 2025 – Devam Ediyor", "December 2025 – Present"),
            ("Mayıs 2026 – Devam Ediyor", "May 2026 – Present"),
            ("Aralık 2025 – Mayıs 2026", "December 2025 – May 2026"),
            ("Şubat 2026 – Mayıs 2026", "February 2026 – May 2026"),
            ("Eylül 2024 – Devam Ediyor", "September 2024 – Present"),
            ("Temmuz 2024 – Kasım 2024", "July 2024 – November 2024"),
            ("Ocak 2024 – Temmuz 2024", "January 2024 – July 2024"),
            ("Ağustos 2026 - Devam Ediyor", "August 2026 - Present"),
        ],
    )
    def test_every_month_in_the_superset(self, source: str, expected: str) -> None:
        assert translate_period(source, "en") == expected

    def test_the_separator_is_preserved(self) -> None:
        """Süper sette hem `–` hem `-` geçiyor; çeviri ayraca dokunmamalı."""
        assert translate_period("Temmuz 2025 - Devam Ediyor", "en") == "July 2025 - Present"


class TestUnrecognised:
    """Anlaşılmayan dönem olduğu gibi kalır — yarım çeviri, çevirmemekten kötü."""

    @pytest.mark.parametrize("source", ["2020-2025", "", "3 ay", "2018 — 2022"])
    def test_left_alone(self, source: str) -> None:
        assert translate_period(source, "en") == source

    def test_an_unknown_language_is_a_no_op(self) -> None:
        assert translate_period("Aralık 2025", "de") == "Aralık 2025"  # type: ignore[arg-type]


class TestRoundTrip:
    @pytest.mark.parametrize(
        "source",
        [
            "Aralık 2025 – Devam Ediyor",
            "Şubat 2026 – Mayıs 2026",
            "Eylül 2024 – Devam Ediyor",
        ],
    )
    def test_translating_back_restores_the_original(self, source: str) -> None:
        assert translate_period(translate_period(source, "en"), "tr") == source

    def test_translating_into_the_same_language_changes_nothing(self) -> None:
        assert translate_period("December 2025 – Present", "en") == "December 2025 – Present"
        assert translate_period("Aralık 2025 – Devam Ediyor", "tr") == "Aralık 2025 – Devam Ediyor"


class TestLongestMatchWins:
    def test_mayis_is_not_read_as_may(self) -> None:
        """`Mayıs` ile `May` aynı tabloda; kısa olan uzunu yemesin."""
        assert translate_period("Mayıs 2026", "en") == "May 2026"

    def test_devam_ediyor_is_not_read_as_devam(self) -> None:
        assert translate_period("Devam Ediyor", "en") == "Present"

    def test_alternative_ongoing_spellings(self) -> None:
        assert translate_period("Eylül 2024 – halen", "en") == "September 2024 – Present"
        assert translate_period("Eylül 2024 – günümüz", "en") == "September 2024 – Present"


class TestTurkishCasefolding:
    """CLAUDE.md tuzağı: düz `.lower()` `İ`yi `i + U+0307` yapıp eşleşmeyi bozuyor."""

    def test_uppercase_turkish_month_is_still_matched(self) -> None:
        assert translate_period("EYLÜL 2024", "en") == "September 2024"

    def test_a_dotted_capital_i_does_not_break_matching(self) -> None:
        assert translate_period("İkinci dönem", "en") == "İkinci dönem"
