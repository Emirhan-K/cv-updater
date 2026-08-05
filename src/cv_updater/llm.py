"""Anthropic istemcisi — model, endpoint ve yapılandırılmış çıktı ayarının **tek** yeri.

Bu modülü içe aktarmak API anahtarı gerektirmez; anahtar yalnızca `get_client()`
çağrıldığında aranır. Böylece `--help` ve `validate` anahtarsız çalışır
(issue 01 kabul kriteri).

## Endpoint

Varsayılan hedef, workspace'teki yerel `llm-gateway` (Antigravity Manager).
`LLM_BASE_URL` verilirse Anthropic SDK oraya konuşur; verilmezse gerçek Anthropic
API'ye gider. **base_url `/v1` olmadan yazılır** — SDK yolu kendi ekler.

## Yapılandırılmış çıktı — neden iki strateji var

Issue 05 `client.messages.parse(output_format=...)` diyor. Gateway bunu
**desteklemiyor**: `output_format` sessizce yok sayılıyor, model düz metin
döndürüyor, ayrıştırma patlıyor (2026-08-05'te ölçüldü). Zorunlu tool-use
(`tool_choice={"type": "tool"}`) aynı gateway üzerinden temiz JSON veriyor.

Bu yüzden `structured()` iki yol tanır ve `auto` modunda `LLM_BASE_URL`'e bakarak
seçer: özel endpoint varsa tool-use, yoksa `parse`. `LLM_STRUCTURED_MODE` ile elle
zorlanabilir — gateway bir gün `output_format` desteklerse tek env değişkeni yeter.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:  # pragma: no cover - yalnızca tip denetimi için
    from anthropic import Anthropic

# --- Model yapılandırması: burayı değiştir, başka hiçbir yeri ---------------
#: Gateway'in Model Router'ı `claude-opus*` kalıbını `claude-opus-4-6-thinking`e
#: düşürüyor; sürüm adı burada kalabilir, doğru hedefe gidiyor.
DEFAULT_MODEL = "claude-opus-5"
THINKING: dict[str, Any] = {"type": "adaptive"}
OUTPUT_CONFIG: dict[str, Any] = {"effort": "high"}
MAX_TOKENS = 16000

MODEL_ENV = "LLM_MODEL"
BASE_URL_ENV = "LLM_BASE_URL"
API_KEY_ENV = "LLM_API_KEY"
#: Gateway'den vazgeçilirse `.env`'den `LLM_*` satırlarını silmek yeter.
FALLBACK_API_KEY_ENV = "ANTHROPIC_API_KEY"
STRUCTURED_MODE_ENV = "LLM_STRUCTURED_MODE"

#: Yerel istekler hızlı ama opus + thinking uzun sürebiliyor.
TIMEOUT_SECONDS = 300.0
# ---------------------------------------------------------------------------

StructuredMode = Literal["auto", "parse", "tool"]

#: Zorunlu tool-use yolunda modele verilen aracın adı.
_TOOL_NAME = "kaydet"


class MissingAPIKeyError(RuntimeError):
    """Anahtar tanımlı değilken LLM adımı çağrıldı."""

    def __init__(self) -> None:
        super().__init__(
            f"{API_KEY_ENV} (ya da {FALLBACK_API_KEY_ENV}) tanımlı değil.\n"
            "`.env.example` dosyasını `.env` olarak kopyala ve doldur:\n"
            f"  {BASE_URL_ENV}=http://127.0.0.1:8045   (yerel llm-gateway)\n"
            f"  {API_KEY_ENV}=<llm-gateway/.env içindeki AG_API_KEY>\n"
            "Gateway kullanıyorsan Docker Desktop'ın açık olduğundan da emin ol."
        )


class StructuredOutputError(RuntimeError):
    """Model, beklenen şemaya uyan bir çıktı vermedi."""


def load_dotenv_once() -> None:
    """Depo kökündeki `.env`'i ortama yükler (varsa).

    CLI girişinde bir kez çağrılır. Ortamda zaten tanımlı olan değişkenler
    ezilmez — kabuktan geçici olarak başka bir anahtar vermek mümkün kalsın.
    """
    from dotenv import load_dotenv

    from cv_updater.paths import REPO_ROOT

    load_dotenv(REPO_ROOT / ".env", override=False)


def model_name() -> str:
    """Kullanılacak model kimliği."""
    return os.environ.get(MODEL_ENV) or DEFAULT_MODEL


def base_url() -> str | None:
    """Özel endpoint (gateway) ya da None (gerçek Anthropic API)."""
    value = os.environ.get(BASE_URL_ENV, "").strip()
    if not value:
        return None
    # Anthropic SDK yola `/v1` ekler; iki kez eklenirse 404 gelir.
    return value.rstrip("/").removesuffix("/v1")


def api_key() -> str | None:
    """`LLM_API_KEY`, yoksa `ANTHROPIC_API_KEY`."""
    return os.environ.get(API_KEY_ENV) or os.environ.get(FALLBACK_API_KEY_ENV) or None


def has_api_key() -> bool:
    """LLM adımlarının çalışabilmesi için anahtar var mı?"""
    return api_key() is not None


def structured_mode() -> StructuredMode:
    """Yapılandırılmış çıktı stratejisi.

    `auto`: özel bir endpoint varsa (gateway) tool-use, yoksa `parse`.
    """
    raw = os.environ.get(STRUCTURED_MODE_ENV, "auto").strip().lower()
    if raw in ("parse", "tool"):
        return raw  # type: ignore[return-value]
    return "tool" if base_url() else "parse"


def get_client() -> Anthropic:
    """Yapılandırılmış bir Anthropic istemcisi döndürür.

    `anthropic` içe aktarımı da tembeldir; `validate` yolu bu bağımlılığa
    hiç dokunmaz.
    """
    key = api_key()
    if key is None:
        raise MissingAPIKeyError

    from anthropic import Anthropic

    return Anthropic(api_key=key, base_url=base_url(), timeout=TIMEOUT_SECONDS)


def request_kwargs(**overrides: Any) -> dict[str, Any]:
    """Her `messages` çağrısında paylaşılan parametreler.

    Model, düşünme ve effort ayarı tek yerden gelsin diye çağrı yerleri bunu
    kullanır; `overrides` ile tek tek alan ezilebilir.
    """
    kwargs: dict[str, Any] = {
        "model": model_name(),
        "max_tokens": MAX_TOKENS,
        "thinking": THINKING,
        "output_config": OUTPUT_CONFIG,
    }
    kwargs.update(overrides)
    return kwargs


def structured[T: BaseModel](
    client: Anthropic,
    output_format: type[T],
    *,
    system: str,
    messages: list[dict[str, Any]],
    mode: StructuredMode | None = None,
    **overrides: Any,
) -> T:
    """Şemaya uyan bir pydantic nesnesi döndürür.

    Strateji `mode` ile ya da `LLM_STRUCTURED_MODE` ile seçilir; modül
    docstring'i neden iki strateji olduğunu anlatıyor.
    """
    chosen = mode or structured_mode()
    kwargs = request_kwargs(**overrides)

    if chosen == "parse":
        return _via_parse(client, output_format, system=system, messages=messages, **kwargs)
    return _via_tool(client, output_format, system=system, messages=messages, **kwargs)


def _via_parse[T: BaseModel](
    client: Anthropic,
    output_format: type[T],
    *,
    system: str,
    messages: list[dict[str, Any]],
    **kwargs: Any,
) -> T:
    """Anthropic'in yerel yapılandırılmış çıktısı."""
    try:
        response = client.messages.parse(
            output_format=output_format,
            system=system,
            messages=messages,
            **kwargs,
        )
    except ValidationError as exc:
        raise StructuredOutputError(
            "Model şemaya uymayan bir çıktı verdi. Endpoint `output_format` "
            f"desteklemiyor olabilir — {STRUCTURED_MODE_ENV}=tool deneyin.\n{exc}"
        ) from exc

    parsed = response.parsed_output
    if parsed is None:
        raise StructuredOutputError("Model boş bir yapılandırılmış çıktı döndürdü.")
    return parsed


