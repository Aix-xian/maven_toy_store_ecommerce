# 商品、订单结构与退款分析口径

## 本阶段问题

1. 各商品贡献多少销售额，2014 年及重点月份的增量由哪些商品构成？
2. 客单价变化同时对应怎样的每单商品明细数、平均明细价格与主/次商品结构？
3. 哪些商品经常出现在同一订单？多明细订单与多种商品订单有何区别？
4. 观察到的退款和销售额减退款是多少？统一 30 天窗口下，各商品的已观测退款率如何？

背景：漏斗已通过 151 项对账；移动端多阶段推进率偏低，2014-12 支付到确认推进率下降。商品模块用于补充解释客单价和收入构成，不将商品结构变化直接当作页面变化的因果解释。

## 数据与粒度

- src/product_analysis.sql：每行一条订单商品明细，关联订单时间和商品目录，不直接与多笔退款连接。
- 退款事件另外读取，每笔事件保留退款编号、明细编号、订单编号、时间和金额，Python 按明细归集，防止部分退款多次发生时放大销售额。
- src/product_baskets.sql：先把明细汇总到订单，再关联订单金额，每行一笔订单。
- src/product_pairs.sql：先将订单内 product_id 去重，再按较小编号与较大编号组成无序对，每个商品对在同一订单最多计一次。
- 商品、订单篮子和搭配均按下单月份归属，与月度报告的 sales_usd/cogs_usd 对账；与渠道模块的会话月份口径区别开。
- 原始 DATETIME 保留原时区未知状态，不自行转换业务时间。过程记录使用 Asia/Shanghai。

## 期间与商品贡献

报告包含全部观测订单期 all_observed，以及前序使用的全部可比月份、2013/2014 全年、2014/2015 年前两月和 2014-11/12。

商品销售额为明细 price_usd 合计；商品成本为明细 cogs_usd 合计。primary_items/secondary_items 根据 is_primary_item 区分主商品和其他明细，不把 secondary 自动解释为某项交叉销售策略的效果。

商品 distinct_orders 为包含该商品的不同订单数；一笔订单可包含多个商品，因此各商品 distinct_orders 不能相加作为整体订单数。金额和明细数可按商品相加。

商品比较中，revenue_delta_contribution 为分组销售额增量 / 整体销售额增量，允许为负或超过 100%；整体增量为零时为空。新出现/退出商品标为 entering/exiting；商品上市时间保留，不把上市时长不同的绝对规模直接解释成需求优劣。

## 客单价与搭配

- items_per_order：商品明细数 / 订单数。数据质量检查已确认 items_purchased 与明细数一致；本项目据此使用明细数度量篮子大小。
- average_item_price_usd：销售额 / 明细数，是商品结构和成交价格共同形成的加权均值，不是单个商品的标价。
- aov_usd：销售额 / 订单数。
- 三者满足 aov_usd = items_per_order × average_item_price_usd，使用未舍入 Decimal 核对。
- multi_item_orders：含多条明细的订单。multi_product_orders：包含多种 product_id 的订单；重复购买同款可以属于前者但不属于后者。
- pair_order_share：包含该商品对的订单数 / 同期全部订单数，不是条件附购率，也不是提升度。
- 三种以上商品的订单会贡献多个商品对，商品对订单数不能直接相加作为不同订单数。

篮子摘要按整体与 primary_product_id 分组；主商品分组互斥，可核对整体。这里比较数量、价格与结构，不估计价格弹性或虚构上线收益。

## 退款截止和 30 天窗口

默认 window_days = 30，可以通过 --window-days 改变并记录。

数据没有明确提取截止说明。脚本使用退款表最大已观测 created_at 作为 refund_observation_cutoff_at 的代理；本数据已核实最大退款时间为 2015-04-01 18:11:08。这个事件时间并不能证明之前每一天都完整采集了所有退款，结果始终描述本数据集内已观测记录，不声称最终退货率。

### 截至代理截止的累计观测口径

- observed_refund_usd：归属这些商品明细、且退款发生时间不晚于截止的金额。
- observed_refunded_items：累计观测到正金额退款的不同商品明细数，重复部分退款只计一条明细。
- observed_refund_item_rate：上述正金额退款明细数 / 全部商品明细数；明细年龄不同，不能直接当作等观察时长的质量排名。
- observed_sales_after_refunds_usd：商品销售额 - 已观测退款。按原订单归属期展示，不等同于当期现金流，也不表示净利润。
- all_refund_events/all_refund_usd 保留源数据全部记录；refund_events_audit.csv 另外保留 refund_month，便于区分退款发生月。零金额退款保留事件，但不计作正金额退款明细。

