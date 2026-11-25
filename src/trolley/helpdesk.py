"""Customer help: policy retrieval for grounded answers, and a refund engine that enforces the policy.

The model never decides a refund amount. It reports the issue; this code checks the order, the
time window and the limits, and returns the outcome and the policy sections it applied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from importlib import resources

from .catalogue import words
from .store import Case, Store, StoreError, money

REPORT_WINDOW = timedelta(days=7)
AUTO_PER_ORDER = 5000
AUTO_PER_30_DAYS = 10000
LATE_CREDIT = 1000
LATE_AFTER_MINUTES = 30
ISSUE_KINDS = ("missing", "damaged", "wrong_item", "expired", "late_delivery", "unwell")


@dataclass
class Section:
    id: str
    title: str
    text: str


@lru_cache(maxsize=1)
def policy() -> list[Section]:
    raw = (resources.files("trolley") / "data" / "help_policy.md").read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r"^## (P\d+) (.+?)\n(.*?)(?=^## |\Z)", raw, re.S | re.M):
        out.append(Section(m.group(1), m.group(2).strip(), " ".join(m.group(3).split())))
    return out


def search_policy(query: str, limit: int = 3) -> list[dict]:
    q = set(words(query))
    if q & {"much", "cost", "costs", "price", "charge", "pay"}:
        q |= {"fee", "fees"}
    scored = []
    for s in policy():
        body = words(s.title) * 3 + words(s.text)
        score = sum(body.count(w) for w in q if len(w) > 2)
        if score:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    return [{"id": s.id, "title": s.title, "text": s.text} for _, s in scored[:limit]]


def _refunded_last_30_days(store: Store) -> int:
    since = store.now - timedelta(days=30)
    return sum(c.amount_cents for c in store.cases if c.outcome == "refunded" and c.created_at >= since)


def report_issue(store: Store, order_id: str, kind: str, items: list[dict], details: str = "") -> dict:
    if kind not in ISSUE_KINDS:
        raise StoreError(f"kind must be one of {', '.join(ISSUE_KINDS)}.")
    order = store.my_order(order_id)
    cid = f"H-{4100 + len(store.cases) + 1}"

    def case(outcome: str, cents: int, policy_ids: list[str], summary: str) -> dict:
        store.cases.append(Case(cid, order.id, kind, summary, outcome, cents, store.now, policy_ids))
        return {"case_id": cid, "outcome": outcome, "amount": money(cents), "policy": policy_ids, "summary": summary}

    if kind == "unwell":
        return case(
            "with_our_team",
            0,
            ["P7"],
            "Possible illness or allergic reaction reported. Passed to our team now. If symptoms are severe, call 000.",
        )

    if order.status != "delivered":
        raise StoreError(f"Order {order.id} hasn't been delivered yet ({order.status}), so there's nothing to report.")
    if store.now - order.delivered_at > REPORT_WINDOW:
        return case("with_our_team", 0, ["P1", "P4"], "Reported more than 7 days after delivery: passed to our team.")

    if kind == "late_delivery":
        prior = [c for c in store.cases if c.order_id == order.id and c.kind == "late_delivery"]
        if prior:
            raise StoreError(f"Already handled in case {prior[-1].id} ({prior[-1].outcome}). Refer to that case.")
        if order.minutes_late <= LATE_AFTER_MINUTES:
            return case("declined", 0, ["P3"], f"Delivered {order.minutes_late} min after the window: within policy.")
        return case("credited", LATE_CREDIT, ["P3"], f"Delivered {order.minutes_late} min late: $10 credit added.")

    # Item problems: refund what was actually on the order, at the price paid.
    claimed: list[tuple[str, int, int, str]] = []
    on_order = {ln.code: ln for ln in order.lines}
    already = {
        code
        for c in store.cases
        if c.order_id == order.id and c.kind in ("missing", "damaged", "wrong_item", "expired")
        for code in re.findall(r"\[(\d+)\]", c.summary)
    }
    for it in items:
        code = str(it.get("code", "")).strip()
        ln = on_order.get(code)
        if not ln:
            raise StoreError(f"Product {code} isn't on order {order.id}. Check the order lines with get_order.")
        if code in already:
            raise StoreError(f"{ln.name} on {order.id} was already reported. Refer to the earlier case.")
        qty = int(it.get("qty") or 1)
        if not 1 <= qty <= ln.qty:
            raise StoreError(f"{ln.name}: quantity must be between 1 and {ln.qty} (what was ordered).")
        claimed.append((code, qty, qty * ln.unit_cents, ln.name))
    if not claimed:
        raise StoreError("Say which items from the order were affected.")

    total = sum(c[2] for c in claimed)
    what = "; ".join(f"{q} x {name} [{code}]" for code, q, _, name in claimed)
    policy_ids = ["P2"] if kind in ("damaged", "wrong_item", "expired") else ["P1"]
    if total > AUTO_PER_ORDER or _refunded_last_30_days(store) + total > AUTO_PER_30_DAYS:
        return case(
            "with_our_team",
            total,
            [*policy_ids, "P4"],
            f"{kind}: {what}. Over the automatic limit, so our team will review it within one business day.",
        )
    return case("refunded", total, [*policy_ids, "P6"], f"{kind}: {what}. Refunded to the original payment method.")


def escalate(store: Store, reason: str, order_id: str | None = None) -> dict:
    if order_id:
        store.my_order(order_id)
    cid = f"H-{4100 + len(store.cases) + 1}"
    store.cases.append(Case(cid, order_id, "escalation", reason[:300], "with_our_team", 0, store.now, ["P4"]))
    return {"case_id": cid, "outcome": "with_our_team", "reply_within": "one business day"}
