"""Azure AI Foundry / Azure OpenAI provider (chat completions with function calling)."""

from __future__ import annotations

import json
import os

from ..types import LLMResponse, Message, ToolCall, ToolSpec, Usage


class AzureOpenAIProvider:
    name = "azure"

    def __init__(self, deployment: str | None = None, client=None, max_tokens: int = 1500):
        if client is None:
            try:
                from openai import AzureOpenAI
            except ImportError as e:  # pragma: no cover - depends on optional extra
                raise RuntimeError("Install the azure extra: pip install 'trolley[azure]'") from e
            client = AzureOpenAI(
                azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                api_key=os.environ["AZURE_OPENAI_API_KEY"],
                api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            )
        self.client = client
        self.model = deployment or os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
        self.max_tokens = max_tokens

    @staticmethod
    def to_wire(system: str, messages: list[Message]) -> list[dict]:
        wire: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m.role == "user":
                wire.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                msg: dict = {"role": "assistant", "content": m.content or None}
                if m.tool_calls:
                    msg["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in m.tool_calls
                    ]
                wire.append(msg)
            else:
                wire.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        return wire

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        resp = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=self.to_wire(system, messages),
            tools=[
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in tools
            ],
        )
        choice = resp.choices[0].message
        calls = []
        for c in choice.tool_calls or []:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_unparseable": c.function.arguments}  # surfaced to the model as a validation error
            calls.append(ToolCall(id=c.id, name=c.function.name, arguments=args))
        usage = Usage(resp.usage.prompt_tokens, resp.usage.completion_tokens) if resp.usage else Usage()
        return LLMResponse(text=choice.content or "", tool_calls=calls, usage=usage, model=self.model)
