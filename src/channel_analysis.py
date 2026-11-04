"""Analyze channel/device performance using read-only MySQL queries."""

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from getpass import getpass
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import URL, create_engine, text

from monthly_overview import CONTEXT_SQL, json_value, month_floor, ratio


ROOT = Path(__file__).resolve().parents[1]
COUNTS = ("sessions", "converted_sessions", "session_orders")
MONEY = ("session_sales_usd", "session_cogs_usd")
MEASURES = (*COUNTS, *MONEY)
RAW_DIMENSIONS = ("utm_source", "utm_campaign", "utm_content", "device_type", "http_referer")
PERIODS = (
    "all_comparable", "year_2013", "year_2014", "jan_feb_2014",
    "jan_feb_2015", "nov_2014", "dec_2014",
)
COMPARISONS = (
    ("2014_vs_2013", "year_2013", "year_2014"),
    ("dec_vs_nov_2014", "nov_2014", "dec_2014"),
    ("jan_feb_2015_vs_2014", "jan_feb_2014", "jan_feb_2015"),
)
GROUPINGS = {
    "channel": ("channel",),
    "device": ("device_type",),
    "channel_device": ("channel", "device_type"),
}
EPSILON = Decimal("0.000000000000000001")
LOW_VOLUME = 100


def require(condition, message):
    if not condition:
        raise ValueError(message)


def clean_tag(value):
    if value is None:
        return None
    value = str(value).strip().lower()
    return value if value and value not in {"null", "\\n"} else None


def classify_channel(row):
    source = clean_tag(row.get("utm_source"))
    campaign = clean_tag(row.get("utm_campaign"))
    content = clean_tag(row.get("utm_content"))
    referrer = clean_tag(row.get("http_referer"))
    if source in {"gsearch", "bsearch"}:
        suffix = campaign if campaign in {"brand", "nonbrand"} else "other_campaign"
        return f"{source}_{suffix}", "recognized_source_and_campaign"
    if source == "socialbook":
        return "socialbook_tagged", "recognized_social_source"
    if source:
        return "other_tagged", "other_nonempty_utm_source"
    if campaign or content:
        return "unclassified_partial_utm", "utm_without_source"
    if not referrer:
        return "direct_candidate", "no_utm_and_no_referrer"
    try:
        host = (urlsplit(referrer).hostname or "").rstrip(".")
    except ValueError:
        host = ""
    for provider in ("gsearch", "bsearch"):
        if host in {f"{provider}.com", f"www.{provider}.com"}:
            return f"organic_{provider}_candidate", "search_referrer_without_utm"
    return "referral_or_unknown_candidate", "other_or_unparseable_referrer_without_utm"


def empty_measures():
    return {**{key: 0 for key in COUNTS}, **{key: Decimal(0) for key in MONEY}}


def totals(rows):
    result = empty_measures()
    for row in rows:
        for key in MEASURES:
            result[key] += row[key]
    return result


def metrics(values):
    result = dict(values)
    result["conversion_rate"] = ratio(values["converted_sessions"], values["sessions"])
    result["revenue_per_session_usd"] = ratio(values["session_sales_usd"], values["sessions"])
    result["session_aov_usd"] = ratio(values["session_sales_usd"], values["session_orders"])
    return result


def aggregate(rows, dimensions):
    grouped = defaultdict(empty_measures)
    for row in rows:
        key = tuple(row[field] for field in dimensions)
        for measure in MEASURES:
            grouped[key][measure] += row[measure]
    return dict(grouped)


def prepare_rows(raw_rows, baseline):
    monthly = {row["month_start"]: row for row in baseline["monthly_rows"]}
    checks, rows, seen = [], [], set()
    for raw in raw_rows:
        month = month_floor(raw["month_start"]).isoformat()
        require(month in monthly, f"渠道数据出现月度报告以外的月份：{month}")
        raw_key = (month, *(raw[field] for field in RAW_DIMENSIONS))
        require(raw_key not in seen, "渠道原始维度组合重复。")
        seen.add(raw_key)
        row = {
            "month_start": month,
            "period_status": monthly[month]["period_status"],
            **{key: raw[key] for key in RAW_DIMENSIONS},
            **{key: int(raw[key]) for key in COUNTS},
            **{key: Decimal(str(raw[key])) for key in MONEY},
        }
        row["channel"], row["classification_rule"] = classify_channel(row)
        require(row["sessions"] > 0, "原始分组应至少有一次会话。")
        require(0 <= row["converted_sessions"] <= row["sessions"], "分组转化率超出边界。")
        require(row["session_orders"] >= row["converted_sessions"], "分组订单数小于转化会话数。")
        require(all(row[key] >= 0 for key in MEASURES), "分组出现负数。")
        rows.append(row)
    by_month = aggregate(rows, ("month_start",))
    for month, expected in monthly.items():
        actual = by_month.get((month,), empty_measures())
        for key in (*COUNTS, "session_sales_usd"):
            passed = Decimal(actual[key]) == Decimal(str(expected[key]))
            checks.append({"check": f"{month}.{key}", "passed": passed})
            require(passed, f"{month} / {key} 与月度经营报告不一致。")
    return rows, checks


