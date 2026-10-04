"""WUXICODE — Provider store.

Secure storage for provider API keys and model lists.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from wx.providers.registry import get_provider, PROVIDER_REGISTRY
from wx.providers.base import ModelInfo


@dataclass
class StoredProvider:
    """Stored provider data with API key and cached models."""
    api_key: str
    base_url: str
    models: list[dict]  # serialized ModelInfo
    last_sync: str | None = None
    enabled: bool = True


class ProviderStore:
    """Manages provider credentials and model caches."""

    def __init__(self, path: Path | None = None):
        self.path = path or self._get_default_path()
        self._data: dict[str, StoredProvider] = {}
        self._load()

    @staticmethod
    def _get_default_path() -> Path:
        """Get default storage path."""
        home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
        if home:
            return Path(home) / ".wuxicode" / "providers.json"
        return Path.cwd() / ".wuxicode" / "providers.json"

    def _load(self) -> None:
        """Load providers from file."""
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            for name, data in data.get("providers", {}).items():
                self._data[name] = StoredProvider(
                    api_key=data.get("api_key", ""),
                    base_url=data.get("base_url", ""),
                    models=data.get("models", []),
                    last_sync=data.get("last_sync"),
                    enabled=data.get("enabled", True),
                )
        except (json.JSONDecodeError, OSError):
            # Corrupted file - start fresh
            pass

    def _save(self) -> None:
        """Save providers to file with restricted permissions."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "providers": {
                name: asdict(provider) for name, provider in self._data.items()
            }
        }
        # Write to temp file first, then atomic rename
        temp_path = self.path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as f:
            json.dump({"providers": {k: asdict(v) for k, v in self._data.items()}}, f, indent=2)
        # Set restrictive permissions (owner read/write only)
        try:
            os.chmod(temp_path, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
        temp_path.replace(self.path)

    def has_key(self, name: str) -> bool:
        """Check if provider has API key stored."""
        provider = self._data.get(name.lower())
        return provider is not None and bool(provider.api_key)

    def get_key(self, name: str) -> str | None:
        """Get API key for provider."""
        provider = self._data.get(name.lower())
        return provider.api_key if provider else None

    def set_key(self, name: str, api_key: str, base_url: str) -> None:
        """Store API key for provider."""
        name = name.lower()
        if name not in self._data:
            self._data[name] = StoredProvider(api_key="", base_url=base_url, models=[])
        self._data[name].api_key = api_key
        self._data[name].base_url = base_url
        self._data[name].enabled = True
        self._save()

    def remove_key(self, name: str) -> bool:
        """Remove stored key for provider."""
        name = name.lower()
        if name in self._data:
            del self._data[name]
            self._save()
            return True
        return False

    def set_models(self, name: str, models: list[ModelInfo]) -> None:
        """Store fetched models for provider."""
        name = name.lower()
        if name not in self._data:
            provider = PROVIDER_REGISTRY.get(name)
            base_url = provider.base_url if provider else ""
            self._data[name] = StoredProvider(api_key="", base_url=base_url, models=[])

        self._data[name].models = [
            {
                "id": m.id,
                "name": m.name,
                "description": m.description,
                "tier": m.tier,
                "context_length": m.context_length,
                "pricing": m.pricing,
            }
            for m in models
        ]
        self._data[name].last_sync = datetime.utcnow().isoformat() + "Z"
        self._save()

    def get_models(self, name: str) -> list[ModelInfo]:
        """Get cached models for provider."""
        name = name.lower()
        provider = self._data.get(name)
        if not provider:
            return []
        models = []
        for m in provider.models:
            models.append(ModelInfo(
                id=m["id"],
                name=m.get("name", m["id"]),
                description=m.get("description"),
                tier=m.get("tier", "free"),
                context_length=m.get("context_length"),
                pricing=m.get("pricing"),
            ))
        return models

    def is_enabled(self, name: str) -> bool:
        """Check if provider is enabled."""
        name = name.lower()
        provider = self._data.get(name)
        return provider is not None and provider.enabled

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Enable/disable provider."""
        name = name.lower()
        if name in self._data:
            self._data[name].enabled = enabled
            self._save()
            return True
        return False

    def list_providers(self) -> list[dict]:
        """List all stored providers with status."""
        result = []
        for name in PROVIDER_REGISTRY:
            stored = self._data.get(name)
            provider = PROVIDER_REGISTRY.get(name)
            if not provider:
                continue
            result.append({
                "name": name,
                "display_name": provider.display_name,
                "has_key": stored is not None and bool(stored.api_key),
                "enabled": stored.enabled if stored else False,
                "model_count": len(stored.models) if stored else 0,
                "last_sync": stored.last_sync if stored else None,
                "base_url": provider.base_url,
            })
        return result


# Global instance
_store: ProviderStore | None = None


def get_provider_store() -> ProviderStore:
    """Get global provider store instance."""
    global _store
    if _store is None:
        _store = ProviderStore()
    return _store


# For testing/override
def set_provider_store(store: ProviderStore) -> None:
    """Set custom provider store (for testing)."""
    global _store
    _store = store