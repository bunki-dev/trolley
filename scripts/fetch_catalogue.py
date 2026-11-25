"""Snapshot real Australian supermarket products from Open Food Facts (and any real AUD prices
from Open Prices) into data/. Run by the "Refresh product data" workflow; the snapshot is committed
so the agent and its evals run against a fixed, reviewable dataset.

Data: Open Food Facts contributors, ODbL 1.0 (https://world.openfoodfacts.org/data).
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data"
UA = "Trolley/0.1 (portfolio project; https://github.com/bunki-dev/trolley)"
SEARCH = "https://world.openfoodfacts.org/api/v2/search"
PRICES = "https://prices.openfoodfacts.org/api/v1/prices"
FIELDS = [
    "code",
    "product_name",
    "product_name_en",
    "generic_name",
    "brands",
    "quantity",
    "product_quantity",
    "product_quantity_unit",
    "serving_size",
    "categories_tags",
    "labels_tags",
    "allergens_tags",
    "traces_tags",
    "ingredients_text",
    "ingredients_text_en",
    "ingredients_analysis_tags",
    "nutriments",
    "nutriscore_grade",
    "nova_group",
    "stores_tags",
    "image_front_small_url",
    "image_front_url",
    "last_modified_t",
]
KEEP_NUTRIENTS = [
    "energy-kcal_100g",
    "energy-kj_100g",
    "proteins_100g",
    "carbohydrates_100g",
    "sugars_100g",
    "fat_100g",
    "saturated-fat_100g",
    "fiber_100g",
    "sodium_100g",
    "salt_100g",
]


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(4):
        try:
            t = time.time()
            with urllib.request.urlopen(req, timeout=40) as r:
                data = json.loads(r.read())
            print(f"  {url[:90]}... {time.time() - t:.1f}s", flush=True)
            return data
        except Exception as e:  # rate limits and blips: back off and retry
            wait = 10 * (attempt + 1)
            print(f"  retry in {wait}s ({e})", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"giving up on {url}")


def slim(p: dict) -> dict:
    n = p.get("nutriments") or {}
    return {
        **{k: p.get(k) for k in FIELDS if k not in ("nutriments",)},
        "nutriments": {k: n[k] for k in KEEP_NUTRIENTS if k in n},
    }


def fetch_products(store: str = "coles", page_size: int = 100) -> list[dict]:
    products: dict[str, dict] = {}
    page = 1
    while True:
        q = {
            "countries_tags": "en:australia",
            "stores_tags": store,
            "fields": ",".join(FIELDS),
            "page_size": page_size,
            "page": page,
            "sort_by": "unique_scans_n",
        }
        try:
            data = get(f"{SEARCH}?{urllib.parse.urlencode(q)}")
        except RuntimeError:
            legacy = {
                "action": "process",
                "json": 1,
                "page_size": page_size,
                "page": page,
                "fields": q["fields"],
                "tagtype_0": "countries",
                "tag_contains_0": "contains",
                "tag_0": "australia",
                "tagtype_1": "stores",
                "tag_contains_1": "contains",
                "tag_1": store,
            }
            data = get(f"https://world.openfoodfacts.org/cgi/search.pl?{urllib.parse.urlencode(legacy)}")
        batch = data.get("products", [])
        for p in batch:
            if p.get("code") and (p.get("product_name") or p.get("product_name_en")):
                products[p["code"]] = slim(p)
        print(f"page {page}: {len(batch)} products ({len(products)} kept of {data.get('count')})", flush=True)
        if len(batch) < page_size:
            break
        page += 1
        time.sleep(7)  # Open Food Facts asks for at most ~10 search requests a minute
    return list(products.values())


def fetch_prices() -> list[dict]:
    out, page = [], 1
    while True:
        data = get(f"{PRICES}?{urllib.parse.urlencode({'currency': 'AUD', 'size': 100, 'page': page})}")
        for it in data.get("items", []):
            loc = it.get("location") or {}
            if it.get("product_code") and it.get("price"):
                out.append(
                    {
                        "code": it["product_code"],
                        "price": it["price"],
                        "price_per": it.get("price_per"),
                        "date": it.get("date"),
                        "store": loc.get("osm_name"),
                        "city": loc.get("osm_address_city"),
                    }
                )
        if page >= (data.get("pages") or 1):
            break
        page += 1
        time.sleep(2)
    return out


def main() -> None:
    OUT.mkdir(exist_ok=True)
    products = fetch_products()
    prices = fetch_prices()
    meta = {
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "Open Food Facts (products sold in Australia, tagged as stocked at a major supermarket)",
        "licence": "Open Database License (ODbL) 1.0; images CC BY-SA. https://world.openfoodfacts.org/data",
        "products": len(products),
        "real_prices": len(prices),
    }
    with gzip.open(OUT / "catalogue.json.gz", "wt", encoding="utf-8") as f:
        json.dump({"meta": meta, "products": products}, f, ensure_ascii=False, separators=(",", ":"))
    (OUT / "prices.json").write_text(json.dumps({"meta": meta, "prices": prices}, indent=1, ensure_ascii=False))
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
