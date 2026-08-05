"""Issue 05 — ilan analizi. LLM sahte bir istemciyle taklit edilir, ağa çıkılmaz."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cv_updater.models.job import JobAnalysis, JobPosting
from cv_updater.steps.analyze_job import (
    JOB_FILENAME,
    analyze,
    find_hallucinated,
    fold,
    latest_analysis,
    map_keywords,
    read_analysis,
    write_analysis,
)
from cv_updater.tags import load_tags

POSTING = """Kıdemli Yapay Zeka Mühendisi
Aranan Nitelikler
- Python ile en az 4 yıl deneyim
- FastAPI ile REST API geliştirme
- Docker ve CI/CD süreçlerine hakimiyet
- Görüntü İşleme alanında tecrübe
- PostgreSQL üzerinde performanslı sorgu
"""


@pytest.fixture
def vocabulary(tags_yaml_path: Path):
    return load_tags(tags_yaml_path)


class TestFold:
    def test_separators_collapse(self) -> None:
        assert fold("CI/CD") == fold("ci-cd") == fold("ci cd")

    def test_case_is_ignored(self) -> None:
        assert fold("FastAPI") == fold("fastapi")

    def test_turkish_letters_survive(self) -> None:
        """ASCII'ye zorlansa 'görüntü işleme' ilandaki yazımıyla eşleşmezdi."""
        assert "görüntü işleme" in fold("Görüntü İşleme")


class TestHallucinationCheck:
    def test_terms_present_in_the_posting_pass(self) -> None:
        assert find_hallucinated(POSTING, ["Python", "FastAPI", "Docker"]) == []

    def test_absent_terms_are_reported(self) -> None:
        """Kabul kriteri: `tech_keywords` içindeki hiçbir terim ilanda eksik olamaz."""
        assert find_hallucinated(POSTING, ["Python", "Django", "Kubernetes"]) == [
            "Django",
            "Kubernetes",
        ]

    def test_separator_differences_are_not_false_alarms(self) -> None:
        assert find_hallucinated(POSTING, ["ci-cd", "rest api"]) == []

    def test_case_differences_are_not_false_alarms(self) -> None:
        assert find_hallucinated(POSTING, ["python", "POSTGRESQL"]) == []

    def test_empty_term_is_reported(self) -> None:
        assert find_hallucinated(POSTING, ["   "]) == ["   "]

    def test_substring_of_a_word_does_not_match(self) -> None:
        """'Java' ilanda yok; 'JavaScript' de yok — 'Python' içinde 'yt' aramıyoruz."""
        assert find_hallucinated("Java geliştirici", ["JavaScript"]) == ["JavaScript"]


class TestKeywordMapping:
    def test_known_terms_become_canonical_tags(self, vocabulary: Any) -> None:
        mapping = map_keywords(POSTING, ["Python", "FastAPI", "Docker"], vocabulary=vocabulary)
        assert mapping.tags == ["python", "fastapi", "docker"]
        assert mapping.unmapped == []

    def test_spelling_variants_map_to_one_tag(self, vocabulary: Any) -> None:
        """'Fast API' / 'FastAPI' / 'fastapi' aynı etikete inmeli."""
        text = "Fast API ve FastAPI ve fastapi"
        mapping = map_keywords(text, ["Fast API", "FastAPI", "fastapi"], vocabulary=vocabulary)
        assert mapping.tags == ["fastapi"]

    def test_turkish_alias_is_resolved(self, vocabulary: Any) -> None:
        mapping = map_keywords(POSTING, ["Görüntü İşleme"], vocabulary=vocabulary)
        assert mapping.tags == ["computer-vision"]

    def test_slash_alias_is_resolved(self, vocabulary: Any) -> None:
        mapping = map_keywords(POSTING, ["CI/CD"], vocabulary=vocabulary)
        assert mapping.tags == ["ci-cd"]

    def test_unknown_terms_go_to_unmapped(self, vocabulary: Any) -> None:
        text = POSTING + "\nKubernetes ve Terraform bilgisi"
        mapping = map_keywords(text, ["Kubernetes", "Terraform"], vocabulary=vocabulary)
        assert mapping.tags == []
        assert mapping.unmapped == ["Kubernetes", "Terraform"]

    def test_hallucinated_terms_are_dropped_not_mapped(self, vocabulary: Any) -> None:
        mapping = map_keywords(POSTING, ["Python", "Django"], vocabulary=vocabulary)
        assert mapping.tags == ["python"]
        assert mapping.dropped == ["Django"]
        assert "Django" not in mapping.unmapped

    def test_duplicates_are_removed(self, vocabulary: Any) -> None:
        mapping = map_keywords(POSTING, ["Python", "python", "PYTHON"], vocabulary=vocabulary)
        assert mapping.tags == ["python"]

    def test_empty_input(self, vocabulary: Any) -> None:
        mapping = map_keywords(POSTING, [], vocabulary=vocabulary)
        assert (mapping.tags, mapping.unmapped, mapping.dropped) == ([], [], [])


class _Block:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.input = payload


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.content = [_Block(payload)]


class _FakeMessages:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        return _Response(self._payload)


