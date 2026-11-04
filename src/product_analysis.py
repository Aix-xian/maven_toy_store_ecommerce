"""Analyze product revenue, baskets and refunds with a uniform observed window."""

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from getpass import getpass
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import URL, create_engine, text

from channel_analysis import COMPARISONS, PERIODS, period_rows, require, write_csv
from monthly_overview import CONTEXT_SQL, json_value, month_floor, ratio


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_PERIODS = ("all_observed", *PERIODS)
REFUND_SQL = """
SELECT order_item_refund_id, created_at AS refund_at, order_item_id,
       order_id, refund_amount_usd
FROM order_item_refunds ORDER BY created_at, order_item_refund_id
"""
PROFILE_SQL = """
SELECT 'products' AS table_name, COUNT(*) AS row_count,
       MIN(created_at) AS first_at, MAX(created_at) AS last_at FROM products
UNION ALL
SELECT 'order_items', COUNT(*), MIN(created_at), MAX(created_at) FROM order_items
UNION ALL
SELECT 'order_item_refunds', COUNT(*), MIN(created_at), MAX(created_at)
FROM order_item_refunds
"""


def stamp(value):
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def select_period(rows, period):
    return rows if period == "all_observed" else period_rows(rows, period)


def prepare_items(items, refunds, baseline, window_days, cutoff):
    require(1 <= window_days <= 365, "退款窗口必须为 1—365 天。")
    cutoff = stamp(cutoff)
    months = {row["month_start"]: row for row in baseline["monthly_rows"]}
    indexed = {}
    for raw in items:
        row = dict(raw)
        identifier = int(row["order_item_id"])
        require(identifier not in indexed, "商品明细编号重复。")
        for key in ("order_item_id", "order_id", "product_id", "is_primary_item"):
            row[key] = int(row[key])
        for key in ("item_at", "order_at", "product_launched_at"):
            row[key] = stamp(row[key])
        for key in ("sales_usd", "cogs_usd"):
            row[key] = Decimal(str(row[key]))
        require(row["sales_usd"] >= 0 and row["cogs_usd"] >= 0, "商品金额为负。")
        require(row["is_primary_item"] in (0, 1), "主商品标记非法。")
        require(row["product_launched_at"] <= row["item_at"] <= cutoff, "商品日期或观察截止存在冲突。")
        month = month_floor(row["order_at"]).isoformat()
        require(month in months, "商品月份超出月度报告。")
        row.update({
            "month_start": month, "period_status": months[month]["period_status"],
            "window_eligible": row["item_at"] + timedelta(days=window_days) <= cutoff,
            "all_refund_events": 0, "all_refund_usd": Decimal(0),
            "observed_refund_events": 0, "observed_refund_usd": Decimal(0),
            "window_refund_usd": Decimal(0),
        })
        indexed[identifier] = row
    refund_ids, events = set(), []
    for raw in refunds:
        row = dict(raw)
        refund_id, item_id = int(row["order_item_refund_id"]), int(row["order_item_id"])
        require(refund_id not in refund_ids, "退款编号重复。")
        refund_ids.add(refund_id)
        require(item_id in indexed, "退款关联不到商品明细。")
        item = indexed[item_id]
        refund_at, amount = stamp(row["refund_at"]), Decimal(str(row["refund_amount_usd"]))
        require(int(row["order_id"]) == item["order_id"], "退款订单归属不一致。")
        require(amount >= 0 and refund_at >= item["item_at"], "退款金额或时间非法。")
        observed = refund_at <= cutoff
        within_window = observed and refund_at <= item["item_at"] + timedelta(days=window_days)
        item["all_refund_events"] += 1
        item["all_refund_usd"] += amount
        if observed:
            item["observed_refund_events"] += 1
            item["observed_refund_usd"] += amount
        if within_window:
            item["window_refund_usd"] += amount
        events.append({
            "order_item_refund_id": refund_id, "order_item_id": item_id,
            "order_id": item["order_id"], "product_id": item["product_id"],
            "refund_at": refund_at, "refund_month": month_floor(refund_at).isoformat(),
            "refund_amount_usd": amount, "within_cutoff": observed,
            "within_window": within_window, "item_window_eligible": item["window_eligible"],
        })
    result = list(indexed.values())
    for row in result:
        require(row["all_refund_usd"] <= row["sales_usd"], "累计商品退款超出商品销售金额。")
        row["observed_refunded_item"] = row["observed_refund_usd"] > 0
        row["window_refunded_item"] = row["window_refund_usd"] > 0
    return result, events


