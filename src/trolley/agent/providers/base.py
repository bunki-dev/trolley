from __future__ import annotations

import os
from typing import Protocol

from ..types import LLMResponse, Message, ToolSpec


class LLMProvider(Protocol):
    """One interface for every model backend, so the agent loop never changes."""

    name: str
    model: str

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse: ...


def get_provider(name: str | None = None) -> LLMProvider:
    """Pick a provider from LLM_PROVIDER (offline | anthropic | azure)."""
    name = (name or os.getenv("LLM_PROVIDER") or "offline").lower()
    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    if name in ("azure", "azure_openai", "foundry"):
        from .azure_provider import AzureOpenAIProvider

        return AzureOpenAIProvider()
    if name == "offline":
        from .offline import OfflineProvider

        return OfflineProvider()
    raise ValueError(f"Unknown LLM_PROVIDER '{name}'. Use offline, anthropic or azure.")
