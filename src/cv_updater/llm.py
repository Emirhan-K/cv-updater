"""Anthropic istemcisi — model/effort ayarının yaşadığı **tek** yer.

Bu modülü içe aktarmak API anahtarı gerektirmez; anahtar yalnızca `get_client()`
çağrıldığında aranır. Böylece `--help` ve `validate` anahtarsız çalışır
(issue 01 kabul kriteri).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - yalnızca tip denetimi için
    from anthropic import Anthropic

# --- Model yapılandırması: burayı değiştir, başka hiçbir yeri ---------------
MODEL = "claude-opus-5"
THINKING: dict[str, Any] = {"type": "adaptive"}
OUTPUT_CONFIG: dict[str, Any] = {"effort": "high"}
MAX_TOKENS = 16000
API_KEY_ENV = "ANTHROPIC_API_KEY"
# ---------------------------------------------------------------------------


class MissingAPIKeyError(RuntimeError):
    """`ANTHROPIC_API_KEY` tanımlı değilken LLM adımı çağrıldı."""

    def __init__(self) -> None:
        super().__init__(
            f"{API_KEY_ENV} tanımlı değil. `.env.example` dosyasını `.env` olarak "
            "kopyalayıp anahtarını yaz, ya da ortam değişkenini dışa aktar."
        )


def has_api_key() -> bool:
    """LLM adımlarının çalışabilmesi için anahtar var mı?"""
    return bool(os.environ.get(API_KEY_ENV))


def get_client() -> Anthropic:
    """Yapılandırılmış bir Anthropic istemcisi döndürür.

    `anthropic` içe aktarımı da tembeldir; `validate` yolu bu bağımlılığa
    hiç dokunmaz.
    """
    if not has_api_key():
        raise MissingAPIKeyError

    from anthropic import Anthropic

    return Anthropic()


def request_kwargs(**overrides: Any) -> dict[str, Any]:
    """Her `messages` çağrısında paylaşılan parametreler.

    Model, düşünme ve effort ayarı tek yerden gelsin diye çağrı yerleri bunu
    kullanır; `overrides` ile tek tek alan ezilebilir.
    """
    kwargs: dict[str, Any] = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "thinking": THINKING,
        "output_config": OUTPUT_CONFIG,
    }
    kwargs.update(overrides)
    return kwargs
