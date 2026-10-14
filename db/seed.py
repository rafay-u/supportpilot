#!/usr/bin/env python3
"""
Seed ShopEase with fake but realistic data.

Usage:
    python seed.py --dry-run                 # build data in memory, print summary, touch nothing
    DATABASE_URL=postgresql://... python seed.py   # wipe tables and load fresh data

Optional:
    TEST_PHONE=+923001234567   # becomes customer #1, so you can test from your own phone

The script has two clearly separate parts:
    1. BUILD  - pure Python, creates lists of dicts (no database needed)
    2. LOAD   - inserts those lists into Postgres inside ONE transaction
"""
import argparse
import os
import random
from collections import Counter
from datetime import datetime, timedelta, timezone

NOW = datetime.now(timezone.utc)
rng = random.Random(42)  # fixed seed = same "random" data every run (reproducible)


def ago(**kwargs):
    return NOW - timedelta(**kwargs)


# ------------------------------------------------------------------
# Raw ingredients
# ------------------------------------------------------------------
FIRST_NAMES = ["Ali", "Ahmed", "Hassan", "Usman", "Bilal", "Hamza", "Zain", "Fahad",
               "Saad", "Omar", "Ayesha", "Fatima", "Hira", "Sana", "Maryam", "Zainab",
               "Noor", "Mahnoor", "Iqra", "Sara"]
LAST_NAMES = ["Khan", "Malik", "Sheikh", "Qureshi", "Siddiqui", "Butt", "Chaudhry",
              "Raza", "Hussain", "Iqbal", "Ansari", "Mirza", "Baig", "Farooq", "Javed"]
CITIES = ["Karachi", "Lahore", "Islamabad", "Rawalpindi", "Faisalabad", "Multan",
          "Peshawar", "Quetta", "Sialkot", "Hyderabad"]

# category -> (product types, (min price, max price), warranty months)
CATALOG = {
    "Audio": (["Wireless Earbuds", "Bluetooth Speaker", "Over-Ear Headphones", "Neckband Earphones"],
              (1500, 12000), 6),
    "Chargers & Cables": (["Fast Charger 33W", "USB-C Cable", "Power Bank 10000mAh", "Car Charger"],
                          (400, 4500), 3),
    "Smart Gadgets": (["Smart Watch", "Fitness Band", "WiFi Smart Plug", "Security Camera"],
                      (3000, 25000), 12),
    "Home Appliances": (["Electric Kettle", "Air Fryer", "Hand Blender", "Rechargeable Fan"],
                        (2500, 30000), 12),
    "Accessories": (["Phone Case", "Laptop Sleeve", "Screen Protector", "Phone Stand"],
                    (300, 3000), 0),
}
BRANDS = ["Zenix", "Auralite"]

PAYMENT_WEIGHTS = {"cod": 70, "card": 10, "easypaisa": 12, "jazzcash": 8}

STATUS_WEIGHTS = {"delivered": 55, "shipped": 8, "out_for_delivery": 4, "packed": 5,
                  "placed": 5, "cancelled": 10, "returned": 8}
# how long ago (days) an order in each status was placed - keeps timelines believable
DAYS_AGO = {"placed": (0, 1), "packed": (1, 2), "shipped": (3, 6),
            "out_for_delivery": (4, 6), "delivered": (8, 45),
            "cancelled": (0, 20), "returned": (14, 40)}

RETURN_REASONS = ["Item arrived damaged", "Wrong item received", "Item not as described",
                  "Changed my mind", "Stopped working after 2 days"]

TICKET_TEMPLATES = {
    "delivery": ["Order is late", "Courier did not call before delivery", "Tracking not updating"],
    "refund": ["Refund not received yet", "Asked for refund twice, no reply"],
    "product_issue": ["Product stopped working", "Missing accessory in the box"],
    "billing": ["Charged twice via card", "Wrong amount collected on delivery"],
    "other": ["Want to change phone number on account"],
}


# ------------------------------------------------------------------
# PART 1: BUILD (pure Python)
# ------------------------------------------------------------------
def build_products():
    products = []
    for category, (bases, (lo, hi), warranty) in CATALOG.items():
        for base in bases:
            for brand in BRANDS:
                products.append({
                    "name": f"{brand} {base}",
                    "category": category,
                    "price": round(rng.randint(lo, hi), -1),  # round to nearest 10
                    "warranty_months": warranty,
                    "returnable": base != "Screen Protector",  # hygiene rule
                })
    return products  # 5 categories x 4 types x 2 brands = 40