def _via_tool[T: BaseModel](
    client: Anthropic,
    output_format: type[T],
    *,
    system: str,
    messages: list[dict[str, Any]],
    **kwargs: Any,
) -> T:
    """Zorunlu tool-use ile yapılandırılmış çıktı — gateway'in desteklediği yol."""
    tool = {
        "name": _TOOL_NAME,
        "description": (output_format.__doc__ or "Çıkarılan alanları döndür.").strip(),
        "input_schema": output_format.model_json_schema(),
    }

    response = client.messages.create(
        system=system,
        messages=messages,
        tools=[tool],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        **kwargs,
    )

    payload = _first_tool_input(response)
    if payload is None:
        raise StructuredOutputError(
            "Model aracı çağırmadı; yapılandırılmış çıktı alınamadı. "
            f"Dönen blok tipleri: {[block.type for block in response.content]}"
        )

    try:
        return output_format.model_validate(payload)
    except ValidationError as exc:
        raise StructuredOutputError(
            f"Araç girdisi şemaya uymuyor:\n{json.dumps(payload, ensure_ascii=False)[:800]}\n{exc}"
        ) from exc


def _first_tool_input(response: Any) -> dict[str, Any] | None:
    """Yanıttaki ilk `tool_use` bloğunun girdisi.

    Thinking açık modellerde `content[0]` bir düşünme bloğu olabiliyor; sırayı
    varsaymadan aramak gerekiyor.
    """
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input)
    return None
