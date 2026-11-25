"""The agent's tools: one typed definition drives the LLM schema, argument validation and the MCP server.

Business rules live here and in the store, not in the prompt: allergen filters, stock checks, order
ownership, change cut-offs, refund limits and the confirmation gate all hold whatever the model says.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from . import helpdesk
from .catalogue import ALLERGENS
from .search import SearchIndex, allergen_keys, passes
from .store import Store, StoreError, fmt_slot, money


class ToolError(Exception):
    """An error the model should see and can recover from."""


@dataclass
class Ctx:
    store: Store
    index: SearchIndex
    seen_codes: set[str]  # products shown to the model this session: replies may only cite these


# ---------- argument models ----------


class NoArgs(BaseModel):
    pass


class SearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=120, description="What the customer is looking for, in plain words.")
    avoid_allergens: list[str] = Field(
        default_factory=list,
        description="Allergens to exclude, e.g. ['peanuts', 'gluten']. The customer's "
        "allergy list is always applied as well.",
    )
    labels: list[str] = Field(default_factory=list, description="Required labels, e.g. ['vegan'], ['gluten free'].")
    aisle: str | None = None
    max_price: float | None = Field(None, gt=0, le=200, description="Maximum price in dollars.")
    limit: int = Field(6, ge=1, le=12)


class CodeArgs(BaseModel):
    code: str = Field(description="Product code (barcode) from a search result.")


class CheckAllergenArgs(BaseModel):
    codes: list[str] = Field(min_length=1, max_length=20)
    allergens: list[str] = Field(min_length=1, max_length=8)


class QtyArgs(BaseModel):
    code: str
    qty: int = Field(ge=0, le=24, description="New quantity; 0 removes it.")


class SubArgs(BaseModel):
    code: str
    limit: int = Field(3, ge=1, le=6)


class SlotsArgs(BaseModel):
    days: int = Field(3, ge=1, le=7)


class PlaceArgs(BaseModel):
    slot_id: str


class ConfirmArgs(BaseModel):
    confirmation_id: str


class OrderArgs(BaseModel):
    order_id: str


class ChangeSlotArgs(BaseModel):
    order_id: str
    slot_id: str


class PolicyArgs(BaseModel):
    question: str = Field(min_length=2, max_length=200)


class IssueItem(BaseModel):
    code: str
    qty: int = Field(1, ge=1, le=24)


class IssueArgs(BaseModel):
    order_id: str
    kind: Literal["missing", "damaged", "wrong_item", "expired", "late_delivery", "unwell"]
    items: list[IssueItem] = Field(default_factory=list, description="Affected lines (not needed for late_delivery).")
    details: str = Field("", max_length=400)


class EscalateArgs(BaseModel):
    reason: str = Field(min_length=5, max_length=300)
    order_id: str | None = None


# ---------- handlers ----------


def _see(ctx: Ctx, cards: list[dict]) -> list[dict]:
    ctx.seen_codes.update(c["code"] for c in cards)
    return cards


def _profile_allergens(ctx: Ctx) -> list[str]:
    return list(ctx.store.customer.allergies)


def get_profile(ctx: Ctx, _: NoArgs) -> dict:
    c = ctx.store.customer
    return {
        "name": c.name,
        "suburb": c.suburb,
        "allergies": [ALLERGENS[a] for a in c.allergies],
        "notes": c.notes,
        "today": ctx.store.now.strftime("%A %-d %B %Y, %-I:%M%p").replace("AM", "am").replace("PM", "pm"),
    }


def search_products(ctx: Ctx, a: SearchArgs) -> dict:
    keys, unknown = allergen_keys(a.avoid_allergens)
    keys = list(dict.fromkeys(keys + _profile_allergens(ctx)))
    hits = ctx.index.search(
        a.query,
        exclude_allergens=keys,
        strict=True,
        labels=a.labels,
        aisle=a.aisle,
        max_price_cents=round(a.max_price * 100) if a.max_price else None,
        limit=a.limit,
    )
    out = {
        "results": _see(ctx, [h.product.card() for h in hits]),
        "filters_applied": {
            "excluded_allergens": [ALLERGENS[k] for k in keys],
            "excluded_also": "products that 'may contain' those allergens or have no ingredient data",
            "labels": a.labels,
            "max_price": money(round(a.max_price * 100)) if a.max_price else None,
        },
    }
    if unknown:
        out["unrecognised_allergens"] = unknown
    if not hits:
        out["note"] = "No matches with these filters. Say so; offer to relax a filter other than allergies."
    return out


def get_product(ctx: Ctx, a: CodeArgs) -> dict:
    p = ctx.store.cat.get(a.code)
    if not p:
        raise ToolError(f"No product with code {a.code}.")
    _see(ctx, [p.card()])
    return {
        **p.card(),
        "ingredients": p.ingredients or "Not available in our data: check the pack.",
        "nutrition_per_100g": {k: v for k, v in p.nutrition.items() if not k.endswith("_unit")},
        "nutri_score": p.nutriscore or None,
        "likely": p.likely,
        "price_note": p.price.note or "Demo price.",
    }


def check_allergens(ctx: Ctx, a: CheckAllergenArgs) -> dict:
    keys, unknown = allergen_keys(a.allergens)
    rows = []
    for code in a.codes:
        p = ctx.store.cat.get(code)
        if not p:
            rows.append({"code": code, "error": "unknown product"})
            continue
        _see(ctx, [p.card()])
        rows.append({"code": code, "name": p.title, **{ALLERGENS[k]: p.allergen_status(k) for k in keys}})
    return {
        "results": rows,
        "meaning": {
            "contains": "listed as an allergen on the label data",
            "may_contain": "possible traces (shared equipment)",
            "not_listed": "not listed in the label data; the customer should still check the pack",
            "unknown": "no ingredient data: we can't say. Tell them to check the pack.",
        },
        "unrecognised": unknown,
    }


def view_basket(ctx: Ctx, _: NoArgs) -> dict:
    v = ctx.store.basket_view()
    _see(ctx, v["lines"])
    return v


def set_basket_qty(ctx: Ctx, a: QtyArgs) -> dict:
    p = ctx.store.cat.get(a.code)
    if p and a.qty > 0:
        hit = [k for k in _profile_allergens(ctx) if p.allergen_status(k) in ("contains", "may_contain")]
        if hit:
            names = ", ".join(ALLERGENS[k] for k in hit)
            return ctx.store.propose(
                "add_allergen_item",
                {"code": a.code, "qty": a.qty},
                f"{p.title} contains or may contain {names}, which is on {ctx.store.customer.name}'s allergy list. "
                f"Add {a.qty} anyway?",
            )
    try:
        p = ctx.store.set_qty(a.code, a.qty)
    except StoreError as e:
        raise ToolError(str(e)) from e
    v = ctx.store.basket_view()
    return {"updated": p.title, "qty": a.qty, "basket_total": v["total"], "items": v["items"]}


def find_substitutes(ctx: Ctx, a: SubArgs) -> dict:
    p = ctx.store.cat.get(a.code)
    if not p:
        raise ToolError(f"No product with code {a.code}.")
    name_words = set(p.name.lower().split())
    cands = []
    for q in ctx.store.cat.products:
        if q.code == p.code or q.aisle != p.aisle:
            continue
        if not passes(q, _profile_allergens(ctx), True, [], None, None, True):
            continue
        # Must keep any allergen-free property the original had, and its dietary labels.
        if any(p.allergen_status(k) == "not_listed" and q.allergen_status(k) != "not_listed" for k in ALLERGENS):
            continue
        if not set(p.labels) <= set(q.labels):
            continue
        overlap = len(name_words & set(q.name.lower().split())) + len(set(p.categories) & set(q.categories)) / 3
        if overlap <= 0.5:
            continue
        cands.append((overlap, -abs(q.price.cents - p.price.cents), q))
    cands.sort(key=lambda x: (-x[0], -x[1]))
    subs = [{**q.card(), "price_difference": money(q.price.cents - p.price.cents)} for _, _, q in cands[: a.limit]]
    return {
        "original": p.card(),
        "substitutes": _see(ctx, subs),
        "rule": "Substitutes keep every allergen the original was free of and all its dietary labels. "
        "If a substitute costs more, the customer pays the original price (P9).",
    }


def delivery_slots(ctx: Ctx, a: SlotsArgs) -> dict:
    return {"slots": ctx.store.slots(a.days), "fee": "$5, free from $100"}


def place_order(ctx: Ctx, a: PlaceArgs) -> dict:
    s = ctx.store
    if not s.basket:
        raise ToolError("The basket is empty.")
    try:
        start = s.slot_start(a.slot_id)
    except StoreError as e:
        raise ToolError(str(e)) from e
    v = s.basket_view()
    when = fmt_slot(start, start.replace(hour=start.hour + 2))
    return s.propose(
        "place_order",
        {"slot_id": a.slot_id},
        f"Place an order of {v['items']} items for {v['total']} "
        f"(including {v['delivery_fee']} delivery), delivered {when}?",
    )


def confirm_action(ctx: Ctx, a: ConfirmArgs) -> dict:
    s = ctx.store
    try:
        p = s.take_confirmed(a.confirmation_id)
        if p.action == "place_order":
            o = s.place(p.args["slot_id"])
            return {"done": "order placed", **s.order_view(o)}
        if p.action == "cancel_order":
            o = s.my_order(p.args["order_id"])
            if s.now >= o.cutoff:
                raise StoreError("Changes for this order closed at 10pm last night.")
            o.status = "cancelled"
            return {
                "done": "order cancelled",
                "order_id": o.id,
                "refund": money(o.subtotal_cents + o.delivery_fee_cents),
                "refund_timing": "3 to 5 business days (P6)",
            }
        if p.action == "add_allergen_item":
            prod = s.set_qty(p.args["code"], p.args["qty"])
            return {"done": "added after the customer confirmed", "product": prod.title, "qty": p.args["qty"]}
    except StoreError as e:
        raise ToolError(str(e)) from e
    raise ToolError("Unknown action.")


def list_orders(ctx: Ctx, _: NoArgs) -> dict:
    s = ctx.store
    mine = sorted((o for o in s.orders.values() if o.customer_id == s.customer.id), key=lambda o: o.slot_start)
    return {
        "orders": [
            {
                "order_id": o.id,
                "status": o.status,
                "delivery": fmt_slot(o.slot_start, o.slot_end),
                "total": money(o.subtotal_cents + o.delivery_fee_cents),
                "items": sum(ln.qty for ln in o.lines),
            }
            for o in reversed(mine)
        ]
    }


def get_order(ctx: Ctx, a: OrderArgs) -> dict:
    try:
        o = ctx.store.my_order(a.order_id)
    except StoreError as e:
        raise ToolError(str(e)) from e
    v = ctx.store.order_view(o)
    ctx.seen_codes.update(ln["code"] for ln in v["lines"])
    return v


def change_delivery_slot(ctx: Ctx, a: ChangeSlotArgs) -> dict:
    s = ctx.store
    try:
        o = s.my_order(a.order_id)
        if o.status != "placed":
            raise StoreError(f"Order {o.id} is {o.status}, so its delivery can't be changed.")
        if s.now >= o.cutoff:
            raise StoreError("Changes closed at 10pm last night (P5). Offer to pass it to our team.")
        start = s.slot_start(a.slot_id)
    except StoreError as e:
        raise ToolError(str(e)) from e
    o.slot_start, o.slot_end = start, start.replace(hour=start.hour + 2)
    return {"done": "delivery changed", **s.order_view(o)}


def cancel_order(ctx: Ctx, a: OrderArgs) -> dict:
    s = ctx.store
    try:
        o = s.my_order(a.order_id)
        if o.status != "placed":
            raise StoreError(f"Order {o.id} is {o.status} and can't be cancelled.")
        if s.now >= o.cutoff:
            raise StoreError("Changes closed at 10pm last night (P5). Offer to pass it to our team.")
    except StoreError as e:
        raise ToolError(str(e)) from e
    return s.propose(
        "cancel_order",
        {"order_id": o.id},
        f"Cancel order {o.id} ({fmt_slot(o.slot_start, o.slot_end)}, "
        f"{money(o.subtotal_cents + o.delivery_fee_cents)})? You'll be refunded in full.",
    )


def search_help(ctx: Ctx, a: PolicyArgs) -> dict:
    hits = helpdesk.search_policy(a.question)
    return {
        "sections": hits or [],
        "note": None if hits else "Nothing in the policy covers this: say so, offer our team.",
    }


def report_issue(ctx: Ctx, a: IssueArgs) -> dict:
    try:
        return helpdesk.report_issue(ctx.store, a.order_id, a.kind, [i.model_dump() for i in a.items], a.details)
    except StoreError as e:
        raise ToolError(str(e)) from e


def escalate_to_human(ctx: Ctx, a: EscalateArgs) -> dict:
    try:
        return helpdesk.escalate(ctx.store, a.reason, a.order_id)
    except StoreError as e:
        raise ToolError(str(e)) from e


# ---------- registry ----------


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[Ctx, Any], dict]
    job: str  # find | basket | orders | help

    def json_schema(self) -> dict:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        return schema

    def run(self, ctx: Ctx, raw_args: dict | None) -> dict:
        try:
            args = self.args_model.model_validate(raw_args or {})
        except ValidationError as e:
            raise ToolError(f"Invalid arguments for {self.name}: {e.errors(include_url=False)}") from e
        return self.handler(ctx, args)


TOOLS: list[Tool] = [
    Tool(
        "get_profile",
        "The signed-in customer: name, allergies, notes, and today's date and time.",
        NoArgs,
        get_profile,
        "find",
    ),
    Tool(
        "search_products",
        "Search the catalogue. The customer's allergies are always excluded, along with "
        "'may contain' and products with no ingredient data.",
        SearchArgs,
        search_products,
        "find",
    ),
    Tool(
        "get_product",
        "Full details for one product: ingredients, allergens, nutrition per 100 g, labels.",
        CodeArgs,
        get_product,
        "find",
    ),
    Tool(
        "check_allergens",
        "Allergen status of products from their label data: contains, may_contain, not_listed or unknown.",
        CheckAllergenArgs,
        check_allergens,
        "find",
    ),
    Tool(
        "find_substitutes",
        "Alternatives for a product (out of stock, or the customer wants a swap) that "
        "keep its allergen-free properties and labels.",
        SubArgs,
        find_substitutes,
        "basket",
    ),
    Tool("view_basket", "The basket: lines, subtotal, delivery fee and total.", NoArgs, view_basket, "basket"),
    Tool(
        "set_basket_qty",
        "Set a product's quantity in the basket (0 removes it). Adding something that "
        "conflicts with the customer's allergies needs their confirmation.",
        QtyArgs,
        set_basket_qty,
        "basket",
    ),
    Tool("delivery_slots", "Available delivery windows.", SlotsArgs, delivery_slots, "orders"),
    Tool(
        "place_order",
        "Prepare to place the basket as an order. Returns a summary to confirm with the "
        "customer; nothing is placed until confirm_action.",
        PlaceArgs,
        place_order,
        "orders",
    ),
    Tool(
        "confirm_action",
        "Carry out an action the customer has just confirmed (in their latest message).",
        ConfirmArgs,
        confirm_action,
        "orders",
    ),
    Tool("list_orders", "The customer's orders, newest first.", NoArgs, list_orders, "orders"),
    Tool(
        "get_order",
        "One of the customer's orders: lines, status, delivery and whether it can still change.",
        OrderArgs,
        get_order,
        "orders",
    ),
    Tool(
        "change_delivery_slot",
        "Move an upcoming order to another delivery window (before the cut-off).",
        ChangeSlotArgs,
        change_delivery_slot,
        "orders",
    ),
    Tool(
        "cancel_order",
        "Prepare to cancel an upcoming order. Needs the customer's confirmation.",
        OrderArgs,
        cancel_order,
        "orders",
    ),
    Tool(
        "search_help",
        "Search the store's help policy. Answer policy questions only from what this returns, "
        "citing section ids like P3.",
        PolicyArgs,
        search_help,
        "help",
    ),
    Tool(
        "report_issue",
        "Report a problem with a delivered order. The store's rules decide any refund or "
        "credit; report the outcome exactly.",
        IssueArgs,
        report_issue,
        "help",
    ),
    Tool(
        "escalate_to_human",
        "Pass something to the customer care team (over limits, can't verify, the "
        "customer asks for a person, or a safety concern).",
        EscalateArgs,
        escalate_to_human,
        "help",
    ),
]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}
