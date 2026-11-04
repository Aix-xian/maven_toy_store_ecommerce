"""Read ordered page-path coverage, compare devices/channels, and export reports."""

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from getpass import getpass
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import URL, create_engine, text

from channel_analysis import (
    COMPARISONS, PERIODS, RAW_DIMENSIONS, classify_channel, period_rows,
    require, write_csv,
)
from monthly_overview import CONTEXT_SQL, json_value, month_floor, ratio


ROOT = Path(__file__).resolve().parents[1]
STAGES = (
    "sessions", "pageview_sessions", "ordered_products", "ordered_detail",
    "ordered_cart", "ordered_shipping", "ordered_billing", "ordered_thank_you",
)
RAW_STAGES = tuple("raw_" + field.removeprefix("ordered_") for field in STAGES[2:])
EXCEPTIONS = (
    "orders_without_ordered_thank_you", "ordered_thank_you_without_order",
    "orders_without_raw_thank_you", "raw_thank_you_without_order",
)
COUNTS = tuple(dict.fromkeys((
    *STAGES, "converted_sessions", "session_orders", "pageview_count",
    "single_page_sessions", *RAW_STAGES, *EXCEPTIONS,
)))
MONEY = ("session_sales_usd", "session_cogs_usd")
MEASURES = (*COUNTS, *MONEY)
GROUPINGS = {
    "overall": (), "device": ("device_type",), "channel": ("channel",),
    "channel_device": ("channel", "device_type"), "landing": ("landing_url",),
}
PAGE_MAP = {
    **{url: "entry" for url in ("/home", *(f"/lander-{i}" for i in range(1, 6)))},
    "/products": "products",
    **{url: "detail" for url in (
        "/the-original-mr-fuzzy", "/the-forever-love-bear",
        "/the-birthday-sugar-panda", "/the-hudson-river-mini-bear",
    )},
    "/cart": "cart", "/shipping": "shipping", "/billing": "billing",
    "/billing-2": "billing", "/thank-you-for-your-order": "thank_you",
}


def empty_values():
    return {**{key: 0 for key in COUNTS}, **{key: Decimal(0) for key in MONEY}}


def aggregate(rows, dimensions):
    grouped = defaultdict(empty_values)
    for row in rows:
        key = tuple(row[field] for field in dimensions)
        for measure in MEASURES:
            grouped[key][measure] += row[measure]
    return dict(grouped)


def labels(dimensions, key):
    return {"channel": "all", "device_type": "all", "landing_url": "all", **dict(zip(dimensions, key))}


def prepare_rows(raw, baseline):
    months = {row["month_start"]: row for row in baseline["monthly_rows"]}
    seen, rows, checks = set(), [], []
    for item in raw:
        month = month_floor(item["month_start"]).isoformat()
        require(month in months, f"漏斗数据出现基准以外的月份：{month}")
        key = (month, *(item[field] for field in RAW_DIMENSIONS), item["landing_url"])
        require(key not in seen, "漏斗维度分组重复。")
        seen.add(key)
        row = {
            "month_start": month, "period_status": months[month]["period_status"],
            **{field: item[field] for field in RAW_DIMENSIONS},
            "landing_url": item["landing_url"] or "no_pageview",
            **{field: int(item[field]) for field in COUNTS},
            **{field: Decimal(str(item[field])) for field in MONEY},
        }
        row["channel"], row["classification_rule"] = classify_channel(row)
        require(row["sessions"] > 0 and all(row[field] >= 0 for field in MEASURES), "漏斗分组出现非法数量或金额。")
        sequence = [row[field] for field in STAGES]
        require(all(left >= right for left, right in zip(sequence, sequence[1:])), "有序漏斗出现后续人数超过前序。")
        for ordered, unordered in zip(STAGES[2:], RAW_STAGES):
            require(row[ordered] <= row[unordered] <= row["pageview_sessions"], "有序路径与原始到达次数不一致。")
        require(row["converted_sessions"] <= row["sessions"] and row["session_orders"] >= row["converted_sessions"], "订单与会话数量不一致。")
        require(row["single_page_sessions"] <= row["pageview_sessions"] <= row["pageview_count"], "页面浏览数量不一致。")
        require(row["orders_without_ordered_thank_you"] <= row["converted_sessions"], "路径异常计数超出转化会话数。")
        require(row["ordered_thank_you_without_order"] <= row["ordered_thank_you"], "确认页异常计数不一致。")
        rows.append(row)
    monthly = aggregate(rows, ("month_start",))
    for month, expected in months.items():
        actual = monthly.get((month,), empty_values())
        for field in ("sessions", "converted_sessions", "session_orders", "session_sales_usd"):
            passed = Decimal(actual[field]) == Decimal(str(expected[field]))
            checks.append({"check": f"{month}.{field}", "passed": passed})
            require(passed, f"{month} / {field} 与月度基准不一致。")
    return rows, checks


