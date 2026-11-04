"""Validate the portfolio files offline; never connect to MySQL or save Excel."""

import argparse
import ast
import csv
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from fnmatch import fnmatch
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_json(name):
    return json.loads((OUTPUT / name).read_bytes())


def write_json(name, value):
    (OUTPUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def csv_matches(name, expected):
    with (OUTPUT / name).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    require(len(rows) == len(expected), f"CSV row count differs: {name}")
    allowed = set().union(*(reference.keys() for reference in expected))
    for index, (row, reference) in enumerate(zip(rows, expected), 1):
        for key, actual in row.items():
            require(key in allowed, f"Missing reference field: {name}:{key}")
            value = reference.get(key)
            if value is None:
                matched = actual == ""
            elif str(value) == actual:
                matched = True
            elif isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?", value):
                matched = actual == value.replace("T", " ", 1)
            else:
                try:
                    matched = abs(Decimal(actual) - Decimal(str(value))) <= Decimal("0.00000001")
                except (InvalidOperation, ValueError):
                    matched = False
            require(matched, f"CSV differs: {name} row {index} {key}")
    return {"path": "output/" + name, "rows": len(rows), "all_fields_match": True}


def workbook_check(reports):
    path = OUTPUT / "ecommerce_dashboard.xlsx"
    original_hash = sha(path)
    with zipfile.ZipFile(path) as archive:
        require(archive.testzip() is None, "Workbook ZIP integrity failed.")
        charts = sum(name.startswith("xl/charts/chart") and name.endswith(".xml") for name in archive.namelist())
    book = load_workbook(path, data_only=True, read_only=True)
    formulas = load_workbook(path, data_only=False, read_only=True)
    expected_sheets = ["经营总览", "渠道设备", "购买漏斗", "商品退款", "月度数据", "渠道数据", "漏斗数据", "商品数据"]
    require(book.sheetnames == expected_sheets and charts == 6, "Workbook sheets or charts differ.")
    start = book["经营总览"]["B4"].value.date().isoformat()
    end = book["经营总览"]["E4"].value.date().isoformat()
    rows = [row for row in reports["monthly"]["monthly_rows"] if start <= row["month_start"] <= end and row["period_status"] == "comparable_month"]
    require(rows, "Saved selection contains no comparable months.")
    total = lambda key: sum((Decimal(str(row[key])) for row in rows), Decimal(0))
    expected = {
        "经营总览!B7": total("sessions"), "经营总览!B8": total("converted_sessions"),
        "经营总览!B9": total("orders"), "经营总览!B10": total("sales_usd"),
        "经营总览!B11": total("converted_sessions") / total("sessions"),
        "经营总览!B12": total("sales_usd") / total("orders"),
        "经营总览!B13": total("session_sales_usd") / total("sessions"),
    }
    products = [row for row in reports["product"]["monthly_products"] if start <= row["month_start"] <= end]
    expected["经营总览!B14"] = sum(row["items"] for row in products) / total("orders")
    periods = {
        ("2014-01-01", "2014-12-01"): "year_2014", ("2013-01-01", "2013-12-01"): "year_2013",
        ("2014-12-01", "2014-12-01"): "dec_2014", ("2014-11-01", "2014-11-01"): "nov_2014",
        ("2012-04-01", "2015-02-01"): "all_comparable", ("2014-01-01", "2014-02-01"): "jan_feb_2014",
        ("2015-01-01", "2015-02-01"): "jan_feb_2015",
    }
    period = periods.get((start, end))
    if period:
        basket = next(row for row in reports["product"]["basket_summaries"] if row["period"] == period and row["grouping"] == "overall")
        expected["经营总览!B15"] = Decimal(basket["multi_product_orders"]) / basket["orders"]
        funnel = next(row for row in reports["funnel"]["group_summaries"] if row["period"] == period and row["grouping"] == "overall")
        expected["购买漏斗!B15"] = funnel["ordered_thank_you"]
    for row_number, product in enumerate(reports["product"]["catalog"], 8):
        selected = [row for row in products if row["product_id"] == product["product_id"]]
        eligible = sum(row["window_eligible_items"] for row in selected)
        refunded = sum(row["window_refunded_items"] for row in selected)
        expected[f"商品退款!B{row_number}"] = sum((Decimal(row["sales_usd"]) for row in selected), Decimal(0))
        expected[f"商品退款!G{row_number}"] = eligible
        expected[f"商品退款!I{row_number}"] = Decimal(refunded) / eligible if eligible else "n.a."
    with (OUTPUT / "channel_monthly.csv").open(encoding="utf-8-sig", newline="") as handle:
        channels = list(csv.DictReader(handle))
    for row_number, device in enumerate(("desktop", "mobile"), 30):
        selected = [row for row in channels if start <= row["month_start"] <= end and row["device_type"] == device]
        expected[f"渠道设备!D{row_number}"] = sum(Decimal(row["converted_sessions"]) for row in selected) / sum(Decimal(row["sessions"]) for row in selected)
    checks = []
    for address, value in expected.items():
        sheet, coordinate = address.split("!")
        actual = book[sheet][coordinate].value
        if isinstance(value, str):
            matched = value == actual
        else:
            matched = isinstance(actual, (int, float)) and abs(Decimal(str(actual)) - Decimal(str(value))) < Decimal("0.000001")
        checks.append({"cell": address, "cached_value": actual, "expected": value, "matched": matched})
    errors = []
    for workbook in (book, formulas):
        for sheet in workbook:
            for row in sheet:
                for cell in row:
                    if cell.data_type == "e" or cell.data_type == "f" and "#REF!" in str(cell.value):
                        errors.append(f"{sheet.title}!{cell.coordinate}")
    book.close()
    formulas.close()
    require(all(row["matched"] for row in checks), "Saved workbook metric differs from analysis outputs.")
    require(not errors, "Workbook contains saved formula errors or invalid references.")
    require(sha(path) == original_hash, "Workbook changed during read-only inspection.")
    result = {
        "status": "PASS", "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "workbook": "output/ecommerce_dashboard.xlsx", "sha256": original_hash,
        "selected_period": {"start": start, "end": end}, "sheet_names": expected_sheets, "chart_count": charts,
        "cached_metric_checks": checks, "saved_errors": errors, "file_unchanged": True,
        "limits": ["只核对已保存期间的计算缓存，没有执行 Excel 公式重算或全月份交互测试。", "窗口退款依赖已观测数据，不证明完整采集。"],
    }
    write_json("excel_validation_report.json", result)
    return {"sheets": len(expected_sheets), "charts": charts, "cached_checks": len(checks), "matched": len(checks), "sha256": original_hash}


def check_documents():
    deferred = {"assets/raw_data/", "output/channel_audit.csv", "output/funnel_audit.csv", "output/product_item_audit.csv", "output/refund_events_audit.csv"}
    paths = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")), ROOT / "assets/data_source.md", ROOT / "env/environment_notes.md"]
    count = 0
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        require(not re.search(r"助手|用户执行|用户已|用户版本|用户调整|AI编程|自动版|展示版|reviewed\.xlsx", text), f"Stale actor/version prose: {path.name}")
        require(not re.search(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]|/home/|/mnt/[a-z]/|\\\\wsl", text), f"Local absolute path: {path.name}")
        for target in re.findall(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", text):
            if "://" in target or target.startswith("#"):
                continue
            actual = (path.parent / target.split("#", 1)[0]).resolve()
            require(actual.is_relative_to(ROOT) and actual.exists(), f"Broken document link: {path.name}: {target}")
            count += 1
        for target in re.findall(r"`((?:src|docs|output|assets|tests|env)/[^`\s]+)`", text):
            if target in deferred or target.startswith("env/python/"):
                continue
            require((ROOT / target).exists(), f"Missing documented file: {target}")
    return {"documents": len(paths), "relative_links_checked": count, "narrative": "consistent"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-project", type=Path, default=ROOT, help="Existing project that owns the isolated Python environment; supports offline cross-platform file inspection.")
    parser.add_argument("--copy-receipt", type=Path, help="Optional migration receipt for checking byte-identical summary outputs.")
    args = parser.parse_args()
    require(sys.prefix != sys.base_prefix and Path(sys.prefix).resolve().is_relative_to(args.runtime_project.resolve() / "env"), "Use the designated project's isolated Python environment.")
    reports = {key: read_json(name) for key, name in {"monthly": "monthly_overview_report.json", "channel": "channel_analysis_report.json", "funnel": "funnel_analysis_report.json", "product": "product_analysis_report.json"}.items()}
    source_checks = []
    for key, report in reports.items():
        require(report["status"] == "PASS" and report["validation"]["failed"] == 0 and all(item["passed"] for item in report["validation"]["checks"]), f"Analysis failed: {key}")
        name = {"monthly": "monthly_overview", "channel": "channel_analysis", "funnel": "funnel_analysis", "product": "product_analysis"}[key] + "_report.json"
        require(read_json(key + "_findings.json")["source_sha256"] == sha(OUTPUT / name), f"Findings source changed: {key}")
        if key != "monthly":
            require(report["monthly_baseline_sha256"] == sha(OUTPUT / "monthly_overview_report.json"), f"Monthly baseline changed: {key}")
        source_checks.append({"module": key, "core_checks_passed": report["validation"]["passed"], "source_fingerprint_match": True})
    require(reports["funnel"]["channel_baseline_sha256"] == sha(OUTPUT / "channel_analysis_report.json"), "Funnel channel baseline changed.")
    require(read_json("data_quality_report.json")["summary"]["overall"] == "PASS", "Quality report failed.")
    require(all(row["rows_match"] for row in read_json("import_summary.json")), "Import counts differ.")
    csv_checks = []
    references = {
        "monthly_overview.csv": reports["monthly"]["monthly_rows"],
        "monthly_overview_comparable.csv": [row for row in reports["monthly"]["monthly_rows"] if row["period_status"] == "comparable_month"],
        "channel_summary.csv": reports["channel"]["period_summaries"], "channel_comparisons.csv": reports["channel"]["comparisons"],
        "channel_decomposition.csv": reports["channel"]["decomposition"], "funnel_groups.csv": reports["funnel"]["group_summaries"],
        "funnel_stages.csv": reports["funnel"]["stage_summaries"], "funnel_comparisons.csv": reports["funnel"]["comparisons"],
        "funnel_page_inventory.csv": reports["funnel"]["page_inventory"], "product_monthly.csv": reports["product"]["monthly_products"],
        "product_summary.csv": reports["product"]["product_summaries"], "product_baskets.csv": reports["product"]["basket_summaries"],
        "product_pairs.csv": reports["product"]["product_pairs"], "product_comparisons.csv": reports["product"]["comparisons"],
    }
    for name, rows in references.items():
        csv_checks.append(csv_matches(name, rows))
    copied = 0
    if args.copy_receipt:
        for receipt in json.loads(args.copy_receipt.read_text(encoding="utf-8-sig")):
            path = ROOT / receipt["path"]
            if path.is_file() and receipt["path"].startswith("output/"):
                require(sha(path) == receipt["source_sha256"], f"Migration bytes differ: {receipt['path']}")
                copied += 1
    for path in [*(ROOT / "src").glob("*.py"), *(ROOT / "tests").glob("*.py")]:
        ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    test_run = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT, capture_output=True, text=True)
    require(test_run.returncode == 0, test_run.stdout + test_run.stderr)
    match = re.search(r"Ran (\d+) tests?", test_run.stderr)
    require(match and int(match.group(1)) == 39, "Unexpected offline test coverage.")
    excel = workbook_check(reports)
    # These records are created below and are valid link targets during this check.
    for name in ("project_validation.json", "migration_manifest.json"):
        if not (OUTPUT / name).exists():
            write_json(name, {"status": "VALIDATING"})
    docs = check_documents()
    entries = []
    # Inspect deliverables, excluding Git metadata and locally generated inputs/runtime files.
    excluded_dirs = {".git", "__pycache__", ".pytest_cache", ".vscode", ".idea", "node_modules", "work", "tmp"}
    excluded_paths = {"output/channel_audit.csv", "output/funnel_audit.csv", "output/product_item_audit.csv", "output/refund_events_audit.csv", "output/environment_check.json"}
    allowed_env = {"env/requirements.txt", "env/requirements_lock.txt", "env/environment_notes.md"}
    import os
    files = []
    for directory, folders, names in os.walk(ROOT):
        folders[:] = [name for name in folders if name not in excluded_dirs]
        for name in names:
            path = Path(directory) / name
            relative = path.relative_to(ROOT).as_posix()
            if relative.startswith("assets/raw_data/") or relative.startswith("env/") and relative not in allowed_env:
                continue
            if relative in excluded_paths or any(fnmatch(name, pattern) for pattern in (".env", ".env.*", "*.pem", "*.key", "*.zip", "*.pyc", "*.tmp", "~$*")):
                continue
            files.append(path)
    for path in sorted(files):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix()
        require(path.name in {"README.md", ".gitignore", ".gitattributes"} or re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", path.name), f"Invalid project filename: {relative}")
        require(path.stat().st_size < 50 * 1024 * 1024, f"Large repository file: {relative}")
        if relative not in {"output/migration_manifest.json", "output/project_validation.json"}:
            entries.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha(path)})
    write_json("migration_manifest.json", {"status": "PASS", "scope": "GitHub portfolio repository", "files": entries, "excludes_self_and_validation_record": True, "raw_data": "Download according to assets/data_source.md; detailed audits are generated locally.", "windows_runtime": "not_configured"})
    validation = {"status": "PASS", "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "runtime": "existing_project_isolated_environment", "windows_native_execution": "not_tested", "database_queries": "not_performed", "offline_tests": {"total": 39, "passed": 39, "failed": 0}, "analysis_sources": source_checks, "csv_checks": csv_checks, "migration_summary_outputs_byte_matched": copied, "excel": excel, "documents": docs, "manifest": "output/migration_manifest.json", "manifest_sha256": sha(OUTPUT / "migration_manifest.json"), "tracked_file_count": len(entries) + 2, "limits": ["原始数据、审计明细和运行环境不提交仓库，完整复核需下载并重算。", "Excel 检查覆盖已保存期间，未执行动态交互重算。"]}
    write_json("project_validation.json", validation)
    print(json.dumps({"status": "PASS", "files": len(entries) + 2, "tests_passed": 39, "csv_files_checked": len(csv_checks), "excel_cached_checks": excel["cached_checks"], "windows_runtime": "not_configured"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
