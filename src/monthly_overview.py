"""Read monthly business metrics from MySQL and export validated local reports."""

import argparse
import csv
import hashlib
import io
import json
import sys
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from getpass import getpass
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import URL, create_engine, text


ROOT = Path(__file__).resolve().parents[1]
COUNTS = ("sessions", "converted_sessions", "session_orders", "orders")
AMOUNTS = ("session_sales_usd", "sales_usd", "cogs_usd")
FIELDS = (
    "month_start", "period_status", *COUNTS, *AMOUNTS,
    "conversion_rate", "aov_usd", "revenue_per_session_usd",
    "product_margin_before_refunds_usd", "sessions_mom", "orders_mom",
    "sales_mom", "conversion_rate_change_pp", "aov_mom",
    "revenue_per_session_mom",
)
CONTEXT_SQL = """
SELECT
    s.sessions, s.session_first_at, s.session_last_at,
    o.orders, o.converted_sessions, o.sales_usd, o.cogs_usd,
    o.order_first_at, o.order_last_at,
    (
        SELECT COUNT(*)
        FROM orders ord
        JOIN website_sessions ses
            ON ord.website_session_id = ses.website_session_id
        WHERE DATE_FORMAT(ord.created_at, '%Y-%m')
           <> DATE_FORMAT(ses.created_at, '%Y-%m')
    ) AS cross_month_orders
FROM (
    SELECT COUNT(*) AS sessions,
           MIN(created_at) AS session_first_at,
           MAX(created_at) AS session_last_at
    FROM website_sessions
) s
CROSS JOIN (
    SELECT COUNT(*) AS orders,
           COUNT(DISTINCT website_session_id) AS converted_sessions,
           COALESCE(SUM(price_usd), 0) AS sales_usd,
           COALESCE(SUM(cogs_usd), 0) AS cogs_usd,
           MIN(created_at) AS order_first_at,
           MAX(created_at) AS order_last_at
    FROM orders
) o
"""


def as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def month_floor(value):
    return as_date(value).replace(day=1)


def next_month(value):
    value = month_floor(value)
    return date(value.year + value.month // 12, value.month % 12 + 1, 1)


def ratio(numerator, denominator):
    return Decimal(numerator) / Decimal(denominator) if denominator else None


def add_metrics(values):
    values["conversion_rate"] = ratio(values["converted_sessions"], values["sessions"])
    values["aov_usd"] = ratio(values["sales_usd"], values["orders"])
    values["revenue_per_session_usd"] = ratio(
        values["session_sales_usd"], values["sessions"]
    )
    values["product_margin_before_refunds_usd"] = values["sales_usd"] - values["cogs_usd"]
    return values


def prepare_months(raw_rows, context):
    first_common_month = max(
        month_floor(context["session_first_at"]),
        month_floor(context["order_first_at"]),
    )
    last_common_month = min(
        month_floor(context["session_last_at"]),
        month_floor(context["order_last_at"]),
    )
    rows = []
    for raw in sorted(raw_rows, key=lambda row: as_date(row["month_start"])):
        row = {"month_start": month_floor(raw["month_start"])}
        row.update({key: int(raw[key]) for key in COUNTS})
        row.update({key: Decimal(str(raw[key])) for key in AMOUNTS})
        row["period_status"] = (
            "comparable_month"
            if first_common_month < row["month_start"] < last_common_month
            else "boundary_month"
        )
        add_metrics(row)
        for field in FIELDS:
            if field.endswith("_mom") or field == "conversion_rate_change_pp":
                row[field] = None
        previous = rows[-1] if rows else None
        comparable_pair = (
            previous is not None
            and previous["period_status"] == row["period_status"] == "comparable_month"
            and next_month(previous["month_start"]) == row["month_start"]
        )
        if comparable_pair:
            for metric, growth in (
                ("sessions", "sessions_mom"), ("orders", "orders_mom"),
                ("sales_usd", "sales_mom"), ("aov_usd", "aov_mom"),
                ("revenue_per_session_usd", "revenue_per_session_mom"),
            ):
                current, old = row[metric], previous[metric]
                if current is not None and old:
                    row[growth] = ratio(current - old, old)
            if row["conversion_rate"] is not None and previous["conversion_rate"] is not None:
                row["conversion_rate_change_pp"] = (
                    row["conversion_rate"] - previous["conversion_rate"]
                ) * 100
        rows.append(row)
    return rows


def summarize(rows):
    totals = {key: sum(row[key] for row in rows) for key in COUNTS}
    totals.update({key: sum((row[key] for row in rows), Decimal(0)) for key in AMOUNTS})
    return add_metrics(totals)


def validate_results(rows, context, quality):
    checks = []

    def require(name, condition):
        checks.append({"check": name, "passed": bool(condition)})

    require("nonempty_source", context["sessions"] > 0 and context["orders"] > 0)
    profiles = {item["table"]: item for item in quality["table_profiles"]}
    for table, count_key, first_key, last_key in (
        ("website_sessions", "sessions", "session_first_at", "session_last_at"),
        ("orders", "orders", "order_first_at", "order_last_at"),
    ):
        profile = profiles[table]
        require(f"{table}.quality_count", int(context[count_key]) == profile["row_count"])
        require(f"{table}.quality_first", str(context[first_key]) == profile["first_at"])
        require(f"{table}.quality_last", str(context[last_key]) == profile["last_at"])
    expected_months = []
    cursor = min(month_floor(context["session_first_at"]), month_floor(context["order_first_at"]))
    last = max(month_floor(context["session_last_at"]), month_floor(context["order_last_at"]))
    while cursor <= last:
        expected_months.append(cursor)
        cursor = next_month(cursor)
    require("unique_contiguous_calendar", [row["month_start"] for row in rows] == expected_months)
    totals = summarize(rows)
    for key in (*COUNTS, "sales_usd", "cogs_usd"):
        expected = context["orders"] if key == "session_orders" else context[key]
        require(f"total.{key}", totals[key] == expected)
    require("total.session_sales", totals["session_sales_usd"] == context["sales_usd"])
    for row in rows:
        month = row["month_start"].isoformat()
        require(f"{month}.conversion_bounds", 0 <= row["converted_sessions"] <= row["sessions"])
        require(f"{month}.orders_per_converted_session", row["session_orders"] >= row["converted_sessions"])
        require(f"{month}.nonnegative", all(row[key] >= 0 for key in (*COUNTS, *AMOUNTS)))
    failures = [check["check"] for check in checks if not check["passed"]]
    if failures:
        raise ValueError("分析结果核对失败：" + ", ".join(failures))
    return checks


def json_value(value):
    if isinstance(value, Decimal):
        return format(value.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP), "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def csv_text(rows):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=FIELDS)
    writer.writeheader()
    for row in rows:
        writer.writerow({
            key: json_value(value) if isinstance(value, (Decimal, date)) else value
            for key, value in row.items()
        })
    return buffer.getvalue()


def append_process_log(status, message):
    path = ROOT / "docs" / "project_process.md"
    path.parent.mkdir(exist_ok=True)
    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            f"\n\n### {timestamp}：月度经营分析运行记录\n\n"
            f"- 状态：{status}。\n- 执行内容：{message}\n"
            "- 命令：`env/python/bin/python src/monthly_overview.py`。\n"
            "- 问题与验证：以本次终端输出及 output/monthly_overview_report.json 为准。\n"
        )


