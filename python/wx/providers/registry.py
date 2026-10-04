"""WUXICODE — Provider registry.

Pre-defined provider configurations and registry.
"""

from __future__ import annotations

from wx.providers.base import Provider, ProviderConfig, OpenAICompatibleProvider, AnthropicProvider, DeepSeekProvider


PROVIDER_REGISTRY: dict[str, Provider] = {}

# ---- OpenRouter ----
_openrouter_config = ProviderConfig(
    name="openrouter",
    display_name="OpenRouter",
    base_url="https://openrouter.ai/api/v1",
    key_env_var="OPENROUTER_API_KEY",
    key_help_url="https://openrouter.ai/keys",
    requires_key=True,
)
PROVIDER_REGISTRY["openrouter"] = OpenAICompatibleProvider(_openrouter_config)

# ---- Groq ----
_groq_config = ProviderConfig(
    name="groq",
    display_name="Groq",
    base_url="https://api.groq.com/openai/v1",
    key_env_var="GROQ_API_KEY",
    key_help_url="https://console.groq.com/keys",
    requires_key=True,
)
PROVIDER_REGISTRY["groq"] = OpenAICompatibleProvider(_groq_config)

# ---- OpenAI ----
_openai_config = ProviderConfig(
    name="openai",
    display_name="OpenAI",
    base_url="https://api.openai.com/v1",
    key_env_var="OPENAI_API_KEY",
    key_help_url="https://platform.openai.com/api-keys",
    requires_key=True,
)
PROVIDER_REGISTRY["openai"] = OpenAICompatibleProvider(_openai_config)

# ---- Anthropic ----
_anthropic_config = ProviderConfig(
    name="anthropic",
    display_name="Anthropic",
    base_url="https://api.anthropic.com",
    key_env_var="ANTHROPIC_API_KEY",
    key_help_url="https://console.anthropic.com/settings/keys",
    requires_key=True,
)
PROVIDER_REGISTRY["anthropic"] = AnthropicProvider(_anthropic_config)

# ---- DeepSeek ----
_deepseek_config = ProviderConfig(
    name="deepseek",
    display_name="DeepSeek",
    base_url="https://api.deepseek.com/v1",
    key_env_var="DEEPSEEK_API_KEY",
    key_help_url="https://platform.deepseek.com/api_keys",
    requires_key=True,
)
PROVIDER_REGISTRY["deepseek"] = DeepSeekProvider(_deepseek_config)


# ---- Registry functions ----

def get_provider(name: str) -> Provider | None:
    """Get provider by name."""
    return PROVIDER_REGISTRY.get(name.lower())


def register_provider(name: str, provider: Provider) -> None:
    """Register a custom provider."""
    PROVIDER_REGISTRY[name.lower()] = provider


def list_providers() -> list[str]:
    """List all registered provider names."""
    return list(PROVIDER_REGISTRY.keys())


def get_provider_config(name: str) -> dict | None:
    """Get provider configuration as dict for UI display."""
    provider = get_provider(name)
    if not provider:
        return None
    return {
        "name": provider.name,
        "display_name": provider.display_name,
        "base_url": provider.base_url,
        "key_env_var": provider.key_env_var,
        "key_help_url": provider.key_help_url,
        "requires_key": provider.requires_key,
    }