def period_rows(rows, period):
    comparable = [row for row in rows if row["period_status"] == "comparable_month"]
    if period == "all_comparable":
        return comparable
    if period.startswith("year_"):
        return [row for row in comparable if row["month_start"].startswith(period[-4:])]
    if period.startswith("jan_feb_"):
        return [row for row in comparable if row["month_start"][:7] in {f"{period[-4:]}-01", f"{period[-4:]}-02"}]
    month = {"nov_2014": "2014-11", "dec_2014": "2014-12"}[period]
    return [row for row in comparable if row["month_start"].startswith(month)]


def display_dimensions(dimensions, key):
    return {"channel": "all", "device_type": "all", **dict(zip(dimensions, key))}


def summarize_periods(rows):
    result = []
    for period in PERIODS:
        selected = period_rows(rows, period)
        total = totals(selected)
        require(total["sessions"] > 0, f"比较区间没有会话：{period}")
        for grouping, dimensions in {"overall": (), **GROUPINGS}.items():
            for key, values in sorted(aggregate(selected, dimensions).items()):
                result.append({
                    "period": period, "grouping": grouping,
                    **display_dimensions(dimensions, key), **metrics(values),
                    "session_share": ratio(values["sessions"], total["sessions"]),
                    "revenue_share": ratio(values["session_sales_usd"], total["session_sales_usd"]),
                    "low_volume_review": values["sessions"] < LOW_VOLUME,
                })
    return result


def decompose(old, new, numerator):
    """Symmetric split of a weighted rate change into mix and within-group effects."""
    n0 = sum(group["sessions"] for group in old.values())
    n1 = sum(group["sessions"] for group in new.values())
    require(n0 > 0 and n1 > 0, "分解需要两个非空时期。")
    details = []
    for key in sorted(old.keys() | new.keys()):
        before, after = old.get(key, empty_measures()), new.get(key, empty_measures())
        w0, w1 = ratio(before["sessions"], n0), ratio(after["sessions"], n1)
        r0 = ratio(before[numerator], before["sessions"])
        r1 = ratio(after[numerator], after["sessions"])
        # For an entering/exiting group, its observed rate is carried over solely
        # for accounting: all its contribution is assigned to the mix effect.
        if r0 is None:
            r0 = r1
        if r1 is None:
            r1 = r0
        details.append({
            "group": key, "old_weight": w0, "new_weight": w1,
            "old_rate_used": r0, "new_rate_used": r1,
            "mix_effect": (w1 - w0) * (r0 + r1) / 2,
            "within_effect": (r1 - r0) * (w0 + w1) / 2,
            "presence": "continuing" if key in old and key in new else "entering" if key in new else "exiting",
        })
    change = ratio(sum(group[numerator] for group in new.values()), n1) - ratio(sum(group[numerator] for group in old.values()), n0)
    mix = sum((row["mix_effect"] for row in details), Decimal(0))
    within = sum((row["within_effect"] for row in details), Decimal(0))
    residual = change - mix - within
    require(abs(residual) <= EPSILON, "结构与组内分解未对平。")
    return {"change": change, "mix_effect": mix, "within_effect": within, "residual": residual, "groups": details}


