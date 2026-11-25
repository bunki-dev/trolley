"""The product catalogue: real products from Open Food Facts, normalised for the agent.

What's real: names, brands, sizes, barcodes, ingredients, allergens, labels, nutrition and photos.
What's demo: prices (unless a real price was reported to Open Prices) and stock levels, which a
supermarket's own systems would supply. Every product says which it is.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path

ALLERGENS = {
    "gluten": "gluten",
    "milk": "milk",
    "eggs": "egg",
    "peanuts": "peanut",
    "nuts": "tree nuts",
    "soybeans": "soy",
    "sesame-seeds": "sesame",
    "fish": "fish",
    "crustaceans": "crustacean shellfish",
    "molluscs": "mollusc",
    "lupin": "lupin",
    "sulphur-dioxide-and-sulphites": "sulphites",
    "celery": "celery",
    "mustard": "mustard",
}
# Words customers use -> canonical allergen key.
ALLERGEN_WORDS = {
    "gluten": "gluten",
    "wheat": "gluten",
    "coeliac": "gluten",
    "celiac": "gluten",
    "milk": "milk",
    "dairy": "milk",
    "lactose": "milk",
    "egg": "eggs",
    "eggs": "eggs",
    "peanut": "peanuts",
    "peanuts": "peanuts",
    "nut": "nuts",
    "nuts": "nuts",
    "tree nut": "nuts",
    "tree nuts": "nuts",
    "almond": "nuts",
    "cashew": "nuts",
    "soy": "soybeans",
    "soya": "soybeans",
    "sesame": "sesame-seeds",
    "fish": "fish",
    "shellfish": "crustaceans",
    "prawn": "crustaceans",
    "crustacean": "crustaceans",
    "mollusc": "molluscs",
    "lupin": "lupin",
    "sulphite": "sulphur-dioxide-and-sulphites",
    "sulfite": "sulphur-dioxide-and-sulphites",
    "celery": "celery",
    "mustard": "mustard",
}

LABELS = {
    "en:vegan": "vegan",
    "en:vegetarian": "vegetarian",
    "en:gluten-free": "gluten free",
    "en:no-gluten": "gluten free",
    "en:organic": "organic",
    "en:australian-made": "Australian made",
    "en:made-in-australia": "Australian made",
    "en:no-added-sugar": "no added sugar",
    "en:halal": "halal",
    "en:kosher": "kosher",
    "en:low-fat": "low fat",
    "en:high-protein": "high protein",
}

# First match wins: category tag fragment -> aisle.
AISLES: list[tuple[str, str]] = [
    ("baby-foods", "Baby"),
    ("frozen", "Frozen"),
    ("breakfast-cereals", "Breakfast"),
    ("mueslis", "Breakfast"),
    ("plant-based-milk", "Drinks"),
    ("milks", "Dairy & eggs"),
    ("cheeses", "Dairy & eggs"),
    ("yogurts", "Dairy & eggs"),
    ("butters", "Dairy & eggs"),
    ("creams", "Dairy & eggs"),
    ("eggs", "Dairy & eggs"),
    ("dairies", "Dairy & eggs"),
    ("meats", "Meat & seafood"),
    ("poultry", "Meat & seafood"),
    ("sausages", "Meat & seafood"),
    ("seafood", "Meat & seafood"),
    ("fishes", "Meat & seafood"),
    ("breads", "Bakery"),
    ("biscuits-and-cakes", "Snacks & sweets"),
    ("chocolates", "Snacks & sweets"),
    ("confectioneries", "Snacks & sweets"),
    ("chips", "Snacks & sweets"),
    ("crisps", "Snacks & sweets"),
    ("snacks", "Snacks & sweets"),
    ("waters", "Drinks"),
    ("juices", "Drinks"),
    ("sodas", "Drinks"),
    ("coffees", "Drinks"),
    ("teas", "Drinks"),
    ("beverages", "Drinks"),
    ("fresh-vegetables", "Fruit & veg"),
    ("fresh-fruits", "Fruit & veg"),
    ("pastas", "Pantry"),
    ("rices", "Pantry"),
    ("sauces", "Pantry"),
    ("condiments", "Pantry"),
    ("spreads", "Pantry"),
    ("canned", "Pantry"),
]

# Demo price model by aisle: fixed + per-kilogram (or litre), AUD.
PRICE_MODEL = {
    "Pantry": (1.3, 4.5),
    "Snacks & sweets": (1.6, 11.0),
    "Drinks": (1.1, 1.8),
    "Dairy & eggs": (1.6, 6.0),
    "Meat & seafood": (3.5, 15.0),
    "Frozen": (2.2, 6.5),
    "Bakery": (2.0, 5.0),
    "Breakfast": (2.2, 8.5),
    "Fruit & veg": (1.2, 4.0),
    "Baby": (1.2, 16.0),
}


@dataclass
class Price:
    cents: int
    source: str  # "open_prices" | "demo"
    note: str = ""

    @property
    def dollars(self) -> str:
        return f"${self.cents / 100:.2f}"


@dataclass
class Product:
    code: str
    name: str
    brand: str
    size: str
    aisle: str
    price: Price
    in_stock: bool
    allergens: list[str] = field(default_factory=list)  # canonical keys, from the label data
    traces: list[str] = field(default_factory=list)  # "may contain"
    labels: list[str] = field(default_factory=list)
    likely: list[str] = field(default_factory=list)  # from ingredient analysis, weaker than a label
    ingredients: str = ""
    categories: list[str] = field(default_factory=list)
    nutrition: dict[str, float] = field(default_factory=dict)  # per 100 g/ml
    nutriscore: str = ""
    image: str = ""
    grams: float | None = None  # pack size in g or ml

    @property
    def title(self) -> str:
        brand = self.brand if self.brand and self.brand.lower() not in self.name.lower() else ""
        return " ".join(x for x in (brand, self.name, self.size) if x)

    @property
    def unit_price(self) -> str:
        if not self.grams:
            return ""
        per = self.price.cents / self.grams * (100 if self.grams < 1000 else 1000)
        return f"${per / 100:.2f} per {'100 g' if self.grams < 1000 else 'kg'}"

    def allergen_status(self, allergen: str) -> str:
        """contains | may_contain | not_listed | unknown. Only `not_listed` supports "free from" wording,
        and even then the customer should check the pack."""
        if allergen in self.allergens:
            return "contains"
        if allergen in self.traces:
            return "may_contain"
        return "not_listed" if self.ingredients else "unknown"

    def card(self) -> dict:
        """What tools return to the model and the UI: facts only, with their provenance."""
        return {
            "code": self.code,
            "name": self.title,
            "aisle": self.aisle,
            "price": self.price.dollars,
            "price_source": self.price.source,
            "unit_price": self.unit_price,
            "in_stock": self.in_stock,
            "allergens": [ALLERGENS[a] for a in self.allergens],
            "may_contain": [ALLERGENS[a] for a in self.traces],
            "labels": self.labels,
            "ingredients_known": bool(self.ingredients),
            "image": self.image,
        }


_WORD = re.compile(r"[a-z0-9]+")


def _hash(code: str, salt: str) -> int:
    return int(hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()[:8], 16)


def _aisle(categories: list[str]) -> str:
    joined = " ".join(categories)
    for frag, aisle in AISLES:
        if frag in joined:
            return aisle
    return "Pantry"


def _grams(p: dict) -> float | None:
    try:
        q = float(p.get("product_quantity") or 0)
    except (TypeError, ValueError):
        q = 0
    unit = (p.get("product_quantity_unit") or "g").lower()
    if q <= 0 or q > 20000:
        return None
    return q * (1000 if unit in ("kg", "l") else 1)


def demo_price(code: str, aisle: str, grams: float | None) -> int:
    fixed, per_kg = PRICE_MODEL.get(aisle, (2.0, 6.0))
    kg = (grams or 400) / 1000
    dollars = fixed + per_kg * min(kg, 5)
    jitter = 0.85 + (_hash(code, "price") % 31) / 100  # +/- 15%, stable per product
    cents = round(dollars * jitter * 20) * 5  # nearest 5c
    return max(80, min(cents, 4000))


def _tags(values: list[str] | None, mapping: dict[str, str]) -> list[str]:
    out = []
    for t in values or []:
        key = t.split(":", 1)[-1]
        if key in mapping and key not in out:
            out.append(key)
    return out


def normalise(raw: dict, real_prices: dict[str, dict]) -> Product:
    cats = raw.get("categories_tags") or []
    aisle = _aisle(cats)
    grams = _grams(raw)
    rp = real_prices.get(raw["code"])
    if rp:
        price = Price(
            round(float(rp["price"]) * 100), "open_prices", f"{rp.get('store') or 'a store'}, {rp.get('date')}"
        )
    else:
        price = Price(demo_price(raw["code"], aisle, grams), "demo")
    n = raw.get("nutriments") or {}
    labels: list[str] = []
    for t in raw.get("labels_tags") or []:
        if t in LABELS and LABELS[t] not in labels:
            labels.append(LABELS[t])
    likely = [
        w
        for t, w in (("en:vegan", "vegan"), ("en:vegetarian", "vegetarian"), ("en:palm-oil-free", "palm oil free"))
        if t in (raw.get("ingredients_analysis_tags") or []) and w not in labels
    ]
    return Product(
        code=raw["code"],
        name=(raw.get("product_name_en") or raw.get("product_name") or "").strip(),
        brand=(raw.get("brands") or "").split(",")[0].strip(),
        size=(raw.get("quantity") or "").strip(),
        aisle=aisle,
        price=price,
        in_stock=_hash(raw["code"], "stock") % 23 != 0,  # ~4% out of stock, to exercise substitutions
        allergens=_tags(raw.get("allergens_tags"), ALLERGENS),
        traces=_tags(raw.get("traces_tags"), ALLERGENS),
        labels=labels,
        likely=likely,
        ingredients=(raw.get("ingredients_text_en") or raw.get("ingredients_text") or "").strip(),
        categories=[c.split(":", 1)[-1] for c in cats if c.startswith("en:")],
        nutrition={k.replace("_100g", ""): float(v) for k, v in n.items() if isinstance(v, (int, float))},
        nutriscore=(raw.get("nutriscore_grade") or "").upper() if raw.get("nutriscore_grade") in list("abcde") else "",
        image=raw.get("image_front_small_url") or "",
        grams=grams,
    )


class Catalogue:
    def __init__(self, products: list[Product], meta: dict | None = None):
        self.products = products
        self.by_code = {p.code: p for p in products}
        self.meta = meta or {}

    def get(self, code: str) -> Product | None:
        return self.by_code.get(str(code).strip())

    @classmethod
    def from_files(cls, catalogue: Path, prices: Path | None = None) -> Catalogue:
        with gzip.open(catalogue, "rt", encoding="utf-8") as f:
            data = json.load(f)
        real = {}
        if prices and prices.exists():
            for p in json.loads(prices.read_text())["prices"]:
                if (p.get("code") not in real) or str(p.get("date") or "") > str(real[p["code"]].get("date") or ""):
                    real[p["code"]] = p
        products, seen = [], set()
        for raw in data["products"]:
            p = normalise(raw, real)
            if p.name and p.code not in seen:
                seen.add(p.code)
                products.append(p)
        return cls(products, data.get("meta"))


def data_dir() -> Path:
    here = Path(__file__).resolve()
    env = os.environ.get("TROLLEY_DATA_DIR")
    candidates = [Path(env)] if env else []
    candidates += [here.parents[2] / "data", Path(str(resources.files("trolley"))) / "data"]
    for candidate in candidates:
        if (candidate / "catalogue.json.gz").exists():
            return candidate
    raise FileNotFoundError("No product snapshot found. Run scripts/fetch_catalogue.py.")


@lru_cache(maxsize=1)
def load() -> Catalogue:
    d = data_dir()
    return Catalogue.from_files(d / "catalogue.json.gz", d / "prices.json")


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())
