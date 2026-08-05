"""llm.py yapılandırması — model, endpoint ve yapılandırılmış çıktı stratejisi.

Hiçbiri ağa çıkmaz; istemci sahte bir nesneyle değiştirilir.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from cv_updater import llm


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Her test bilinen bir ortamdan başlasın — geliştiricinin `.env`'i sızmasın."""
    for variable in (
        llm.API_KEY_ENV,
        llm.FALLBACK_API_KEY_ENV,
        llm.BASE_URL_ENV,
        llm.MODEL_ENV,
        llm.STRUCTURED_MODE_ENV,
    ):
        monkeypatch.delenv(variable, raising=False)


class TestApiKey:
    def test_import_does_not_require_api_key(self) -> None:
        assert llm.has_api_key() is False

    def test_get_client_raises_without_key(self) -> None:
        with pytest.raises(llm.MissingAPIKeyError):
            llm.get_client()

    def test_error_message_names_both_variables_and_the_gateway(self) -> None:
        message = str(llm.MissingAPIKeyError())
        assert llm.API_KEY_ENV in message
        assert llm.FALLBACK_API_KEY_ENV in message
        assert "8045" in message  # gateway adresi ipucu olarak veriliyor

    def test_llm_api_key_is_preferred(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(llm.API_KEY_ENV, "gateway-anahtari")
        monkeypatch.setenv(llm.FALLBACK_API_KEY_ENV, "anthropic-anahtari")
        assert llm.api_key() == "gateway-anahtari"

    def test_anthropic_key_is_the_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(llm.FALLBACK_API_KEY_ENV, "anthropic-anahtari")
        assert llm.api_key() == "anthropic-anahtari"
        assert llm.has_api_key() is True


class TestBaseUrl:
    def test_unset_means_real_anthropic(self) -> None:
        assert llm.base_url() is None

    def test_empty_string_means_real_anthropic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(llm.BASE_URL_ENV, "   ")
        assert llm.base_url() is None

    def test_trailing_slash_is_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(llm.BASE_URL_ENV, "http://127.0.0.1:8045/")
        assert llm.base_url() == "http://127.0.0.1:8045"

    def test_v1_suffix_is_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SDK yola `/v1` ekler; iki kez eklenirse 404 gelir."""
        monkeypatch.setenv(llm.BASE_URL_ENV, "http://127.0.0.1:8045/v1")
        assert llm.base_url() == "http://127.0.0.1:8045"


class TestModelName:
    def test_default_is_opus(self) -> None:
        assert llm.model_name() == llm.DEFAULT_MODEL
        assert "opus" in llm.DEFAULT_MODEL

    def test_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(llm.MODEL_ENV, "gemini-3-pro-high")
        assert llm.model_name() == "gemini-3-pro-high"


class TestRequestKwargs:
    def test_carries_model_and_effort(self) -> None:
        kwargs = llm.request_kwargs()
        assert kwargs["model"] == llm.DEFAULT_MODEL
        assert kwargs["thinking"] == {"type": "adaptive"}
        assert kwargs["output_config"]["effort"] == "high"

    def test_overrides(self) -> None:
        kwargs = llm.request_kwargs(max_tokens=42)
        assert kwargs["max_tokens"] == 42
        assert kwargs["model"] == llm.DEFAULT_MODEL


class TestStructuredMode:
    def test_gateway_gets_tool_use(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Gateway `output_format`'ı yok sayıyor — orada tool-use şart."""
        monkeypatch.setenv(llm.BASE_URL_ENV, "http://127.0.0.1:8045")
        assert llm.structured_mode() == "tool"

    def test_real_api_gets_parse(self) -> None:
        assert llm.structured_mode() == "parse"

    def test_env_can_force_either(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(llm.BASE_URL_ENV, "http://127.0.0.1:8045")
        monkeypatch.setenv(llm.STRUCTURED_MODE_ENV, "parse")
        assert llm.structured_mode() == "parse"

    def test_unknown_value_falls_back_to_auto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(llm.STRUCTURED_MODE_ENV, "sacmalik")
        assert llm.structured_mode() == "parse"


class Ornek(BaseModel):
    """Test şeması."""

    ad: str
    sayi: int


class _Block:
    def __init__(self, type_: str, **fields: Any) -> None:
        self.type = type_
        for key, value in fields.items():
            setattr(self, key, value)


class _Response:
    def __init__(self, *blocks: _Block) -> None:
        self.content = list(blocks)


class _FakeMessages:
    def __init__(self, response: Any = None, parsed: Any = None) -> None:
        self._response = response
        self._parsed = parsed
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._response

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return type("Parsed", (), {"parsed_output": self._parsed})()


class _FakeClient:
    def __init__(self, messages: _FakeMessages) -> None:
        self.messages = messages


class TestStructuredViaTool:
    def _client(self, *blocks: _Block) -> tuple[_FakeClient, _FakeMessages]:
        messages = _FakeMessages(response=_Response(*blocks))
        return _FakeClient(messages), messages

    def test_tool_input_becomes_a_model(self) -> None:
        client, _ = self._client(_Block("tool_use", input={"ad": "Acme", "sayi": 3}))

        result = llm.structured(
            client,  # type: ignore[arg-type]
            Ornek,
            system="sistem",
            messages=[{"role": "user", "content": "merhaba"}],
            mode="tool",
        )

        assert result == Ornek(ad="Acme", sayi=3)

    def test_thinking_block_before_tool_use_is_skipped(self) -> None:
        """Thinking açıkken `content[0]` düşünme bloğu olabiliyor."""
        client, _ = self._client(
            _Block("thinking", thinking="..."),
            _Block("text", text="Buyurun"),
            _Block("tool_use", input={"ad": "Acme", "sayi": 3}),
        )

        result = llm.structured(
            client,  # type: ignore[arg-type]
            Ornek,
            system="s",
            messages=[],
            mode="tool",
        )

        assert result.ad == "Acme"

    def test_tool_use_is_forced(self) -> None:
        client, messages = self._client(_Block("tool_use", input={"ad": "A", "sayi": 1}))

        llm.structured(
            client,  # type: ignore[arg-type]
            Ornek,
            system="s",
            messages=[],
            mode="tool",
        )

        call = messages.calls[0]
        assert call["tool_choice"]["type"] == "tool"
        assert call["tools"][0]["input_schema"]["properties"].keys() == {"ad", "sayi"}
        assert call["model"] == llm.DEFAULT_MODEL

    def test_missing_tool_use_raises(self) -> None:
        client, _ = self._client(_Block("text", text="Bir aciklama yazdim"))

        with pytest.raises(llm.StructuredOutputError, match="aracı çağırmadı"):
            llm.structured(
                client,  # type: ignore[arg-type]
                Ornek,
                system="s",
                messages=[],
                mode="tool",
            )

    def test_schema_violation_raises(self) -> None:
        client, _ = self._client(_Block("tool_use", input={"ad": "Acme"}))

        with pytest.raises(llm.StructuredOutputError, match="şemaya uymuyor"):
            llm.structured(
                client,  # type: ignore[arg-type]
                Ornek,
                system="s",
                messages=[],
                mode="tool",
            )


class TestStructuredViaParse:
    def test_parsed_output_is_returned(self) -> None:
        client = _FakeClient(_FakeMessages(parsed=Ornek(ad="Acme", sayi=3)))

        result = llm.structured(
            client,  # type: ignore[arg-type]
            Ornek,
            system="s",
            messages=[],
            mode="parse",
        )

        assert result.sayi == 3

    def test_empty_parse_raises(self) -> None:
        client = _FakeClient(_FakeMessages(parsed=None))

        with pytest.raises(llm.StructuredOutputError, match="boş"):
            llm.structured(
                client,  # type: ignore[arg-type]
                Ornek,
                system="s",
                messages=[],
                mode="parse",
            )
