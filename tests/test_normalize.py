"""Issue 04 — ilan metni normalizasyonu. Hiçbiri ağa çıkmaz."""

from __future__ import annotations

from cv_updater.steps.normalize import (
    MIN_MEANINGFUL_CHARS,
    expand_ligatures,
    normalize,
)


class TestLigatures:
    """Kabul kriteri: ligatürlü girdi normalizasyondan sonra `fi`/`fl` içerir."""

    def test_ligatured_input_contains_plain_fi_and_fl(self) -> None:
        raw = "ﬁne-tuning ve workﬂow deneyimi"
        result = normalize(raw)
        assert "fine-tuning" in result.text
        assert "workflow" in result.text
        assert "ﬁ" not in result.text
        assert "ﬂ" not in result.text

    def test_ligature_count_is_reported(self) -> None:
        result = normalize("ﬁ ﬂ ﬀ")
        assert result.ligatures_fixed == 3

    def test_expand_ligatures_is_idempotent(self) -> None:
        once, first = expand_ligatures("ﬁne")
        twice, second = expand_ligatures(once)
        assert once == twice == "fine"
        assert (first, second) == (1, 0)


class TestWhitespace:
    def test_nbsp_and_zero_width_characters_are_removed(self) -> None:
        raw = "Python geliştirici​ deneyimi"
        result = normalize(raw)
        assert " " not in result.text
        assert "​" not in result.text
        assert "Python geliştirici deneyimi" in result.text

    def test_repeated_spaces_collapse(self) -> None:
        result = normalize("FastAPI     ve      Docker")
        assert "FastAPI ve Docker" in result.text

    def test_long_blank_runs_collapse_to_one(self) -> None:
        result = normalize("Başlık\n\n\n\n\nGövde")
        assert result.text == "Başlık\n\nGövde"

    def test_crlf_becomes_lf(self) -> None:
        result = normalize("bir\r\niki\r\nüç")
        assert "\r" not in result.text
        assert result.text.splitlines() == ["bir", "iki", "üç"]


class TestBullets:
    def test_various_bullet_markers_become_dash(self) -> None:
        raw = "• Python\n▪ Docker\n‣ AWS\n* Linux"
        result = normalize(raw)
        assert result.text.splitlines() == ["- Python", "- Docker", "- AWS", "- Linux"]
        assert result.bullets_rewritten == 4

    def test_numbered_items_become_dash(self) -> None:
        result = normalize("1. Python\n2) Docker")
        assert result.text.splitlines() == ["- Python", "- Docker"]

    def test_plain_text_is_not_rewritten(self) -> None:
        result = normalize("Python bilgisi aranıyor")
        assert result.bullets_rewritten == 0
        assert result.text == "Python bilgisi aranıyor"


class TestChromeStripping:
    def test_linkedin_chrome_lines_are_dropped(self) -> None:
        raw = (
            "Kaydet\n"
            "Bu ilanı paylaş\n"
            "142 başvuran\n"
            "3 gün önce yayınlandı\n"
            "Senior Python Developer\n"
            "FastAPI ile mikroservis geliştirme deneyimi.\n"
        )
        result = normalize(raw)
        assert "Senior Python Developer" in result.text
        assert "FastAPI" in result.text
        for chrome in ("Kaydet", "Bu ilanı paylaş", "142 başvuran"):
            assert chrome not in result.text
        assert len(result.dropped) == 4

    def test_english_chrome_lines_are_dropped(self) -> None:
        raw = "Easy Apply\nSave\nShare this posting\nRemote Backend Engineer\nWe use Django."
        result = normalize(raw)
        assert "Remote Backend Engineer" in result.text
        assert "Django" in result.text
        assert "Easy Apply" not in result.text

    def test_turkish_navigation_is_dropped(self) -> None:
        raw = "Ana Sayfa\nBildirimler\nMesajlar\nAğım\nKıdemli Yapay Zeka Mühendisi"
        result = normalize(raw)
        assert result.text == "Kıdemli Yapay Zeka Mühendisi"

    def test_cookie_and_legal_text_is_dropped(self) -> None:
        raw = (
            "Bu site çerez kullanmaktadır.\n"
            "Gizlilik Politikası\n"
            "Aranan nitelikler: Python, Docker.\n"
        )
        result = normalize(raw)
        assert "Aranan nitelikler" in result.text
        assert "çerez" not in result.text.lower()

    def test_repeated_short_lines_are_treated_as_boilerplate(self) -> None:
        raw = "Şirket A\nBaşvur\nŞirket B\nBaşvur\nŞirket C\nBaşvur\nGerçek ilan metni burada."
        result = normalize(raw)
        assert result.text.count("Başvur") == 0
        assert "Gerçek ilan metni burada." in result.text

    def test_a_word_inside_a_sentence_survives(self) -> None:
        """Kural satırın tamamına bakar; cümle içindeki aynı kelime korunur."""
        raw = "Adayların özgeçmişlerini kaydet butonuyla saklayan bir ürün geliştiriyoruz."
        result = normalize(raw)
        assert "kaydet butonuyla" in result.text
        assert result.dropped == ()

    def test_long_repeated_lines_are_kept(self) -> None:
        """Uzun satırlar tekrar kuralının dışında — içerik olabilirler."""
        line = "Bu pozisyon için en az üç yıl profesyonel Python deneyimi aranmaktadır."
        result = normalize("\n".join([line] * 3))
        assert result.text.count(line) == 3


class TestShortInput:
    def test_short_posting_is_flagged(self) -> None:
        assert normalize("Python geliştirici aranıyor.").is_short

    def test_long_enough_posting_is_not_flagged(self) -> None:
        assert not normalize("Python. " * 60).is_short

    def test_threshold_is_measured_on_clean_text(self) -> None:
        """Arayüz artığı eşiği şişirmemeli: temizlenmiş metin sayılır."""
        chrome = "\n".join(["Kaydet", "Paylaş", "Başvur"] * 12)
        result = normalize(chrome + "\nPython bilgisi.")
        assert len(result.text) < MIN_MEANINGFUL_CHARS
        assert result.is_short


class TestDeterminism:
    def test_normalizing_twice_changes_nothing(self) -> None:
        raw = "• ﬁne-tuning\nKaydet\n\n\n\nPython ve Docker"
        once = normalize(raw).text
        assert normalize(once).text == once

    def test_stats_are_reported(self) -> None:
        result = normalize("• ﬁne-tuning deneyimi\nKaydet")
        assert result.stats["ligatür düzeltildi"] == 1
        assert result.stats["madde işareti"] == 1
        assert result.stats["atılan satır"] == 1
