# 玩具电商经营分析

本项目通过codex辅助完成，主要学习ai处理问题的思路，训练vibecoding能力。

基于 [Maven Analytics Toy Store E-Commerce Database](https://mavenanalytics.io/data-playground/toy-store-e-commerce-database) 的 MySQL + Python + Excel 项目，分析经营增长、渠道与设备、购买漏斗、商品结构和退款。使用公开模拟数据，定位为电商数据分析作品。

## 阅读入口

- [业务报告与逐步产出](docs/business_report.md)：每一步解决什么问题、如何处理、验证什么、生成哪些文件。
- [最终 Excel 看板](output/ecommerce_dashboard.xlsx)：四个业务视图、八张工作表、六张图表。
- [指标定义](docs/metric_definitions.md)、[渠道方法](docs/channel_methodology.md)、[漏斗方法](docs/funnel_methodology.md)、[商品退款方法](docs/product_methodology.md)。
- [实施记录](docs/project_process.md)、[环境与复现](docs/environment_setup.md)、[数据下载说明](assets/data_source.md)、[Excel 使用说明](docs/excel_guide.md)。

## 业务发现

2014 年销售额 1,075,612.19 美元，同比增长 173.52%；客单价 63.80 美元，同比增长 20.81%，主要伴随每单件数提高。gsearch_nonbrand 贡献年度会话收入增量的 51.95%。

2014 年桌面转化率 9.18%、移动 3.31%；列表到详情推进率相差 18.40 个百分点。2014-12 销售额增长的同时，每次访问收入环比下降 4.48%，客单价下降 3.07%，支付到确认推进率下降 2.44 个百分点，值得结合商品搭配和支付流程进一步核查。

这些是历史描述性发现，没有广告成本、实验分组和完整经营费用，不计算 ROAS 或净利润，不声称建议已带来实际收益。

## 数据与处理范围

六表共 1,735,068 行：会话 472,871、浏览 1,188,124、订单 32,313、商品明细 40,025、退款事件 1,731、商品 4。主要比较范围为 2012-04 至 2015-02，共 35 个可比月；订单与会话按各自发生月归属。退款同时报告累计观测和统一 30 天窗口。

## 目录

```text
src/        SQL、导入、质量检查、分析、结果复核、看板构建与仓库检查
docs/       唯一业务报告、计算方法、过程、复现和使用说明
output/     汇总 CSV、分析与发现 JSON、最终 Excel、验证及交付清单
assets/     来源说明与原始字段字典
tests/      四模块离线测试
env/        依赖清单、版本锁和隔离说明
```

## 环境与验证

分析环境为 WSL Ubuntu aarch64、MySQL 8.4.11、Python 3.14.4，依赖隔离于项目 env/python/。

最终文件检查记录见 [项目验证](output/project_validation.json)、[Excel 验证](output/excel_validation_report.json) 和 [交付文件清单](output/migration_manifest.json)。看板缓存验证覆盖已保存期间，不代表所有月份交互已经逐一验证。

本轮整理保留 70 个文件；31 个文档链接有效，14 份汇总 CSV 与分析 JSON 全字段核对一致，26 份保留结果与迁移来源字节一致。最终 Excel 保存期间的 24 项关键指标缓存均匹配，文件指纹保持一致。

## 工作日志

| 日期 | 内容 | 问题与处理 | 验证 |
| --- | --- | --- | --- |
| 2026-09-30 | 数据准备、隔离环境、建模导入、质量、月度与渠道分析 | 环境引导、不同时间归属、边界月、渠道缺失按规则处理 | 导入数量一致；质量/月度/渠道检查通过 |
| 2026-10-09 | 漏斗、商品退款、Excel、业务报告 | 有序事件、部分退款、观察窗口与比率分母明确化 | 漏斗/商品检查与结果重算一致 |
| 2026-11-04 | Windows GitHub 仓库文件整理与文档统一 | 原始大数据通过来源说明获取，运行环境留在原环境，业务说明集中为一份 | 复制指纹、文件引用、最终 Excel 与 39 项离线测试通过 |

## 问题与解决方法

会话多订单及明细多退款可能放大金额，采用先聚合再关联。边界月份不直接参与整月比较；近期商品退款观察不足单列。缺失 UTM 结合来源网址分类并保留候选标记。缺少广告成本、退款原因和其他费用时，只提出可验证建议，不填补未经支持的结论。

## 文件命名与可复现约定

项目自编文件使用英文小写与下划线，引用使用相对路径。README.md、.gitignore、.gitattributes 是首页和 Git 标准文件名的必要例外。Python 与依赖的生态文件仅在 env/ 内生成，不提交仓库。JSON 分析报告以字节指纹相互核对，.gitattributes 保持其换行不被 Git 转换。
