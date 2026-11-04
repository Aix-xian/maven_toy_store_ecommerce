"""Verify existing channel outputs and write findings without a database login."""

import csv
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

from channel_analysis import (
    aggregate, compare_periods, json_value, metrics, prepare_rows,
    require, summarize_periods,
)


ROOT = Path(__file__).resolve().parents[1]


def verify_csv(name, expected):
    with (ROOT / "output" / name).open(encoding="utf-8-sig", newline="") as handle:
        actual = list(csv.DictReader(handle))
    require(len(actual) == len(expected), f"{name} 行数不一致。")
    for raw, reference in zip(actual, expected):
        for key in raw:
            value = reference.get(key)
            serialized = "" if value is None else json_value(value) if isinstance(value, Decimal) else str(value)
            require(raw[key] == serialized, f"{name} 的 {key} 不一致。")
    return {"file": name, "rows": len(actual), "matched": True}


def main():
    require(sys.prefix != sys.base_prefix and Path(sys.prefix).resolve().is_relative_to(ROOT / "env"), "使用项目 env/python/bin/python。")
    source = ROOT / "output" / "channel_analysis_report.json"
    report = json.loads(source.read_text(encoding="utf-8"))
    require(report["status"] == "PASS", "渠道源报告未通过。")
    require(all(item["passed"] for item in report["validation"]["checks"]), "渠道对账有失败。")
    monthly_bytes = (ROOT / "output" / "monthly_overview_report.json").read_bytes()
    require(hashlib.sha256(monthly_bytes).hexdigest() == report["monthly_baseline_sha256"], "月度基准指纹发生变化。")
    baseline = json.loads(monthly_bytes)
    with (ROOT / "output" / "channel_audit.csv").open(encoding="utf-8-sig", newline="") as handle:
        audit = list(csv.DictReader(handle))
    rows, _ = prepare_rows(audit, baseline)
    require(len(rows) == report["raw_profile_rows"], "审计记录数量不一致。")
    for actual, expected in zip(audit, rows):
        require(actual["channel"] == expected["channel"] and actual["classification_rule"] == expected["classification_rule"], "渠道分类记录与规则不一致。")
    summaries = summarize_periods(rows)
    comparisons, decomposition = compare_periods(rows)
    for key, expected in (("period_summaries", summaries), ("comparisons", comparisons), ("decomposition", decomposition)):
        require(json.loads(json.dumps(expected, default=json_value)) == report[key], f"{key} 与审计记录重算不一致。")
    month_totals = aggregate(rows, ("month_start",))
    monthly = []
    for key, values in sorted(aggregate(rows, ("month_start", "period_status", "channel", "device_type")).items()):
        month, status, channel, device = key
        monthly.append({
            "month_start": month, "period_status": status, "channel": channel,
            "device_type": device, **metrics(values),
            "session_share": Decimal(values["sessions"]) / month_totals[(month,)]["sessions"],
            "low_volume_review": values["sessions"] < report["low_volume_threshold_sessions"],
        })
    csv_checks = [verify_csv(name, expected) for name, expected in (
        ("channel_monthly.csv", monthly), ("channel_summary.csv", summaries),
        ("channel_comparisons.csv", comparisons), ("channel_decomposition.csv", decomposition),
    )]
    top = sorted(
        (item for item in comparisons if item["comparison"] == "2014_vs_2013" and item["grouping"] == "channel"),
        key=lambda item: item["session_sales_delta_usd"], reverse=True,
    )[:4]
    devices = [item for item in summaries if item["period"] == "year_2014" and item["grouping"] == "device"]
    dec = next(item for item in decomposition if item["comparison"] == "dec_vs_nov_2014" and item["grouping"] == "channel_device" and item["metric"] == "revenue_per_session_usd" and item["presence"] == "total")
    dec_groups = sorted(
        (item for item in decomposition if item["comparison"] == "dec_vs_nov_2014" and item["grouping"] == "channel_device" and item["metric"] == "revenue_per_session_usd" and item["presence"] != "total"),
        key=lambda item: item["within_effect"],
    )[:3]
    early = next(item for item in decomposition if item["comparison"] == "jan_feb_2015_vs_2014" and item["grouping"] == "channel_device" and item["metric"] == "conversion_rate" and item["presence"] == "total")
    findings = {
        "source_generated_at": report["generated_at"],
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_validation_passed": report["validation"]["passed"],
        "audit_reclassified": len(rows), "csv_checks": csv_checks,
        "annual_top_revenue_contributors": top, "year_2014_devices": devices,
        "december_rps_decomposition": dec, "december_largest_within_declines": dec_groups,
        "early_2015_conversion_decomposition": early,
    }
    (ROOT / "output" / "channel_findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2, default=json_value) + "\n", encoding="utf-8")
    print(json.dumps({"source_checks": report["validation"]["passed"], "audit_reclassified": len(rows), "csv_checks": csv_checks, "written": ["output/channel_findings.json"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
