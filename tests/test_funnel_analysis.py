"""Execute the actual funnel SQL on small SQLite fixtures without MySQL access."""

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
from funnel_analysis import STAGES, build_comparisons, build_summaries, prepare_rows

COMPLETE_PATH = (
    "/home", "/products", "/the-original-mr-fuzzy", "/cart",
    "/shipping", "/billing", "/thank-you-for-your-order",
)


class FunnelSQLTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.create_function("DATE_FORMAT", 2, lambda value, fmt: datetime.fromisoformat(value).strftime(fmt))
        self.db.executescript("""
            CREATE TABLE website_sessions (
                website_session_id INTEGER, created_at TEXT, utm_source TEXT,
                utm_campaign TEXT, utm_content TEXT, device_type TEXT, http_referer TEXT
            );
            CREATE TABLE website_pageviews (
                website_pageview_id INTEGER, website_session_id INTEGER,
                created_at TEXT, pageview_url TEXT
            );
            CREATE TABLE orders (
                order_id INTEGER, website_session_id INTEGER,
                price_usd NUMERIC, cogs_usd NUMERIC
            );
        """)
        self.sql = (ROOT / "src" / "funnel_analysis.sql").read_text(encoding="utf-8")

    def tearDown(self):
        self.db.close()

    def add_session(self, identifier=1, path=COMPLETE_PATH, order_amounts=(50,), same_timestamp=False):
        self.db.execute("INSERT INTO website_sessions VALUES (?, ?, ?, ?, ?, ?, ?)", (identifier, "2014-11-01 10:00:00", "gsearch", "nonbrand", "ad_1", "desktop", "https://www.gsearch.com"))
        events = []
        for index, url in enumerate(path):
            events.append((identifier * 100 + index, identifier, f"2014-11-01 10:00:{0 if same_timestamp else index:02}", url))
        self.db.executemany("INSERT INTO website_pageviews VALUES (?, ?, ?, ?)", reversed(events))
        self.db.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", [(identifier * 10 + index, identifier, amount, 20) for index, amount in enumerate(order_amounts)])

    def query(self):
        return [dict(row) for row in self.db.execute(self.sql)]

    def test_complete_path_and_multiple_orders_do_not_multiply_page_counts(self):
        self.add_session(order_amounts=(30, 20))
        result = self.query()[0]
        self.assertEqual(result["sessions"], 1)
        self.assertEqual(result["converted_sessions"], 1)
        self.assertEqual(result["session_orders"], 2)
        self.assertEqual(result["session_sales_usd"], 50)
        self.assertEqual(result["pageview_count"], len(COMPLETE_PATH))
        self.assertTrue(all(result[stage] == 1 for stage in STAGES))

    def test_out_of_order_pages_are_not_a_complete_path(self):
        self.add_session(path=("/home", "/cart", "/the-original-mr-fuzzy", "/products", "/shipping", "/billing", "/thank-you-for-your-order"))
        result = self.query()[0]
        self.assertEqual(result["raw_detail"], 1)
        self.assertEqual(result["ordered_detail"], 0)
        self.assertEqual(result["ordered_thank_you"], 0)
        self.assertEqual(result["orders_without_ordered_thank_you"], 1)
        self.assertEqual(result["orders_without_raw_thank_you"], 0)

    def test_revisiting_a_stage_can_complete_the_path_later(self):
        self.add_session(path=("/the-original-mr-fuzzy", "/products", "/cart", "/the-original-mr-fuzzy", "/cart", "/shipping", "/billing", "/thank-you-for-your-order"))
        result = self.query()[0]
        self.assertEqual(result["ordered_thank_you"], 1)
        self.assertEqual(result["raw_cart"], 1)
        self.assertEqual(result["pageview_count"], 8)

    def test_second_billing_page_and_other_product_details_are_supported(self):
        self.add_session(path=("/lander-5", "/products", "/the-forever-love-bear", "/the-hudson-river-mini-bear", "/cart", "/shipping", "/billing-2", "/thank-you-for-your-order"))
        result = self.query()[0]
        self.assertEqual(result["landing_url"], "/lander-5")
        self.assertEqual(result["ordered_thank_you"], 1)
        self.assertEqual(result["raw_detail"], 1)

    def test_sessions_without_pageviews_are_retained(self):
        self.add_session(path=(), order_amounts=())
        result = self.query()[0]
        self.assertEqual(result["sessions"], 1)
        self.assertEqual(result["pageview_sessions"], 0)
        self.assertEqual(result["pageview_count"], 0)
        self.assertIsNone(result["landing_url"])

    def test_pageview_id_breaks_timestamp_ties(self):
        self.add_session(same_timestamp=True)
        result = self.query()[0]
        self.assertEqual(result["landing_url"], "/home")
        self.assertEqual(result["ordered_thank_you"], 1)

    def test_confirmation_page_and_orders_are_independent(self):
        self.add_session(order_amounts=())
        result = self.query()[0]
        self.assertEqual(result["converted_sessions"], 0)
        self.assertEqual(result["ordered_thank_you"], 1)
        self.assertEqual(result["ordered_thank_you_without_order"], 1)
        self.assertEqual(result["raw_thank_you_without_order"], 1)

    def test_baseline_and_monotonicity_are_checked(self):
        self.add_session()
        raw = self.query()
        baseline = {"monthly_rows": [{"month_start": "2014-11-01", "period_status": "comparable_month", **{key: raw[0][key] for key in ("sessions", "converted_sessions", "session_orders", "session_sales_usd")}}]}
        rows, checks = prepare_rows(raw, baseline)
        self.assertTrue(all(item["passed"] for item in checks))
        self.assertEqual(rows[0]["channel"], "gsearch_nonbrand")
        with self.assertRaisesRegex(ValueError, "前序"):
            prepare_rows([{**raw[0], "ordered_cart": 2}], baseline)
        with self.assertRaisesRegex(ValueError, "重复"):
            prepare_rows(raw + raw, baseline)

    def test_summary_uses_conditional_step_denominators(self):
        self.add_session()
        self.add_session(identifier=2, path=("/home", "/products"), order_amounts=())
        raw = self.query()
        expected = {key: sum(item[key] for item in raw) for key in ("sessions", "converted_sessions", "session_orders", "session_sales_usd")}
        baseline = {"monthly_rows": [{"month_start": "2014-11-01", "period_status": "comparable_month", **expected}]}
        rows, _ = prepare_rows(raw, baseline)
        groups, stages = build_summaries(rows)
        detail = next(item for item in stages if item["period"] == "nov_2014" and item["grouping"] == "overall" and item["stage"] == "ordered_detail")
        self.assertEqual(detail["previous_stage_sessions"], 2)
        self.assertEqual(detail["step_rate"], Decimal("0.5"))
        summary = next(item for item in groups if item["period"] == "nov_2014" and item["grouping"] == "overall")
        self.assertEqual(summary["conversion_rate"], Decimal("0.5"))
        comparison = next(item for item in build_comparisons(rows) if item["comparison"] == "dec_vs_nov_2014" and item["grouping"] == "overall" and item["to_stage"] == "ordered_detail")
        self.assertIsNone(comparison["new_step_rate"])
        self.assertIsNone(comparison["step_rate_change_pp"])

    def test_mysql_compilation_has_no_parameter_binds(self):
        compiled = text(self.sql).compile(dialect=mysql.dialect())
        self.assertEqual(compiled.params, {})
        self.assertIn("'%%Y-%%m-01'", str(compiled))


if __name__ == "__main__":
    unittest.main()
