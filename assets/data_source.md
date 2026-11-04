# 数据来源与准备

来源：[Maven Analytics Toy Store E-Commerce Database](https://mavenanalytics.io/data-playground/toy-store-e-commerce-database)。数据为 Maven Fuzzy Factory 公开模拟电商记录，发布页许可标注 Public Domain。项目不包含真实企业的内部数据，数据下载与使用条件以来源页为准。

仓库保留 [字段字典](data_dictionary.csv)、计算代码、汇总结果和最终看板。六份原始业务 CSV 和逐条审计文件不提交 GitHub。

## 准备输入

1. 从来源页下载并解压数据包。
2. 在项目根目录创建 assets/raw_data/。
3. 放入以下六个文件，文件名保持不变：

```text
assets/raw_data/
    products.csv
    website_sessions.csv
    orders.csv
    order_items.csv
    order_item_refunds.csv
    website_pageviews.csv
```

无需把字典再次放入 raw_data/，仓库已有 assets/data_dictionary.csv。原始文件不要编辑；导入程序负责读取缺失值、时间与金额。

## 已验证的输入数量

| 表 | 数据行数（不含表头） |
| --- | ---: |
| products | 4 |
| website_sessions | 472,871 |
| orders | 32,313 |
| order_items | 40,025 |
| order_item_refunds | 1,731 |
| website_pageviews | 1,188,124 |

原始数据覆盖约 2012-03 至 2015-03，退款延续到 2015-04。复现顺序见 [环境与运行说明](../docs/environment_setup.md)。已有汇总结果和 Excel 可以直接查看，无需下载全部数据或安装数据库。