def build_customers(test_phone, n=50):
    customers, used = [], {test_phone}
    for i in range(n):
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        if i == 0:
            phone = test_phone
        else:
            phone = "+923" + "".join(rng.choice("0123456789") for _ in range(9))
            while phone in used:  # phone must be unique
                phone = "+923" + "".join(rng.choice("0123456789") for _ in range(9))
            used.add(phone)
        customers.append({
            "phone": phone,
            "name": f"{first} {last}",
            "email": f"{first}.{last}{rng.randint(1, 99)}@example.com".lower(),
            "city": rng.choice(CITIES),
            "language": rng.choices(["en", "roman_ur", "ur"], weights=[50, 35, 15])[0],
        })
    return customers


def timeline(status, placed_at):
    """Given a status, decide which timestamps must exist (matches the CHECK constraints)."""
    shipped = delivered = None
    if status in ("shipped", "out_for_delivery", "delivered", "returned"):
        shipped = placed_at + timedelta(hours=rng.randint(20, 40))
    if status in ("delivered", "returned"):
        delivered = shipped + timedelta(days=rng.randint(2, 4))
    return shipped, delivered


def make_order(order_no, customer_idx, status, placed_at, shipped_at, delivered_at,
               payment, customers, products):
    n_items = rng.choices([1, 2, 3], weights=[60, 30, 10])[0]
    items = []
    for product_idx in rng.sample(range(len(products)), n_items):
        items.append({"product_idx": product_idx,
                      "qty": rng.choices([1, 2], weights=[85, 15])[0],
                      "unit_price": products[product_idx]["price"]})
    total = sum(it["qty"] * it["unit_price"] for it in items)
    city = customers[customer_idx]["city"]
    return {
        "order_no": order_no,
        "customer_idx": customer_idx,
        "status": status,
        "payment_method": payment,
        "total": total,
        "address": f"House {rng.randint(1, 200)}, Street {rng.randint(1, 30)}, {city}",
        "tracking_no": f"SE-TRK-{rng.randint(10**8, 10**9 - 1)}" if shipped_at else None,
        "placed_at": placed_at,
        "shipped_at": shipped_at,
        "delivered_at": delivered_at,
        "items": items,
    }


def build_orders(customers, products, n=200):
    """First 7 orders are FIXED SCENARIOS we will reuse as eval test cases later."""
    def mk(num, cust, status, placed, shipped=None, delivered=None, pay="cod"):
        return make_order(f"SE-{num}", cust, status, placed, shipped, delivered,
                          pay, customers, products)

    orders = [
        # delivered 3 days ago -> INSIDE the 7-day return window
        mk(1001, 0, "delivered", ago(days=7), ago(days=6), ago(days=3)),
        # delivered 20 days ago -> OUTSIDE the return window
        mk(1002, 0, "delivered", ago(days=25), ago(days=24), ago(days=20), pay="card"),
        # placed 2 hours ago -> can still be cancelled / address changed
        mk(1003, 0, "placed", ago(hours=2)),
        # shipped 3 days ago -> too late to cancel
        mk(1004, 0, "shipped", ago(days=4), ago(days=3)),
        # shipped 10 days ago, never delivered -> "where is my order?" complaint
        mk(1005, 0, "shipped", ago(days=11), ago(days=10)),
        # out for delivery today
        mk(1006, 0, "out_for_delivery", ago(days=5), ago(days=4)),
        # belongs to ANOTHER customer -> tests that the agent refuses to leak it
        mk(1007, 1, "delivered", ago(days=12), ago(days=11), ago(days=8)),
    ]

    for i in range(len(orders), n):
        status = rng.choices(list(STATUS_WEIGHTS), weights=list(STATUS_WEIGHTS.values()))[0]
        lo, hi = DAYS_AGO[status]
        placed = NOW - timedelta(days=rng.uniform(lo, hi))
        shipped, delivered = timeline(status, placed)
        payment = rng.choices(list(PAYMENT_WEIGHTS), weights=list(PAYMENT_WEIGHTS.values()))[0]
        orders.append(make_order(f"SE-{1001 + i}", rng.randrange(len(customers)),
                                 status, placed, shipped, delivered, payment,
                                 customers, products))
    return orders


def build_returns(orders):
    returns = []
    for idx, o in enumerate(orders):
        if idx < 7:  # leave the fixed scenarios alone
            continue
        if o["status"] == "returned":
            outcome = "refunded"
        elif o["status"] == "delivered" and rng.random() < 0.06:
            outcome = rng.choice(["requested", "approved", "rejected"])
        else:
            continue
        requested = min(o["delivered_at"] + timedelta(days=rng.randint(1, 3)), NOW)
        resolved = None if outcome == "requested" else min(requested + timedelta(days=rng.randint(1, 3)), NOW)
        returns.append({"order_idx": idx, "reason": rng.choice(RETURN_REASONS),
                        "status": outcome, "requested_at": requested, "resolved_at": resolved})
    return returns


