"""Offline tests for attribution, comparable months and result reconciliation."""

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.dialects import mysql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from monthly_overview import CONTEXT_SQL, prepare_months, summarize, validate_results


def fixture():
    context = {
        "session_first_at": "2020-01-15 00:00:00",
        "session_last_at": "2020-04-15 00:00:00",
        "order_first_at": "2020-01-15 01:00:00",
        "order_last_at": "2020-04-15 01:00:00",
        "sessions": 60, "converted_sessions": 12, "orders": 14,
        "sales_usd": Decimal("600.00"), "cogs_usd": Decimal("240.00"),
    }
    rows = []
    for month, sessions, converted, orders, sales in (
        (1, 10, 1, 1, "50"), (2, 10, 1, 2, "150"),
        (3, 30, 9, 10, "350"), (4, 10, 1, 1, "50"),
    ):
        rows.append({
            "month_start": date(2020, month, 1), "sessions": sessions,
            "converted_sessions": converted, "session_orders": orders,
            "session_sales_usd": Decimal(sales), "orders": orders,
            "sales_usd": Decimal(sales), "cogs_usd": Decimal(sales) * Decimal("0.4"),
        })
    quality = {"table_profiles": [
        {"table": table, "row_count": context[count], "first_at": context[first], "last_at": context[last]}
        for table, count, first, last in (
            ("website_sessions", "sessions", "session_first_at", "session_last_at"),
            ("orders", "orders", "order_first_at", "order_last_at"),
        )
    ]}
    return context, rows, quality


class MonthlyOverviewTests(unittest.TestCase):
    def test_boundary_months_and_growth(self):
        context, raw, _ = fixture()
        rows = prepare_months(raw, context)
        self.assertEqual([row["period_status"] for row in rows], [
            "boundary_month", "comparable_month", "comparable_month", "boundary_month"
        ])
        self.assertIsNone(rows[1]["sessions_mom"])
        self.assertEqual(rows[2]["sessions_mom"], Decimal("2"))
        self.assertEqual(rows[2]["conversion_rate_change_pp"], Decimal("20"))
        self.assertIsNone(rows[3]["orders_mom"])

    def test_multi_order_session_is_counted_once_for_conversion(self):
        context, raw, _ = fixture()
        february = prepare_months(raw, context)[1]
        self.assertEqual(february["session_orders"], 2)
        self.assertEqual(february["conversion_rate"], Decimal("0.1"))

    def test_revenue_per_session_uses_session_attribution(self):
        context, raw, _ = fixture()
        raw[1]["session_sales_usd"] = Decimal("100")
        row = prepare_months(raw, context)[1]
        self.assertEqual(row["revenue_per_session_usd"], Decimal("10"))
        self.assertEqual(row["aov_usd"], Decimal("75"))

    def test_summary_uses_weighted_ratios(self):
        context, raw, _ = fixture()
        comparable = prepare_months(raw, context)[1:3]
        summary = summarize(comparable)
        self.assertEqual(summary["conversion_rate"], Decimal("0.25"))
        self.assertEqual(summary["aov_usd"], Decimal("500") / 12)

    def test_zero_denominators_are_unknown_not_zero(self):
        context, raw, _ = fixture()
        raw[1].update({key: 0 for key in ("sessions", "converted_sessions", "session_orders", "orders")})
        raw[1].update({key: Decimal(0) for key in ("session_sales_usd", "sales_usd", "cogs_usd")})
        rows = prepare_months(raw, context)
        self.assertIsNone(rows[1]["conversion_rate"])
        self.assertIsNone(rows[1]["aov_usd"])
        self.assertIsNone(rows[2]["sessions_mom"])
        self.assertIsNone(rows[2]["conversion_rate_change_pp"])

    def test_reconciliation_rejects_missing_months_and_fanout(self):
        context, raw, quality = fixture()
        rows = prepare_months(raw, context)
        self.assertTrue(all(item["passed"] for item in validate_results(rows, context, quality)))
        with self.assertRaisesRegex(ValueError, "unique_contiguous_calendar"):
            validate_results(rows[:2] + rows[3:], context, quality)
        rows[1]["session_sales_usd"] *= 2
        with self.assertRaisesRegex(ValueError, "total.session_sales"):
            validate_results(rows, context, quality)

    def test_query_is_compatible_with_sqlalchemy_mysql_compilation(self):
        query = text((ROOT / "src" / "monthly_overview.sql").read_text(encoding="utf-8"))
        compiled = query.compile(dialect=mysql.dialect())
        self.assertEqual(compiled.params, {})
        self.assertIn("'%%Y-%%m-01'", str(compiled))
        context = text(CONTEXT_SQL).compile(dialect=mysql.dialect())
        self.assertEqual(context.params, {})
        self.assertIn("'%%Y-%%m'", str(context))


if __name__ == "__main__":
    unittest.main()
