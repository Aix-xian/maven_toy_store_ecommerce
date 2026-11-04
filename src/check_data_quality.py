import csv
import json
from collections import Counter
from datetime import datetime
from getpass import getpass
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import URL, create_engine, text


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "assets" / "raw_data"

TABLES = [
    "products",
    "website_sessions",
    "orders",
    "order_items",
    "order_item_refunds",
    "website_pageviews",
]

# 子表、关联字段、父表、父表主键
FOREIGN_KEYS = [
    ("website_pageviews", "website_session_id",
     "website_sessions", "website_session_id"),
    ("orders", "website_session_id",
     "website_sessions", "website_session_id"),
    ("orders", "primary_product_id", "products", "product_id"),
    ("order_items", "order_id", "orders", "order_id"),
    ("order_items", "product_id", "products", "product_id"),
    ("order_item_refunds", "order_item_id",
     "order_items", "order_item_id"),
    ("order_item_refunds", "order_id", "orders", "order_id"),
]

report = {
    "checked_at": datetime.now(
        ZoneInfo("Asia/Shanghai")
    ).isoformat(timespec="seconds"),
    "table_profiles": [],
    "checks": [],
    "context": {},
}


def record(name, violations, level="FAIL", details=None):
    violations = int(violations or 0)
    result = {
        "check": name,
        "status": "PASS" if violations == 0 else level,
        "violations": violations,
    }
    if details is not None:
        result["details"] = details
    report["checks"].append(result)


def check_sql(connection, name, sql, level="FAIL"):
    violations = connection.execute(text(sql)).scalar_one()
    record(name, violations, level)


def save_report():
    counts = Counter(item["status"] for item in report["checks"])
    overall = (
        "FAIL" if counts["FAIL"]
        else "REVIEW" if counts["REVIEW"]
        else "PASS"
    )

    report["summary"] = {
        "overall": overall,
        "checks_total": len(report["checks"]),
        "passed": counts["PASS"],
        "failed": counts["FAIL"],
        "review": counts["REVIEW"],
    }

    output = ROOT / "output"
    output.mkdir(exist_ok=True)

    (output / "data_quality_report.json").write_text(
        json.dumps(
            report, ensure_ascii=False, indent=2, default=str
        ) + "\n",
        encoding="utf-8",
    )

    # 终端只展示汇总、概况和异常，完整结果保存在文件中。
    brief = {
        "summary": report["summary"],
        "table_profiles": report["table_profiles"],
        "issues": [
            item for item in report["checks"]
            if item["status"] != "PASS"
        ],
        "context": report["context"],
    }
    print(json.dumps(
        brief, ensure_ascii=False, indent=2, default=str
    ))


# 从官方数据字典读取预期字段和主键。
with (DATA / "maven_fuzzy_factory_data_dictionary.csv").open(
    encoding="utf-8-sig", newline=""
) as file:
    dictionary = list(csv.DictReader(file))

expected_columns = {
    (row["Table"], row["Field"]) for row in dictionary
}
expected_primary_keys = {
    (row["Table"], row["Field"])
    for row in dictionary
    if "(PK)" in row["Description"]
}

# 使用之前导入脚本生成的行数基准。
baseline_path = ROOT / "output" / "import_summary.json"
baseline = {}
if baseline_path.exists():
    baseline = {
        row["table"]: int(row["csv_rows"])
        for row in json.loads(
            baseline_path.read_text(encoding="utf-8")
        )
    }

url = URL.create(
    "mysql+pymysql",
    username="toy_analyst",
    password=getpass("请输入 MySQL 密码："),
    host="127.0.0.1",
    port=3306,
    database="toy_store_ecommerce",
    query={"charset": "utf8mb4"},
)

engine = create_engine(url, hide_parameters=True)