def product_metrics(rows):
    eligible = [row for row in rows if row["window_eligible"]]
    sales = sum((row["sales_usd"] for row in rows), Decimal(0))
    cost = sum((row["cogs_usd"] for row in rows), Decimal(0))
    refunds = sum((row["observed_refund_usd"] for row in rows), Decimal(0))
    primary = sum(row["is_primary_item"] for row in rows)
    window_refunded = sum(row["window_refunded_item"] for row in eligible)
    return {
        "items": len(rows), "distinct_orders": len({row["order_id"] for row in rows}),
        "primary_items": primary, "secondary_items": len(rows) - primary,
        "sales_usd": sales, "cogs_usd": cost,
        "cost_difference_before_refunds_usd": sales - cost,
        "observed_refunded_items": sum(row["observed_refunded_item"] for row in rows),
        "observed_refund_events": sum(row["observed_refund_events"] for row in rows),
        "observed_refund_usd": refunds, "observed_sales_after_refunds_usd": sales - refunds,
        "observed_refund_item_rate": ratio(sum(row["observed_refunded_item"] for row in rows), len(rows)),
        "window_eligible_items": len(eligible), "window_ineligible_items": len(rows) - len(eligible),
        "window_refunded_items": window_refunded,
        "window_refund_usd": sum((row["window_refund_usd"] for row in eligible), Decimal(0)),
        "window_refund_item_rate": ratio(window_refunded, len(eligible)),
        "low_volume_refund_review": len(eligible) < 100,
    }


def product_groups(rows, periods=True):
    output = []
    selectors = PRODUCT_PERIODS if periods else sorted({row["month_start"] for row in rows})
    for selector in selectors:
        selected = select_period(rows, selector) if periods else [row for row in rows if row["month_start"] == selector]
        total_sales = sum((row["sales_usd"] for row in selected), Decimal(0))
        grouped = defaultdict(list)
        for row in selected:
            grouped[row["product_id"]].append(row)
        for product_id, group in sorted(grouped.items()):
            values = product_metrics(group)
            output.append({
                "period" if periods else "month_start": selector,
                "product_id": product_id, "product_name": group[0]["product_name"],
                "product_launched_at": group[0]["product_launched_at"],
                **values, "sales_share": ratio(values["sales_usd"], total_sales),
            })
    return output


def prepare_baskets(raw, baseline):
    months = {row["month_start"]: row for row in baseline["monthly_rows"]}
    rows, seen = [], set()
    for item in raw:
        row = dict(item)
        for key in ("order_id", "primary_product_id", "items_purchased", "item_count", "distinct_products", "primary_items", "secondary_items"):
            row[key] = int(row[key])
        require(row["order_id"] not in seen, "订单篮子重复。")
        seen.add(row["order_id"])
        for key in ("sales_usd", "cogs_usd", "secondary_sales_usd", "item_sales_usd", "item_cogs_usd"):
            row[key] = Decimal(str(row[key]))
        require(row["items_purchased"] == row["item_count"] and row["primary_items"] == 1, "订单明细数量或主商品对账失败。")
        require(row["sales_usd"] == row["item_sales_usd"] and row["cogs_usd"] == row["item_cogs_usd"], "订单与明细金额对账失败。")
        row["month_start"] = month_floor(row["order_at"]).isoformat()
        require(row["month_start"] in months, "订单月份不在月度基准中。")
        row["period_status"] = months[row["month_start"]]["period_status"]
        rows.append(row)
    return rows


