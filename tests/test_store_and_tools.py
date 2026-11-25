import json

import pytest

from trolley.agent.agent import Session
from trolley.search import SearchIndex
from trolley.tools import TOOLS_BY_NAME, ToolError


def call(s, name, **args):
    return TOOLS_BY_NAME[name].run(s.ctx, args)


@pytest.fixture
def s(catalogue):
    return Session(catalogue, SearchIndex(catalogue))


def test_search_always_applies_the_customers_allergy(s):
    out = call(s, "search_products", query="spread")
    names = [r["name"] for r in out["results"]]
    assert "Nutty Co Crunchy Peanut Butter 375 g" not in names  # contains peanuts
    assert not any("Almond" in n for n in names)  # may contain peanuts
    assert any("Sunflower" in n for n in names)
    assert "peanut" in out["filters_applied"]["excluded_allergens"]


def test_strict_filter_drops_products_without_ingredient_data(s):
    out = call(s, "search_products", query="crackers chips", avoid_allergens=["gluten"])
    assert all("Mystery" not in r["name"] for r in out["results"])
    assert any("Chips" in r["name"] for r in out["results"])


def test_labels_and_price_filters(s):
    out = call(s, "search_products", query="penne pasta", labels=["gluten free"])
    assert [r["name"] for r in out["results"]] == ["Bella Gluten Free Penne Pasta 500 g"]


def test_allergen_item_needs_confirmation_on_the_next_turn(s):
    s.store.new_turn("add the peanut butter")
    out = call(s, "set_basket_qty", code="9300000000011", qty=1)
    assert out["needs_confirmation"] and not s.store.basket
    with pytest.raises(ToolError, match="hasn't replied"):
        call(s, "confirm_action", confirmation_id=out["confirmation_id"])
    s.store.new_turn("maybe later")
    with pytest.raises(ToolError, match="clear yes"):
        call(s, "confirm_action", confirmation_id=out["confirmation_id"])
    s.store.new_turn("yes please, add it")
    call(s, "confirm_action", confirmation_id=out["confirmation_id"])
    assert s.store.basket == {"9300000000011": 1}


def test_no_cancels_pending_actions(s):
    s.store.new_turn("cancel my order")
    out = call(s, "cancel_order", order_id="T-1003")
    s.store.new_turn("no, don't")
    with pytest.raises(ToolError, match="Nothing is waiting"):
        call(s, "confirm_action", confirmation_id=out["confirmation_id"])
    assert s.store.orders["T-1003"].status == "placed"


def test_other_customers_orders_are_invisible(s):
    with pytest.raises(ToolError, match="can't find order T-2001"):
        call(s, "get_order", order_id="T-2001")
    assert "T-2001" not in json.dumps(call(s, "list_orders"))


def test_place_order_flow(s):
    s.store.new_turn("add chips")
    call(s, "set_basket_qty", code="9300000000097", qty=2)
    slot = next(x for x in call(s, "delivery_slots", days=2)["slots"] if x["available"])
    out = call(s, "place_order", slot_id=slot["slot_id"])
    s.store.new_turn("yes")
    done = call(s, "confirm_action", confirmation_id=out["confirmation_id"])
    assert done["done"] == "order placed" and not s.store.basket
    assert done["order_id"] in s.store.orders


def test_change_slot_respects_cutoff(s):
    o = s.store.orders["T-1003"]
    slot = [x for x in call(s, "delivery_slots", days=3)["slots"] if x["available"]][-1]
    call(s, "change_delivery_slot", order_id="T-1003", slot_id=slot["slot_id"])
    assert s.store.orders["T-1003"].slot_start != o.placed_at
    s.store.now = o.cutoff  # 10pm the night before: changes close
    with pytest.raises(ToolError, match="closed"):
        call(s, "change_delivery_slot", order_id="T-1003", slot_id=slot["slot_id"])


def test_refund_engine_decides_amounts(s):
    o = s.store.orders["T-1001"]
    line = o.lines[0]
    out = call(s, "report_issue", order_id="T-1001", kind="missing", items=[{"code": line.code, "qty": 1}])
    assert out["outcome"] == "refunded" and out["amount"] == f"${line.unit_cents / 100:.2f}"
    with pytest.raises(ToolError, match="already reported"):
        call(s, "report_issue", order_id="T-1001", kind="missing", items=[{"code": line.code, "qty": 1}])
    with pytest.raises(ToolError, match="isn't on order"):
        call(s, "report_issue", order_id="T-1001", kind="damaged", items=[{"code": "9300000000158", "qty": 1}])


def test_late_credit_once(s):
    first = call(s, "report_issue", order_id="T-1002", kind="late_delivery")
    assert first["outcome"] == "credited" and first["amount"] == "$10.00"
    again = call(s, "report_issue", order_id="T-1002", kind="late_delivery")
    assert again["outcome"] == "declined"
    on_time = call(s, "report_issue", order_id="T-1001", kind="late_delivery")
    assert on_time["outcome"] == "declined"


def test_over_limit_goes_to_a_person(s):
    o = s.store.orders["T-1001"]
    for ln in o.lines:
        ln.unit_cents = 3000
    out = call(s, "report_issue", order_id="T-1001", kind="damaged",
               items=[{"code": ln.code, "qty": 1} for ln in o.lines[:2]])  # fmt: skip
    assert out["outcome"] == "with_our_team" and "P4" in out["policy"]


def test_substitutes_keep_allergen_profile(s):
    out = call(s, "find_substitutes", code="9300000000097")  # chips, no listed allergens
    assert out["substitutes"] and all(sub["allergens"] == [] for sub in out["substitutes"])


def test_policy_search_returns_sections(s):
    out = call(s, "search_help", question="my delivery was late, do I get anything?")
    assert out["sections"][0]["id"] == "P3"
