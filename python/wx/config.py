"""WUXICODE — configuration loader.

Loads config.json with environment variable overrides. Environment variables
take precedence over file config, which takes precedence over defaults.

Config file locations (in order of precedence):
  1. --config-path argument (if passed to load_config)
  2. WX_CONFIG_PATH environment variable
  3. ~/.wuxicode/config.json (or %USERPROFILE%\\.wuxicode\\config.json on Windows)
  4. ./config.json (package default, for backwards compat)

Env var mapping:
  WX_LLM_PROVIDER        -> provider
  WX_LLM_MODEL           -> model
  WX_LLM_BASE_URL        -> base_url
  WX_LLM_API_KEY         -> api_key (priority 2)
  WX_LLM_TIMEOUT         -> timeout
  WX_LLM_CONNECT_TIMEOUT -> connect_timeout
  WX_LLM_MAX_RETRIES     -> max_retries
  WX_LLM_FALLBACK_CHAIN  -> fallback_chain (JSON array)

API key priority (highest first):
  1. Explicit --key argument (handled in cli.py)
  2. WX_LLM_API_KEY environment variable
  3. OPENAI_API_KEY environment variable
  4. config.json api_key field
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["Config", "load_config", "save_config", "get_user_config_path", "find_config_file", "validate_models"]

# Provider-specific defaults
PROVIDER_DEFAULTS = {
    "spacebunny": {
        "base_url": "https://opencode.ai/zen/v1",
        "model": "space-bunny-free",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "qwen/qwen3.8-27b",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "custom": {
        "base_url": None,
        "model": None,
    },
}

DEFAULT_PROVIDER = "spacebunny"

# Providers that serve requests without a credential. The opencode zen route
# accepts anonymous requests; demanding a key for it would block the CLI
# entirely even though the endpoint is ready.
KEYLESS_PROVIDERS = frozenset({"spacebunny"})


def provider_needs_key(provider: str) -> bool:
    """Whether this provider refuses to answer without an API key."""
    return provider not in KEYLESS_PROVIDERS

# No default fallback chain. Every other model on the opencode zen catalog
# answers 401/403 without a funded key — verified by request, not by reading
# /models. Listing them here would only manufacture retry noise on the primary.
DEFAULT_FALLBACK_CHAIN: list[str] = []

# Models confirmed to answer on the opencode zen route without a key.
KNOWN_MODELS = frozenset([
    "space-bunny-free",
])

# Groq remains reachable for anyone who opts into it explicitly.
GROQ_MODELS = frozenset([
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "allam-2-7b",
])


def _catalog_for(provider: str) -> frozenset[str]:
    return GROQ_MODELS if provider == "groq" else KNOWN_MODELS


def validate_models(model: str, fallback_chain: list[str], provider: str = DEFAULT_PROVIDER) -> list[str]:
    """Validate model and fallback chain against the provider's catalog.
    Returns list of warnings (empty if all valid)."""
    catalog = _catalog_for(provider)
    warnings = []
    if model not in catalog:
        warnings.append(f"model '{model}' not in {provider} catalog")
    for fb in fallback_chain:
        if fb not in catalog:
            warnings.append(f"fallback '{fb}' not in {provider} catalog")
    return warnings


def _debug_log(msg: str) -> None:
    """Print debug message if WX_DEBUG=1."""
    if os.environ.get("WX_DEBUG") == "1":
        print(f"[debug] {msg}", file=os.sys.stderr)


def get_user_config_path() -> Path:
    """Get the user config directory path (~/.wuxicode or %USERPROFILE%\\.wuxicode)."""
    # On Windows, use USERPROFILE; on Unix, use HOME
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if home:
        return Path(home) / ".wuxicode" / "config.json"
    # Fallback to current directory
    return Path.cwd() / ".wuxicode" / "config.json"


def find_config_file(explicit_path: Path | None = None) -> Path | None:
    """Find config file in order of precedence."""
    # 1. Explicit path passed to load_config
    if explicit_path and explicit_path.exists():
        return explicit_path

    # 2. WX_CONFIG_PATH environment variable
    env_path = os.environ.get("WX_CONFIG_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    # 3. User config directory
    user_config = get_user_config_path()
    if user_config.exists():
        return user_config

    # 4. Package default (backwards compat)
    package_config = Path(__file__).parent / "config.json"
    if package_config.exists():
        return package_config

    return None


def _get_env(key: str, default: Any = None) -> Any:
    """Get environment variable with type conversion."""
    value = os.environ.get(key)
    if value is None:
        return default
    # Try to parse as JSON for numbers/booleans/arrays
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _resolve(env_key: str, file_config: dict[str, Any], field: str, default: Any = None) -> Any:
    """Environment beats file beats default.

    Every setting goes through here so the precedence is stated once. It was
    written inline per field before, and the fields disagreed: only api_key
    honoured the documented order, while provider/model/base_url/timeout read
    the file first. The visible symptom was WX_LLM_BASE_URL -- the documented
    way to point at a local Ollama -- being silently ignored whenever a
    config.json happened to carry a base_url.

    An empty environment variable counts as unset. `WX_LLM_MODEL=""` means
    "I did not set this", not "set it to the empty string", and falling through
    matches how shells treat an unset export.
    """
    env = _get_env(env_key)
    if env not in (None, ""):
        return env
    value = file_config.get(field)
    if value not in (None, ""):
        return value
    return default


def load_config(path: Path | None = None) -> Config:
    """Load configuration from file and environment."""
    config_path = find_config_file(path)

    # Start with file config
    file_config: dict[str, Any] = {}
    if config_path:
        try:
            with config_path.open("r", encoding="utf-8") as f:
                file_config = json.load(f)
            _debug_log(f"config loaded from: {config_path}")
        except (json.JSONDecodeError, OSError) as exc:
            _debug_log(f"config load failed ({config_path}): {exc}")
    else:
        _debug_log("no config file found")

    # Provider first: its defaults are the floor everything else sits on.
    provider = _resolve("WX_LLM_PROVIDER", file_config, "provider", DEFAULT_PROVIDER)
    provider_defaults = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS[DEFAULT_PROVIDER])

    # API key keeps its own longer chain: WX_LLM_API_KEY > OPENAI_API_KEY >
    # config.json. _resolve only knows about one env name.
    env_api_key = _get_env("WX_LLM_API_KEY") or _get_env("OPENAI_API_KEY")
    file_api_key = file_config.get("api_key")
    if env_api_key:
        api_key = env_api_key
        _debug_log(
            "api_key loaded from: WX_LLM_API_KEY"
            if _get_env("WX_LLM_API_KEY")
            else "api_key loaded from: OPENAI_API_KEY"
        )
    elif file_api_key:
        api_key = file_api_key
        _debug_log("api_key loaded from: config.json")
    else:
        api_key = None
        _debug_log("api_key: NOT FOUND")

    model = _resolve("WX_LLM_MODEL", file_config, "model", provider_defaults.get("model"))

    # An explicit empty list is a real setting -- "no fallback, single model" --
    # not a missing value, so this cannot go through `or`. An empty *env* string
    # is still unset, matching _resolve.
    file_fallback = file_config.get("fallback_chain")
    if isinstance(file_fallback, list):
        fallback_chain = list(file_fallback)
    else:
        env_fallback = _get_env("WX_LLM_FALLBACK_CHAIN")
        fallback_chain = list(env_fallback) if isinstance(env_fallback, list) else list(DEFAULT_FALLBACK_CHAIN)

    # Validate models
    for warning in validate_models(model, fallback_chain, provider):
        _debug_log(f"model validation warning: {warning}")

    config = Config(
        provider=provider,
        model=model,
        base_url=_resolve("WX_LLM_BASE_URL", file_config, "base_url", provider_defaults.get("base_url")),
        api_key=api_key,
        timeout=_resolve("WX_LLM_TIMEOUT", file_config, "timeout", 60.0),
        connect_timeout=_resolve("WX_LLM_CONNECT_TIMEOUT", file_config, "connect_timeout", 10.0),
        max_retries=_resolve("WX_LLM_MAX_RETRIES", file_config, "max_retries", 3),
        fallback_chain=fallback_chain,
        model_presets=file_config.get("model_presets"),
        ui=file_config.get("ui"),
        workspace=file_config.get("workspace") or os.getcwd(),
    )

    return config


def save_config(config: Config, path: Path | None = None) -> None:
    """Save configuration to file."""
    config_path = path or get_user_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "provider": config.provider,
        "model": config.model,
        "base_url": config.base_url,
        "api_key": config.api_key,
        "timeout": config.timeout,
        "connect_timeout": config.connect_timeout,
        "max_retries": config.max_retries,
        "fallback_chain": config.fallback_chain,
        "model_presets": config.model_presets,
        "ui": config.ui,
        "workspace": config.workspace,
    }
    with config_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    _debug_log(f"config saved to: {config_path}")


@dataclass(frozen=True, slots=True)
class Config:
    provider: str
    model: str
    base_url: str | None
    api_key: str | None
    timeout: float
    connect_timeout: float
    max_retries: int
    fallback_chain: list[str]
    model_presets: list[dict] | None = None
    ui: dict | None = None
    workspace: str | None = None

    @property
    def effective_base_url(self) -> str:
        """Resolved base URL with provider default fallback."""
        if self.base_url:
            return self.base_url.rstrip("/")
        return PROVIDER_DEFAULTS.get(self.provider, {}).get(
            "base_url", PROVIDER_DEFAULTS[DEFAULT_PROVIDER]["base_url"]
        )

    @property
    def effective_model(self) -> str:
        """Resolved model with provider default fallback."""
        if self.model:
            return self.model
        return PROVIDER_DEFAULTS.get(self.provider, {}).get(
            "model", PROVIDER_DEFAULTS[DEFAULT_PROVIDER]["model"]
        )

    @property
    def effective_fallback_chain(self) -> list[str]:
        """Resolved fallback chain. An explicit empty list stays empty."""
        return list(self.fallback_chain)
