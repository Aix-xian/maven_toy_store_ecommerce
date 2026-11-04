"""Test refund-window boundaries, partial refunds, and real basket/pair SQL."""

import sqlite3
import sys
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.dialects import mysql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from product_analysis import (
    basket_metrics, prepare_baskets, prepare_items, product_groups, product_metrics,
)


def item(identifier=1, date="2020-01-01 10:00:00", product=1, primary=1):
    return {"order_item_id": identifier, "item_at": date, "order_at": date,
            "order_id": 1, "product_id": product, "product_name": f"product_{product}",
            "product_launched_at": "2019-01-01 00:00:00", "is_primary_item": primary,
            "sales_usd": "20.00", "cogs_usd": "8.00"}


def refund(identifier, date, amount="5.00", item_id=1):
    return {"order_item_refund_id": identifier, "refund_at": date,
            "order_item_id": item_id, "order_id": 1, "refund_amount_usd": amount}


BASELINE = {"monthly_rows": [
    {"month_start": "2020-01-01", "period_status": "comparable_month"},
    {"month_start": "2020-02-01", "period_status": "comparable_month"},
]}


class RefundTests(unittest.TestCase):
    def test_partial_refunds_count_item_once_and_separate_late_refund(self):
        items, events = prepare_items([item()], [refund(1, "2020-01-16 10:00:00"), refund(2, "2020-02-10 10:00:00")], BASELINE, 30, "2020-02-15 10:00:00")
        result = product_metrics(items)
        self.assertEqual(result["observed_refund_events"], 2)
        self.assertEqual(result["observed_refunded_items"], 1)
        self.assertEqual(result["observed_refund_usd"], Decimal("10"))
        self.assertEqual(result["window_refund_usd"], Decimal("5"))
        self.assertEqual(result["window_refunded_items"], 1)
        self.assertEqual(result["observed_sales_after_refunds_usd"], Decimal("10"))
        self.assertTrue(events[0]["within_window"])
        self.assertFalse(events[1]["within_window"])

    def test_exact_30_day_boundary_is_inclusive(self):
        rows, _ = prepare_items([item()], [refund(1, "2020-01-31 10:00:00")], BASELINE, 30, "2020-01-31 10:00:00")
        self.assertTrue(rows[0]["window_eligible"])
        self.assertEqual(product_metrics(rows)["window_refund_item_rate"], 1)
        rows, _ = prepare_items([item()], [refund(1, "2020-01-31 10:00:01")], BASELINE, 30, "2020-02-15 10:00:00")
        self.assertEqual(product_metrics(rows)["window_refund_usd"], 0)

    def test_immature_items_do_not_enter_window_numerator_or_denominator(self):
        rows, _ = prepare_items([item(date="2020-02-01 10:00:00")], [refund(1, "2020-02-02 10:00:00")], BASELINE, 30, "2020-02-15 10:00:00")
        result = product_metrics(rows)
        self.assertEqual(result["observed_refunded_items"], 1)
        self.assertEqual(result["window_eligible_items"], 0)
        self.assertEqual(result["window_refunded_items"], 0)
        self.assertEqual(result["window_refund_usd"], 0)
        self.assertIsNone(result["window_refund_item_rate"])

    def test_refunds_after_cutoff_still_exist_in_audit(self):
        rows, events = prepare_items([item()], [refund(1, "2020-02-10 10:00:00")], BASELINE, 30, "2020-01-31 10:00:00")
        self.assertEqual(rows[0]["all_refund_events"], 1)
        self.assertEqual(rows[0]["observed_refund_events"], 0)
        self.assertFalse(events[0]["within_cutoff"])

    def test_duplicate_or_invalid_refunds_are_rejected(self):
        event = refund(1, "2020-01-16 10:00:00")
        with self.assertRaisesRegex(ValueError, "重复"):
            prepare_items([item()], [event, event], BASELINE, 30, "2020-02-15 10:00:00")
        with self.assertRaisesRegex(ValueError, "超出"):
            prepare_items([item()], [refund(1, "2020-01-16 10:00:00", "21")], BASELINE, 30, "2020-02-15 10:00:00")
        with self.assertRaisesRegex(ValueError, "归属"):
            prepare_items([item()], [{**event, "order_id": 2}], BASELINE, 30, "2020-02-15 10:00:00")

    def test_product_order_counts_are_not_additive_across_products(self):
        rows, _ = prepare_items([item(), item(2, product=2, primary=0)], [], BASELINE, 30, "2020-02-15 10:00:00")
        self.assertEqual(product_metrics(rows)["distinct_orders"], 1)
        summaries = [row for row in product_groups(rows) if row["period"] == "all_observed"]
        self.assertEqual(sum(row["distinct_orders"] for row in summaries), 2)
        self.assertEqual(sum(row["sales_usd"] for row in summaries), Decimal("40"))


class ProductSQLTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.create_function("DATE_FORMAT", 2, lambda value, fmt: datetime.fromisoformat(value).strftime(fmt))
        self.db.executescript("""
            CREATE TABLE products(product_id INTEGER, created_at TEXT, product_name TEXT);
            CREATE TABLE orders(order_id INTEGER, created_at TEXT, primary_product_id INTEGER,
                items_purchased INTEGER, price_usd NUMERIC, cogs_usd NUMERIC);
            CREATE TABLE order_items(order_item_id INTEGER, created_at TEXT, order_id INTEGER,
                product_id INTEGER, is_primary_item INTEGER, price_usd NUMERIC, cogs_usd NUMERIC);
        """)
        self.db.executemany("INSERT INTO products VALUES (?, ?, ?)", [(number, "2019-01-01 00:00:00", f"p{number}") for number in (1, 2, 3)])

    def tearDown(self):
        self.db.close()

    def add_order(self, products):
        count = len(products)
        self.db.execute("INSERT INTO orders VALUES (1, '2020-01-01 10:00:00', 1, ?, ?, ?)", (count, count * 20, count * 8))
        self.db.executemany("INSERT INTO order_items VALUES (?, '2020-01-01 10:00:00', 1, ?, ?, 20, 8)", [(index + 1, product, int(index == 0)) for index, product in enumerate(products)])

    def query(self, filename):
        return [dict(row) for row in self.db.execute((ROOT / "src" / filename).read_text(encoding="utf-8"))]

    def test_order_money_is_not_multiplied_by_items(self):
        self.add_order([1, 2])
        rows = self.query("product_baskets.sql")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sales_usd"], 40)
        self.assertEqual(rows[0]["item_count"], 2)
        result = basket_metrics(prepare_baskets(rows, BASELINE))
        self.assertEqual(result["aov_usd"], 40)
        self.assertEqual(result["items_per_order"], 2)
        self.assertEqual(result["average_item_price_usd"], 20)
        self.assertEqual(result["multi_product_order_rate"], 1)

    def test_multiple_items_of_same_product_are_not_cross_product_order(self):
        self.add_order([1, 1])
        result = basket_metrics(prepare_baskets(self.query("product_baskets.sql"), BASELINE))
        self.assertEqual(result["multi_item_orders"], 1)
        self.assertEqual(result["multi_product_orders"], 0)
        self.assertEqual(self.query("product_pairs.sql"), [])

    def test_each_unordered_pair_is_counted_once_per_order(self):
        self.add_order([1, 1, 2, 3])
        pairs = self.query("product_pairs.sql")
        self.assertEqual([(row["product_a"], row["product_b"], row["pair_orders"]) for row in pairs], [(1, 2, 1), (1, 3, 1), (2, 3, 1)])

    def test_item_extract_preserves_item_grain(self):
        self.add_order([1, 2])
        rows = self.query("product_analysis.sql")
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(row["sales_usd"] for row in rows), 40)
        self.assertEqual([row["is_primary_item"] for row in rows], [1, 0])

    def test_mysql_queries_compile_without_parameter_binds(self):
        for name in ("product_analysis.sql", "product_baskets.sql", "product_pairs.sql"):
            compiled = text((ROOT / "src" / name).read_text(encoding="utf-8")).compile(dialect=mysql.dialect())
            self.assertEqual(compiled.params, {})


if __name__ == "__main__":
    unittest.main()
