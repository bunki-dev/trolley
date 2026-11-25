"""Run Trolley's eval scenarios and grade them in code.

    python -m evals.run                          # offline keyword router (no key, used in CI)
    LLM_PROVIDER=anthropic python -m evals.run   # a real model
    python -m evals.run --only guard- --verbose

Every check is deterministic: it reads the reply, the tool trace, guardrail flags and the simulated
store, never another model's opinion. Writes evals/results/<model>.json and prints a summary table.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from trolley.agent.agent import Session, TrolleyAgent, Turn
from trolley.agent.providers.base import get_provider
from trolley.catalogue import Catalogue, load
from trolley.search import SearchIndex, allergen_keys

HERE = Path(__file__).parent
CODE = re.compile(r"\[\[(\d{6,14})\]\]")
GROUNDING_FAIL = {"grounding_failed_after_retry", "max_steps_reached"}
SEEDED_ORDERS = {"T-1001", "T-1002", "T-1003", "T-2001"}


@dataclass
class Result:
    id: str
    job: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    turns: list[dict] = field(default_factory=list)
    tool_calls: int = 0
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    retries: int = 0


def _state(session: Session) -> dict:
    s = session.store
    return {
        "basket": dict(s.basket),
        "orders": {k: o.status for k, o in s.orders.items()},
        "slots": {k: o.slot_start.isoformat() for k, o in s.orders.items()},
        "cases": len(s.cases),
    }


def _key(name: str) -> str:
    keys, _ = allergen_keys([name])
    return keys[0] if keys else name


def grade(exp: dict, turn: Turn, session: Session, before: dict, after: dict) -> list[str]:
    cat = session.store.cat
    reply, fails = turn.reply, []
    called = [e.name for e in turn.tool_events]
    cited = [cat.get(c) for c in dict.fromkeys(CODE.findall(reply))]
    cited = [p for p in cited if p]

    for t in exp.get("tools", []):
        if t not in called:
            fails.append(f"expected tool {t}, called {called or 'none'}")
    for t in exp.get("no_tools", []):
        if t in called:
            fails.append(f"tool {t} must not be called")
    if "reply" in exp and not re.search(exp["reply"], reply, re.I):
        fails.append(f"reply doesn't match /{exp['reply']}/")
    if "not_reply" in exp and re.search(exp["not_reply"], reply, re.I):
        fails.append(f"reply matches forbidden /{exp['not_reply']}/")
    for f in exp.get("flags", []):
        if f not in turn.flags:
            fails.append(f"expected flag {f}, got {turn.flags}")
    for f in exp.get("no_flags", []):
        if f in turn.flags:
            fails.append(f"unexpected flag {f}")
    if exp.get("grounded", True) and GROUNDING_FAIL & set(turn.flags):
        fails.append(f"not grounded: {turn.flags}")
    if len(cited) < exp.get("cites_at_least", 0):
        fails.append(f"cited {len(cited)} products, expected at least {exp['cites_at_least']}")
    if "cited_free_of" in exp:
        k = _key(exp["cited_free_of"])
        bad = [p.code for p in cited if p.allergen_status(k) in ("contains", "may_contain")]
        if bad:
            fails.append(f"cited products with {k}: {bad}")
    if "cited_label" in exp:
        bad = [p.code for p in cited if exp["cited_label"] not in p.labels]
        if bad:
            fails.append(f"cited products without label {exp['cited_label']}: {bad}")
    if "cited_max_price" in exp:
        cap = round(float(exp["cited_max_price"]) * 100)
        bad = [p.code for p in cited if p.price.cents > cap]
        if bad:
            fails.append(f"cited products over ${exp['cited_max_price']}: {bad}")
    if "search_excluded" in exp:
        k = _key(exp["search_excluded"])
        searches = [e for e in turn.tool_events if e.name == "search_products"]
        for e in searches:
            asked = allergen_keys(e.arguments.get("avoid_allergens", []))[0]
            if k not in asked + list(session.store.customer.allergies):
                fails.append(f"search {e.arguments} didn't exclude {k}")
    if "basket_items" in exp and sum(after["basket"].values()) != exp["basket_items"]:
        fails.append(f"basket has {sum(after['basket'].values())} items, expected {exp['basket_items']}")
    if "basket_free_of" in exp:
        k = _key(exp["basket_free_of"])
        bad = [c for c in after["basket"] if cat.get(c) and cat.get(c).allergen_status(k) == "contains"]
        if bad:
            fails.append(f"basket contains {k}: {bad}")
    for oid, status in (exp.get("order_status") or {}).items():
        if after["orders"].get(oid) != status:
            fails.append(f"{oid} is {after['orders'].get(oid)}, expected {status}")
    if "orders_placed" in exp:
        n = len(set(after["orders"]) - SEEDED_ORDERS)
        if n != exp["orders_placed"]:
            fails.append(f"{n} orders placed, expected {exp['orders_placed']}")
    if "cases" in exp and after["cases"] != exp["cases"]:
        fails.append(f"{after['cases']} help cases, expected {exp['cases']}")
    if "case_outcome" in exp:
        got = session.store.cases[-1].outcome if session.store.cases else None
        if got != exp["case_outcome"]:
            fails.append(f"latest case outcome {got}, expected {exp['case_outcome']}")
    if exp.get("no_mutations") and before != after:
        fails.append("store state changed")
    return fails


def run_scenario(sc: dict, agent: TrolleyAgent, catalogue: Catalogue, index: SearchIndex) -> Result:
    session = Session(catalogue, index)
    res = Result(sc["id"], sc["job"], True)
    for t in sc["turns"]:
        msg = t["user"] if "user" in t else t["user_repeat"][0] * t["user_repeat"][1]
        before = _state(session)
        try:
            turn = agent.run(session, msg)
        except Exception as e:  # a crash is a failure, not the end of the run
            res.passed = False
            res.failures.append(f"crashed on {msg[:40]!r}: {e!r}")
            break
        after = _state(session)
        fails = grade(t.get("expect") or {}, turn, session, before, after)
        res.turns.append(
            {
                "user": msg[:200],
                "reply": turn.reply,
                "tools": [{"name": e.name, "args": e.arguments, "ok": e.ok} for e in turn.tool_events],
                "flags": turn.flags,
                "failures": fails,
            }
        )
        res.tool_calls += len(turn.tool_events)
        res.latency_ms += turn.latency_ms
        res.input_tokens += turn.usage.input_tokens
        res.output_tokens += turn.usage.output_tokens
        res.retries += int(turn.retried)
        if fails:
            res.passed = False
            res.failures += [f"turn {len(res.turns)}: {f}" for f in fails]
    return res


def summarise(results: list[Result]) -> dict:
    by_job: dict[str, list[Result]] = defaultdict(list)
    for r in results:
        by_job[r.job].append(r)
    n = len(results) or 1
    return {
        "passed": sum(r.passed for r in results),
        "total": len(results),
        "pass_rate": round(sum(r.passed for r in results) / n, 3),
        "by_job": {j: {"passed": sum(r.passed for r in rs), "total": len(rs)} for j, rs in by_job.items()},
        "avg_tool_calls": round(sum(r.tool_calls for r in results) / n, 2),
        "retries": sum(r.retries for r in results),
        "avg_latency_ms": round(sum(r.latency_ms for r in results) / n, 1),
        "tokens": {"input": sum(r.input_tokens for r in results), "output": sum(r.output_tokens for r in results)},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", help="run scenarios whose id starts with this")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--min-pass", type=float, default=0.0, help="exit 1 below this overall pass rate")
    ap.add_argument("--safety-must-pass", action="store_true", help="exit 1 if any safety scenario fails")
    args = ap.parse_args(argv)

    scenarios = yaml.safe_load((HERE / "scenarios.yaml").read_text())
    if args.only:
        scenarios = [s for s in scenarios if s["id"].startswith(args.only)]
    catalogue = load()
    index = SearchIndex(catalogue)
    agent = TrolleyAgent(get_provider())
    started = time.time()
    results = [run_scenario(sc, agent, catalogue, index) for sc in scenarios]

    summary = summarise(results)
    summary.update(model=agent.provider.model, products=len(catalogue.products), seconds=round(time.time() - started))
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    safe_model = re.sub(r"[^\w.-]+", "_", agent.provider.model)
    (out / f"{safe_model}.json").write_text(
        json.dumps({"summary": summary, "results": [r.__dict__ for r in results]}, indent=1, default=str)
    )

    width = max(len(r.id) for r in results) if results else 10
    for r in results:
        print(f"{'PASS' if r.passed else 'FAIL'}  {r.id:<{width}}  {r.job}")
        if not r.passed or args.verbose:
            for f in r.failures:
                print(f"        - {f}")
        if args.verbose:
            for t in r.turns:
                print(f"        you> {t['user'][:80]}\n        bot> {t['reply'][:160]!r}  {t['flags'] or ''}")
    print(
        f"\n{summary['model']}: {summary['passed']}/{summary['total']} passed "
        + " ".join(f"{j} {v['passed']}/{v['total']}" for j, v in summary["by_job"].items())
        + f" · {summary['avg_tool_calls']} tool calls/scenario · {summary['retries']} grounding retries"
    )
    safety_failed = [r.id for r in results if r.job == "safety" and not r.passed]
    if args.safety_must_pass and safety_failed:
        print(f"Safety scenarios failed: {safety_failed}")
        return 1
    return int(summary["pass_rate"] < args.min_pass)


if __name__ == "__main__":
    sys.exit(main())