def run(args):
    if sys.prefix == sys.base_prefix or not Path(sys.prefix).resolve().is_relative_to(ROOT / "env"):
        raise RuntimeError("请使用本项目的 env/python/bin/python 运行。")
    quality_path = ROOT / "output" / "data_quality_report.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    if quality["summary"]["overall"] != "PASS":
        raise ValueError("数据质量报告未通过，请先处理或复核质量检查结果。")
    query_path = ROOT / "src" / "monthly_overview.sql"
    sql = query_path.read_text(encoding="utf-8")
    url = URL.create(
        "mysql+pymysql", username=args.user, password=getpass("请输入 MySQL 密码："),
        host=args.host, port=args.port, database=args.database,
        query={"charset": "utf8mb4"},
    )
    engine = create_engine(url, hide_parameters=True, isolation_level="REPEATABLE READ")
    try:
        with engine.connect() as connection:
            print("正在读取经营基准与月度汇总……", flush=True)
            context = dict(connection.execute(text(CONTEXT_SQL)).mappings().one())
            if not context["sessions"] or not context["orders"]:
                raise ValueError("访问表或订单表为空，无法分析。")
            raw_rows = [dict(row) for row in connection.execute(text(sql)).mappings()]
    finally:
        engine.dispose()
    rows = prepare_months(raw_rows, context)
    checks = validate_results(rows, context, quality)
    comparable = [row for row in rows if row["period_status"] == "comparable_month"]
    report = {
        "status": "PASS",
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "quality_checked_at": quality["checked_at"],
        "sql_sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        "source_context": context,
        "validation": {"passed": len(checks), "failed": 0, "checks": checks},
        "period": {
            "all_months": len(rows), "comparable_months": len(comparable),
            "first_comparable_month": comparable[0]["month_start"] if comparable else None,
            "last_comparable_month": comparable[-1]["month_start"] if comparable else None,
            "boundary_months": [row["month_start"] for row in rows if row["period_status"] != "comparable_month"],
        },
        "all_period_metrics": summarize(rows),
        "comparable_period_metrics": summarize(comparable),
        "highest_sales_comparable_month": max(comparable, key=lambda row: row["sales_usd"]) if comparable else None,
        "monthly_rows": rows,
    }
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    # Write only after source totals, calendar and metric bounds have passed.
    (output / "monthly_overview.csv").write_text(csv_text(rows), encoding="utf-8-sig")
    (output / "monthly_overview_comparable.csv").write_text(csv_text(comparable), encoding="utf-8-sig")
    (output / "monthly_overview_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=json_value) + "\n", encoding="utf-8"
    )
    append_process_log(
        "成功", f"生成 {len(rows)} 个月度记录，其中 {len(comparable)} 个可比月份；"
        f"{len(checks)} 项分析结果核对通过。输出月度 CSV、可比月份 CSV 和 JSON 报告。",
    )
    print(json.dumps({
        "status": report["status"], "period": report["period"],
        "validation_passed": len(checks),
        "all_period_metrics": report["all_period_metrics"],
        "comparable_period_metrics": report["comparable_period_metrics"],
    }, ensure_ascii=False, indent=2, default=json_value))
    print("已生成月度结果，并追加项目过程记录。")


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
        (output / "monthly_overview_report.json").write_text(json.dumps({
            "status": "FAIL", "error_type": type(error).__name__,
            "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
            "note": "本次运行未完成。已有 CSV 可能来自较早运行，请先处理终端报错。",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        append_process_log("失败", f"错误类型为 {type(error).__name__}；具体原因见终端，本次结果未通过完整生成流程。")
        print(f"运行失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