def compare_periods(rows):
    comparisons, decomposition = [], []
    for comparison, old_period, new_period in COMPARISONS:
        before_rows, after_rows = period_rows(rows, old_period), period_rows(rows, new_period)
        before_total, after_total = totals(before_rows), totals(after_rows)
        revenue_delta = after_total["session_sales_usd"] - before_total["session_sales_usd"]
        for grouping, dimensions in GROUPINGS.items():
            before, after = aggregate(before_rows, dimensions), aggregate(after_rows, dimensions)
            for key in sorted(before.keys() | after.keys()):
                old, new = metrics(before.get(key, empty_measures())), metrics(after.get(key, empty_measures()))
                result = {
                    "comparison": comparison, "grouping": grouping,
                    **display_dimensions(dimensions, key),
                    "presence": "continuing" if key in before and key in after else "entering" if key in after else "exiting",
                    "old_sessions": old["sessions"], "new_sessions": new["sessions"],
                    "sessions_delta": new["sessions"] - old["sessions"],
                    "old_session_sales_usd": old["session_sales_usd"],
                    "new_session_sales_usd": new["session_sales_usd"],
                    "session_sales_delta_usd": new["session_sales_usd"] - old["session_sales_usd"],
                    "old_conversion_rate": old["conversion_rate"], "new_conversion_rate": new["conversion_rate"],
                    "old_revenue_per_session_usd": old["revenue_per_session_usd"],
                    "new_revenue_per_session_usd": new["revenue_per_session_usd"],
                    "old_session_share": ratio(old["sessions"], before_total["sessions"]),
                    "new_session_share": ratio(new["sessions"], after_total["sessions"]),
                    "low_volume_review": min(old["sessions"], new["sessions"]) < LOW_VOLUME,
                }
                result["session_sales_growth"] = ratio(result["session_sales_delta_usd"], old["session_sales_usd"])
                result["revenue_delta_contribution"] = ratio(result["session_sales_delta_usd"], revenue_delta)
                result["conversion_change_pp"] = (
                    (new["conversion_rate"] - old["conversion_rate"]) * 100
                    if old["conversion_rate"] is not None and new["conversion_rate"] is not None else None
                )
                comparisons.append(result)
            for metric, numerator in (
                ("conversion_rate", "converted_sessions"),
                ("revenue_per_session_usd", "session_sales_usd"),
            ):
                split = decompose(before, after, numerator)
                for group in split.pop("groups"):
                    group.update(display_dimensions(dimensions, group.pop("group")))
                    # Raw effects are rate fractions; pp fields multiply by 100.
                    if metric == "conversion_rate":
                        group["mix_effect_pp"] = group["mix_effect"] * 100
                        group["within_effect_pp"] = group["within_effect"] * 100
                    group.update({"comparison": comparison, "grouping": grouping, "metric": metric})
                    decomposition.append(group)
                decomposition.append({
                    "comparison": comparison, "grouping": grouping, "metric": metric,
                    "channel": "all", "device_type": "all", "presence": "total", **split,
                    "mix_effect_pp": split["mix_effect"] * 100 if metric == "conversion_rate" else None,
                    "within_effect_pp": split["within_effect"] * 100 if metric == "conversion_rate" else None,
                })
    return comparisons, decomposition


def write_csv(name, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with (ROOT / "output" / name).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json_value(value) if isinstance(value, Decimal) else value for key, value in row.items()})


def log_run(status, message):
    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    with (ROOT / "docs" / "project_process.md").open("a", encoding="utf-8") as handle:
        handle.write(
            f"\n\n### {timestamp}：渠道与设备分析运行\n\n"
            f"- 状态：{status}。\n- 内容：{message}\n"
            "- 命令：`env/python/bin/python -B src/channel_analysis.py`。\n"
            "- 问题与验证：详见本次终端输出与 output/channel_analysis_report.json。\n"
        )


