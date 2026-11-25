"""The agent loop: model -> tools -> model, with input guardrails, a grounding check on every reply
(one self-correction retry), step and tool-call limits, and a trace of every span."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from ..catalogue import Catalogue, load
from ..search import SearchIndex
from ..store import Store
from ..tools import TOOLS, TOOLS_BY_NAME, Ctx, ToolError
from .guardrails import check_input, check_output, scrub
from .prompts import RETRY_NOTE, SYSTEM_PROMPT
from .providers.base import LLMProvider, get_provider
from .tracing import Trace
from .types import Message, ToolSpec, Usage

MAX_STEPS = 8
MAX_TOOL_CALLS = 14
MAX_TOOL_RESULT_CHARS = 7000


@dataclass
class ToolEvent:
    name: str
    arguments: dict
    ok: bool
    duration_ms: float
    error: str | None = None


@dataclass
class Turn:
    reply: str
    tool_events: list[ToolEvent] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    latency_ms: float = 0.0
    flags: list[str] = field(default_factory=list)
    retried: bool = False
    trace: dict = field(default_factory=dict)
    model: str = ""


class Session:
    """One customer conversation: the store state, what the model has been shown, and the history."""

    def __init__(self, catalogue: Catalogue | None = None, index: SearchIndex | None = None):
        cat = catalogue or load()
        self.store = Store(cat)
        self.ctx = Ctx(self.store, index or SearchIndex(cat), set())
        self.history: list[Message] = []


class TrolleyAgent:
    def __init__(self, provider: LLMProvider | None = None):
        self.provider = provider or get_provider()
        self.specs = [ToolSpec(t.name, t.description, t.json_schema()) for t in TOOLS]

    def run(self, session: Session, user_message: str) -> Turn:
        if hasattr(self.provider, "acomplete"):
            return asyncio.run(self.arun(session, user_message))
        coro = self.arun(session, user_message)
        try:
            coro.send(None)
        except StopIteration as done:
            return done.value
        coro.close()
        raise RuntimeError("Provider suspended unexpectedly; give it an acomplete() method.")

    async def _complete(self, history: list[Message]):
        if hasattr(self.provider, "acomplete"):
            return await self.provider.acomplete(SYSTEM_PROMPT, history, self.specs)
        return self.provider.complete(SYSTEM_PROMPT, history, self.specs)

    async def arun(self, session: Session, user_message: str) -> Turn:
        trace = Trace()
        store, history = session.store, session.history
        store.new_turn(user_message)
        turn = Turn("", model=self.provider.model)

        with trace.span("input_guardrail", "guardrail") as s:
            guard = check_input(user_message)
            s.attributes["flags"] = guard.flags
        history.append(Message("user", user_message))
        turn.flags += guard.flags
        if not guard.allowed:
            turn.reply = guard.response or ""
            history.append(Message("assistant", turn.reply))
            return self._finish(trace, turn)

        tool_calls = 0
        retried = False
        for step in range(MAX_STEPS):
            with trace.span("llm", "llm", step=step, model=self.provider.model) as s:
                resp = await self._complete(history)
                s.attributes.update(
                    input_tokens=resp.usage.input_tokens,
                    output_tokens=resp.usage.output_tokens,
                    tool_calls=[c.name for c in resp.tool_calls],
                )
            turn.usage += resp.usage
            history.append(Message("assistant", resp.text, tool_calls=resp.tool_calls))

            if resp.tool_calls:
                for call in resp.tool_calls:
                    tool_calls += 1
                    if tool_calls > MAX_TOOL_CALLS:
                        content, ok = "Tool call limit reached for this turn. Answer with what you have.", False
                    else:
                        content, ok = self._execute(call.name, call.arguments, session, trace, turn)
                    history.append(Message("tool", content, tool_call_id=call.id, name=call.name, is_error=not ok))
                continue

            # A final reply: check it against everything the tools returned in this conversation.
            tool_text = "\n".join(m.content for m in history if m.role == "tool")
            with trace.span("grounding_check", "guardrail") as s:
                check = check_output(resp.text, session.ctx.seen_codes, tool_text)
                s.attributes.update(ok=check.ok, problems=check.problems)
            if check.ok:
                turn.reply = resp.text
                break
            turn.flags += check.flags
            if not retried:
                retried = turn.retried = True
                history.pop()  # the rejected reply is never shown or kept
                history.append(Message("user", RETRY_NOTE.format(problems="\n".join(f"- {p}" for p in check.problems))))
                continue
            # Still failing after one retry: drop ungrounded citations and add the label reminder.
            turn.flags.append("grounding_failed_after_retry")
            turn.reply = scrub(resp.text, session.ctx.seen_codes)
            if "unsafe_assurance" in check.flags:
                turn.reply += "\n\nPlease check the pack for allergen information before eating."
            history[-1] = Message("assistant", turn.reply)
            break
        else:
            turn.flags.append("max_steps_reached")
            turn.reply = "I've done what I can in one go. Tell me what you'd like next and I'll keep going."
            history.append(Message("assistant", turn.reply))

        # Retry notes are internal; keep the transcript clean for the next turn.
        session.history = [m for m in history if not (m.role == "user" and m.content.startswith("[Automatic reply"))]
        return self._finish(trace, turn)

    def _execute(self, name: str, arguments: dict, session: Session, trace: Trace, turn: Turn) -> tuple[str, bool]:
        tool = TOOLS_BY_NAME.get(name)
        with trace.span(name, "tool", arguments=arguments) as s:
            err = None
            try:
                if tool is None:
                    raise ToolError(f"Unknown tool '{name}'.")
                out = tool.run(session.ctx, arguments)
                content, ok = json.dumps(out, default=str)[:MAX_TOOL_RESULT_CHARS], True
            except ToolError as e:
                content, ok, err = f"Error: {e}", False, str(e)
                s.error = err
            except Exception as e:  # a bug: don't leak internals to the model
                content, ok, err = "Error: that didn't work. Try another approach.", False, repr(e)
                s.error = err
        turn.tool_events.append(ToolEvent(name, arguments, ok, s.duration_ms, err))
        return content, ok

    @staticmethod
    def _finish(trace: Trace, turn: Turn) -> Turn:
        turn.latency_ms = trace.duration_ms
        turn.trace = trace.to_dict()
        trace.export({"model": turn.model, "flags": turn.flags})
        return turn
