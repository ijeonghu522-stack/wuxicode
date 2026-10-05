"""Config precedence: environment beats file beats defaults.

The module docstring promises that order. It was only true for api_key, so
`WX_LLM_BASE_URL` -- the documented way to point WX at a local Ollama -- lost to
whatever base_url happened to be sitting in config.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

from wx.config import load_config  # noqa: E402

ENV_KEYS = (
    "WX_CONFIG_PATH",
    "WX_LLM_PROVIDER",
    "WX_LLM_MODEL",
    "WX_LLM_BASE_URL",
    "WX_LLM_API_KEY",
    "OPENAI_API_KEY",
    "WX_LLM_TIMEOUT",
    "WX_LLM_CONNECT_TIMEOUT",
    "WX_LLM_MAX_RETRIES",
    "WX_LLM_FALLBACK_CHAIN",
    "NO_COLOR",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    yield


@pytest.fixture()
def cfg_file(tmp_path: Path, monkeypatch) -> Path:
    p = tmp_path / "config.json"
    p.write_text(
        json.dumps(
            {
                "provider": "groq",
                "model": "from-file",
                "base_url": "https://file.example/v1",
                "timeout": 11,
                "max_retries": 9,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WX_CONFIG_PATH", str(p))
    return p


class TestEnvBeatsFile:
    def test_base_url(self, cfg_file: Path, monkeypatch) -> None:
        monkeypatch.setenv("WX_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
        assert load_config().base_url == "http://127.0.0.1:11434/v1"

    def test_model(self, cfg_file: Path, monkeypatch) -> None:
        monkeypatch.setenv("WX_LLM_MODEL", "wx-1")
        assert load_config().model == "wx-1"

    def test_provider(self, cfg_file: Path, monkeypatch) -> None:
        monkeypatch.setenv("WX_LLM_PROVIDER", "openai")
        assert load_config().provider == "openai"

    def test_timeout(self, cfg_file: Path, monkeypatch) -> None:
        monkeypatch.setenv("WX_LLM_TIMEOUT", "99")
        assert load_config().timeout == 99.0

    def test_max_retries(self, cfg_file: Path, monkeypatch) -> None:
        monkeypatch.setenv("WX_LLM_MAX_RETRIES", "1")
        assert load_config().max_retries == 1


class TestFileBeatsDefault:
    def test_all_fields(self, cfg_file: Path) -> None:
        c = load_config()
        assert c.provider == "groq"
        assert c.model == "from-file"
        assert c.base_url == "https://file.example/v1"
        assert c.timeout == 11
        assert c.max_retries == 9


class TestEmptyEnvMeansUnset:
    """`FOO=""` in a shell script is how people unset something, not how they
    ask for an empty value."""

    def test_empty_base_url_falls_back_to_file(self, cfg_file: Path, monkeypatch) -> None:
        monkeypatch.setenv("WX_LLM_BASE_URL", "")
        assert load_config().base_url == "https://file.example/v1"

    def test_empty_model_falls_back_to_file(self, cfg_file: Path, monkeypatch) -> None:
        monkeypatch.setenv("WX_LLM_MODEL", "")
        assert load_config().model == "from-file"


class TestApiKeyChain:
    def test_wx_key_beats_openai_key(self, cfg_file: Path, monkeypatch) -> None:
        monkeypatch.setenv("WX_LLM_API_KEY", "wx-key")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        assert load_config().api_key == "wx-key"

    def test_openai_key_beats_file(self, cfg_file: Path, monkeypatch) -> None:
        cfg_file.write_text(
            json.dumps({**json.loads(cfg_file.read_text()), "api_key": "file-key"}),
            encoding="utf-8",
        )
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        assert load_config().api_key == "openai-key"

    def test_file_key_last(self, cfg_file: Path) -> None:
        cfg_file.write_text(
            json.dumps({**json.loads(cfg_file.read_text()), "api_key": "file-key"}),
            encoding="utf-8",
        )
        assert load_config().api_key == "file-key"


class TestFallbackChain:
    def test_explicit_empty_list_in_file_means_no_fallback(self, cfg_file: Path) -> None:
        cfg_file.write_text(
            json.dumps({**json.loads(cfg_file.read_text()), "fallback_chain": []}),
            encoding="utf-8",
        )
        assert load_config().fallback_chain == []

    def test_env_array_wins_when_file_has_none(self, cfg_file: Path, monkeypatch) -> None:
        monkeypatch.setenv("WX_LLM_FALLBACK_CHAIN", '["a", "b"]')
        assert load_config().fallback_chain == ["a", "b"]


class TestBrokenConfig:
    def test_corrupt_json_falls_back_to_defaults(self, tmp_path: Path, monkeypatch) -> None:
        p = tmp_path / "config.json"
        p.write_text("{not json", encoding="utf-8")
        monkeypatch.setenv("WX_CONFIG_PATH", str(p))
        c = load_config()
        assert c.base_url  # still usable

    def test_missing_file_is_not_fatal(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("WX_CONFIG_PATH", str(tmp_path / "nope.json"))
        assert load_config().base_url