def basket_metrics(rows):
    count, items = len(rows), sum(row["item_count"] for row in rows)
    sales = sum((row["sales_usd"] for row in rows), Decimal(0))
    multiple = sum(row["item_count"] > 1 for row in rows)
    cross_product = sum(row["distinct_products"] > 1 for row in rows)
    values = {
        "orders": count, "items": items, "sales_usd": sales,
        "cogs_usd": sum((row["cogs_usd"] for row in rows), Decimal(0)),
        "multi_item_orders": multiple, "multi_product_orders": cross_product,
        "multi_item_order_rate": ratio(multiple, count),
        "multi_product_order_rate": ratio(cross_product, count),
        "items_per_order": ratio(items, count), "aov_usd": ratio(sales, count),
        "average_item_price_usd": ratio(sales, items),
        "secondary_items": sum(row["secondary_items"] for row in rows),
        "secondary_sales_usd": sum((row["secondary_sales_usd"] for row in rows), Decimal(0)),
    }
    if count:
        require(abs(values["aov_usd"] - values["items_per_order"] * values["average_item_price_usd"]) < Decimal("1e-18"), "客单价数量与价格恒等式未对平。")
    return values


def basket_summaries(rows, catalog):
    output = []
    for period in PRODUCT_PERIODS:
        selected = select_period(rows, period)
        output.append({"period": period, "grouping": "overall", "primary_product_id": None, "product_name": "all", **basket_metrics(selected)})
        grouped = defaultdict(list)
        for row in selected:
            grouped[row["primary_product_id"]].append(row)
        for product_id, group in sorted(grouped.items()):
            output.append({"period": period, "grouping": "primary_product", "primary_product_id": product_id, "product_name": catalog[product_id]["product_name"], **basket_metrics(group)})
    return output


def pair_summaries(raw, baskets, catalog, baseline):
    months = {row["month_start"]: row for row in baseline["monthly_rows"]}
    rows, seen = [], set()
    for item in raw:
        row = {"month_start": month_floor(item["month_start"]).isoformat(), **{key: int(item[key]) for key in ("product_a", "product_b", "pair_orders")}}
        key = (row["month_start"], row["product_a"], row["product_b"])
        require(key not in seen and row["product_a"] < row["product_b"], "商品搭配重复或方向错误。")
        seen.add(key)
        row["period_status"] = months[row["month_start"]]["period_status"]
        rows.append(row)
    output = []
    for period in PRODUCT_PERIODS:
        total_orders = len(select_period(baskets, period))
        pairs = defaultdict(int)
        for row in select_period(rows, period):
            pairs[(row["product_a"], row["product_b"])] += row["pair_orders"]
        for (left, right), count in sorted(pairs.items()):
            require(count <= total_orders, "商品组合订单数超出同期全部订单数。")
            output.append({"period": period, "product_a": left, "product_a_name": catalog[left]["product_name"], "product_b": right, "product_b_name": catalog[right]["product_name"], "pair_orders": count, "all_orders": total_orders, "pair_order_share": ratio(count, total_orders)})
    return output


def compare_products(summary):
    lookup = {(row["period"], row["product_id"]): row for row in summary}
    result = []
    for comparison, old_period, new_period in COMPARISONS:
        old = {key[1]: value for key, value in lookup.items() if key[0] == old_period}
        new = {key[1]: value for key, value in lookup.items() if key[0] == new_period}
        old_total = sum((value["sales_usd"] for value in old.values()), Decimal(0))
        new_total = sum((value["sales_usd"] for value in new.values()), Decimal(0))
        for product in sorted(old.keys() | new.keys()):
            before, after = old.get(product), new.get(product)
            old_sales = before["sales_usd"] if before else Decimal(0)
            new_sales = after["sales_usd"] if after else Decimal(0)
            result.append({
                "comparison": comparison, "product_id": product, "product_name": (after or before)["product_name"],
                "presence": "continuing" if before and after else "entering" if after else "exiting",
                "old_sales_usd": old_sales, "new_sales_usd": new_sales,
                "sales_delta_usd": new_sales - old_sales,
                "sales_growth": ratio(new_sales - old_sales, old_sales),
                "revenue_delta_contribution": ratio(new_sales - old_sales, new_total - old_total),
                "old_window_refund_item_rate": before["window_refund_item_rate"] if before else None,
                "new_window_refund_item_rate": after["window_refund_item_rate"] if after else None,
            })
    return result


