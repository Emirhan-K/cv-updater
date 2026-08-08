"""Paylaşılan fixture'lar. Hiçbir test ağa çıkmaz — bu dosya bunu zorluyor."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: LLM yapılandırmasını taşıyan ortam değişkenleri. Testler bunları görmemeli:
#: geliştiricinin `.env`'i sızarsa test süiti sessizce ağa çıkar ve yavaşlar.
_LLM_ENV = (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_STRUCTURED_MODE",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
)


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ağı ve `.env` yüklemesini kapatır.

    İki katmanlı: (1) anahtarlar ortamdan silinir ve `.env` okunmaz, (2) soket
    bağlantısı komple bloklanır. İkincisi güvenlik ağı — birinci katmanı atlayan
    bir kod yolu eklendiğinde test yavaşlayarak değil, hata vererek uyarsın.
    """
    for variable in _LLM_ENV:
        monkeypatch.delenv(variable, raising=False)

    # `cli` bu ismi kendi ad alanına aldı; iki referansı da kapatmak gerekiyor.
    monkeypatch.setattr("cv_updater.llm.load_dotenv_once", lambda: None)
    monkeypatch.setattr("cv_updater.cli.load_dotenv_once", lambda: None)

    def _blocked(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError(
            "Test ağa çıkmaya çalıştı. LLM adımları sahte istemciyle test edilir "
            "(bkz. tests/test_analyze_job.py)."
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)


@pytest.fixture(autouse=True)
def _isolated_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Başvuru arşivini (issue 12) her test için geçici bir dizine yönlendirir.

    `_offline` ile aynı sınıftan bir güvenlik ağı ve aynı sebeple var: `tailor`
    artık her çalıştırmada bir başvuru kaydı açıyor, ve `test_cli_tailor.py`
    boru hattını gerçekten çalıştırıyor. Bu fixture eklenmeden önce süit
    geliştiricinin **gerçek `applications/` dizinine** 34 sahte kayıt yazdı.

    Testler kendi `root=` argümanlarını vermeye devam edebilir; bu yalnızca
    kimsenin unutmadığından emin oluyor.
    """
    archive = tmp_path / "applications"
    monkeypatch.setattr("cv_updater.paths.APPLICATIONS_DIR", archive)
    monkeypatch.setattr("cv_updater.steps.applications.APPLICATIONS_DIR", archive)
    return archive


@pytest.fixture(scope="session")
def rxresume_schema() -> dict[str, Any]:
    """Pakete gömülü şema anlık görüntüsü — `validate` de çalışma zamanında bunu okur."""
    from cv_updater.schema import rxresume_schema as load

    return load()


@pytest.fixture(scope="session")
def master_example_path() -> Path:
    return REPO_ROOT / "data" / "master.example.json"


@pytest.fixture(scope="session")
def master_example_raw(master_example_path: Path) -> str:
    return master_example_path.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def tags_yaml_path() -> Path:
    return REPO_ROOT / "data" / "tags.yaml"
