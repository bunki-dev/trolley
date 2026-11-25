import asyncio
import gzip
import json

from conftest import RAW

from trolley import browser


def test_browser_bridge_round_trip(tmp_path):
    with gzip.open(tmp_path / "catalogue.json.gz", "wt") as f:
        json.dump({"meta": {"products": len(RAW)}, "products": RAW}, f)
    start = json.loads(browser.init(str(tmp_path)))
    assert start["products"] == len(RAW) and start["state"]["customer"]["name"] == "Alex"

    res = json.loads(asyncio.run(browser.chat("find me some potato chips", "{}")))
    assert res["products"] and all(c in res["reply"] for c in res["products"])
    assert res["trace"]["spans"] and res["flags"] == []

    # A tapped Add on a peanut product asks first; the tap on Confirm is the customer's yes.
    asked = json.loads(browser.tool("set_basket_qty", json.dumps({"code": "9300000000011", "qty": 1})))
    assert asked["result"]["needs_confirmation"] and asked["state"]["basket"]["items"] == 0
    done = json.loads(browser.confirm(asked["result"]["confirmation_id"]))
    assert done["state"]["basket"]["items"] == 1

    # The app can't call tools that spend, cancel or refund directly.
    assert "error" in json.loads(browser.tool("cancel_order", json.dumps({"order_id": "T-1003"})))
