"""Provider-neutral message types. Each provider converts these to its own wire format."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # tool results only
    tool_call_id: str | None = None
    name: str | None = None
    is_error: bool = False


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __iadd__(self, other: Usage) -> Usage:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        return self


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    usage: Usage
    model: str


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema


def message_to_dict(m: Message) -> dict:
    d = {"role": m.role, "content": m.content}
    if m.tool_calls:
        d["tool_calls"] = [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in m.tool_calls]
    if m.role == "tool":
        d.update(tool_call_id=m.tool_call_id, name=m.name, is_error=m.is_error)
    return d


def message_from_dict(d: dict) -> Message:
    return Message(
        role=d["role"],
        content=d.get("content", ""),
        tool_calls=[ToolCall(**c) for c in d.get("tool_calls", [])],
        tool_call_id=d.get("tool_call_id"),
        name=d.get("name"),
        is_error=d.get("is_error", False),
    )
