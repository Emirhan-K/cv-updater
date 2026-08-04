"""llm.py yapılandırması — model ayarı tek yerde yaşıyor mu?"""

from __future__ import annotations

import pytest

from cv_updater import llm


def test_import_does_not_require_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(llm.API_KEY_ENV, raising=False)
    assert llm.has_api_key() is False


def test_get_client_raises_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(llm.API_KEY_ENV, raising=False)
    with pytest.raises(llm.MissingAPIKeyError):
        llm.get_client()


def test_request_kwargs_carries_model_and_effort() -> None:
    kwargs = llm.request_kwargs()
    assert kwargs["model"] == llm.MODEL
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"]["effort"] == "high"


def test_request_kwargs_overrides() -> None:
    kwargs = llm.request_kwargs(max_tokens=42)
    assert kwargs["max_tokens"] == 42
    assert kwargs["model"] == llm.MODEL