class _FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.messages = _FakeMessages(payload)


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "company": "Novatek",
        "title": "Kıdemli Yapay Zeka Mühendisi",
        "language": "tr",
        "seniority": "senior",
        "location": "İstanbul",
        "work_mode": "hybrid",
        "requirements": [
            {"text": "Python ile 4 yıl deneyim", "kind": "must", "keywords": ["Python"]},
        ],
        "tech_keywords": ["Python", "FastAPI", "Docker"],
        "soft_signals": ["mentorluk"],
        "domain": "kurumsal yazılım",
        "tone_notes": "Resmi ve kurumsal.",
    }
    base.update(overrides)
    return base


class TestAnalyze:
    def test_returns_a_structured_analysis(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_STRUCTURED_MODE", "tool")
        client = _FakeClient(_payload())

        analysis = analyze(POSTING, source_kind="file", client=client)

        assert analysis.posting.company == "Novatek"
        assert analysis.posting.language == "tr"
        assert analysis.posting.seniority == "senior"
        assert analysis.source_kind == "file"

    def test_keywords_are_mapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_STRUCTURED_MODE", "tool")
        client = _FakeClient(_payload())

        analysis = analyze(POSTING, client=client)

        assert analysis.keywords.tags == ["python", "fastapi", "docker"]

    def test_hallucinated_keywords_are_removed_from_the_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Süzgeç sadece rapor etmiyor, `tech_keywords`'ü de temizliyor."""
        monkeypatch.setenv("LLM_STRUCTURED_MODE", "tool")
        client = _FakeClient(_payload(tech_keywords=["Python", "Django", "Kubernetes"]))

        analysis = analyze(POSTING, client=client)

        assert "Django" not in analysis.posting.tech_keywords
        assert analysis.keywords.dropped == ["Django", "Kubernetes"]

    def test_every_remaining_keyword_appears_in_the_posting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Kabul kriteri, doğrudan: çıktıdaki hiçbir terim ilanda eksik değil."""
        monkeypatch.setenv("LLM_STRUCTURED_MODE", "tool")
        client = _FakeClient(_payload(tech_keywords=["Python", "Django", "PostgreSQL"]))

        analysis = analyze(POSTING, client=client)

        assert find_hallucinated(POSTING, analysis.posting.tech_keywords) == []

    def test_the_model_is_recorded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_STRUCTURED_MODE", "tool")
        monkeypatch.setenv("LLM_MODEL", "claude-opus-5")
        client = _FakeClient(_payload())

        assert analyze(POSTING, client=client).model == "claude-opus-5"

    def test_system_prompt_forbids_invention(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_STRUCTURED_MODE", "tool")
        client = _FakeClient(_payload())

        analyze(POSTING, client=client)

        system = client.messages.calls[0]["system"]
        assert "uydurma" in system
        assert "YALNIZCA" in system

    def test_posting_text_is_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_STRUCTURED_MODE", "tool")
        client = _FakeClient(_payload())

        analyze(POSTING, client=client)

        content = client.messages.calls[0]["messages"][0]["content"]
        assert "Görüntü İşleme" in content


class TestPersistence:
    def test_round_trip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_STRUCTURED_MODE", "tool")
        analysis = analyze(POSTING, client=_FakeClient(_payload()))

        path = write_analysis(tmp_path, analysis)
        loaded = read_analysis(path)

        assert path.name == JOB_FILENAME
        assert loaded.posting == analysis.posting
        assert loaded.keywords == analysis.keywords

    def test_json_is_readable(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Türkçe karakterler kaçış dizisine dönmemeli — dosya elle okunacak."""
        monkeypatch.setenv("LLM_STRUCTURED_MODE", "tool")
        analysis = analyze(POSTING, client=_FakeClient(_payload()))

        path = write_analysis(tmp_path, analysis)

        assert "Kıdemli" in path.read_text(encoding="utf-8")


class TestLatestAnalysis:
    def test_none_when_nothing_written(self, tmp_path: Path) -> None:
        assert latest_analysis(tmp_path) is None

    def test_none_when_directory_missing(self, tmp_path: Path) -> None:
        assert latest_analysis(tmp_path / "yok") is None

    def test_picks_the_newest_by_timestamped_name(self, tmp_path: Path) -> None:
        for name in ("acme-20260806-100000", "acme-20260806-120000", "acme-20260805-235959"):
            run_dir = tmp_path / name
            run_dir.mkdir()
            analysis = JobAnalysis(
                posting=JobPosting(title=name, language="tr", seniority="mid", work_mode="remote")
            )
            write_analysis(run_dir, analysis)

        latest = latest_analysis(tmp_path)

        assert latest is not None
        assert latest.parent.name == "acme-20260806-120000"

    def test_ignores_runs_without_analysis(self, tmp_path: Path) -> None:
        (tmp_path / "acme-20260806-130000").mkdir()  # sadece job_clean.txt var gibi
        run_dir = tmp_path / "acme-20260806-100000"
        run_dir.mkdir()
        write_analysis(
            run_dir,
            JobAnalysis(
                posting=JobPosting(title="x", language="tr", seniority="mid", work_mode="remote")
            ),
        )

        latest = latest_analysis(tmp_path)

        assert latest is not None
        assert latest.parent.name == "acme-20260806-100000"
