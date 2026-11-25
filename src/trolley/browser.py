"""Bridge for running Trolley in the browser with Pyodide (no server: the API key stays on the device).

One demo session lives in memory; reloading the page starts a fresh demo.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from .agent.agent import Session, TrolleyAgent
from .agent.providers.browser_http import AnthropicBrowserProvider, AzureBrowserProvider, ProviderError
from .agent.providers.offline import OfflineProvider
from .catalogue import Catalogue
from .search import SearchIndex

_cat: Catalogue | None = None
_index: SearchIndex | None = None
_session: Session | None = None


def _provider(settings: dict):
    kind = (settings or {}).get("provider", "offline")
    key = (settings or {}).get("api_key", "").strip()
    if kind == "anthropic" and key:
        return AnthropicBrowserProvider(key, settings.get("model") or "claude-sonnet-5-5")
    if kind == "azure" and key and settings.get("endpoint") and settings.get("deployment"):
        return AzureBrowserProvider(
            key, settings["endpoint"], settings["deployment"], settings.get("api_version") or "2024-10-21"
        )
    return OfflineProvider()


def init(data_dir: str) -> str:
    global _cat, _index, _session
    from pathlib import Path

    d = Path(data_dir)
    _cat = Catalogue.from_files(d / "catalogue.json.gz", d / "prices.json")
    _index = SearchIndex(_cat)
    _session = Session(_cat, _index)
    return json.dumps({"state": _session.store.snapshot(), "meta": _cat.meta, "products": len(_cat.products)})


def reset() -> str:
    global _session
    _session = Session(_cat, _index)
    return json.dumps({"state": _session.store.snapshot()})


def products(codes_json: str) -> str:
    """Product cards for codes the agent cited, so the UI can render them."""
    codes = json.loads(codes_json or "[]")
    return json.dumps({c: _cat.by_code[c].card() for c in codes if _cat and c in _cat.by_code})


async def chat(message: str, settings_json: str = "{}") -> str:
    agent = TrolleyAgent(_provider(json.loads(settings_json or "{}")))
    try:
        turn = await agent.arun(_session, message)
    except ProviderError as e:
        return json.dumps({"error": str(e)})
    return json.dumps(
        {
            "reply": turn.reply,
            "tool_events": [asdict(e) for e in turn.tool_events],
            "usage": {"input_tokens": turn.usage.input_tokens, "output_tokens": turn.usage.output_tokens},
            "latency_ms": turn.latency_ms,
            "flags": turn.flags,
            "retried": turn.retried,
            "trace": turn.trace,
            "model": turn.model,
            "state": _session.store.snapshot(),
        },
        default=str,
    )
