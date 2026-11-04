"""Reconcile saved product audits and write reproducible findings, without MySQL."""

import csv
import hashlib
import json
import sys
from collections import defaultdict
from decimal import Decimal
from itertools import combinations
from pathlib import Path

from channel_analysis import COMPARISONS, require
from monthly_overview import json_value, ratio
from product_analysis import (
    basket_summaries, compare_products, pair_summaries, prepare_baskets,
    prepare_items, product_groups, product_metrics,
)
from review_channel_results import verify_csv


ROOT = Path(__file__).resolve().parents[1]
ITEM_FIELDS = (
    "order_item_id", "item_at", "order_at", "order_id", "product_id",
    "product_name", "product_launched_at", "is_primary_item", "sales_usd", "cogs_usd",
)
EVENT_FIELDS = (
    "order_item_refund_id", "order_item_id", "order_id", "refund_at", "refund_amount_usd",
)


def read_csv(name):
    with (ROOT / "output" / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def verify_complete_csv(name, expected):
    actual = read_csv(name)
    require(actual and expected, f"{name} 意外为空。")
    require(set(actual[0]) == set(expected[0]), f"{name} 字段缺失或多余。")
    return verify_csv(name, expected)


def rebuild_baskets_and_pairs(items, baseline):
    grouped, pair_counts = defaultdict(list), defaultdict(int)
    for item in items:
        grouped[item["order_id"]].append(item)
    raw_baskets = []
    for order_id, group in sorted(grouped.items()):
        primary = [item for item in group if item["is_primary_item"]]
        require(len(primary) == 1, "明细无法重建唯一主商品。")
        require(len({item["order_at"] for item in group}) == 1, "同订单的下单日期不一致。")
        product_ids = sorted({item["product_id"] for item in group})
        sales = sum((item["sales_usd"] for item in group), Decimal(0))
        cost = sum((item["cogs_usd"] for item in group), Decimal(0))
        raw_baskets.append({
            "order_id": order_id, "order_at": group[0]["order_at"],
            "primary_product_id": primary[0]["product_id"],
            "items_purchased": len(group), "item_count": len(group),
            "distinct_products": len(product_ids), "primary_items": 1,
            "secondary_items": len(group) - 1, "sales_usd": sales, "cogs_usd": cost,
            "secondary_sales_usd": sum((item["sales_usd"] for item in group if not item["is_primary_item"]), Decimal(0)),
            "item_sales_usd": sales, "item_cogs_usd": cost,
        })
        for left, right in combinations(product_ids, 2):
            pair_counts[(group[0]["month_start"], left, right)] += 1
    pairs = [{"month_start": month, "product_a": left, "product_b": right, "pair_orders": count}
             for (month, left, right), count in sorted(pair_counts.items())]
    return prepare_baskets(raw_baskets, baseline), pairs


def aov_decomposition(before, after, label):
    # Exact symmetric split of AOV = quantity per order * average item price.
    q0, q1 = before["items_per_order"], after["items_per_order"]
    p0, p1 = before["average_item_price_usd"], after["average_item_price_usd"]
    quantity = (q1 - q0) * (p0 + p1) / 2
    price_mix = (p1 - p0) * (q0 + q1) / 2
    delta = after["aov_usd"] - before["aov_usd"]
    require(abs(quantity + price_mix - delta) < Decimal("1e-18"), "客单价分解未对平。")
    return {
        "comparison": label, "old": before, "new": after,
        "aov_delta_usd": delta, "aov_growth": ratio(delta, before["aov_usd"]),
        "quantity_effect_usd": quantity, "average_price_mix_effect_usd": price_mix,
        "multi_product_rate_change_pp": (after["multi_product_order_rate"] - before["multi_product_order_rate"]) * 100,
        "limit": "描述性恒等分解。平均单件价格含商品结构，不等同于同款商品调价或策略因果。",
    }


def main():
    require(sys.prefix != sys.base_prefix and Path(sys.prefix).resolve().is_relative_to(ROOT / "env"), "使用项目 env/python/bin/python。")
    source = ROOT / "output" / "product_analysis_report.json"
    report = json.loads(source.read_bytes())
    require(report["status"] == "PASS" and report["validation"]["failed"] == 0, "商品报告未通过。")
    require(all(row["passed"] for row in report["validation"]["checks"]), "商品对账存在失败。")
    baseline_bytes = (ROOT / "output" / "monthly_overview_report.json").read_bytes()
    require(hashlib.sha256(baseline_bytes).hexdigest() == report["monthly_baseline_sha256"], "月度基准变化。")
    baseline = json.loads(baseline_bytes)
    for name, expected in report["sql_sha256"].items():
        require(hashlib.sha256((ROOT / "src" / name).read_bytes()).hexdigest() == expected, "商品 SQL 指纹变化。")
    raw_items = [{field: row[field] for field in ITEM_FIELDS} for row in read_csv("product_item_audit.csv")]
    raw_events = [{field: row[field] for field in EVENT_FIELDS} for row in read_csv("refund_events_audit.csv")]
    items, events = prepare_items(raw_items, raw_events, baseline, report["window_days"], report["refund_observation_cutoff_at"])
    catalog = {int(row["product_id"]): row for row in report["catalog"]}
    for item in items:
        product = catalog[item["product_id"]]
        require(item["product_name"] == product["product_name"], "商品名称与目录不一致。")
        require(item["product_launched_at"].isoformat() == product["product_launched_at"], "上市时间与目录不一致。")
    baskets, raw_pairs = rebuild_baskets_and_pairs(items, baseline)
    summaries, monthly = product_groups(items), product_groups(items, periods=False)
    basket_rows = basket_summaries(baskets, catalog)
    pairs = pair_summaries(raw_pairs, baskets, catalog, baseline)
    comparisons = compare_products(summaries)
    all_products = product_metrics(items)
    for field, expected in (
        ("all_period_products", all_products), ("product_summaries", summaries),
        ("monthly_products", monthly), ("basket_summaries", basket_rows),
        ("product_pairs", pairs), ("comparisons", comparisons),
    ):
        require(json.loads(json.dumps(expected, default=json_value)) == report[field], f"{field} 与审计重算不一致。")
    for month in baseline["monthly_rows"]:
        group = [row for row in items if row["month_start"] == month["month_start"]]
        require(sum((row["sales_usd"] for row in group), Decimal(0)) == Decimal(month["sales_usd"]), "逐月销售额无法对平。")
        require(len({row["order_id"] for row in group}) == month["orders"], "逐月订单数无法对平。")
    checks = [verify_complete_csv(name, expected) for name, expected in (
        ("product_item_audit.csv", items), ("refund_events_audit.csv", events),
        ("product_monthly.csv", monthly), ("product_summary.csv", summaries),
        ("product_baskets.csv", basket_rows), ("product_pairs.csv", pairs),
        ("product_comparisons.csv", comparisons),
    )]
    overall = {row["period"]: row for row in basket_rows if row["grouping"] == "overall"}
    decompositions = [aov_decomposition(overall[old], overall[new], name) for name, old, new in COMPARISONS]
    annual = [row for row in summaries if row["period"] == "year_2014"]
    annual_contribution = [row for row in comparisons if row["comparison"] == "2014_vs_2013"]
    annual_pairs = sorted((row for row in pairs if row["period"] == "year_2014"), key=lambda row: row["pair_orders"], reverse=True)
    findings = {
        "status": "PASS", "source_generated_at": report["generated_at"],
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "validation_passed": report["validation"]["passed"], "audit_items": len(items),
        "audit_refund_events": len(events), "reconstructed_orders": len(baskets),
        "csv_checks": checks, "window_days": report["window_days"],
        "refund_observation_cutoff_at": report["refund_observation_cutoff_at"],
        "cutoff_basis": report["cutoff_basis"], "cutoff_limit": report["cutoff_limit"],
        "all_period_products": all_products, "year_2014_products": annual,
        "annual_product_contributions": annual_contribution,
        "aov_decompositions": decompositions, "year_2014_pairs": annual_pairs,
        "mini_bear_secondary_item_share": ratio(next(row for row in annual if row["product_id"] == 4)["secondary_items"], next(row for row in annual if row["product_id"] == 4)["items"]),
    }
    (ROOT / "output" / "product_findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2, default=json_value) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "validation_passed": findings["validation_passed"], "audit_items": len(items), "audit_refund_events": len(events), "csv_checks": checks, "aov_decompositions": [{key: value for key, value in row.items() if key not in {"old", "new", "limit"}} for row in decompositions], "written": ["output/product_findings.json"]}, ensure_ascii=False, indent=2, default=json_value))


if __name__ == "__main__":
    main()
