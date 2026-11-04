"""Review generated funnel files and write findings without querying MySQL."""

import csv
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

from channel_analysis import require
from funnel_analysis import build_comparisons, build_summaries, prepare_rows
from monthly_overview import json_value
from review_channel_results import verify_csv


ROOT = Path(__file__).resolve().parents[1]
STAGE_NAMES = {
    "ordered_products": "首次页面 → 商品列表",
    "ordered_detail": "商品列表 → 商品详情",
    "ordered_cart": "商品详情 → 购物车",
    "ordered_shipping": "购物车 → 配送",
    "ordered_billing": "配送 → 支付",
    "ordered_thank_you": "支付 → 确认页",
}


def main():
    require(sys.prefix != sys.base_prefix and Path(sys.prefix).resolve().is_relative_to(ROOT / "env"), "使用本项目 Python 环境。")
    path = ROOT / "output" / "funnel_analysis_report.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    require(report["status"] == "PASS" and all(row["passed"] for row in report["validation"]["checks"]), "漏斗报告未通过。")
    monthly_bytes = (ROOT / "output" / "monthly_overview_report.json").read_bytes()
    channel_bytes = (ROOT / "output" / "channel_analysis_report.json").read_bytes()
    require(hashlib.sha256(monthly_bytes).hexdigest() == report["monthly_baseline_sha256"], "月度基准指纹变化。")
    require(hashlib.sha256(channel_bytes).hexdigest() == report["channel_baseline_sha256"], "渠道基准指纹变化。")
    with (ROOT / "output" / "funnel_audit.csv").open(encoding="utf-8-sig", newline="") as handle:
        audit = list(csv.DictReader(handle))
    rows, _ = prepare_rows(audit, json.loads(monthly_bytes))
    require(len(rows) == report["raw_group_rows"], "审计记录数量不一致。")
    summaries, stages = build_summaries(rows)
    comparisons = build_comparisons(rows)
    for field, expected in (("group_summaries", summaries), ("stage_summaries", stages), ("comparisons", comparisons)):
        require(json.loads(json.dumps(expected, default=json_value)) == report[field], f"{field} 重算不一致。")
    checks = [verify_csv(name, expected) for name, expected in (
        ("funnel_groups.csv", summaries), ("funnel_stages.csv", stages),
        ("funnel_comparisons.csv", comparisons), ("funnel_page_inventory.csv", report["page_inventory"]),
    )]
    devices = {device: {row["stage"]: row for row in stages if row["period"] == "year_2014" and row["grouping"] == "device" and row["device_type"] == device} for device in ("desktop", "mobile")}
    gaps = []
    for stage, name in STAGE_NAMES.items():
        desktop, mobile = devices["desktop"][stage], devices["mobile"][stage]
        gaps.append({"stage": stage, "label": name, "desktop_rate": desktop["step_rate"], "mobile_rate": mobile["step_rate"], "mobile_minus_desktop_pp": (mobile["step_rate"] - desktop["step_rate"]) * 100})
    december = [row for row in comparisons if row["comparison"] == "dec_vs_nov_2014" and row["grouping"] == "overall" and row["to_stage"] in STAGE_NAMES]
    findings = {
        "source_generated_at": report["generated_at"], "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "validation_passed": report["validation"]["passed"], "audit_rows": len(rows),
        "csv_checks": checks, "review_notes": report["review_notes"],
        "year_2014_device_stage_gaps": gaps, "december_stage_changes": december,
    }
    (ROOT / "output" / "funnel_findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2, default=json_value) + "\n", encoding="utf-8")
    print(json.dumps({"validation_passed": findings["validation_passed"], "audit_rows": len(rows), "csv_checks": checks, "written": ["output/funnel_findings.json"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
