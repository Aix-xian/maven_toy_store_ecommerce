"""Review existing monthly JSON/CSV outputs and write findings without MySQL."""

import calendar
import csv
import hashlib
import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
METRICS = (
    "sessions", "converted_sessions", "orders", "sales_usd", "session_sales_usd"
)


def aggregate(rows):
    values = {
        key: sum((Decimal(str(row[key])) for row in rows), Decimal(0))
        for key in METRICS
    }
    values["conversion_rate"] = values["converted_sessions"] / values["sessions"]
    values["aov_usd"] = values["sales_usd"] / values["orders"]
    values["revenue_per_session_usd"] = values["session_sales_usd"] / values["sessions"]
    return values


def compare(old, new):
    result = {
        key: (new[key] / old[key] - 1) * 100
        for key in ("sessions", "orders", "sales_usd", "aov_usd", "revenue_per_session_usd")
    }
    result["conversion_change_pp"] = (
        new["conversion_rate"] - old["conversion_rate"]
    ) * 100
    return result


def number(value):
    return f"{value:,.2f}"


def percentage(value):
    return f"{value * 100:.2f}%"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    require(
        sys.prefix != sys.base_prefix
        and Path(sys.prefix).resolve().is_relative_to(ROOT / "env"),
        "请使用本项目 env/python/bin/python 运行。",
    )
    source = ROOT / "output" / "monthly_overview_report.json"
    source_bytes = source.read_bytes()
    report = json.loads(source_bytes)
    require(report["status"] == "PASS", "月度源报告未通过。")
    checks = report["validation"]["checks"]
    require(all(item["passed"] for item in checks), "源报告包含未通过核对。")
    require(len(checks) == report["validation"]["passed"], "源报告核对数量不一致。")
    rows = report["monthly_rows"]
    by_month = {row["month_start"][:7]: row for row in rows}
    require(len(by_month) == len(rows), "月份重复。")
    comparable = [row for row in rows if row["period_status"] == "comparable_month"]
    csv_checks = []
    for name, expected in (
        ("monthly_overview.csv", rows),
        ("monthly_overview_comparable.csv", comparable),
    ):
        with (ROOT / "output" / name).open(encoding="utf-8-sig", newline="") as handle:
            actual = list(csv.DictReader(handle))
        require(len(actual) == len(expected), f"{name} 行数与 JSON 不一致。")
        for csv_row, json_row in zip(actual, expected):
            require(set(csv_row) == set(json_row), f"{name} 字段与 JSON 不一致。")
            for key, value in json_row.items():
                if value is None:
                    same = csv_row[key] == ""
                elif key in {"month_start", "period_status"}:
                    same = csv_row[key] == value
                else:
                    same = Decimal(csv_row[key]) == Decimal(str(value))
                require(same, f"{name} 的 {json_row['month_start']} / {key} 不一致。")
        csv_checks.append({"file": name, "rows": len(actual), "all_fields_match": True})

    annual = {}
    early = {}
    for year in ("2013", "2014"):
        year_rows = [row for row in comparable if row["month_start"].startswith(year)]
        require(len(year_rows) == 12, f"{year} 不是十二个可比月份。")
        annual[year] = aggregate(year_rows)
    for year in ("2014", "2015"):
        early_rows = [by_month[f"{year}-{month:02}"] for month in (1, 2)]
        require(all(row["period_status"] == "comparable_month" for row in early_rows), "同期范围包含边界月份。")
        early[year] = aggregate(early_rows)
    annual_change = compare(annual["2013"], annual["2014"])
    early_change = compare(early["2014"], early["2015"])
    daily = {}
    for month in ("2015-01", "2015-02"):
        year, month_number = map(int, month.split("-"))
        days = calendar.monthrange(year, month_number)[1]
        daily[month] = {
            "calendar_days": days,
            "sales_per_day_usd": Decimal(by_month[month]["sales_usd"]) / days,
        }
    daily_change = (
        daily["2015-02"]["sales_per_day_usd"] / daily["2015-01"]["sales_per_day_usd"] - 1
    ) * 100
    peak = max(comparable, key=lambda row: Decimal(row["sales_usd"]))
    require(peak["month_start"] == report["highest_sales_comparable_month"]["month_start"], "销售额峰值月份不一致。")
    first = aggregate(comparable)
    findings = {
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "source": "output/monthly_overview_report.json",
        "source_generated_at": report["generated_at"],
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_validation_passed": len(checks),
        "csv_checks": csv_checks,
        "annual": annual,
        "annual_change_pct": annual_change,
        "jan_feb": early,
        "jan_feb_change_pct": early_change,
        "daily_sales": daily,
        "feb_2015_daily_sales_mom_pct": daily_change,
        "highest_sales_comparable_month": peak,
    }
    (ROOT / "output" / "monthly_findings.json").write_text(
        json.dumps(findings, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    print(json.dumps({
        "source_validation_passed": len(checks), "csv_checks": csv_checks,
        "annual_sales_growth_pct": str(annual_change["sales_usd"]),
        "feb_daily_sales_growth_pct": str(daily_change),
        "written": ["output/monthly_findings.json"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
