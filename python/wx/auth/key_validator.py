"""WUXICODE — API key validation and model fetching."""

from __future__ import annotations

from typing import Any

from wx.providers.registry import get_provider, PROVIDER_REGISTRY
from wx.providers.base import ModelInfo
from wx.auth.provider_store import get_provider_store


async def validate_key(provider_name: str, api_key: str) -> tuple[bool, str | None]:
    """Validate API key for a provider.
    
    Returns:
        (success: bool, error_message: str | None)
    """
    provider = get_provider(provider_name)
    if not provider:
        return False, f"Unknown provider: {provider_name}"

    if not provider.requires_key and not api_key:
        return True, None

    if not api_key:
        return False, "API key is required"

    try:
        valid = await provider.validate_key(api_key)
        if valid:
            return True, None
        return False, "Invalid API key or authentication failed"
    except Exception as e:
        return False, f"Validation error: {e}"


async def fetch_and_store_models(provider_name: str, api_key: str) -> tuple[bool, list, str | None]:
    """Fetch models from provider and store in cache.
    
    Returns:
        (success: bool, models: list[ModelInfo], error: str | None)
    """
    provider = get_provider(provider_name)
    if not provider:
        return False, [], f"Unknown provider: {provider_name}"

    try:
        models = await provider.fetch_models(api_key)
        if models:
            # Store in provider store
            store = get_provider_store()
            store.set_models(provider_name, models)
            return True, models, None
        return False, [], "No models returned"
    except Exception as e:
        return False, [], f"Fetch error: {e}"


async def add_provider(name: str, api_key: str) -> tuple[bool, str | None]:
    """Add a new provider with validation and model fetching.
    
    Returns:
        (success: bool, error_message: str | None)
    """
    name = name.lower()
    provider = get_provider(name)
    if not provider:
        return False, f"Unknown provider: {name}. Available: {', '.join(PROVIDER_REGISTRY.keys())}"

    # Validate key
    valid, error = await validate_key(name, api_key)
    if not valid:
        return False, error or "Invalid API key"

    # Get base URL
    provider_obj = get_provider(name)
    base_url = provider.base_url if provider else ""

    # Store key
    store = get_provider_store()
    store.set_key(name, api_key, base_url)

    # Fetch and store models
    success, models, error = await fetch_and_store_models(name, api_key)
    if not success:
        # Rollback key on fetch failure
        get_provider_store().remove_key(name)
        return False, error or "Failed to fetch models"

    return True, None


async def refresh_provider_models(name: str) -> tuple[bool, str | None]:
    """Refresh models for an existing provider."""
    name = name.lower()
    store = get_provider_store()
    api_key = store.get_key(name)
    if not api_key:
        return False, "No API key stored for provider"

    success, models, error = await fetch_and_store_models(name, api_key)
    return success, error


def mask_key(api_key: str) -> str:
    """Mask API key for display (first 6, last 4)."""
    if not api_key:
        return ""
    if len(api_key) <= 10:
        return "*" * len(api_key)
    return api_key[:6] + "*" * (len(api_key) - 10) + api_key[-4:]


def list_configured_providers() -> list[dict]:
    """List all providers with their configuration status."""
    store = get_provider_store()
    providers = []
    for name in sorted(PROVIDER_REGISTRY.keys()):
        provider = PROVIDER_REGISTRY[name]
        stored = store._data.get(name)  # Access internal for status
        has_key = stored is not None and bool(stored.api_key)
        key_display = mask_key(stored.api_key) if stored and stored.api_key else ""
        providers.append({
            "name": name,
            "display_name": provider.display_name,
            "has_key": has_key,
            "enabled": stored.enabled if stored else False,
            "model_count": len(stored.models) if stored else 0,
            "last_sync": stored.last_sync if stored else None,
            "key_display": key_display,
            "base_url": provider.base_url,
            "key_help_url": provider.key_help_url,
            "requires_key": provider.requires_key,
        })
    return providers