"""Offline providers: no API key, deterministic.

`OfflineProvider` is a keyword router standing in for the model, so the app, the MCP server and the
guardrail evals run without credentials. It goes through exactly the same tools, confirmation gate
and grounding check as a real model; it's just much less clever at understanding people.

`ScriptedProvider` replays canned responses for unit tests.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import re

from ...store import AFFIRMATIVE
from ..types import LLMResponse, Message, ToolCall, ToolSpec, Usage

_ids = itertools.count(1)
ORDINALS = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3, "last": -1}
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "a": 1, "an": 1}
ALLERGY_WORDS = r"peanuts?|nuts?|gluten|dairy|milk|lactose|eggs?|soy|sesame|fish|shellfish"
LABEL_WORDS = {"vegan": "vegan", "vegetarian": "vegetarian", "gluten free": "gluten free", "organic": "organic"}


def _call(name: str, **args) -> ToolCall:
    return ToolCall(id=f"offline_{next(_ids)}", name=name, arguments=args)


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _results(messages: list[Message], names: tuple[str, ...]) -> list[tuple[str, dict]]:
    """Tool results (oldest first) for the given tool names, parsed."""
    out = []
    by_id = {c.id: c.name for m in messages if m.role == "assistant" for c in m.tool_calls}
    for m in messages:
        if m.role == "tool" and by_id.get(m.tool_call_id) in names and not m.is_error:
            with contextlib.suppress(json.JSONDecodeError):
                out.append((by_id[m.tool_call_id], json.loads(m.content)))
    return out


def _last_products(messages: list[Message]) -> list[dict]:
    for _name, data in reversed(_results(messages, ("search_products", "find_substitutes", "view_basket"))):
        items = data.get("results") or data.get("substitutes") or data.get("lines") or []
        if items:
            return items
    return []


def _order_id(text: str) -> str | None:
    m = re.search(r"\bT-?(\d{4})\b", text, re.I)
    return f"T-{m.group(1)}" if m else None


class OfflineProvider:
    name = "offline"
    model = "offline-router"

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        last_user = max(
            i for i, m in enumerate(messages) if m.role == "user" and not m.content.startswith("[Automatic")
        )
        user = messages[last_user].content
        this_turn = messages[last_user + 1 :]
        done = _results(this_turn, tuple(t.name for t in tools))
        errors = [m.content for m in this_turn if m.role == "tool" and m.is_error]
        calls = self._next(user, messages[:last_user], done, errors)
        text = "" if calls else self._reply(user, messages[:last_user], done, errors)
        usage = Usage(_tokens(system + "".join(m.content for m in messages)), _tokens(text) + 20 * len(calls))
        return LLMResponse(text, calls, usage, self.model)

    # ---------- deciding what to call ----------

    def _next(self, user: str, before: list[Message], done: list, errors: list[str]) -> list[ToolCall]:
        t = user.lower()
        called = [n for n, _ in done]
        if errors:
            return []
        # A yes to something waiting for confirmation.
        if AFFIRMATIVE.search(user) and not called:
            pending = [d for n, d in _results(before, tuple(self._confirmable())) if d.get("needs_confirmation")]
            if pending:
                return [_call("confirm_action", confirmation_id=pending[-1]["confirmation_id"])]
        oid = _order_id(user)

        if re.search(r"\b(person|human|someone real|manager|complain)\b", t) and not called:
            return [_call("escalate_to_human", reason=user[:300], **({"order_id": oid} if oid else {}))]

        issue = re.search(
            r"\b(missing|didn'?t (arrive|come|get)|never arrived|damaged|broken|smashed|leak|crushed|"
            r"wrong (item|product)|instead of|expired|out of date|use-?by|late|unwell|sick)\b",
            t,
        )
        if issue:
            kind = (
                "late_delivery"
                if "late" in t
                else "unwell"
                if re.search(r"unwell|sick", t)
                else "damaged"
                if re.search(r"damaged|broken|smashed|leak|crushed", t)
                else "wrong_item"
                if re.search(r"wrong|instead of", t)
                else "expired"
                if re.search(r"expired|out of date|use-?by", t)
                else "missing"
            )
            if "list_orders" not in called and not oid:
                return [_call("list_orders")]
            if not oid:
                delivered = [o for o in done[called.index("list_orders")][1]["orders"] if o["status"] == "delivered"]
                if not delivered:
                    return []
                oid = delivered[0]["order_id"]
            if "get_order" not in called:
                return [_call("get_order", order_id=oid)]
            if "report_issue" in called:
                return []
            order = done[called.index("get_order")][1]
            if kind in ("late_delivery", "unwell"):
                return [_call("report_issue", order_id=oid, kind=kind, details=user[:400])]
            words = set(re.findall(r"[a-z]{3,}", t)) - {
                "the",
                "and",
                "was",
                "were",
                "from",
                "order",
                "missing",
                "damaged",
            }
            items = [
                {"code": ln["code"], "qty": 1}
                for ln in order["lines"]
                if words & set(re.findall(r"[a-z]{3,}", ln["name"].lower()))
            ]
            if not items:
                return []  # ask which item
            return [_call("report_issue", order_id=oid, kind=kind, items=items, details=user[:400])]

        if re.search(r"\bcancel\b", t):
            if not oid:
                if "list_orders" not in called:
                    return [_call("list_orders")]
                upcoming = [o for o in done[called.index("list_orders")][1]["orders"] if o["status"] == "placed"]
                oid = upcoming[0]["order_id"] if upcoming else None
            return [_call("cancel_order", order_id=oid)] if oid and "cancel_order" not in called else []

        if re.search(r"\b(change|move|reschedule|different (day|time))\b", t) and re.search(r"deliver|order|slot", t):
            if not oid:
                if "list_orders" not in called:
                    return [_call("list_orders")]
                upcoming = [o for o in done[called.index("list_orders")][1]["orders"] if o["status"] == "placed"]
                oid = upcoming[0]["order_id"] if upcoming else None
            if oid and "delivery_slots" not in called:
                return [_call("delivery_slots", days=3)]
            if oid and "change_delivery_slot" not in called:
                slots = [s for s in done[called.index("delivery_slots")][1]["slots"] if s["available"]]
                want = next((s for s in slots if any(d in s["when"].lower() for d in self._days(t))), None)
                chosen = want or (slots[1] if len(slots) > 1 else slots[0])
                return [_call("change_delivery_slot", order_id=oid, slot_id=chosen["slot_id"])]
            return []

        if re.search(r"\b(check ?out|place (the |my |an )?order|order it|buy (it|them|these)|send it)\b", t):
            if "delivery_slots" not in called:
                return [_call("delivery_slots", days=3)]
            if "place_order" not in called:
                slots = [s for s in done[called.index("delivery_slots")][1]["slots"] if s["available"]]
                want = next((s for s in slots if any(d in s["when"].lower() for d in self._days(t))), slots[0])
                return [_call("place_order", slot_id=want["slot_id"])]
            return []

        if re.search(r"\b(slots?|deliver(y|ies)? (times?|windows?)|when can you deliver)\b", t):
            return [] if called else [_call("delivery_slots", days=3)]

        if re.search(r"\b(my orders?|order status|where'?s my|when is my|upcoming)\b", t) or oid:
            if called:
                return []
            return [_call("get_order", order_id=oid)] if oid else [_call("list_orders")]

        if re.search(r"\b(polic(y|ies)|refund|how long|fee|cut-?off|substitut(e|ion) policy|privacy)\b", t):
            return [] if called else [_call("search_help", question=user[:200])]

        if re.search(r"\b(swap|substitute|alternative|instead|replace)\b", t):
            prods = _last_products(before)
            idx = self._ordinal(t)
            if prods and not called:
                target = prods[idx] if idx is not None and -len(prods) <= idx < len(prods) else prods[0]
                return [_call("find_substitutes", code=target["code"])]
            return []

        add = re.search(r"\b(add|put|grab|i'?ll (take|have))\b", t)
        remove = re.search(r"\b(remove|take out|delete|drop)\b", t)
        if (add or remove) and not called:
            prods = _last_products(before)
            idx = self._ordinal(t)
            if prods and (idx is not None or re.search(r"\b(it|that|this|them|those)\b", t)):
                target = prods[idx if idx is not None and -len(prods) <= idx < len(prods) else 0]
                qty = 0 if remove else self._qty(t)
                return [_call("set_basket_qty", code=target["code"], qty=qty)]

        if re.search(r"\b(basket|trolley|cart|total)\b", t):
            return [] if called else [_call("view_basket")]

        if re.search(rf"\b(allerg|contain|{ALLERGY_WORDS})", t) and re.search(r"\b(does|do|is|are|have|has)\b", t):
            prods = _last_products(before)
            if prods and not called:
                allergens = re.findall(rf"\b({ALLERGY_WORDS})\b", t) or ["peanuts"]
                return [_call("check_allergens", codes=[p["code"] for p in prods[:5]], allergens=allergens)]

        if called:
            return []
        query = re.sub(
            r"\b(can you|could you|please|find|show me|search for|look for|do you (have|sell)|i need|"
            r"i want|i'?m after|looking for|some|any|under \$?\d+(\.\d+)?|without|no|free|that'?s|"
            r"which is|for|me)\b",
            " ",
            t,
        )
        for w in LABEL_WORDS:
            query = query.replace(w, " ")
        query = re.sub(rf"\b({ALLERGY_WORDS})\b", " ", query)
        query = " ".join(query.split()).strip(" ?.!,")
        if len(query) < 2:
            return []
        args: dict = {"query": query}
        avoid = re.findall(rf"(?:no|without|free of|free from|allergic to)\s+({ALLERGY_WORDS})", t)
        avoid += re.findall(rf"\b({ALLERGY_WORDS})[- ]free\b", t)
        if avoid:
            args["avoid_allergens"] = sorted(set(avoid))
        labels = [v for k, v in LABEL_WORDS.items() if k in t and not (k == "gluten free" and "gluten" in avoid)]
        if labels:
            args["labels"] = labels
        m = re.search(r"under \$?(\d+(?:\.\d+)?)", t)
        if m:
            args["max_price"] = float(m.group(1))
        return [_call("search_products", **args)]

    @staticmethod
    def _confirmable() -> list[str]:
        return ["place_order", "cancel_order", "set_basket_qty"]

    @staticmethod
    def _days(t: str) -> list[str]:
        return [d for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun") if re.search(rf"\b{d}", t)]

    @staticmethod
    def _ordinal(t: str) -> int | None:
        for w, i in ORDINALS.items():
            if re.search(rf"\b{w}\b", t):
                return i
        m = re.search(r"\b(?:number|#|no\.?)\s*(\d)\b", t)
        return int(m.group(1)) - 1 if m else None

    @staticmethod
    def _qty(t: str) -> int:
        m = re.search(r"\b(\d{1,2})\b(?!\s*(st|nd|rd|th))", t)
        if m:
            return max(1, min(int(m.group(1)), 24))
        for w, n in NUMBERS.items():
            if re.search(rf"\b{w}\b(?! (bit|few))", t) and w not in ("a", "an"):
                return n
        return 1

    # ---------- writing the reply from tool results ----------

    def _reply(self, user: str, before: list[Message], done: list, errors: list[str]) -> str:
        if errors:
            msg = errors[-1].removeprefix("Error: ")
            return f"I couldn't do that: {msg}"
        if not done:
            if re.search(r"\b(missing|damaged|wrong|broken|expired)\b", user.lower()):
                return "Sorry about that. Which item was it, and which order? I'll sort it out."
            return (
                "I can help you find products (with your allergies filtered out), build your basket, change or "
                "cancel an upcoming order, and sort out problems with a delivery. What would you like to do?"
            )
        name, data = done[-1]
        if data.get("needs_confirmation"):
            return f"{data['summary']} Reply yes to confirm."
        return getattr(self, f"_r_{name}", self._r_default)(data, done)

    def _r_default(self, data: dict, done: list) -> str:
        return "Done."

    def _r_search_products(self, data: dict, done: list) -> str:
        res = data["results"]
        if not res:
            return "I couldn't find anything matching that with your allergy filters on. Want to try other words?"
        lines = [f"- [[{p['code']}]]" + ("" if p["in_stock"] else " (out of stock)") for p in res[:5]]
        excl = data["filters_applied"]["excluded_allergens"]
        note = f" I've left out anything with {', '.join(excl)} or that may contain it." if excl else ""
        return "Here's what I found:" + note + "\n" + "\n".join(lines) + "\nWant me to add one?"

    def _r_get_product(self, data: dict, done: list) -> str:
        return f"[[{data['code']}]]: {data['ingredients'][:300]}"

    def _r_check_allergens(self, data: dict, done: list) -> str:
        words = {
            "contains": "contains",
            "may_contain": "may contain",
            "not_listed": "doesn't list",
            "unknown": "has no ingredient data for",
        }
        out = []
        for r in data["results"]:
            if "error" in r:
                continue
            parts = [f"{words[v]} {k}" for k, v in r.items() if k not in ("code", "name")]
            out.append(f"- [[{r['code']}]] {', '.join(parts)}")
        return "From the label data:\n" + "\n".join(out) + "\nRecipes change, so please check the pack too."

    def _r_set_basket_qty(self, data: dict, done: list) -> str:
        if data.get("qty") == 0:
            return f"Removed it. Your basket is now {data['basket_total']}."
        return f"Added {data['qty']} x {data['updated']}. Basket total: {data['basket_total']}."

    def _r_view_basket(self, data: dict, done: list) -> str:
        if not data["lines"]:
            return "Your basket is empty. What are you after?"
        lines = [f"- {ln['qty']} x [[{ln['code']}]] {ln['line_total']}" for ln in data["lines"]]
        return "\n".join(lines) + f"\nTotal {data['total']} including {data['delivery_fee']} delivery."

    def _r_find_substitutes(self, data: dict, done: list) -> str:
        subs = data["substitutes"]
        if not subs:
            return f"I couldn't find a close swap for [[{data['original']['code']}]] that keeps your allergy filters."
        lines = [f"- [[{s['code']}]] ({s['price_difference']} difference)" for s in subs]
        return f"Swaps for [[{data['original']['code']}]] that keep the same allergen and label profile:\n" + "\n".join(
            lines
        )

    def _r_delivery_slots(self, data: dict, done: list) -> str:
        free = [s["when"] for s in data["slots"] if s["available"]][:6]
        return "Next available delivery windows: " + "; ".join(free) + ". Delivery is " + data["fee"] + "."

    def _r_confirm_action(self, data: dict, done: list) -> str:
        if data["done"] == "order placed":
            return f"Done! Order {data['order_id']} is booked for {data['delivery']}, total {data['total']}."
        if data["done"] == "order cancelled":
            return (
                f"Order {data['order_id']} is cancelled. {data['refund']} goes back to your card "
                f"within {data['refund_timing']}."
            )
        return f"Added {data['qty']} x {data['product']}."

    def _r_list_orders(self, data: dict, done: list) -> str:
        lines = [f"- {o['order_id']}: {o['status']}, {o['delivery']}, {o['total']}" for o in data["orders"]]
        return "Your orders:\n" + "\n".join(lines)

    def _r_get_order(self, data: dict, done: list) -> str:
        if any(n == "report_issue" for n, _ in done):
            return self._r_report_issue(done[-1][1], done)
        head = f"Order {data['order_id']} is {data['status']}, delivery {data['delivery']}, total {data['total']}."
        if data.get("can_change"):
            head += f" You can still change it until {data['changes_close']}."
        if data["status"] == "delivered":
            head += " Which item had the problem?"
        return head

    def _r_change_delivery_slot(self, data: dict, done: list) -> str:
        return f"Moved {data['order_id']} to {data['delivery']}. Changes close {data['changes_close']}."

    def _r_search_help(self, data: dict, done: list) -> str:
        if not data["sections"]:
            return "Our policy doesn't cover that. I can pass it to our customer care team if you like."
        s = data["sections"][0]
        return f"{s['text']} ({s['id']})"

    def _r_report_issue(self, data: dict, done: list) -> str:
        o = data["outcome"]
        if o == "refunded":
            return (
                f"Sorry about that. I've refunded {data['amount']} to your original payment method "
                f"(case {data['case_id']}). It usually shows within 3 to 5 business days."
            )
        if o == "credited":
            return f"Sorry it was late. I've added a {data['amount']} credit to your account (case {data['case_id']})."
        if o == "with_our_team":
            return f"I've passed this to our customer care team (case {data['case_id']}). {data['summary']}"
        return f"{data['summary']} (case {data['case_id']})"

    def _r_escalate_to_human(self, data: dict, done: list) -> str:
        return (
            f"I've passed this to our customer care team (case {data['case_id']}). "
            f"They'll reply within {data['reply_within']}."
        )


class ScriptedProvider:
    """Replays canned LLMResponses in order (tests)."""

    name = "scripted"
    model = "scripted"

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.seen: list[list[Message]] = []

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        self.seen.append(list(messages))
        if not self.responses:
            return LLMResponse("(script ended)", [], Usage(), self.model)
        return self.responses.pop(0)