def run(args):
    require(sys.prefix != sys.base_prefix and Path(sys.prefix).resolve().is_relative_to(ROOT / "env"), "请使用本项目 env/python/bin/python 运行。")
    baseline_path = ROOT / "output" / "monthly_overview_report.json"
    baseline_bytes = baseline_path.read_bytes()
    baseline = json.loads(baseline_bytes)
    require(baseline["status"] == "PASS" and all(row["passed"] for row in baseline["validation"]["checks"]), "月度分析基准未通过。")
    comparable_months = {row["month_start"][:7] for row in baseline["monthly_rows"] if row["period_status"] == "comparable_month"}
    required_months = {f"{year}-{month:02}" for year in (2013, 2014) for month in range(1, 13)} | {"2015-01", "2015-02"}
    require(required_months <= comparable_months, "本模块比较区间需要 2013/2014 全年及 2015 年前两月。")
    sql = (ROOT / "src" / "channel_analysis.sql").read_text(encoding="utf-8")
    url = URL.create("mysql+pymysql", username=args.user, password=getpass("请输入 MySQL 密码："), host=args.host, port=args.port, database=args.database, query={"charset": "utf8mb4"})
    engine = create_engine(url, hide_parameters=True, isolation_level="REPEATABLE READ")
    try:
        with engine.connect() as connection:
            print("正在读取渠道、活动、广告内容与设备汇总……", flush=True)
            context = dict(connection.execute(text(CONTEXT_SQL)).mappings().one())
            for key, expected in baseline["source_context"].items():
                actual = context[key]
                if isinstance(actual, datetime):
                    matched = actual.isoformat() == expected
                else:
                    matched = Decimal(str(actual)) == Decimal(str(expected))
                require(matched, f"数据库基准变化：{key}，请重新生成质量和月度报告。")
            raw = [dict(row) for row in connection.execute(text(sql)).mappings()]
    finally:
        engine.dispose()
    rows, checks = prepare_rows(raw, baseline)
    require(totals(rows)["session_cogs_usd"] == Decimal(baseline["source_context"]["cogs_usd"]), "会话口径成本合计未与原表对平。")
    checks.append({"check": "all_period.session_cogs_usd", "passed": True})
    monthly = []
    by_month_group = aggregate(rows, ("month_start", "period_status", "channel", "device_type"))
    month_totals = aggregate(rows, ("month_start",))
    for key, values in sorted(by_month_group.items()):
        month, status, channel, device = key
        monthly.append({
            "month_start": month, "period_status": status, "channel": channel,
            "device_type": device, **metrics(values),
            "session_share": ratio(values["sessions"], month_totals[(month,)]["sessions"]),
            "low_volume_review": values["sessions"] < LOW_VOLUME,
        })
    for key in MEASURES:
        require(totals(monthly)[key] == totals(rows)[key], "渠道设备聚合后总量不一致。")
    summary = summarize_periods(rows)
    comparisons, decomposition = compare_periods(rows)
    for comparison, _, _ in COMPARISONS:
        for grouping in GROUPINGS:
            shares = [row["revenue_delta_contribution"] for row in comparisons if row["comparison"] == comparison and row["grouping"] == grouping]
            if all(value is not None for value in shares):
                require(abs(sum(shares) - 1) <= EPSILON, "分组销售增量贡献之和不为 1。")
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    report = {
        "status": "PASS", "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "monthly_baseline_generated_at": baseline["generated_at"],
        "monthly_baseline_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
        "sql_sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        "classification_version": "1", "low_volume_threshold_sessions": LOW_VOLUME,
        "validation": {"passed": len(checks), "failed": 0, "checks": checks,
                       "additional_checks": ["source_context_match", "unique_raw_groups", "group_metric_bounds", "aggregation_totals", "growth_contribution_sum", "decomposition_reconciled"]},
        "raw_profile_rows": len(rows), "monthly_channel_device_rows": len(monthly),
        "channel_rules": "docs/channel_methodology.md",
        "period_summaries": summary, "comparisons": comparisons,
        "decomposition": decomposition,
    }
    write_csv("channel_audit.csv", rows)
    write_csv("channel_monthly.csv", monthly)
    write_csv("channel_summary.csv", summary)
    write_csv("channel_comparisons.csv", comparisons)
    write_csv("channel_decomposition.csv", decomposition)
    (output / "channel_analysis_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=json_value) + "\n", encoding="utf-8")
    log_run("成功", f"生成 {len(rows)} 条原始维度审计记录、{len(monthly)} 条月度渠道设备记录；{len(checks)} 项逐月/成本核对通过，附加分组与分解核对通过。")
    print(json.dumps({
        "status": "PASS", "validation_passed": len(checks),
        "raw_profile_rows": len(rows), "monthly_channel_device_rows": len(monthly),
        "period_summaries": [row for row in summary if row["grouping"] == "overall"],
        "dec_vs_nov_channel_device_decomposition": [row for row in decomposition if row["comparison"] == "dec_vs_nov_2014" and row["grouping"] == "channel_device" and row["presence"] == "total"],
    }, ensure_ascii=False, indent=2, default=json_value))
    print("渠道与设备结果已生成，项目过程记录已追加。")


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
        (output / "channel_analysis_report.json").write_text(json.dumps({
            "status": "FAIL", "error_type": type(error).__name__,
            "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
            "note": "本次渠道分析未完成；已有 CSV 可能属于较早运行。",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log_run("失败", f"错误类型：{type(error).__name__}；具体原因见终端，本次未完成。")
        print(f"运行失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
