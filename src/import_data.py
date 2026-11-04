import json
from getpass import getpass
from pathlib import Path

import pandas as pd
from sqlalchemy import URL, create_engine, inspect, text


root = Path(__file__).resolve().parents[1]
data_dir = root / "assets" / "raw_data"

# 先导入被引用的表，保证外键能够匹配。
tables = [
    "products",
    "website_sessions",
    "orders",
    "order_items",
    "order_item_refunds",
    "website_pageviews",
]

url = URL.create(
    drivername="mysql+pymysql",
    username="toy_analyst",
    password=getpass("请输入 toy_analyst 的 MySQL 密码："),
    host="127.0.0.1",
    port=3306,
    database="toy_store_ecommerce",
    query={"charset": "utf8mb4"},
)

engine = create_engine(url, hide_parameters=True)
source_counts = {}

# 所有表的写入放在同一个事务中，出错时回滚本次导入。
with engine.begin() as connection:
    for table in tables:
        if not inspect(connection).has_table(table):
            raise RuntimeError(f"缺少数据表：{table}，请先执行建表 SQL")

    for table in tables:
        print(f"正在导入 {table}……", flush=True)
        source_counts[table] = 0

        chunks = pd.read_csv(
            data_dir / f"{table}.csv",
            dtype=str,
            keep_default_na=False,
            na_values=["", "NULL", "\\N"],
            chunksize=20_000,
        )

        for chunk in chunks:
            chunk.to_sql(
                name=table,
                con=connection,
                if_exists="append",
                index=False,
                chunksize=1_000,
            )
            source_counts[table] += len(chunk)

        print(
            f"{table}：已处理 {source_counts[table]:,} 行",
            flush=True,
        )

print("全部导入已提交，开始核对行数。")

summary = []

with engine.connect() as connection:
    for table in tables:
        # 表名全部来自上面固定的列表。
        database_rows = connection.execute(
            text(f"SELECT COUNT(*) FROM `{table}`")
        ).scalar_one()

        summary.append({
            "table": table,
            "csv_rows": source_counts[table],
            "database_rows": database_rows,
            "rows_match": source_counts[table] == database_rows,
        })

output_dir = root / "output"
output_dir.mkdir(exist_ok=True)

report = json.dumps(summary, ensure_ascii=False, indent=2)
(output_dir / "import_summary.json").write_text(
    report + "\n",
    encoding="utf-8",
)

print(report)
engine.dispose()