def build_summaries(rows):
    summaries, stages = [], []
    for period in PERIODS:
        selected = period_rows(rows, period)
        for grouping, dimensions in GROUPINGS.items():
            grouped = aggregate(selected, dimensions)
            for key, values in sorted(grouped.items()):
                attributes = {"period": period, "grouping": grouping, **labels(dimensions, key)}
                summaries.append({
                    **attributes, **values,
                    "conversion_rate": ratio(values["converted_sessions"], values["sessions"]),
                    "single_page_rate_observed": ratio(values["single_page_sessions"], values["pageview_sessions"]),
                    "revenue_per_session_usd": ratio(values["session_sales_usd"], values["sessions"]),
                    "low_volume_review": values["sessions"] < 100,
                })
                previous = None
                for stage in STAGES:
                    count = values[stage]
                    stages.append({
                        **attributes, "stage": stage, "stage_sessions": count,
                        "previous_stage_sessions": previous,
                        "step_rate": ratio(count, previous) if previous is not None else None,
                        "reach_rate": ratio(count, values["sessions"]),
                        "not_following_next_stage": previous - count if previous is not None else None,
                        "low_volume_review": previous < 100 if previous is not None else values["sessions"] < 100,
                    })
                    previous = count
    return summaries, stages


def build_comparisons(rows):
    comparisons = []
    for comparison, old_period, new_period in COMPARISONS:
        for grouping, dimensions in GROUPINGS.items():
            if grouping == "landing":
                continue
            old = aggregate(period_rows(rows, old_period), dimensions)
            new = aggregate(period_rows(rows, new_period), dimensions)
            for key in sorted(old.keys() | new.keys()):
                before, after = old.get(key, empty_values()), new.get(key, empty_values())
                for previous, stage in zip(STAGES, STAGES[1:]):
                    old_rate, new_rate = ratio(before[stage], before[previous]), ratio(after[stage], after[previous])
                    comparisons.append({
                        "comparison": comparison, "grouping": grouping, **labels(dimensions, key),
                        "from_stage": previous, "to_stage": stage,
                        "old_previous_sessions": before[previous], "new_previous_sessions": after[previous],
                        "old_stage_sessions": before[stage], "new_stage_sessions": after[stage],
                        "old_step_rate": old_rate, "new_step_rate": new_rate,
                        "step_rate_change_pp": (new_rate - old_rate) * 100 if old_rate is not None and new_rate is not None else None,
                        "low_volume_review": min(before[previous], after[previous]) < 100,
                    })
    return comparisons


def log_run(status, message):
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    with (ROOT / "docs" / "project_process.md").open("a", encoding="utf-8") as handle:
        handle.write(f"\n\n### {stamp}：购买漏斗分析运行\n\n- 状态：{status}。\n- 内容：{message}\n- 命令：`env/python/bin/python -B src/funnel_analysis.py`。\n- 验证和问题：见 output/funnel_analysis_report.json 与本次终端输出。\n")


