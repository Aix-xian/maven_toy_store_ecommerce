# 渠道与设备分析口径

## 本阶段回答的问题

1. 2014 相比 2013，哪些渠道、设备贡献了会话收入增量？
2. 2014-12 相比 2014-11，整体每次访问收入下降，分别对应怎样的流量结构变化与相同分组内部表现变化？
3. 2015 年 1—2 月相比上年同期，转化变化是否广泛存在于主要渠道、设备，还是集中于少数组？

指标是描述性结果，后续不把分解项直接解释为广告、产品措施的因果影响。

## 数据与时间归属

- 输入：现有 MySQL 的 website_sessions 与 orders，以及已通过的 output/monthly_overview_report.json。
- SQL 先将订单汇总为每次会话一行，再 LEFT JOIN 到会话；保留未下单会话。
- 本模块全部指标按会话开始月份归属，包括订单数、金额和商品成本。字段名明确为 session_orders、session_sales_usd、session_cogs_usd。
- 会话口径收入与此前报告中按下单月份汇总的销售额，可能因跨月订单而不同；核对时对比前一阶段的会话口径字段。
- 不扣退款，不计算缺少广告成本支持的 ROI、ROAS 或获客成本。
- 原始提取按月份、utm_source、utm_campaign、utm_content、device_type、http_referer 分组；不丢弃广告内容与来源网址。channel_audit.csv 保留这些原字段，分类使用的规范化值不会覆盖原值。

## 渠道分类版本 1

分类按下表从上至下匹配。规范化仅用于分类：去除首尾空格，转为小写，将空字符串、NULL 文本和反斜杠 N 视为缺失。原始值保留。

| 条件 | channel | 含义与限制 |
| --- | --- | --- |
| source 为 gsearch 或 bsearch，campaign 为 nonbrand | gsearch_nonbrand / bsearch_nonbrand | 已标记的非品牌搜索流量 |
| source 为 gsearch 或 bsearch，campaign 为 brand | gsearch_brand / bsearch_brand | 已标记的品牌搜索流量 |
| source 为 gsearch 或 bsearch，campaign 为其他值或缺失 | gsearch_other_campaign / bsearch_other_campaign | 搜索来源已标记，活动类型另查原值 |
| source 为 socialbook | socialbook_tagged | 已标记的社交流量，pilot/desktop_targeted 等活动保留在审计表 |
| source 为其他非空值 | other_tagged | 未单独命名的已标记来源，原值保留 |
| source 缺失但 campaign 或 content 不缺失 | unclassified_partial_utm | 标签不完整，保留待复核 |
| 所有 UTM 缺失且来源网址缺失 | direct_candidate | 直接访问候选，也可能有来源丢失，不能据此证明用户主动输入网址 |
| 所有 UTM 缺失，来源主机为 gsearch.com 或 www.gsearch.com | organic_gsearch_candidate | 自然搜索候选，也可能有未标记的搜索访问 |
| 所有 UTM 缺失，来源主机为 bsearch.com 或 www.bsearch.com | organic_bsearch_candidate | 同上 |
| 其余所有 UTM 缺失且有来源值的情况 | referral_or_unknown_candidate | 其他引荐或无法解析的来源，原值保留 |

主机匹配使用 URL 解析后的完整主机名，不把 gsearch.com.example.org 当作 gsearch.com。分类不把已有 UTM 标签等同于已确认付费投放；具体投放性质需要额外业务资料。

原始访问数据的来源、活动、设备与来源网址组合用于制定并检查分类规则；渠道结果已通过真实 MySQL 查询与离线重算核对。

## 比较范围与分组

| period | 范围 |
| --- | --- |
| all_comparable | 前一阶段标记为 comparable_month 的全部月份，目前为 2012-04 至 2015-02 |
| year_2013 | 2013 年 1—12 月 |
| year_2014 | 2014 年 1—12 月 |
| jan_feb_2014 | 2014 年 1—2 月 |
| jan_feb_2015 | 2015 年 1—2 月 |
| nov_2014 | 2014 年 11 月 |
| dec_2014 | 2014 年 12 月 |

摘要按 overall、channel、device、channel_device 四种粒度分别提供。同比或环比的详细比较按 channel、device、channel_device 生成，每一种粒度都是同一整体的独立拆分，不能将三种粒度的金额再相加。

重点比较：2014_vs_2013、dec_vs_nov_2014、jan_feb_2015_vs_2014。本模块需要这些区间全部包含在已有可比月份中，否则提示重新确认范围。

## 指标

