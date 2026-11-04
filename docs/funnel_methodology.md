# 购买漏斗与页面路径口径

## 选题依据

已核实的渠道结果显示：2014 年移动端转化率约 3.31%，桌面约 9.18%；2014-12 每次访问收入下降主要落在渠道设备组内部。下一步观察页面路径，比较设备、搜索非品牌渠道及 2014-11/12 的阶段推进情况。

本模块用于描述可观测页面路径和提出进一步核查的问题，不据此直接认定移动页面故障、确定营销因果或估算已经实现的改善收益。

## 路径与事件顺序

同一次网站会话内，按 created_at、website_pageview_id 依次排序；时间相同时按唯一浏览编号打破并列。这是明确的排序约定，不推断更精细的真实时序。

| 阶段字段 | 定义 |
| --- | --- |
| sessions | 全部网站会话 |
| pageview_sessions | 至少观测到一次页面浏览的会话 |
| ordered_products | 浏览过 /products 的会话 |
| ordered_detail | 在有效 products 事件之后浏览商品详情的会话 |
| ordered_cart | 在有效商品详情事件之后浏览 /cart 的会话 |
| ordered_shipping | 在有效 cart 事件之后浏览 /shipping 的会话 |
| ordered_billing | 在有效 shipping 事件之后浏览任一支付页的会话 |
| ordered_thank_you | 在有效 billing 事件之后浏览确认页的会话 |

商品详情包括四种商品页面：/the-original-mr-fuzzy、/the-forever-love-bear、/the-birthday-sugar-panda、/the-hudson-river-mini-bear。支付页 /billing 与 /billing-2 归入同一阶段。确认页为 /thank-you-for-your-order。

每一步取前一步之后最早的有效事件；重复访问同一页面不重复计会话。允许页面回访，只要后来形成有效有序路径即可。首个页面本身可以是 /products，因此 pageview_sessions 与 ordered_products 可以在同一次页面事件上成立。

落地页取该会话第一条已观测页面浏览的 URL，不强制为 /home 或 /lander 页面。没有页面浏览的会话也保留，落地页标为 no_pageview。落地页分组用于描述，不将历史不同版本的差异直接称为随机 A/B 实验效果。

## 顺序路径与原始到达分开

raw_products、raw_detail、raw_cart、raw_shipping、raw_billing、raw_thank_you 表示会话是否曾到达对应页面家族，不要求顺序。ordered_* 要求指定完整前序。

例如直接进入商品详情后下单的会话，可能有订单且 raw_thank_you 为 1，但不满足从 /products 开始的指定完整路径。未走完整路径不自动等于丢失订单，阶段差值也不全部等同于用户放弃购买。

实际转化单独来自 orders：converted_sessions 为至少下过一笔订单的会话数，session_orders 为订单数。始终先把订单汇总到每次会话一行，再与路径结果关联，避免重复浏览或多订单放大计数和金额。

会话开始月份决定全部路径、订单和收入的归属；与前序渠道分析一致。销售额未扣退款。计数单位为会话，不是去重用户数。

## 指标与异常解释

- stage_sessions：到达当前有序阶段的会话数。
- step_rate：当前阶段会话数 / 前一阶段会话数。根阶段没有上一步，保留空值。
- reach_rate：当前阶段会话数 / 本分组全部会话数。
- not_following_next_stage：前一阶段会话数 - 当前阶段会话数，表示未继续满足这条指定路径的会话，不自动解释为放弃购买。
- conversion_rate：实际转化会话数 / 全部会话数，独立于页面确认路径。
- single_page_rate_observed：仅观测到一次页面浏览的会话数 / 有页面浏览的会话数。它是可观测单页会话率，不等同于所有平台定义的跳出率或无互动率。
- step_rate_change_pp：新期阶段推进率 - 旧期阶段推进率，再乘 100，单位为百分点。
- 分母为零保留空值。前序阶段会话不足 100 时提示低量；该阈值不表示统计显著性。

报告 review_notes 单列以下待解释情况：

- orders_without_ordered_thank_you：有订单但未走完整有序路径。
- ordered_thank_you_without_order：走完指定路径但未匹配到该会话的实际订单。
- orders_without_raw_thank_you：有订单但没有观测到任何确认页。
- raw_thank_you_without_order：观测到确认页但没有匹配到实际订单。
- sessions_without_pageviews：没有观测到页面的会话。
- unmapped_page：没有在当前页面字典中的新 URL。

其中有些情况可能是有效的其他路径，也可能需要排查埋点、会话关联或映射；脚本保留数据并记录，不自动删除。JSON status 为 PASS 表示本轮程序和核对完成，review_notes 仍需阅读解释。

## 分组与比较

沿用渠道分类版本 1，详见 [渠道口径](channel_methodology.md)。原始来源、活动、广告内容、设备、来源网址以及落地页全部保存在审计表中。

摘要按整体、设备、渠道、渠道设备、落地页提供。区间沿用全部可比月份、2013 全年、2014 全年、2014/2015 年 1—2 月，以及 2014-11 和 2014-12。比较表按整体、设备、渠道、渠道设备计算各阶段推进率的变化；落地页版本不作无条件的跨期因果比较。

## 运行与结果

在 WSL 项目根目录执行：

```bash
env/python/bin/python -B src/funnel_analysis.py
```

输入本机项目账号口令。脚本只执行 SELECT；在同一事务里核对基准、读取页面清单并计算路径，不修改数据库。需要对全部页面浏览排序，可能比月度汇总耗时更长。

| 文件 | 用途 |
| --- | --- |
| output/funnel_audit.csv | 月份与原始营销维度、落地页的路径/订单审计计数 |
| output/funnel_page_inventory.csv | 页面 URL、浏览数量及家族映射 |
| output/funnel_groups.csv | 各时期与分组的总量、原始到达、有序路径和实际订单 |
| output/funnel_stages.csv | 各时期与分组的逐阶段人数、条件推进率、覆盖率 |
| output/funnel_comparisons.csv | 同比与重点月份的逐阶段比较 |
| output/funnel_analysis_report.json | 核对、来源指纹、待解释记录和明细 |

脚本每次结束追加 docs/project_process.md。以本次 JSON status 判断是否完成；失败时旧 CSV 可能属于之前的运行。

## 验证证据与限制

- 逐月核对会话数、实际转化会话数、会话订单数、会话收入，与月度基准一致；页面浏览总数、页面清单合计和商品成本总数对账。
- 每组有序路径必须逐级不增，有序人数不能超过同页家族的原始到达人数；订单、页面和路径计数保持各自粒度。
- 关键分组的会话、转化、订单、金额和成本须与已完成的渠道结果一致。
- 离线测试命令：env/python/bin/python -B -m unittest discover -s tests -p test_funnel_analysis.py -v。
- 10 项测试通过。测试使用 Python 标准库 SQLite 内存数据库执行同一份漏斗 SQL，仅为 SQLite 注册等价的 DATE_FORMAT 函数；覆盖完整路径、多订单、乱序、回访、两种支付页、不同商品详情、无页面会话、同秒事件、订单与确认页分离、核对拒绝与条件分母，另检查 MySQL 方言编译。
- SQLite 测试验证小型样例逻辑；真实 MySQL 查询已完成，151 项核心核对及附加检查通过，阶段结论见业务报告。


原始数据需按 [数据来源说明](../assets/data_source.md) 准备。逐条审计 CSV 为本地复现产物，不提交 GitHub；汇总结果和核对报告保留在 output/。