with engine.connect() as connection:
    print("正在检查表结构……", flush=True)

    metadata = connection.execute(text("""
        SELECT
            TABLE_NAME AS table_name,
            COLUMN_NAME AS column_name,
            DATA_TYPE AS data_type,
            COLUMN_TYPE AS column_type,
            IS_NULLABLE AS is_nullable,
            COLUMN_KEY AS column_key,
            NUMERIC_PRECISION AS numeric_precision,
            NUMERIC_SCALE AS numeric_scale
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
    """)).mappings().all()

    actual = {
        (row["table_name"], row["column_name"]): dict(row)
        for row in metadata
        if row["table_name"] in TABLES
    }

    missing = expected_columns - set(actual)
    extra = set(actual) - expected_columns
    schema_issues = [
        f"缺少字段：{table}.{column}"
        for table, column in sorted(missing)
    ] + [
        f"额外字段：{table}.{column}"
        for table, column in sorted(extra)
    ]

    nullable_fields = {
        "utm_source", "utm_campaign",
        "utm_content", "http_referer",
    }
    flag_fields = {"is_repeat_session", "is_primary_item"}

    for table, column in sorted(expected_columns & set(actual)):
        info = actual[(table, column)]

        if column == "created_at":
            expected_type = "datetime"
        elif column.endswith("_usd"):
            expected_type = "decimal"
        elif column in flag_fields:
            expected_type = "tinyint"
        elif column == "items_purchased":
            expected_type = "smallint"
        elif column.endswith("_id"):
            expected_type = "int"
        else:
            expected_type = "varchar"

        if info["data_type"] != expected_type:
            schema_issues.append(
                f"{table}.{column}：类型为 {info['data_type']}，"
                f"预期 {expected_type}"
            )

        if expected_type in {"int", "smallint", "tinyint"}:
            if "unsigned" not in info["column_type"].lower():
                schema_issues.append(
                    f"{table}.{column}：预期 UNSIGNED"
                )

        if expected_type == "decimal":
            if (
                info["numeric_precision"] != 12
                or info["numeric_scale"] != 2
            ):
                schema_issues.append(
                    f"{table}.{column}：预期 DECIMAL(12, 2)"
                )

        expected_nullable = (
            "YES" if column in nullable_fields else "NO"
        )
        if info["is_nullable"] != expected_nullable:
            schema_issues.append(
                f"{table}.{column}：空值约束与设计不一致"
            )

    actual_primary_keys = {
        key for key, info in actual.items()
        if info["column_key"] == "PRI"
    }
    if actual_primary_keys != expected_primary_keys:
        schema_issues.append("主键定义与数据字典不一致")

    record("schema", len(schema_issues), details=schema_issues)

    # 缺少字段时，后续查询无法完整执行。
    if missing:
        save_report()
        raise SystemExit("存在缺失字段，请先查看质量报告。")

    actual_foreign_keys = {
        tuple(row)
        for row in connection.execute(text("""
            SELECT
                TABLE_NAME,
                COLUMN_NAME,
                REFERENCED_TABLE_NAME,
                REFERENCED_COLUMN_NAME
            FROM information_schema.key_column_usage
            WHERE constraint_schema = DATABASE()
              AND referenced_table_schema = DATABASE()
        """))
    }
    missing_foreign_keys = set(FOREIGN_KEYS) - actual_foreign_keys
    record(
        "foreign_key_constraints",
        len(missing_foreign_keys),
        level="REVIEW",
        details=sorted(missing_foreign_keys),
    )

    print("正在检查行数和时间范围……", flush=True)

    if not baseline:
        record(
            "import_baseline_missing",
            1,
            level="REVIEW",
            details="未找到导入行数基准，跳过行数比对。",
        )

    for table in TABLES:
        profile = dict(connection.execute(text(f"""
            SELECT
                COUNT(*) AS row_count,
                MIN(created_at) AS first_at,
                MAX(created_at) AS last_at,
                SUM(
                    created_at IS NULL
                    OR YEAR(created_at) = 0
                ) AS invalid_time_rows
            FROM `{table}`
        """)).mappings().one())

        invalid_times = profile.pop("invalid_time_rows")
        profile["table"] = table
        report["table_profiles"].append(profile)

        record(f"{table}.invalid_time", invalid_times)

        if table in baseline:
            record(
                f"{table}.row_count",
                abs(profile["row_count"] - baseline[table]),
                details={
                    "expected": baseline[table],
                    "actual": profile["row_count"],
                },
            )

    print("正在检查关联关系和业务一致性……", flush=True)

    # 查找无法匹配父表的记录。
    for child, child_key, parent, parent_key in FOREIGN_KEYS:
        check_sql(
            connection,
            f"{child}.{child_key}.orphan",
            f"""
                SELECT COUNT(*)
                FROM `{child}` c
                LEFT JOIN `{parent}` p
                    ON c.`{child_key}` = p.`{parent_key}`
                WHERE p.`{parent_key}` IS NULL
            """,
        )

    # 字段取值检查。
    rules = [
        (
            "blank_product_name",
            "products",
            "product_name IS NULL OR TRIM(product_name) = ''",
            "FAIL",
        ),
        (
            "invalid_repeat_flag",
            "website_sessions",
            "is_repeat_session IS NULL "
            "OR is_repeat_session NOT IN (0, 1)",
            "FAIL",
        ),
        (
            "unexpected_device_type",
            "website_sessions",
            "device_type IS NULL "
            "OR device_type NOT IN ('desktop', 'mobile')",
            "REVIEW",
        ),
        (
            "blank_pageview_url",
            "website_pageviews",
            "pageview_url IS NULL OR TRIM(pageview_url) = ''",
            "FAIL",
        ),
        (
            "invalid_primary_flag",
            "order_items",
            "is_primary_item IS NULL "
            "OR is_primary_item NOT IN (0, 1)",
            "FAIL",
        ),
        (
            "invalid_items_purchased",
            "orders",
            "items_purchased IS NULL OR items_purchased <= 0",
            "FAIL",
        ),
        (
            "invalid_order_amount",
            "orders",
            "price_usd IS NULL OR cogs_usd IS NULL "
            "OR price_usd < 0 OR cogs_usd < 0",
            "FAIL",
        ),
        (
            "invalid_item_amount",
            "order_items",
            "price_usd IS NULL OR cogs_usd IS NULL "
            "OR price_usd < 0 OR cogs_usd < 0",
            "FAIL",
        ),
        (
            "invalid_refund_amount",
            "order_item_refunds",
            "refund_amount_usd IS NULL OR refund_amount_usd < 0",
            "FAIL",
        ),
        (
            "zero_refund_amount",
            "order_item_refunds",
            "refund_amount_usd = 0",
            "REVIEW",
        ),
    ]

    for name, table, condition, level in rules:
        check_sql(
            connection, name,
            f"SELECT COUNT(*) FROM `{table}` WHERE {condition}",
            level,
        )

    # 时间顺序：子事件不能早于对应的父事件。
    time_rules = [
        (
            "pageview_before_session",
            "website_pageviews", "website_sessions",
            "website_session_id", "website_session_id",
        ),
        (
            "order_before_session",
            "orders", "website_sessions",
            "website_session_id", "website_session_id",
        ),
        (
            "item_before_order",
            "order_items", "orders",
            "order_id", "order_id",
        ),
        (
            "refund_before_item",
            "order_item_refunds", "order_items",
            "order_item_id", "order_item_id",
        ),
        (
            "item_before_product_launch",
            "order_items", "products",
            "product_id", "product_id",
        ),
    ]

    for name, child, parent, child_key, parent_key in time_rules:
        check_sql(connection, name, f"""
            SELECT COUNT(*)
            FROM `{child}` c
            JOIN `{parent}` p
                ON c.`{child_key}` = p.`{parent_key}`
            WHERE c.created_at < p.created_at
        """)

    check_sql(connection, "order_session_user_mismatch", """
        SELECT COUNT(*)
        FROM orders o
        JOIN website_sessions s
            ON o.website_session_id = s.website_session_id
        WHERE o.user_id <> s.user_id
    """)

    check_sql(connection, "refund_order_mismatch", """
        SELECT COUNT(*)
        FROM order_item_refunds r
        JOIN order_items i
            ON r.order_item_id = i.order_item_id
        WHERE r.order_id <> i.order_id
    """)

    # 先把明细汇总到订单粒度，再与订单表核对。
    reconciliation = connection.execute(text("""
        WITH item_totals AS (
            SELECT
                order_id,
                COUNT(*) AS item_count,
                SUM(price_usd) AS item_sales,
                SUM(cogs_usd) AS item_cost,
                SUM(is_primary_item = 1) AS primary_count,
                MAX(
                    CASE WHEN is_primary_item = 1
                         THEN product_id END
                ) AS primary_product_id
            FROM order_items
            GROUP BY order_id
        )
        SELECT
            SUM(i.order_id IS NULL)
                AS orders_without_items,
            SUM(i.order_id IS NOT NULL
                AND o.items_purchased <> i.item_count)
                AS item_count_mismatch,
            SUM(i.order_id IS NOT NULL
                AND o.price_usd <> i.item_sales)
                AS sales_mismatch,
            SUM(i.order_id IS NOT NULL
                AND o.cogs_usd <> i.item_cost)
                AS cost_mismatch,
            SUM(i.order_id IS NOT NULL
                AND i.primary_count <> 1)
                AS primary_item_count_mismatch,
            SUM(i.primary_count = 1
                AND o.primary_product_id <> i.primary_product_id)
                AS primary_product_mismatch
        FROM orders o
        LEFT JOIN item_totals i ON o.order_id = i.order_id
    """)).mappings().one()

    for name, violations in reconciliation.items():
        record(name, violations)

    check_sql(connection, "refund_exceeds_item_price", """
        SELECT COUNT(*)
        FROM (
            SELECT
                order_item_id,
                SUM(refund_amount_usd) AS refunded
            FROM order_item_refunds
            GROUP BY order_item_id
        ) r
        JOIN order_items i
            ON r.order_item_id = i.order_item_id
        WHERE r.refunded > i.price_usd
    """)

    # 缺少营销标签先单独记录，不直接判定为错误。
    marketing = connection.execute(text("""
        SELECT
            SUM(utm_source IS NULL OR TRIM(utm_source) = '')
                AS missing_utm_source,
            SUM(utm_campaign IS NULL OR TRIM(utm_campaign) = '')
                AS missing_utm_campaign,
            SUM(utm_content IS NULL OR TRIM(utm_content) = '')
                AS missing_utm_content,
            SUM(http_referer IS NULL OR TRIM(http_referer) = '')
                AS missing_http_referer
        FROM website_sessions
    """)).mappings().one()

    report["context"]["marketing_missing"] = {
        key: int(value or 0)
        for key, value in marketing.items()
    }
    report["context"]["marketing_note"] = (
        "缺少 UTM 标签可能来自直接访问、自然流量或未打标签的访问，"
        "后续结合 http_referer 分析，不自动认定为错误。"
    )

engine.dispose()
save_report()