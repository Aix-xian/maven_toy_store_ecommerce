"""Test channel classification, attribution checks and accounting decomposition."""

import sys
import unittest
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.dialects import mysql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from channel_analysis import (
    classify_channel, compare_periods, decompose, metrics, period_rows,
    prepare_rows, summarize_periods,
)


def measures(sessions, converted, revenue=None):
    return {
        "sessions": sessions, "converted_sessions": converted,
        "session_orders": converted,
        "session_sales_usd": Decimal(revenue if revenue is not None else converted * 10),
        "session_cogs_usd": Decimal(converted * 4),
    }


def row(month, channel="gsearch_nonbrand", device="desktop", sessions=100, converted=10):
    return {
        "month_start": month, "period_status": "comparable_month",
        "channel": channel, "device_type": device,
        **measures(sessions, converted),
    }


class ChannelAnalysisTests(unittest.TestCase):
    def test_tagged_search_and_social(self):
        self.assertEqual(classify_channel({"utm_source": " GSEARCH ", "utm_campaign": "Brand"})[0], "gsearch_brand")
        self.assertEqual(classify_channel({"utm_source": "bsearch", "utm_campaign": "new_test"})[0], "bsearch_other_campaign")
        self.assertEqual(classify_channel({"utm_source": "socialbook"})[0], "socialbook_tagged")
        self.assertEqual(classify_channel({"utm_source": "new_provider"})[0], "other_tagged")

    def test_untagged_traffic_is_classified_as_candidate(self):
        self.assertEqual(classify_channel({"http_referer": None})[0], "direct_candidate")
        self.assertEqual(classify_channel({"http_referer": "https://www.gsearch.com/search"})[0], "organic_gsearch_candidate")
        self.assertEqual(classify_channel({"http_referer": "https://www.bsearch.com"})[0], "organic_bsearch_candidate")
        self.assertEqual(classify_channel({"utm_source": "NULL", "http_referer": "\\N"})[0], "direct_candidate")

    def test_partial_tags_and_lookalike_hosts_are_not_organic(self):
        self.assertEqual(classify_channel({"utm_campaign": "brand", "http_referer": "https://www.gsearch.com"})[0], "unclassified_partial_utm")
        self.assertEqual(classify_channel({"utm_content": "ad_1"})[0], "unclassified_partial_utm")
        self.assertEqual(classify_channel({"http_referer": "https://www.gsearch.com.example.org"})[0], "referral_or_unknown_candidate")
        self.assertEqual(classify_channel({"http_referer": "https://[bad"})[0], "referral_or_unknown_candidate")

    def test_pure_mix_change(self):
        old = {("a",): measures(50, 5), ("b",): measures(50, 15)}
        new = {("a",): measures(80, 8), ("b",): measures(20, 6)}
        result = decompose(old, new, "converted_sessions")
        self.assertEqual(result["change"], Decimal("-0.06"))
        self.assertEqual(result["mix_effect"], Decimal("-0.06"))
        self.assertEqual(result["within_effect"], 0)

    def test_pure_within_change(self):
        old = {("a",): measures(50, 5), ("b",): measures(50, 15)}
        new = {("a",): measures(50, 10), ("b",): measures(50, 20)}
        result = decompose(old, new, "converted_sessions")
        self.assertEqual(result["change"], Decimal("0.1"))
        self.assertEqual(result["mix_effect"], 0)
        self.assertEqual(result["within_effect"], Decimal("0.1"))

    def test_entering_and_exiting_group_policy(self):
        old = {("a",): measures(100, 10)}
        new = {("a",): measures(50, 5), ("b",): measures(50, 15)}
        result = decompose(old, new, "converted_sessions")
        self.assertEqual(result["mix_effect"], Decimal("0.1"))
        self.assertEqual(result["within_effect"], 0)
        reverse = decompose(new, old, "converted_sessions")
        self.assertEqual(reverse["mix_effect"], Decimal("-0.1"))
        self.assertEqual(reverse["within_effect"], 0)

    def test_revenue_decomposition_and_weighted_rates(self):
        old = {("a",): measures(10, 1, 10), ("b",): measures(90, 18, 360)}
        new = {("a",): measures(50, 10, 150), ("b",): measures(50, 15, 450)}
        result = decompose(old, new, "session_sales_usd")
        self.assertEqual(result["change"], Decimal("2.3"))
        self.assertEqual(result["mix_effect"] + result["within_effect"], result["change"])
        grouped = summarize_periods_fixture()
        overall = next(item for item in grouped if item["period"] == "nov_2014" and item["grouping"] == "overall")
        self.assertEqual(overall["conversion_rate"], Decimal("0.19"))

    def test_prepare_rows_rejects_lost_or_duplicated_counts(self):
        raw = {
            "month_start": "2014-11-01", "utm_source": "gsearch",
            "utm_campaign": "nonbrand", "utm_content": "ad_1",
            "http_referer": "https://www.gsearch.com", "device_type": "desktop",
            **measures(100, 10),
        }
        baseline = {"monthly_rows": [row("2014-11-01")]}
        prepared, checks = prepare_rows([raw], baseline)
        self.assertEqual(prepared[0]["channel"], "gsearch_nonbrand")
        self.assertTrue(all(check["passed"] for check in checks))
        with self.assertRaisesRegex(ValueError, "不一致"):
            prepare_rows([{**raw, "sessions": 101}], baseline)
        with self.assertRaisesRegex(ValueError, "重复"):
            prepare_rows([raw, raw], baseline)

    def test_comparison_contributions_sum_to_one(self):
        rows = fixture_rows()
        comparisons, decomposition = compare_periods(rows)
        for grouping in ("channel", "device", "channel_device"):
            selected = [item for item in comparisons if item["comparison"] == "dec_vs_nov_2014" and item["grouping"] == grouping]
            self.assertAlmostEqual(sum(item["revenue_delta_contribution"] for item in selected), Decimal(1))
        for item in decomposition:
            if item["presence"] == "total":
                self.assertLess(abs(item["residual"]), Decimal("1e-18"))

    def test_zero_orders_have_no_aov_and_boundary_months_are_excluded(self):
        self.assertIsNone(metrics(measures(100, 0))["session_aov_usd"])
        boundary = {**row("2014-11-01"), "period_status": "boundary_month"}
        self.assertEqual(period_rows([boundary], "all_comparable"), [])

    def test_sql_compilation_has_no_binds_and_escapes_date_format(self):
        sql = (ROOT / "src" / "channel_analysis.sql").read_text(encoding="utf-8")
        compiled = text(sql).compile(dialect=mysql.dialect())
        self.assertEqual(compiled.params, {})
        self.assertIn("'%%Y-%%m-01'", str(compiled))


def fixture_rows():
    rows = []
    for month in ("2013-01-01", "2014-01-01", "2014-11-01", "2014-12-01", "2015-01-01"):
        newer = month >= "2014-12-01"
        rows.append(row(month, sessions=50 if newer else 10, converted=10 if newer else 1))
        rows.append(row(month, "direct_candidate", "mobile", sessions=50 if newer else 90, converted=15 if newer else 18))
    return rows


def summarize_periods_fixture():
    return summarize_periods(fixture_rows())


if __name__ == "__main__":
    unittest.main()
