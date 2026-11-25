"""Anthropic Messages API provider (Claude)."""

from __future__ import annotations

import json
import os

from ..types import LLMResponse, Message, ToolCall, ToolSpec, Usage


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str | None = None, max_tokens: int = 1500, client=None):
        if client is None:
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover - depends on optional extra
                raise RuntimeError("Install the anthropic extra: pip install 'trolley[anthropic]'") from e
            client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.client = client
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
        self.max_tokens = max_tokens

    @staticmethod
    def to_wire(messages: list[Message]) -> list[dict]:
        wire: list[dict] = []
        for m in messages:
            if m.role == "user":
                wire.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                blocks: list[dict] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in m.tool_calls]
                wire.append({"role": "assistant", "content": blocks})
            else:  # tool result: Claude expects these inside a user turn, grouped
                block = {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": m.content,
                    "is_error": m.is_error,
                }
                if wire and wire[-1]["role"] == "user" and isinstance(wire[-1]["content"], list):
                    wire[-1]["content"].append(block)
                else:
                    wire.append({"role": "user", "content": [block]})
        return wire

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            # Cache the long, stable prefix (system prompt + tool schemas) across turns.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=[{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools],
            messages=self.to_wire(messages),
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [
            ToolCall(id=b.id, name=b.name, arguments=b.input if isinstance(b.input, dict) else json.loads(b.input))
            for b in resp.content
            if b.type == "tool_use"
        ]
        usage = Usage(resp.usage.input_tokens, resp.usage.output_tokens)
        return LLMResponse(text=text, tool_calls=calls, usage=usage, model=self.model)