- sessions：会话数；converted_sessions：至少产生一笔订单的会话数；session_orders：这些会话产生的订单数。
- conversion_rate = converted_sessions / sessions。
- revenue_per_session_usd = session_sales_usd / sessions。
- session_aov_usd = session_sales_usd / session_orders，与月度报告下单月份口径的客单价作区分。
- session_share = 分组会话数 / 同期整体会话数；revenue_share = 分组会话收入 / 同期整体会话收入。
- session_sales_delta_usd = 新期会话收入 - 旧期会话收入。
- session_sales_growth = 分组会话收入增量 / 旧期分组会话收入，旧期为零时为空。
- revenue_delta_contribution = 分组会话收入增量 / 同期整体会话收入增量，整体增量为零时为空。贡献允许为负或超过 100%，表示抵消或超额贡献；每一种独立分组的贡献合计为 100%。
- conversion_change_pp = (新期分组转化率 - 旧期分组转化率) × 100，单位为百分点。某一期没有分组会话时为空。
- low_volume_review：任一期分组会话不足 100 则提示；这是浏览结果的约定，不是显著性检验，不能把超过阈值等同于有统计显著性。

比率导出为小数，百分点字段另乘 100。分母为零保留空值；汇总比率始终使用合计分子 / 合计分母。金额使用 Decimal，计算和对账先于八位小数导出舍入。

## 流量结构与组内表现的对称分解

对转化率或每次访问收入，都可以写成整体指标 R = Σ(w × r)，其中 w 是组的会话占比，r 是组内转化率或组内每次访问收入。设旧期为 0、新期为 1：

```text
结构项 = Σ[(w1 - w0) × (r0 + r1) / 2]
组内项 = Σ[(r1 - r0) × (w0 + w1) / 2]
整体变化 = 结构项 + 组内项
```

这是对两个因素对称分摊的会计分解，避免固定只用某一期权重。它描述结构和组内表现对应的变化，不是因果估计。

对只在某一期出现的分组，另一时期没有可观测组内指标。为保持可核对性，计算中把该组已观测到的指标延用到缺席期，将进入/退出的影响全部分到结构项；presence 标为 entering/exiting，old_rate_used/new_rate_used 明确表示用于分解的值，不把延用值当成实际观测。

分解同时按 channel、device、channel_device 三种独立粒度计算。channel_device 更细，组内项排除了这两种已观察分组的占比变化，但仍可能包含商品、落地页、广告内容等未分组因素。

channel_decomposition.csv 中：

- presence 为 total 的行表示该比较/分组粒度/指标的整体变化。
- change、mix_effect、within_effect 对转化率使用小数单位；mix_effect_pp、within_effect_pp 为对应百分点值。
- 对每次访问收入，change、mix_effect、within_effect 的单位为美元/次会话。
- 各组的影响贡献与 total 行不能再次相加；三种 grouping 也不能相加。
- 原始精度下核对残差不超过 1e-18；导出后的八位小数可能产生微小舍入差异。

## 执行与输出

在 WSL 项目根目录执行：

```bash
env/python/bin/python -B src/channel_analysis.py
```

口令在本机输入。脚本只执行 SELECT；使用一致事务读取基准和渠道汇总，不创建表或修改数据。

| 结果文件 | 用途 |
| --- | --- |
| output/channel_audit.csv | 按月和全部原始营销维度聚合，附分类及规则，供审核 |
| output/channel_monthly.csv | 每月渠道与设备的指标、占比和低量提示 |
| output/channel_summary.csv | 各比较区间的整体/渠道/设备/渠道设备摘要 |
| output/channel_comparisons.csv | 同比和重点月份比较，含收入增量贡献 |
| output/channel_decomposition.csv | 转化率与每次访问收入的结构/组内分解 |
| output/channel_analysis_report.json | 运行状态、对账、基准指纹、摘要、比较与分解 |

脚本自动向 docs/project_process.md 追加成功或失败记录。失败时以 JSON status 为准，旧 CSV 可能来自之前的运行。真实数据库查询已完成，149 项核心核对及附加检查通过，结果见业务报告。

## 验证

- 数据库当前总量和时间边界与月度报告一致，按会话口径逐月对账数量和收入；总成本与原表合计对账。
- 原始维度组合唯一；分组数量、转化范围和非负金额符合约定；渠道设备汇总不会增加或减少总量。
- 每种分组的收入增量贡献和结构/组内分解均对平。
- 离线命令：env/python/bin/python -B -m unittest discover -s tests -p test_channel_analysis.py -v。
- 已通过 11 项测试，覆盖分类、误匹配主机、缺失标签、新增/退出分组、纯结构/纯组内变化、金额分解、加权比率、漏行/重复记录、零分母和 SQLAlchemy 编译；命令行入口已检查。
- 实际 MySQL 查询与离线样例测试分别验证数据一致性和计算边界，不互相替代。


原始数据需按 [数据来源说明](../assets/data_source.md) 准备。逐条审计 CSV 为本地复现产物，不提交 GitHub；汇总结果和核对报告保留在 output/。
