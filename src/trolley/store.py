"""A demo supermarket back end: one signed-in customer, their basket, orders and help cases.

Orders, slots and the clock are simulated (a real store would supply them); every product in them
is a real product from the catalogue. The clock is fixed so demos and evals are repeatable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from .catalogue import Catalogue, Product

AEDT = timezone(timedelta(hours=11), "AEDT")
DEMO_NOW = datetime(2026, 11, 26, 10, 0, tzinfo=AEDT)
DELIVERY_FEE_CENTS = 500
FREE_DELIVERY_OVER_CENTS = 10000
WINDOWS = [(8, 10), (12, 14), (16, 18), (18, 20)]

AFFIRMATIVE = re.compile(
    r"^\s*(yes|yep|yeah|yup|sure|ok|okay|confirm(ed)?|go ahead|do it|please do|place it|cancel it|add it|"
    r"that's (fine|right|correct)|sounds good|correct)\b",
    re.I,
)
NEGATIVE = re.compile(r"\b(no|nope|don't|do not|stop|wait|hold on|never ?mind|not yet)\b", re.I)


class StoreError(Exception):
    """A rule the store enforces (shown to the model so it can explain or recover)."""


@dataclass
class Customer:
    id: str
    name: str
    suburb: str
    allergies: list[str] = field(default_factory=list)  # canonical allergen keys
    notes: str = ""


@dataclass
class OrderLine:
    code: str
    name: str
    qty: int
    unit_cents: int


@dataclass
class Order:
    id: str
    customer_id: str
    lines: list[OrderLine]
    status: str  # placed | delivered | cancelled
    slot_start: datetime
    slot_end: datetime
    placed_at: datetime
    delivered_at: datetime | None = None
    delivery_fee_cents: int = DELIVERY_FEE_CENTS

    @property
    def subtotal_cents(self) -> int:
        return sum(line.qty * line.unit_cents for line in self.lines)

    @property
    def cutoff(self) -> datetime:
        """Changes close at 10pm the night before delivery."""
        day_before = (self.slot_start - timedelta(days=1)).date()
        return datetime(day_before.year, day_before.month, day_before.day, 22, 0, tzinfo=AEDT)

    @property
    def minutes_late(self) -> int:
        if not self.delivered_at:
            return 0
        return max(0, int((self.delivered_at - self.slot_end).total_seconds() // 60))


@dataclass
class Case:
    id: str
    order_id: str | None
    kind: str
    summary: str
    outcome: str  # refunded | credited | with_our_team | declined
    amount_cents: int
    created_at: datetime
    policy: list[str]


@dataclass
class Pending:
    token: str
    action: str
    args: dict
    summary: str
    turn: int


def money(cents: int) -> str:
    return f"${cents / 100:.2f}"


def slot_id(start: datetime) -> str:
    return start.strftime("%Y-%m-%d-%H%M")


def fmt_slot(start: datetime, end: datetime) -> str:
    return f"{start.strftime('%a %-d %b')}, {start.strftime('%-I%p').lower()}-{end.strftime('%-I%p').lower()}"


class Store:
    def __init__(self, catalogue: Catalogue, now: datetime = DEMO_NOW):
        self.cat = catalogue
        self.now = now
        self.customer = Customer(
            "c-001", "Alex", "Hawthorn East", allergies=["peanuts"], notes="Prefers Australian made"
        )
        self.basket: dict[str, int] = {}
        self.orders: dict[str, Order] = {}
        self.cases: list[Case] = []
        self.pending: dict[str, Pending] = {}
        self.turn = 0
        self.last_user_message = ""
        self._seed()

    # ---------- conversation bookkeeping (used by the confirmation gate) ----------

    def new_turn(self, user_message: str) -> None:
        self.turn += 1
        self.last_user_message = user_message
        if NEGATIVE.search(user_message):
            self.pending.clear()  # "no", "wait": nothing waiting may go ahead

    # ---------- seed data: real products, simulated orders ----------

    def _picks(self, n: int) -> list[Product]:
        """A stable spread of in-stock products across aisles, avoiding the demo customer's allergy."""
        pool = [
            p
            for p in self.cat.products
            if p.in_stock and not set(self.customer.allergies) & set(p.allergens + p.traces)
        ]
        pool.sort(key=lambda p: hashlib.sha256(f"seed{p.code}".encode()).hexdigest())
        by_aisle: dict[str, list[Product]] = {}
        for p in pool:
            by_aisle.setdefault(p.aisle, []).append(p)
        out: list[Product] = []
        while len(out) < n and any(by_aisle.values()):
            for aisle in sorted(by_aisle):
                if by_aisle[aisle] and len(out) < n:
                    out.append(by_aisle[aisle].pop(0))
        return out

    def _order(self, oid: str, customer: str, picks: list[tuple[Product, int]], slot_start: datetime, **kw) -> Order:
        lines = [OrderLine(p.code, p.title, q, p.price.cents) for p, q in picks]
        o = Order(
            oid,
            customer,
            lines,
            kw.pop("status", "placed"),
            slot_start,
            slot_start + timedelta(hours=2),
            kw.pop("placed_at", slot_start - timedelta(days=2)),
            **kw,
        )
        self.orders[oid] = o
        return o

    def _seed(self) -> None:
        def at(days: int, hour: int) -> datetime:
            d = (self.now + timedelta(days=days)).date()
            return datetime(d.year, d.month, d.day, hour, tzinfo=AEDT)

        p = self._picks(13)
        if not p:
            return
        p = (p * 13)[:13]  # small catalogues (tests) reuse products
        # Delivered three days ago, on time.
        s = at(-3, 16)
        self._order(
            "T-1001",
            self.customer.id,
            [(p[0], 2), (p[1], 1), (p[2], 3), (p[3], 1)],
            s,
            status="delivered",
            delivered_at=s + timedelta(minutes=50),
        )
        # Delivered yesterday, 75 minutes after the window closed.
        s = at(-1, 18)
        self._order(
            "T-1002",
            self.customer.id,
            [(p[4], 1), (p[5], 2), (p[6], 1), (p[7], 2)],
            s,
            status="delivered",
            delivered_at=s + timedelta(hours=2, minutes=75),
        )
        # Coming tomorrow afternoon; changes close tonight at 10pm.
        self._order(
            "T-1003",
            self.customer.id,
            [(p[8], 1), (p[9], 2), (p[10], 1), (p[11], 1)],
            at(1, 16),
            placed_at=self.now - timedelta(days=1),
        )
        # Someone else's order, to prove the agent can't see it.
        self._order("T-2001", "c-999", [(p[12], 4)], at(1, 8))

    # ---------- basket ----------

    def basket_view(self) -> dict:
        lines, subtotal = [], 0
        for code, qty in self.basket.items():
            p = self.cat.get(code)
            if not p:
                continue
            lines.append({**p.card(), "qty": qty, "line_total": money(qty * p.price.cents)})
            subtotal += qty * p.price.cents
        fee = 0 if subtotal >= FREE_DELIVERY_OVER_CENTS or not lines else DELIVERY_FEE_CENTS
        return {
            "lines": lines,
            "items": sum(self.basket.values()),
            "subtotal": money(subtotal),
            "delivery_fee": money(fee),
            "total": money(subtotal + fee),
            "free_delivery_from": money(FREE_DELIVERY_OVER_CENTS),
            "prices_note": "Demo prices unless price_source is open_prices.",
        }

    def set_qty(self, code: str, qty: int) -> Product:
        p = self.cat.get(code)
        if not p:
            raise StoreError(f"No product with code {code}. Search for it first.")
        if qty < 0 or qty > 24:
            raise StoreError("Quantity must be between 0 and 24.")
        if qty and not p.in_stock:
            raise StoreError(f"{p.title} is out of stock. Offer a substitute with find_substitutes.")
        if qty == 0:
            self.basket.pop(code, None)
        else:
            self.basket[code] = qty
        return p

    def basket_cents(self) -> int:
        return sum(q * self.cat.by_code[c].price.cents for c, q in self.basket.items() if c in self.cat.by_code)

    # ---------- orders ----------

    def my_order(self, order_id: str) -> Order:
        o = self.orders.get(str(order_id).strip().upper())
        if not o or o.customer_id != self.customer.id:
            # Same answer whether it doesn't exist or belongs to someone else: no information leaks.
            raise StoreError(f"I can't find order {order_id} on this account.")
        return o

    def order_view(self, o: Order) -> dict:
        editable = o.status == "placed" and self.now < o.cutoff
        return {
            "order_id": o.id,
            "status": o.status,
            "delivery": fmt_slot(o.slot_start, o.slot_end),
            "delivered_at": o.delivered_at.strftime("%a %-d %b, %-I:%M%p").lower() if o.delivered_at else None,
            "minutes_late": o.minutes_late,
            "changes_close": o.cutoff.strftime("%a %-d %b, %-I%p").lower() if o.status == "placed" else None,
            "can_change": editable,
            "lines": [
                {"code": ln.code, "name": ln.name, "qty": ln.qty, "price": money(ln.unit_cents)} for ln in o.lines
            ],
            "subtotal": money(o.subtotal_cents),
            "delivery_fee": money(o.delivery_fee_cents),
            "total": money(o.subtotal_cents + o.delivery_fee_cents),
        }

    def slots(self, days: int = 4) -> list[dict]:
        out = []
        for d in range(1, days + 1):
            day = (self.now + timedelta(days=d)).date()
            for h0, _h1 in WINDOWS:
                start = datetime(day.year, day.month, day.day, h0, tzinfo=AEDT)
                full = int(hashlib.sha256(start.isoformat().encode()).hexdigest(), 16) % 5 == 0
                out.append(
                    {
                        "slot_id": slot_id(start),
                        "when": fmt_slot(start, start + timedelta(hours=2)),
                        "available": not full,
                    }
                )
        return out

    def slot_start(self, sid: str) -> datetime:
        for s in self.slots(7):
            if s["slot_id"] == sid:
                if not s["available"]:
                    raise StoreError(f"{s['when']} is full. Pick another slot.")
                return datetime.strptime(sid, "%Y-%m-%d-%H%M").replace(tzinfo=AEDT)
        raise StoreError(f"Unknown slot {sid}. Call delivery_slots for valid ids.")

    def place(self, sid: str) -> Order:
        if not self.basket:
            raise StoreError("The basket is empty.")
        start = self.slot_start(sid)
        n = 1004 + sum(1 for o in self.orders.values() if o.customer_id == self.customer.id) - 3
        oid = f"T-{n}"
        picks = [(self.cat.by_code[c], q) for c, q in self.basket.items()]
        o = self._order(oid, self.customer.id, picks, start, placed_at=self.now)
        o.delivery_fee_cents = 0 if o.subtotal_cents >= FREE_DELIVERY_OVER_CENTS else DELIVERY_FEE_CENTS
        self.basket.clear()
        return o

    # ---------- confirmation gate ----------

    def propose(self, action: str, args: dict, summary: str) -> dict:
        token = hashlib.sha256(f"{action}{args}{self.turn}".encode()).hexdigest()[:8]
        self.pending[token] = Pending(token, action, args, summary, self.turn)
        return {
            "needs_confirmation": True,
            "confirmation_id": token,
            "summary": summary,
            "instruction": "Tell the customer exactly this and ask them to confirm. Call confirm_action only "
            "after they reply yes in their next message.",
        }

    def take_confirmed(self, token: str) -> Pending:
        p = self.pending.get(token)
        if not p:
            raise StoreError("Nothing is waiting for confirmation with that id (it may have expired).")
        if self.turn <= p.turn:
            raise StoreError("The customer hasn't replied yet. Ask them to confirm, then wait for their answer.")
        if not AFFIRMATIVE.search(self.last_user_message) or NEGATIVE.search(self.last_user_message):
            raise StoreError("The customer's last message isn't a clear yes. Ask again before doing this.")
        del self.pending[token]
        return p

    def snapshot(self) -> dict:
        """What the app shows beside the chat."""
        mine = [o for o in self.orders.values() if o.customer_id == self.customer.id]
        mine.sort(key=lambda o: o.slot_start, reverse=True)
        return {
            "customer": asdict(self.customer),
            "basket": self.basket_view(),
            "orders": [self.order_view(o) for o in mine],
            "cases": [
                {**asdict(c), "amount": money(c.amount_cents), "created_at": c.created_at.isoformat()}
                for c in self.cases
            ],
            "now": self.now.isoformat(),
        }