def build_tickets(orders, n=15):
    tickets = []
    for _ in range(n):
        order_idx = rng.randrange(7, len(orders))
        category = rng.choice(list(TICKET_TEMPLATES))
        status = rng.choices(["open", "pending", "resolved"], weights=[30, 20, 50])[0]
        tickets.append({
            "customer_idx": orders[order_idx]["customer_idx"],
            "order_idx": order_idx,
            "category": category,
            "priority": rng.choices(["low", "medium", "high", "urgent"], weights=[20, 50, 25, 5])[0],
            "status": status,
            "summary": rng.choice(TICKET_TEMPLATES[category]),
            "assigned_to": rng.choice(["Hina", "Kamran", "Sadia"]) if status != "open" else None,
            "created_at": NOW - timedelta(days=rng.uniform(0, 30)),
        })
    return tickets


def build_data(test_phone):
    customers = build_customers(test_phone)
    products = build_products()
    orders = build_orders(customers, products)
    return {"customers": customers, "products": products, "orders": orders,
            "returns": build_returns(orders), "tickets": build_tickets(orders)}


def print_summary(data):
    print("Rows to be created:")
    for name in ("customers", "products", "orders", "returns", "tickets"):
        print(f"  {name:<10} {len(data[name])}")
    print(f"  {'items':<10} {sum(len(o['items']) for o in data['orders'])}")
    counts = Counter(o["status"] for o in data["orders"])
    print("Order statuses:", dict(counts))
    print("Fixed scenario orders: SE-1001 ... SE-1007 (customer #1 phone:",
          data["customers"][0]["phone"] + ")")


# ------------------------------------------------------------------
# PART 2: LOAD (talks to Postgres)
# ------------------------------------------------------------------
def load(database_url, data):
    import psycopg2
    from psycopg2.extras import execute_values

    conn = psycopg2.connect(database_url)
    # "with conn" = ONE transaction: everything succeeds, or nothing is saved.
    with conn, conn.cursor() as cur:
        cur.execute("truncate tickets, returns, order_items, orders, products, customers "
                    "restart identity cascade")

        customer_ids = [r[0] for r in execute_values(
            cur,
            "insert into customers (phone, name, email, city, language) values %s returning id",
            [(c["phone"], c["name"], c["email"], c["city"], c["language"]) for c in data["customers"]],
            fetch=True)]

        product_ids = [r[0] for r in execute_values(
            cur,
            "insert into products (name, category, price, warranty_months, returnable) values %s returning id",
            [(p["name"], p["category"], p["price"], p["warranty_months"], p["returnable"])
             for p in data["products"]],
            fetch=True)]

        order_ids, item_rows = [], []
        for o in data["orders"]:
            cur.execute(
                """insert into orders (order_no, customer_id, status, payment_method, total, address,
                                       tracking_no, placed_at, shipped_at, delivered_at)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                (o["order_no"], customer_ids[o["customer_idx"]], o["status"], o["payment_method"],
                 o["total"], o["address"], o["tracking_no"], o["placed_at"], o["shipped_at"],
                 o["delivered_at"]))
            order_id = cur.fetchone()[0]
            order_ids.append(order_id)
            for it in o["items"]:
                item_rows.append((order_id, product_ids[it["product_idx"]], it["qty"], it["unit_price"]))

        execute_values(cur, "insert into order_items (order_id, product_id, qty, unit_price) values %s",
                       item_rows)

        execute_values(
            cur,
            "insert into returns (order_id, reason, status, requested_at, resolved_at) values %s",
            [(order_ids[r["order_idx"]], r["reason"], r["status"], r["requested_at"], r["resolved_at"])
             for r in data["returns"]])

        execute_values(
            cur,
            """insert into tickets (customer_id, order_id, category, priority, status, summary,
                                    assigned_to, created_at) values %s""",
            [(customer_ids[t["customer_idx"]], order_ids[t["order_idx"]], t["category"], t["priority"],
              t["status"], t["summary"], t["assigned_to"], t["created_at"]) for t in data["tickets"]])
    conn.close()
    print("Seed complete.")


def main():
    parser = argparse.ArgumentParser(description="Seed the ShopEase database")
    parser.add_argument("--dry-run", action="store_true", help="build data but do not touch the database")
    args = parser.parse_args()

    data = build_data(os.getenv("TEST_PHONE", "+920000000001"))
    print_summary(data)
    if args.dry_run:
        print("Dry run: database untouched.")
        return

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("Set DATABASE_URL first (Supabase: Project Settings > Database > Connection string).")
    load(database_url, data)


if __name__ == "__main__":
    main()
