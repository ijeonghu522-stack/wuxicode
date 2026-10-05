"""WUXICODE — Provider base classes.

Abstract provider interface for model fetching and validation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Provider configuration from registry."""
    name: str
    display_name: str
    base_url: str
    key_env_var: str
    key_help_url: str
    requires_key: bool = True


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """Model information from provider."""
    id: str
    name: str
    description: str | None = None
    tier: str = "free"  # "free" | "paid"
    context_length: int | None = None
    pricing: dict | None = None  # {prompt, completion} per 1M tokens


class Provider(ABC):
    """Abstract base class for LLM providers."""

    def __init__(self, config: ProviderConfig):
        self.config = config

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def display_name(self) -> str:
        return self.config.display_name

    @property
    def base_url(self) -> str:
        return self.config.base_url

    @property
    def key_env_var(self) -> str:
        return self.config.key_env_var

    @property
    def key_help_url(self) -> str:
        return self.config.key_help_url

    @property
    def requires_key(self) -> bool:
        return self.config.requires_key

    # =========================================================================
    # Abstract methods
    # =========================================================================

    @abstractmethod
    async def validate_key(self, api_key: str) -> bool:
        """Validate API key by making a test request.
        Should be lightweight - just check auth works.
        """
        ...

    @abstractmethod
    async def fetch_models(self, api_key: str) -> list[ModelInfo]:
        """Fetch available models from provider.
        Called after successful validation.
        """
        ...

    def _get_headers(self, api_key: str) -> dict[str, str]:
        """Default headers for OpenAI-compatible APIs."""
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _get_client(self, timeout: float = 30.0) -> httpx.AsyncClient:
        """Create HTTP client with appropriate settings."""
        return httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=timeout, write=timeout, pool=timeout),
            follow_redirects=True,
        )


class OpenAICompatibleProvider(Provider):
    """Base class for OpenAI-compatible providers (OpenRouter, Groq, etc.)."""

    def __init__(self, config: ProviderConfig, models_endpoint: str = "/models"):
        super().__init__(config)
        self.models_endpoint = models_endpoint

    async def validate_key(self, api_key: str) -> bool:
        """Validate key by fetching models (lightweight check)."""
        try:
            async with self._get_client(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.base_url}{self.models_endpoint}",
                    headers=self._get_headers(api_key),
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def fetch_models(self, api_key: str) -> list[ModelInfo]:
        """Fetch and parse models from OpenAI-compatible endpoint."""
        async with self._get_client(timeout=30.0) as client:
            resp = await client.get(
                f"{self.base_url}{self.models_endpoint}",
                headers=self._get_headers(api_key),
            )
            resp.raise_for_status()
            data = resp.json()
            return self._parse_models(data)

    def _parse_models(self, data: dict) -> list[ModelInfo]:
        """Parse OpenAI-compatible /models response."""
        models = []
        for item in data.get("data", []):
            model_id = item.get("id", "")
            if not model_id:
                continue

            # Try to extract pricing if available
            pricing = None
            if "pricing" in item:
                pricing = item["pricing"]

            tier = "free"
            if pricing:
                # Pricing values may be strings, convert to float for comparison
                try:
                    prompt_price = float(pricing.get("prompt", 0) or 0)
                    completion_price = float(pricing.get("completion", 0) or 0)
                    if prompt_price > 0 or completion_price > 0:
                        tier = "paid"
                except (ValueError, TypeError):
                    pass
            elif ":free" in model_id.lower():
                tier = "free"
            elif any(x in model_id.lower() for x in ["gpt-4", "claude", "gemini-pro"]):
                tier = "paid"

            models.append(ModelInfo(
                id=model_id,
                name=item.get("name", model_id),
                description=item.get("description"),
                tier=tier,
                context_length=item.get("context_length"),
                pricing=pricing,
            ))
        return models


class AnthropicProvider(Provider):
    """Anthropic provider (different API format)."""

    async def validate_key(self, api_key: str) -> bool:
        try:
            async with self._get_client(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.base_url}/v1/models",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                    },
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def fetch_models(self, api_key: str) -> list[ModelInfo]:
        async with self._get_client(timeout=30.0) as client:
            resp = await client.get(
                f"{self.base_url}/v1/models",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            models = []
            for item in data.get("data", []):
                model_id = item.get("id", "")
                if not model_id:
                    continue
                tier = "paid"  # Anthropic models are typically paid
                if "haiku" in model_id.lower():
                    tier = "free"  # Haiku sometimes free tier
                models.append(ModelInfo(
                    id=model_id,
                    name=item.get("display_name", model_id),
                    tier=tier,
                ))
            return models


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek provider (OpenAI-compatible)."""
    pass