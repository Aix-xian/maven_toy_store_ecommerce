"""Check an isolated runtime and locally downloaded CSV inputs without login."""
import csv
import hashlib
import importlib
import importlib.metadata
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLES = {"orders", "order_items", "order_item_refunds", "products", "website_pageviews", "website_sessions"}

def main():
    if sys.prefix == sys.base_prefix or not Path(sys.prefix).resolve().is_relative_to(ROOT / "env"):
        raise RuntimeError("Use this project's isolated Python environment in env/.")
    versions = {}
    for distribution, module in {"pandas": "pandas", "SQLAlchemy": "sqlalchemy", "PyMySQL": "pymysql", "openpyxl": "openpyxl", "cryptography": "cryptography"}.items():
        importlib.import_module(module)
        versions[distribution] = importlib.metadata.version(distribution)
    dictionary_path = ROOT / "assets/data_dictionary.csv"
    with dictionary_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        dictionary = list(reader)
        if reader.fieldnames != ["Table", "Field", "Description"] or {row["Table"] for row in dictionary} != TABLES:
            raise ValueError("Invalid data dictionary.")
    inputs = []
    for table in sorted(TABLES):
        path = ROOT / "assets/raw_data" / f"{table}.csv"
        if not path.is_file():
            raise FileNotFoundError(f"Download the dataset first: assets/raw_data/{table}.csv; see assets/data_source.md.")
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        inputs.append({"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": digest})
    report = {"status": "PASS", "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "python": platform.python_version(), "architecture": platform.machine(), "project_environment": True, "packages": versions, "dictionary_fields": len(dictionary), "inputs": inputs, "database_login": "not_performed"}
    (ROOT / "output").mkdir(exist_ok=True)
    (ROOT / "output/environment_check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