def run(args):
    require(sys.prefix != sys.base_prefix and Path(sys.prefix).resolve().is_relative_to(ROOT / "env"), "请使用项目 env/python/bin/python。")
    monthly_bytes = (ROOT / "output" / "monthly_overview_report.json").read_bytes()
    channel_bytes = (ROOT / "output" / "channel_analysis_report.json").read_bytes()
    baseline, channels = json.loads(monthly_bytes), json.loads(channel_bytes)
    quality = json.loads((ROOT / "output" / "data_quality_report.json").read_text(encoding="utf-8"))
    require(baseline["status"] == channels["status"] == quality["summary"]["overall"] == "PASS", "前序报告未全部通过。")
    require(hashlib.sha256(monthly_bytes).hexdigest() == channels["monthly_baseline_sha256"], "渠道和月度基准不一致。")
    page_baseline = next(item for item in quality["table_profiles"] if item["table"] == "website_pageviews")
    sql = (ROOT / "src" / "funnel_analysis.sql").read_text(encoding="utf-8")
    url = URL.create("mysql+pymysql", username=args.user, password=getpass("请输入 MySQL 密码："), host=args.host, port=args.port, database=args.database, query={"charset": "utf8mb4"})
    engine = create_engine(url, hide_parameters=True, isolation_level="REPEATABLE READ")
    try:
        with engine.connect() as connection:
            print("正在核对访问、订单与页面浏览基准……", flush=True)
            context = dict(connection.execute(text(CONTEXT_SQL)).mappings().one())
            for key, expected in baseline["source_context"].items():
                actual = context[key]
                require(actual.isoformat() == expected if isinstance(actual, datetime) else Decimal(str(actual)) == Decimal(str(expected)), f"{key} 基准变化，请重新检查前序报告。")
            page_profile = dict(connection.execute(text("SELECT COUNT(*) AS row_count, MIN(created_at) AS first_at, MAX(created_at) AS last_at FROM website_pageviews")).mappings().one())
            require(page_profile["row_count"] == page_baseline["row_count"] and str(page_profile["first_at"]) == page_baseline["first_at"] and str(page_profile["last_at"]) == page_baseline["last_at"], "页面浏览基准变化。")
            inventory = [dict(item) for item in connection.execute(text("SELECT pageview_url, COUNT(*) AS pageviews FROM website_pageviews GROUP BY pageview_url ORDER BY pageview_url")).mappings()]
            print("正在按页面时间与浏览编号计算有序路径……", flush=True)
            raw = [dict(item) for item in connection.execute(text(sql)).mappings()]
    finally:
        engine.dispose()
    rows, checks = prepare_rows(raw, baseline)
    total = next(iter(aggregate(rows, ()).values()))
    for name, actual, expected in (
        ("pageviews_count", total["pageview_count"], page_baseline["row_count"]),
        ("page_inventory_count", sum(item["pageviews"] for item in inventory), page_baseline["row_count"]),
        ("session_cogs_total", total["session_cogs_usd"], Decimal(baseline["source_context"]["cogs_usd"])),
    ):
        passed = actual == expected
        checks.append({"check": name, "passed": passed})
        require(passed, f"漏斗 {name} 对账失败。")
    summaries, stages = build_summaries(rows)
    comparisons = build_comparisons(rows)
    previous = {(item["period"], item["grouping"], item["channel"], item["device_type"]): item for item in channels["period_summaries"]}
    for summary in summaries:
        if summary["grouping"] == "landing":
            continue
        key = tuple(summary[field] for field in ("period", "grouping", "channel", "device_type"))
        require(key in previous, "漏斗出现前序渠道报告未包含的分组。")
        for field in ("sessions", "converted_sessions", "session_orders", *MONEY):
            require(Decimal(summary[field]) == Decimal(str(previous[key][field])), "漏斗分组与前序渠道汇总不一致。")
    review = []
    for item in inventory:
        item["mapped_family"] = PAGE_MAP.get(item["pageview_url"], "unmapped")
        if item["mapped_family"] == "unmapped":
            review.append({"type": "unmapped_page", **item})
    for field in EXCEPTIONS:
        if total[field]:
            review.append({"type": field, "sessions": total[field], "note": "需解释实际订单与指定页面路径的差异，不自动认定为丢单或数据错误。"})
    if total["sessions"] > total["pageview_sessions"]:
        review.append({"type": "sessions_without_pageviews", "sessions": total["sessions"] - total["pageview_sessions"]})
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    for name, data in (
        ("funnel_audit.csv", rows), ("funnel_page_inventory.csv", inventory),
        ("funnel_groups.csv", summaries), ("funnel_stages.csv", stages),
        ("funnel_comparisons.csv", comparisons),
    ):
        write_csv(name, data)
    report = {
        "status": "PASS", "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "monthly_baseline_sha256": hashlib.sha256(monthly_bytes).hexdigest(),
        "channel_baseline_sha256": hashlib.sha256(channel_bytes).hexdigest(),
        "sql_sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        "validation": {"passed": len(checks), "failed": 0, "checks": checks, "additional_checks": ["source_context", "pageview_source", "unique_groups", "ordered_monotonicity", "raw_vs_ordered", "channel_group_totals"]},
        "page_inventory": inventory, "raw_group_rows": len(rows), "all_period_totals": total,
        "review_notes": review, "group_summaries": summaries, "stage_summaries": stages,
        "comparisons": comparisons, "methodology": "docs/funnel_methodology.md",
    }
    (output / "funnel_analysis_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=json_value) + "\n", encoding="utf-8")
    log_run("成功", f"{len(checks)} 项逐月/总量对账及附加检查通过；生成 {len(rows)} 条原始维度记录，{len(review)} 条待解释记录，5 个 CSV 和 1 个 JSON。")
    print(json.dumps({
        "status": "PASS", "validation_passed": len(checks), "review_notes": review,
        "year_2014_devices": [item for item in summaries if item["period"] == "year_2014" and item["grouping"] == "device"],
    }, ensure_ascii=False, indent=2, default=json_value))
    print("漏斗结果已生成，并追加过程记录。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", default="toy_analyst")
    parser.add_argument("--database", default="toy_store_ecommerce")
    args = parser.parse_args()
    try:
        run(args)
    except Exception as error:
        output = ROOT / "output"
        output.mkdir(exist_ok=True)
        (output / "funnel_analysis_report.json").write_text(json.dumps({"status": "FAIL", "error_type": type(error).__name__, "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"), "note": "本次未完成，旧 CSV 可能属于较早运行。"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log_run("失败", f"错误类型为 {type(error).__name__}，具体原因见终端。")
        print(f"运行失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
