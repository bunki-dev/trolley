"""Minimal tracing: one trace per agent turn, with spans for each model call and tool call.

Traces are returned to the UI (so you can see what the agent did) and optionally written
as JSONL to TROLLEY_TRACE_DIR for offline analysis and evals.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Span:
    name: str
    kind: str  # "llm" | "tool" | "guardrail"
    start_ms: float
    duration_ms: float = 0.0
    attributes: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class Trace:
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    started_at: float = field(default_factory=time.time)
    spans: list[Span] = field(default_factory=list)
    _t0: float = field(default_factory=time.perf_counter, repr=False)

    @contextmanager
    def span(self, name: str, kind: str, **attributes):
        s = Span(
            name=name,
            kind=kind,
            start_ms=round((time.perf_counter() - self._t0) * 1000, 1),
            attributes=dict(attributes),
        )
        self.spans.append(s)
        t = time.perf_counter()
        try:
            yield s
        except Exception as e:
            s.error = f"{type(e).__name__}: {e}"
            raise
        finally:
            s.duration_ms = round((time.perf_counter() - t) * 1000, 1)

    @property
    def duration_ms(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000, 1)

    def to_dict(self) -> dict:
        return {"trace_id": self.trace_id, "started_at": self.started_at, "spans": [asdict(s) for s in self.spans]}

    def export(self, extra: dict | None = None) -> None:
        directory = os.getenv("TROLLEY_TRACE_DIR")
        if not directory:
            return
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        with (path / "traces.jsonl").open("a") as f:
            f.write(json.dumps({**self.to_dict(), **(extra or {})}, default=str) + "\n")
