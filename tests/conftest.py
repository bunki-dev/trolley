import pytest

from trolley.catalogue import Catalogue, normalise

RAW = [
    {"code": "9300000000011", "product_name": "Crunchy Peanut Butter", "brands": "Nutty Co", "quantity": "375 g",
     "product_quantity": 375, "categories_tags": ["en:spreads", "en:peanut-butters"],
     "allergens_tags": ["en:peanuts"], "ingredients_text": "Peanuts (99%), salt"},
    {"code": "9300000000028", "product_name": "Smooth Almond Spread", "brands": "Nutty Co", "quantity": "250 g",
     "product_quantity": 250, "categories_tags": ["en:spreads", "en:nut-butters"],
     "allergens_tags": ["en:nuts"], "traces_tags": ["en:peanuts"], "ingredients_text": "Almonds"},
    {"code": "9300000000035", "product_name": "Sunflower Seed Spread", "brands": "Seedy", "quantity": "250 g",
     "product_quantity": 250, "categories_tags": ["en:spreads", "en:seed-butters"], "allergens_tags": [],
     "ingredients_text": "Sunflower seeds, salt", "labels_tags": ["en:vegan"]},
    {"code": "9300000000042", "product_name": "Full Cream Milk", "brands": "Dairy Farm", "quantity": "2 L",
     "product_quantity": 2, "product_quantity_unit": "l", "categories_tags": ["en:dairies", "en:milks"],
     "allergens_tags": ["en:milk"], "ingredients_text": "Milk"},
    {"code": "9300000000059", "product_name": "Oat Milk Barista", "brands": "Oaty", "quantity": "1 L",
     "product_quantity": 1, "product_quantity_unit": "l", "categories_tags": ["en:beverages", "en:plant-based-milks"],
     "allergens_tags": ["en:gluten"], "ingredients_text": "Water, oats", "labels_tags": ["en:vegan"]},
    {"code": "9300000000066", "product_name": "Penne Pasta", "brands": "Bella", "quantity": "500 g",
     "product_quantity": 500, "categories_tags": ["en:pastas"], "allergens_tags": ["en:gluten"],
     "ingredients_text": "Durum wheat semolina"},
    {"code": "9300000000073", "product_name": "Gluten Free Penne Pasta", "brands": "Bella", "quantity": "500 g",
     "product_quantity": 500, "categories_tags": ["en:pastas"], "allergens_tags": [],
     "ingredients_text": "Corn flour, rice flour", "labels_tags": ["en:gluten-free"]},
    {"code": "9300000000080", "product_name": "Mystery Crackers", "brands": "Snacko", "quantity": "200 g",
     "product_quantity": 200, "categories_tags": ["en:snacks", "en:crackers"]},
    {"code": "9300000000097", "product_name": "Salted Potato Chips", "brands": "Snacko", "quantity": "175 g",
     "product_quantity": 175, "categories_tags": ["en:snacks", "en:chips"], "allergens_tags": [],
     "ingredients_text": "Potatoes, oil, salt"},
    {"code": "9300000000103", "product_name": "Sea Salt Potato Chips", "brands": "Crispo", "quantity": "150 g",
     "product_quantity": 150, "categories_tags": ["en:snacks", "en:chips"], "allergens_tags": [],
     "ingredients_text": "Potatoes, sunflower oil, sea salt"},
    {"code": "9300000000110", "product_name": "Greek Yoghurt", "brands": "Dairy Farm", "quantity": "1 kg",
     "product_quantity": 1000, "categories_tags": ["en:dairies", "en:yogurts"], "allergens_tags": ["en:milk"],
     "ingredients_text": "Milk, cultures"},
    {"code": "9300000000127", "product_name": "Tomato Pasta Sauce", "brands": "Bella", "quantity": "500 g",
     "product_quantity": 500, "categories_tags": ["en:sauces", "en:pasta-sauces"], "allergens_tags": [],
     "ingredients_text": "Tomatoes, onion, garlic"},
    {"code": "9300000000134", "product_name": "Sparkling Water", "brands": "Fizz", "quantity": "1.25 L",
     "product_quantity": 1.25, "product_quantity_unit": "l", "categories_tags": ["en:beverages", "en:waters"],
     "allergens_tags": [], "ingredients_text": "Carbonated water"},
    {"code": "9300000000141", "product_name": "Frozen Peas", "brands": "Green", "quantity": "1 kg",
     "product_quantity": 1000, "categories_tags": ["en:frozen-foods", "en:peas"], "allergens_tags": [],
     "ingredients_text": "Peas"},
    {"code": "9300000000158", "product_name": "Chocolate Peanut Bar", "brands": "Snacko", "quantity": "50 g",
     "product_quantity": 50, "categories_tags": ["en:snacks", "en:chocolates"],
     "allergens_tags": ["en:peanuts", "en:milk"],
     "ingredients_text": "Chocolate, peanuts"},
]  # fmt: skip


@pytest.fixture
def catalogue():
    return Catalogue([normalise(r, {}) for r in RAW])
