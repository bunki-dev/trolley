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


def get(url: str, attempts: int = 5) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(attempts):
        try:
            t = time.time()
            with urllib.request.urlopen(req, timeout=40) as r:
                data = json.loads(r.read())
            print(f"  {url[:90]}... {time.time() - t:.1f}s", flush=True)
            return data
        except Exception as e:  # Open Food Facts sheds load with 503s: back off patiently
            wait = 30 * (attempt + 1)
            print(f"  retry in {wait}s ({e})", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"giving up on {url[:120]}")


def slim(p: dict) -> dict:
    n = p.get("nutriments") or {}
    return {
        **{k: p.get(k) for k in FIELDS if k not in ("nutriments",)},
        "nutriments": {k: n[k] for k in KEEP_NUTRIENTS if k in n},
    }


# Open Food Facts often refuses deep pages of one big query, so the catalogue is gathered as several
# smaller slices (the most-scanned products, then aisle by aisle) and merged with the last snapshot.
SLICES: list[dict] = [{}] + [
    {"categories_tags": f"en:{c}"}
    for c in (
        "snacks", "dairies", "beverages", "breakfasts", "cereals-and-potatoes", "biscuits-and-cakes",
        "chocolates", "sauces", "spreads", "breads", "frozen-foods", "meats", "plant-based-foods",
        "canned-foods", "condiments", "pastas", "cheeses", "yogurts", "confectioneries", "baby-foods",
    )
]  # fmt: skip
MAX_PAGES_PER_SLICE = 8
TIME_BUDGET_S = 70 * 60


def fetch_products(store: str = "coles", page_size: int = 100) -> dict[str, dict]:
    """Collect products slice by slice. A slice stops at its first page that keeps failing; the run stops
    when the time budget is spent. Whatever arrived is kept, and the next refresh adds to it."""
    products: dict[str, dict] = {}
    started = time.time()
    for extra in SLICES:
        label = extra.get("categories_tags", "most scanned")
        for page in range(1, MAX_PAGES_PER_SLICE + 1):
            if time.time() - started > TIME_BUDGET_S:
                print("time budget spent", flush=True)
                return products
            q = {
                "countries_tags": "en:australia",
                "stores_tags": store,
                **extra,
                "fields": ",".join(FIELDS),
                "page_size": page_size,
                "page": page,
                "sort_by": "unique_scans_n",
            }
            try:
                data = get(f"{SEARCH}?{urllib.parse.urlencode(q)}", attempts=4)
            except RuntimeError:
                print(f"{label} page {page}: skipped, moving on", flush=True)
                break
            batch = data.get("products", [])
            for p in batch:
                if p.get("code") and (p.get("product_name") or p.get("product_name_en")):
                    products[p["code"]] = slim(p)
            print(f"{label} page {page}: {len(batch)} products, {len(products)} in total", flush=True)
            time.sleep(10)  # Open Food Facts asks for at most ~10 search requests a minute
            if len(batch) < page_size:
                break
    return products


def previous_products() -> dict[str, dict]:
    path = OUT / "catalogue.json.gz"
    if not path.exists():
        return {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return {p["code"]: p for p in json.load(f).get("products", [])}


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
    fresh = fetch_products()
    merged = {**previous_products(), **fresh}  # fresh data wins; products not seen this time are kept
    if len(merged) < 300:
        raise SystemExit(f"Only {len(merged)} products; keeping the previous snapshot.")
    products = list(merged.values())
    print(f"{len(fresh)} fetched this run, {len(products)} in the snapshot", flush=True)
    try:
        prices = fetch_prices()
    except RuntimeError as e:
        print(f"Open Prices unavailable ({e}); continuing with demo prices only.")
        prices = []
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
