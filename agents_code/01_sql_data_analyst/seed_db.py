"""Create a small sample sales database so the agent runs out of the box."""
import random
import sqlite3
from datetime import date, timedelta

PRODUCTS = [
    ("Aurora Lamp", "Lighting", 129.0),
    ("Nimbus Chair", "Furniture", 249.0),
    ("Terra Desk", "Furniture", 399.0),
    ("Pulse Speaker", "Audio", 89.0),
    ("Echo Buds", "Audio", 59.0),
    ("Halo Monitor", "Displays", 319.0),
    ("Drift Mouse", "Accessories", 39.0),
    ("Key Slate", "Accessories", 79.0),
]
REGIONS = ["North", "South", "East", "West"]


def seed(path: str, n_orders: int = 1500, seed_value: int = 7) -> None:
    random.seed(seed_value)
    con = sqlite3.connect(path)
    con.executescript(
        """
        DROP TABLE IF EXISTS orders; DROP TABLE IF EXISTS products; DROP TABLE IF EXISTS customers;
        CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, category TEXT, list_price REAL);
        CREATE TABLE customers (id INTEGER PRIMARY KEY, region TEXT, signup_date TEXT, email TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, product_id INTEGER, customer_id INTEGER,
                             quantity INTEGER, amount REAL, order_date TEXT,
                             FOREIGN KEY(product_id) REFERENCES products(id),
                             FOREIGN KEY(customer_id) REFERENCES customers(id));
        """
    )
    con.executemany("INSERT INTO products(name, category, list_price) VALUES (?,?,?)", PRODUCTS)
    today = date.today()
    customers = [
        (random.choice(REGIONS), (today - timedelta(days=random.randint(30, 700))).isoformat(), f"user{i}@example.com")
        for i in range(1, 201)
    ]
    con.executemany("INSERT INTO customers(region, signup_date, email) VALUES (?,?,?)", customers)
    orders = []
    for _ in range(n_orders):
        pid = random.randint(1, len(PRODUCTS))
        qty = random.choice([1, 1, 1, 2, 3])
        price = PRODUCTS[pid - 1][2] * random.uniform(0.85, 1.0)
        d = today - timedelta(days=random.randint(0, 180))
        orders.append((pid, random.randint(1, 200), qty, round(price * qty, 2), d.isoformat()))
    con.executemany("INSERT INTO orders(product_id, customer_id, quantity, amount, order_date) VALUES (?,?,?,?,?)", orders)
    con.commit()
    con.close()


if __name__ == "__main__":
    import sys

    seed(sys.argv[1] if len(sys.argv) > 1 else "sales.db")
    print("seeded")
