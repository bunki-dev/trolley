"""Product search: BM25 over names, brands and categories, with hard filters for dietary needs.

Filters are applied in code, never left to the model: a peanut-free search can't return a product
whose label data lists peanuts, and with strict=True it also drops "may contain" and products whose
ingredients are unknown.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from .catalogue import ALLERGEN_WORDS, Catalogue, Product, words

SYNONYMS = {
    "chips": ["crisps", "chips"],
    "crisps": ["chips", "crisps"],
    "mince": ["mince", "minced", "ground"],
    "soda": ["soft", "drink", "soda"],
    "lollies": ["confectionery", "candy", "lollies", "sweets"],
    "bikkies": ["biscuits"],
    "biscuits": ["biscuits", "cookies"],
    "cookies": ["cookies", "biscuits"],
    "yoghurt": ["yogurt", "yoghurt"],
    "yogurt": ["yogurt", "yoghurt"],
    "brekkie": ["breakfast", "cereal"],
    "cereal": ["cereal", "cereals", "muesli", "oats"],
    "pasta": ["pasta", "spaghetti", "penne", "fusilli"],
    "noodles": ["noodles", "noodle"],
    "snags": ["sausages"],
    "capsicum": ["capsicum", "pepper"],
}
STOP = {"a", "an", "the", "some", "for", "of", "and", "with", "to", "me", "i", "want", "need", "find", "get", "any"}


@dataclass
class Hit:
    product: Product
    score: float


class SearchIndex:
    def __init__(self, catalogue: Catalogue, k1: float = 1.3, b: float = 0.6):
        self.cat = catalogue
        self.k1, self.b = k1, b
        self.docs: list[Counter] = []
        df: Counter = Counter()
        for p in catalogue.products:
            # Name words count twice: they matter more than category tags.
            toks = words(p.name) * 2 + words(p.brand) + words(" ".join(p.categories).replace("-", " ")) + words(p.aisle)
            c = Counter(toks)
            self.docs.append(c)
            df.update(c.keys())
        n = len(self.docs) or 1
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.avg = sum(sum(d.values()) for d in self.docs) / n

    def _expand(self, query: str) -> list[str]:
        out: list[str] = []
        for w in words(query):
            if w in STOP:
                continue
            out += SYNONYMS.get(w, [w])
            if w.endswith("s") and len(w) > 3:
                out.append(w[:-1])
        return out

    def search(
        self,
        query: str,
        *,
        exclude_allergens: list[str] = (),
        strict: bool = True,
        labels: list[str] = (),
        aisle: str | None = None,
        max_price_cents: int | None = None,
        in_stock_only: bool = False,
        limit: int = 8,
    ) -> list[Hit]:
        terms = self._expand(query)
        groups = [self._expand(w) for w in words(query) if w not in STOP]
        groups = [g for g in groups if g]
        hits: list[Hit] = []
        for p, doc in zip(self.cat.products, self.docs, strict=True):
            if not passes(p, exclude_allergens, strict, labels, aisle, max_price_cents, in_stock_only):
                continue
            score = 0.0
            dl = sum(doc.values())
            for t in terms:
                tf = doc.get(t, 0)
                if tf:
                    score += (
                        self.idf.get(t, 0) * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * dl / self.avg))
                    )
            if terms and score <= 0:
                continue
            # Favour products that match more of what was asked: "gluten free pasta" should rank pasta
            # above gluten-free chips. Each query word counts once, whichever synonym matched it.
            if groups:
                covered = sum(any(doc.get(t) for t in g) for g in groups) / len(groups)
                score *= 0.25 + 0.75 * covered**2
            # Small, explainable boosts: in stock, has full label data.
            score += 0.3 * p.in_stock + 0.2 * bool(p.ingredients)
            hits.append(Hit(p, score))
        hits.sort(key=lambda h: (-h.score, h.product.price.cents))
        return hits[:limit]


def passes(
    p: Product,
    exclude_allergens: list[str] = (),
    strict: bool = True,
    labels: list[str] = (),
    aisle: str | None = None,
    max_price_cents: int | None = None,
    in_stock_only: bool = False,
) -> bool:
    for a in exclude_allergens:
        status = p.allergen_status(a)
        if status == "contains" or (strict and status in ("may_contain", "unknown")):
            return False
    for label in labels:
        if label.lower() not in [x.lower() for x in p.labels]:
            return False
    if aisle and p.aisle.lower() != aisle.lower():
        return False
    if max_price_cents is not None and p.price.cents > max_price_cents:
        return False
    return not (in_stock_only and not p.in_stock)


def allergen_keys(names: list[str]) -> tuple[list[str], list[str]]:
    """Customer words ("dairy", "peanuts") -> canonical keys; also returns words we didn't recognise."""
    keys, unknown = [], []
    for n in names:
        k = ALLERGEN_WORDS.get(n.strip().lower().rstrip("s")) or ALLERGEN_WORDS.get(n.strip().lower())
        if k and k not in keys:
            keys.append(k)
        elif not k:
            unknown.append(n)
    return keys, unknown