### 统一窗口口径

一条商品明细只有在 item_at + 30 天 <= 截止时，才进入 window_eligible_items；其他明细单独报告为 window_ineligible_items。

在符合资格的明细中：

- window_refunded_items：从 item_at 起、截至 item_at + 30 天内，累计正金额退款的不同明细数。
- window_refund_item_rate = window_refunded_items / window_eligible_items。
- window_refund_usd：这些有完整资格的明细在窗口内发生的退款金额合计。
- 第 30 天同一时刻的退款包括在内，之后一秒不包括；部分退款金额相加，但明细只计一次。
- 尚未达到窗口年龄的明细，即使已退款，也不进入这个可比口径的分子或分母；其退款仍在累计观测口径和审计明细中保留。
- 资格为零时比率保留空值；资格不足 100 条时提示低量，不作统计显著性声明。

标准窗口改善了明细观察时长的可比性，仍依赖数据采集情况；尚未控制商品上市、季节、渠道和用户构成，也不能单凭该比率判断质量原因。

## 成本与利润边界

cost_difference_before_refunds_usd = sales_usd - cogs_usd，仅为退款前销售额与商品成本差额。退款后成本是否冲回、退货能否回收和其他经营费用均不完整，不计算或标注净利润。不把退款金额直接当作永久成本损失。

## 执行与产物

在 WSL 项目根目录执行：

```bash
env/python/bin/python -B src/product_analysis.py
```

脚本提示输入本机 MySQL 项目账号口令，只执行 SELECT。沿用 env/，不增加依赖。首先核对前序基准及源表范围，再读取明细、退款、篮子和搭配，在内存归集并逐月对账。

| 文件 | 内容 |
| --- | --- |
| output/product_item_audit.csv | 每条商品明细、累计退款、窗口资格及窗口退款 |
| output/refund_events_audit.csv | 每笔退款、发生月、截止与窗口标记 |
| output/product_monthly.csv | 每月每商品的销售、成本、退款和窗口口径 |
| output/product_summary.csv | 各比较期间每商品汇总 |
| output/product_baskets.csv | 各期间整体及主商品订单结构和客单价指标 |
| output/product_pairs.csv | 各期间无序商品对和订单占比 |
| output/product_comparisons.csv | 商品销售贡献与窗口退款率的时期对照 |
| output/product_analysis_report.json | 状态、指纹、截止依据、对账与各表摘要 |

脚本自动向 docs/project_process.md 追加成功/失败状态；失败时旧 CSV 可能来自先前运行，以 JSON status 为准。CSV 金额及比率输出八位小数，计算与对账在舍入前进行。

## 验证状态

- 核对现有订单/会话基准，商品/明细/退款表行数与时间范围，商品和退款提取完整性。
- 按下单月核对商品销售额、成本、主商品数、订单篮子数、明细数与销售额，共计当前 37 个月 × 6 项 = 222 项逐月核对；另检查每订单对账、退款金额、归属、时间及客单价恒等式。
- 离线验证命令：env/python/bin/python -B -m unittest discover -s tests -p test_product_analysis.py -v。
- 11 项离线测试通过，包含部分/晚到退款、恰好 30 天边界、观察不足、截止后事件保留、退款重复/归属/金额拒绝、跨商品订单非可加性，以及在 SQLite 小型样例执行真实的明细/篮子/搭配 SQL，验证金额不扩行、同款重复和无序搭配去重。
- 真实 MySQL 查询已完成，222 项逐月核对及附加检查通过；重新归集 40,025 条明细、1,731 笔退款并重建订单和搭配，七份 CSV 全字段一致。结论见 [业务报告](business_report.md)。

## 客单价派生分解

离线复核进一步对 AOV = Q × P 进行对称恒等分解，Q 为每单件数，P 为平均单件成交价：件数项 = (Q1 − Q0) × (P0 + P1) / 2，平均价格/结构项 = (P1 − P0) × (Q0 + Q1) / 2，两项之和等于客单价差额。该分解不估计因果，P 包含商品结构与实际成交价格，不能解释成单个商品调价。


原始数据需按 [数据来源说明](../assets/data_source.md) 准备。逐条审计 CSV 为本地复现产物，不提交 GitHub；汇总结果和核对报告保留在 output/。
