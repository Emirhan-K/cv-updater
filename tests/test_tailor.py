"""Issue 07 — uyarlama adımı. LLM sahte istemciyle taklit edilir, ağa çıkılmaz."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from cv_updater.models.candidates import CandidatePool
from cv_updater.models.job import JobAnalysis, JobPosting, KeywordMapping, Requirement
from cv_updater.models.resume import ExperienceItem, Resume, SkillItem, XBullet, XMeta
from cv_updater.steps.score import build_pool
from cv_updater.steps.tailor import (
    PLAN_FILENAME,
    read_plan,
    render_pool,
    render_posting,
    tailor,
    write_plan,
)
from cv_updater.tags import load_tags

TODAY = date(2026, 8, 6)


@pytest.fixture
def vocabulary(tags_yaml_path: Path):
    return load_tags(tags_yaml_path)


@pytest.fixture
def resume() -> Resume:
    doc = Resume()
    doc.sections.experience.items = [
        ExperienceItem(
            id="exp-ai",
            company="Örnek AI",
            position="Yazılım Mühendisi",
            period="Ocak 2026 – Devam Ediyor",
            x_bullets=[
                XBullet(
                    id="exp-ai.vision.tr",
                    text="Görüntü işleme modellerini veri hattına bağladı.",
                    x_meta=XMeta(tags=["computer-vision", "python"], lang="tr"),
                ),
                XBullet(
                    id="exp-ai.docs.tr",
                    text="Dokümantasyonu güncelledi.",
                    x_meta=XMeta(tags=["documentation"], lang="tr"),
                ),
            ],
        )
    ]
    doc.sections.skills.items = [
        SkillItem(id="sk-python", name="Python", x_meta=XMeta(tags=["python"])),
        SkillItem(id="sk-flutter", name="Flutter", x_meta=XMeta(tags=["flutter"])),
    ]
    return doc


@pytest.fixture
def analysis() -> JobAnalysis:
    return JobAnalysis(
        posting=JobPosting(
            company="Acme",
            title="Görüntü İşleme Mühendisi",
            language="tr",
            seniority="mid",
            work_mode="remote",
            requirements=[
                Requirement(
                    text="Görüntü işleme deneyimi", kind="must", keywords=["Görüntü İşleme"]
                ),
                Requirement(text="Docker artı", kind="nice", keywords=["Docker"]),
            ],
            tech_keywords=["Görüntü İşleme", "Python"],
            soft_signals=["takım çalışması"],
            domain="tarım teknolojileri",
            tone_notes="Samimi, startup üslubu.",
        ),
        keywords=KeywordMapping(tags=["computer-vision", "python"]),
    )


@pytest.fixture
def pool(resume: Resume, analysis: JobAnalysis, vocabulary: Any) -> CandidatePool:
    return build_pool(
        resume,
        analysis,
        "Görüntü İşleme mühendisi aranıyor, Python bilgisi şart.",
        vocabulary=vocabulary,
        today=TODAY,
    )


PLAN_PAYLOAD: dict[str, Any] = {
    "summary": "Görüntü işleme üzerine çalışan bir mühendis.",
    "summary_source_ids": ["exp-ai.vision.tr"],
    "experiences": [
        {
            "source_id": "exp-ai",
            "bullets": [
                {
                    "source_id": "exp-ai.vision.tr",
                    "rewritten": "Görüntü işleme modellerini veri hattına bağladı.",
                    "rationale": "İlanın ilk zorunlu gereksinimini karşılıyor.",
                }
            ],
        }
    ],
    "skill_ids": ["sk-python"],
    "project_ids": [],
    "omitted_reasons": [{"source_id": "exp-ai.docs.tr", "reason": "İlanla ilgisi yok."}],
}


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
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.messages = _FakeMessages(payload or PLAN_PAYLOAD)


class TestRenderPosting:
    def test_requirements_are_grouped_by_kind(self, analysis: JobAnalysis) -> None:
        text = render_posting(analysis)
        assert "Zorunlu gereksinimler:" in text
        assert "Tercih edilen nitelikler:" in text

    def test_tone_and_domain_reach_the_model(self, analysis: JobAnalysis) -> None:
        text = render_posting(analysis)
        assert "startup" in text
        assert "tarım teknolojileri" in text


class TestRenderPool:
    def test_only_selected_bullets_are_sent(self, pool: CandidatePool) -> None:
        text = render_pool(pool)
        assert "exp-ai.vision.tr" in text

    def test_ids_are_visible_for_every_bullet(self, pool: CandidatePool) -> None:
        """Model yalnızca id ile konuşuyor; id görünmezse plan kurulamaz."""
        for entry in pool.experiences:
            for bullet in entry.selected_bullets:
                assert f"[{bullet.id}]" in render_pool(pool)

    def test_missing_tags_are_declared_as_off_limits(
        self, resume: Resume, analysis: JobAnalysis, vocabulary: Any
    ) -> None:
        analysis.posting.requirements.append(
            Requirement(text="Kubernetes", kind="must", keywords=["Flutter"])
        )
        analysis.posting.requirements.append(
            Requirement(text="RabbitMQ deneyimi", kind="must", keywords=["RabbitMQ"])
        )
        pool = build_pool(resume, analysis, "RabbitMQ", vocabulary=vocabulary, today=TODAY)

        text = render_pool(pool)

        assert "OLMAYAN" in text
        assert "rabbitmq" in text

    def test_the_superset_is_not_sent_whole(self, pool: CandidatePool) -> None:
        """Ön filtrenin bütün anlamı bu: elenen madde modele hiç gitmiyor."""
        text = render_pool(pool)
        omitted = [
            bullet.id
            for entry in pool.experiences
            for bullet in entry.bullets
            if not bullet.selected
        ]
        for bullet_id in omitted:
            assert bullet_id not in text


class TestTailor:
    def test_returns_a_plan(
        self, analysis: JobAnalysis, pool: CandidatePool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_STRUCTURED_MODE", "tool")
        result = tailor(analysis, pool, client=_FakeClient())

        assert result.plan.summary_source_ids == ["exp-ai.vision.tr"]
        assert result.plan.source_ids() == ["exp-ai.vision.tr"]

    def test_the_model_is_recorded(
        self, analysis: JobAnalysis, pool: CandidatePool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_STRUCTURED_MODE", "tool")
        monkeypatch.setenv("LLM_MODEL", "claude-opus-5")

        assert tailor(analysis, pool, client=_FakeClient()).model == "claude-opus-5"

    def test_the_system_prompt_states_the_honesty_rule(
        self, analysis: JobAnalysis, pool: CandidatePool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_STRUCTURED_MODE", "tool")
        client = _FakeClient()

        tailor(analysis, pool, client=client)

        system = client.messages.calls[0]["system"]
        assert "DÜRÜSTLÜK KURALI" in system
        assert "EKLEME" in system
        assert "YANLIŞ:" in system  # örneklerle anlatılıyor

    def test_the_raw_posting_text_is_not_resent(
        self, analysis: JobAnalysis, pool: CandidatePool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """İlanın ham metni ikinci kez gitmiyor — havuzda olmayan teknolojiyi hatırlatırdı."""
        monkeypatch.setenv("LLM_STRUCTURED_MODE", "tool")
        client = _FakeClient()

        tailor(analysis, pool, client=client)

        content = client.messages.calls[0]["messages"][0]["content"]
        assert "aranıyor" not in content

    def test_the_tool_schema_has_no_free_form_bullet_field(
        self, analysis: JobAnalysis, pool: CandidatePool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_STRUCTURED_MODE", "tool")
        client = _FakeClient()

        tailor(analysis, pool, client=client)

        schema = client.messages.calls[0]["tools"][0]["input_schema"]
        assert set(schema["$defs"]["SelectedBullet"]["properties"]) == {
            "source_id",
            "rewritten",
            "rationale",
        }


class TestPersistence:
    def test_round_trip(
        self,
        tmp_path: Path,
        analysis: JobAnalysis,
        pool: CandidatePool,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("LLM_STRUCTURED_MODE", "tool")
        result = tailor(analysis, pool, client=_FakeClient())

        path = write_plan(tmp_path, result)
        loaded = read_plan(path)

        assert path.name == PLAN_FILENAME
        assert loaded.plan == result.plan

    def test_turkish_characters_are_readable(
        self,
        tmp_path: Path,
        analysis: JobAnalysis,
        pool: CandidatePool,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("LLM_STRUCTURED_MODE", "tool")
        result = tailor(analysis, pool, client=_FakeClient())

        path = write_plan(tmp_path, result)

        assert "Görüntü" in path.read_text(encoding="utf-8")


class TestTargetLanguage:
    """`--lang` ilanın dilini ezebiliyor; modelin bunu görmesi gerekiyor."""

    def test_the_pool_language_is_stated_in_the_prompt(
        self, analysis: JobAnalysis, pool: CandidatePool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_STRUCTURED_MODE", "tool")
        client = _FakeClient()

        tailor(analysis, pool, client=client)

        content = client.messages.calls[0]["messages"][0]["content"]
        assert "Üretilecek CV'nin dili: Türkçe" in content

    def test_a_forced_language_overrides_the_posting_language(
        self,
        resume: Resume,
        analysis: JobAnalysis,
        vocabulary: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Türkçe ilan + `--lang en`: modele İngilizce yazması söylenmeli."""
        monkeypatch.setenv("LLM_STRUCTURED_MODE", "tool")
        english_pool = build_pool(
            resume, analysis, "Görüntü İşleme", language="en", vocabulary=vocabulary, today=TODAY
        )
        client = _FakeClient()

        tailor(analysis, english_pool, client=client)

        content = client.messages.calls[0]["messages"][0]["content"]
        assert "English" in content
        assert "Dil: tr" in content  # ilanın dili yine görünüyor, ama bağlayıcı değil