def log_run(status, message):
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    with (ROOT / "docs" / "project_process.md").open("a", encoding="utf-8") as handle:
        handle.write(f"\n\n### {now}：商品与退款分析运行\n\n- 状态：{status}。\n- 内容：{message}\n- 命令：`env/python/bin/python -B src/product_analysis.py`。\n- 验证与问题：见 output/product_analysis_report.json 与终端输出。\n")


def run(args):
    require(sys.prefix != sys.base_prefix and Path(sys.prefix).resolve().is_relative_to(ROOT / "env"), "请使用项目 Python 环境。")
    require(1 <= args.window_days <= 365, "窗口天数必须为 1—365。")
    baseline_bytes = (ROOT / "output" / "monthly_overview_report.json").read_bytes()
    baseline = json.loads(baseline_bytes)
    quality = json.loads((ROOT / "output" / "data_quality_report.json").read_text(encoding="utf-8"))
    require(baseline["status"] == quality["summary"]["overall"] == "PASS", "前序质量或月度报告未通过。")
    sources = {name: (ROOT / "src" / name).read_text(encoding="utf-8") for name in ("product_analysis.sql", "product_baskets.sql", "product_pairs.sql")}
    url = URL.create("mysql+pymysql", username=args.user, password=getpass("请输入 MySQL 密码："), host=args.host, port=args.port, database=args.database, query={"charset": "utf8mb4"})
    engine = create_engine(url, hide_parameters=True, isolation_level="REPEATABLE READ")
    try:
        with engine.connect() as connection:
            print("正在核对订单、商品明细与退款来源……", flush=True)
            context = dict(connection.execute(text(CONTEXT_SQL)).mappings().one())
            for key, expected in baseline["source_context"].items():
                value = context[key]
                require(value.isoformat() == expected if isinstance(value, datetime) else Decimal(str(value)) == Decimal(str(expected)), f"{key} 基准变化，请重查前序报告。")
            profiles = [dict(row) for row in connection.execute(text(PROFILE_SQL)).mappings()]
            expected_profiles = {row["table"]: row for row in quality["table_profiles"]}
            for profile in profiles:
                expected = expected_profiles[profile["table_name"]]
                require(profile["row_count"] == expected["row_count"] and str(profile["first_at"]) == expected["first_at"] and str(profile["last_at"]) == expected["last_at"], "商品或退款源记录范围变化。")
            catalog = {int(row["product_id"]): dict(row) for row in connection.execute(text("SELECT product_id, created_at AS product_launched_at, product_name FROM products ORDER BY product_id")).mappings()}
            print("正在读取商品、订单篮子、搭配和退款事件……", flush=True)
            items = [dict(row) for row in connection.execute(text(sources["product_analysis.sql"])).mappings()]
            refunds = [dict(row) for row in connection.execute(text(REFUND_SQL)).mappings()]
            raw_baskets = [dict(row) for row in connection.execute(text(sources["product_baskets.sql"])).mappings()]
            raw_pairs = [dict(row) for row in connection.execute(text(sources["product_pairs.sql"])).mappings()]
    finally:
        engine.dispose()
    require(len(items) == expected_profiles["order_items"]["row_count"] and len(refunds) == expected_profiles["order_item_refunds"]["row_count"], "商品/退款提取数量不一致。")
    require(len(raw_baskets) == context["orders"], "订单篮子提取数量不一致。")
    cutoff = max((stamp(row["refund_at"]) for row in refunds), default=stamp(context["order_last_at"]))
    item_rows, events = prepare_items(items, refunds, baseline, args.window_days, cutoff)
    baskets = prepare_baskets(raw_baskets, baseline)
    checks = []
    for month in baseline["monthly_rows"]:
        selected_items = [row for row in item_rows if row["month_start"] == month["month_start"]]
        selected_baskets = [row for row in baskets if row["month_start"] == month["month_start"]]
        product, basket = product_metrics(selected_items), basket_metrics(selected_baskets)
        for field, actual, expected in (
            ("sales_usd", product["sales_usd"], Decimal(month["sales_usd"])),
            ("cogs_usd", product["cogs_usd"], Decimal(month["cogs_usd"])),
            ("primary_items", product["primary_items"], month["orders"]),
            ("basket_orders", basket["orders"], month["orders"]),
            ("basket_items", basket["items"], product["items"]),
            ("basket_sales", basket["sales_usd"], product["sales_usd"]),
        ):
            passed = actual == expected
            checks.append({"check": f"{month['month_start']}.{field}", "passed": passed})
            require(passed, f"商品/篮子逐月对账失败：{field}")
    all_products = product_metrics(item_rows)
    require(sum(row["all_refund_events"] for row in item_rows) == len(events), "退款事件重复或丢失。")
    require(sum((row["observed_refund_usd"] for row in item_rows), Decimal(0)) == sum((row["refund_amount_usd"] for row in events if row["within_cutoff"]), Decimal(0)), "退款金额未对平。")
    summary, monthly = product_groups(item_rows), product_groups(item_rows, periods=False)
    basket_summary = basket_summaries(baskets, catalog)
    pairs = pair_summaries(raw_pairs, baskets, catalog, baseline)
    comparisons = compare_products(summary)
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    for name, rows in (
        ("product_item_audit.csv", item_rows), ("refund_events_audit.csv", events),
        ("product_monthly.csv", monthly), ("product_summary.csv", summary),
        ("product_baskets.csv", basket_summary), ("product_pairs.csv", pairs),
        ("product_comparisons.csv", comparisons),
    ):
        write_csv(name, rows)
    report = {
        "status": "PASS", "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "monthly_baseline_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
        "sql_sha256": {name: hashlib.sha256(sql.encode()).hexdigest() for name, sql in sources.items()},
        "window_days": args.window_days, "refund_observation_cutoff_at": cutoff,
        "cutoff_basis": "last_observed_refund_at_proxy" if refunds else "last_order_at_proxy_no_refunds",
        "cutoff_limit": "最大已观测事件时间是分析截止代理，不证明之前每一天的退款数据都采集完整。",
        "validation": {"passed": len(checks), "failed": 0, "checks": checks, "additional_checks": ["source_profiles", "item_and_refund_uniqueness", "refund_parent_time_amount", "basket_per_order_reconciliation", "refund_ledger_totals", "aov_identity"]},
        "catalog": list(catalog.values()), "all_period_products": all_products,
        "product_summaries": summary, "monthly_products": monthly,
        "basket_summaries": basket_summary, "product_pairs": pairs, "comparisons": comparisons,
        "methodology": "docs/product_methodology.md",
    }
    (output / "product_analysis_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=json_value) + "\n", encoding="utf-8")
    log_run("成功", f"{len(checks)} 项逐月对账及附加检查通过；{len(item_rows)} 条商品明细、{len(events)} 条退款事件；窗口 {args.window_days} 天，不足窗口明细 {all_products['window_ineligible_items']} 条。")
    print(json.dumps({"status": "PASS", "validation_passed": len(checks), "window_days": args.window_days, "refund_observation_cutoff_at": cutoff, "cutoff_basis": report["cutoff_basis"], "all_period_products": all_products, "nov_dec_baskets": [row for row in basket_summary if row["grouping"] == "overall" and row["period"] in ("nov_2014", "dec_2014")]}, ensure_ascii=False, indent=2, default=json_value))
    print("商品与退款结果已生成，过程记录已追加。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", default="toy_analyst")
    parser.add_argument("--database", default="toy_store_ecommerce")
    parser.add_argument("--window-days", type=int, default=30)
    args = parser.parse_args()
    try:
        run(args)
    except Exception as error:
        output = ROOT / "output"
        output.mkdir(exist_ok=True)
        (output / "product_analysis_report.json").write_text(json.dumps({"status": "FAIL", "error_type": type(error).__name__, "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"), "note": "本次未完成，旧 CSV 可能来自之前运行。"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log_run("失败", f"错误类型：{type(error).__name__}，具体原因见终端。")
        print(f"运行失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
