"""Async, dependency-free model providers for running inside the browser (Pyodide).

The official SDKs don't run in WebAssembly, so these call the REST APIs with fetch().
They reuse the same wire-format converters as the server-side providers. The API key is
supplied by the user and only ever lives on their own device.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from ..types import LLMResponse, Message, ToolCall, ToolSpec, Usage
from .anthropic_provider import AnthropicProvider
from .azure_provider import AzureOpenAIProvider

FetchJson = Callable[[str, dict, dict], Awaitable[tuple[int, dict]]]


class ProviderError(Exception):
    """A model call failed (bad key, quota, network). Shown to the user, not the model."""


async def pyodide_fetch_json(url: str, headers: dict, body: dict) -> tuple[int, dict]:
    from pyodide.http import pyfetch  # only importable inside Pyodide

    resp = await pyfetch(url, method="POST", headers=headers, body=json.dumps(body))
    try:
        data = await resp.json()
    except Exception:
        data = {"error": {"message": await resp.string()}}
    return resp.status, data


def _error_message(status: int, data: dict) -> str:
    err = data.get("error") if isinstance(data, dict) else None
    msg = err.get("message") if isinstance(err, dict) else str(err or data)
    if status in (401, 403):
        return f"The API key was rejected ({status}). Check it in Settings."
    if status == 429:
        return "The model provider is rate limiting or out of credit (429). Try again shortly."
    return f"Model call failed ({status}): {msg}"


class AnthropicBrowserProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-sonnet-5-5", fetch_json: FetchJson | None = None):
        self.api_key = api_key
        self.model = model
        self.max_tokens = 1500
        self._fetch = fetch_json or pyodide_fetch_json

    async def acomplete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "tools": [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools],
            "messages": AnthropicProvider.to_wire(messages),
        }
        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            # Required for calls made directly from a web page with the user's own key.
            "anthropic-dangerous-direct-browser-access": "true",
        }
        status, data = await self._fetch("https://api.anthropic.com/v1/messages", headers, body)
        if status >= 400:
            raise ProviderError(_error_message(status, data))
        blocks = data.get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        calls = [
            ToolCall(id=b["id"], name=b["name"], arguments=b.get("input") or {})
            for b in blocks
            if b.get("type") == "tool_use"
        ]
        u = data.get("usage", {})
        return LLMResponse(text, calls, Usage(u.get("input_tokens", 0), u.get("output_tokens", 0)), self.model)


class AzureBrowserProvider:
    name = "azure"

    def __init__(
        self,
        api_key: str,
        endpoint: str,
        deployment: str,
        api_version: str = "2024-10-21",
        fetch_json: FetchJson | None = None,
    ):
        self.api_key = api_key
        self.url = f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
        self.model = deployment
        self.max_tokens = 1500
        self._fetch = fetch_json or pyodide_fetch_json

    async def acomplete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        body = {
            "max_tokens": self.max_tokens,
            "messages": AzureOpenAIProvider.to_wire(system, messages),
            "tools": [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in tools
            ],
        }
        status, data = await self._fetch(self.url, {"content-type": "application/json", "api-key": self.api_key}, body)
        if status >= 400:
            raise ProviderError(_error_message(status, data))
        msg = data["choices"][0]["message"]
        calls = []
        for c in msg.get("tool_calls") or []:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_unparseable": c["function"].get("arguments")}
            calls.append(ToolCall(id=c["id"], name=c["function"]["name"], arguments=args))
        u = data.get("usage") or {}
        usage = Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
        return LLMResponse(msg.get("content") or "", calls, usage, self.model)
