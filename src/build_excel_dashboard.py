"""Build an Excel report from reviewed local outputs; no database connection."""

import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.chart.series import SeriesLabel
from openpyxl.chart.text import RichText
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.workbook.properties import CalcProperties
from openpyxl.drawing.text import CharacterProperties, Paragraph, ParagraphProperties

from channel_analysis import require
from monthly_overview import json_value, ratio


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"
FONT = "Microsoft YaHei"
INK, BLUE, MUTED = "172B4D", "335C99", "63758A"
PALETTE = ("335C99", "D79036", "589C87", "8F78A8")
COUNT, MONEY, RATE = '#,##0', '"$"#,##0.00', '0.00%'
URL = "https://mavenanalytics.io/data-playground/toy-store-e-commerce-database"
START, END, STATE = "'经营总览'!$B$4", "'经营总览'!$E$4", "'经营总览'!$L$4"
VIEWS = ("经营总览", "渠道设备", "购买漏斗", "商品退款")


def read_csv(name):
    with (OUTPUT / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_json(name):
    return json.loads((OUTPUT / name).read_bytes())


def number(value):
    return float(Decimal(str(value)))


def load_inputs():
    reports = {key: read_json(f"{key}_report.json") for key in (
        "monthly_overview", "channel_analysis", "funnel_analysis", "product_analysis",
    )}
    for report in reports.values():
        require(report["status"] == "PASS" and report["validation"]["failed"] == 0, "分析报告未全部通过。")
    fingerprints = {}
    for key, analysis in (("monthly", "monthly_overview"), ("channel", "channel_analysis"), ("funnel", "funnel_analysis"), ("product", "product_analysis")):
        finding = read_json(f"{key}_findings.json")
        path = OUTPUT / f"{analysis}_report.json"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        require(finding["source_sha256"] == actual, f"{key} 分析已变更，先重新复核。")
        fingerprints[path.name] = actual
    monthly = reports["monthly_overview"]
    for key in ("channel_analysis", "funnel_analysis", "product_analysis"):
        require(reports[key]["monthly_baseline_sha256"] == fingerprints["monthly_overview_report.json"], "模块月度基准不一致。")
    return reports, fingerprints


def base_sheet(workbook, name, title, note, end_column="O", rows=60):
    sheet = workbook.create_sheet(name)
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 90
    for row in sheet.iter_rows(min_row=1, max_row=rows, max_col=15):
        for cell in row:
            cell.font = Font(name=FONT, size=11, color=INK)
            cell.alignment = Alignment(vertical="center")
    for col in "ABCDEFGHIJKLMNO":
        sheet.column_dimensions[col].width = 14
    sheet.column_dimensions["A"].width = 29
    sheet.row_dimensions[1].height = 10
    sheet.row_dimensions[2].height = 28
    sheet["A2"], sheet["A3"] = title, note
    sheet["A2"].font = Font(name=FONT, size=16, bold=True, color=INK)
    sheet["A3"].font = Font(name=FONT, size=10, italic=True, color=MUTED)
    for row in range(4, rows + 1):
        sheet.row_dimensions[row].height = 24
    for cell in sheet[f"A2:{end_column}2"][0]:
        cell.border = Border(bottom=Side(style="thin", color="CCD6E2"))
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_options.horizontalCentered = True
    sheet.print_area = f"A1:{end_column}{rows}"
    if name in VIEWS:
        sheet.sheet_properties.tabColor = BLUE
        if name != VIEWS[0]:
            sheet["A4"] = "所选月份"
            sheet["B4"] = f'=TEXT({START},"yyyy-mm")&" 至 "&TEXT({END},"yyyy-mm")'
    return sheet


def header(sheet, row, labels):
    for column, label in enumerate(labels, 1):
        cell = sheet.cell(row, column, label)
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.font = Font(name=FONT, size=11, bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[row].height = 36


def data_sheet(workbook, name, labels, rows, source, table_name, date_columns=(1,), money_columns=()):
    sheet = base_sheet(workbook, name, name, f"计算来源：{source}", rows=len(rows) + 5)
    sheet["A4"] = URL
    sheet["A4"].hyperlink = URL
    sheet["A4"].font = Font(name=FONT, size=10, color=BLUE, underline="single")
    header(sheet, 5, labels)
    for row_number, values in enumerate(rows, 6):
        for column, value in enumerate(values, 1):
            cell = sheet.cell(row_number, column, value)
            if isinstance(value, (int, float)):
                cell.number_format = COUNT
                cell.alignment = Alignment(horizontal="right", vertical="center")
            if column in date_columns:
                cell.number_format = "yyyy-mm"
            if column in money_columns:
                cell.number_format = MONEY
    last_col = sheet.cell(5, len(labels)).column_letter
    table = Table(displayName=table_name, ref=f"A5:{last_col}{len(rows) + 5}")
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    sheet.add_table(table)
    sheet.freeze_panes = "C6"
    sheet.column_dimensions["A"].width = 14
    sheet.column_dimensions["B"].width = 22
    sheet.row_dimensions[4].height = 20
    return sheet


def sum_formula(sheet, last_row, value_col, criteria=()):
    terms = [f"'{sheet}'!${value_col}$6:${value_col}${last_row}",
             f"'{sheet}'!$A$6:$A${last_row}", '">="&' + START,
             f"'{sheet}'!$A$6:$A${last_row}", '"<="&' + END,
             f"'{sheet}'!$B$6:$B${last_row}", '"comparable_month"']
    for column, value in criteria:
        terms.extend((f"'{sheet}'!${column}$6:${column}${last_row}", f'"{value}"'))
    return f'=IF({STATE}<>"可比月份",NA(),SUMIFS({",".join(terms)}))'


def safe_rate(numerator, denominator):
    return f'=IF({denominator}=0,"n.a.",{numerator}/{denominator})'


def table_formats(sheet, start, end, formats):
    for column, fmt in formats.items():
        for row in range(start, end + 1):
            cell = sheet[f"{column}{row}"]
            cell.number_format = fmt
            cell.alignment = Alignment(horizontal="right", vertical="center")


def chart_font():
    return RichText(p=[Paragraph(pPr=ParagraphProperties(defRPr=CharacterProperties(sz=1000)), endParaRPr=CharacterProperties(lang="zh-CN"))])


def add_chart(sheet, title, category_col, value_cols, start, end, position, kind="bar", percent=False, width=20, height=9, ceiling=None):
    chart = LineChart() if kind == "line" else BarChart()
    chart.title, chart.style = title, 13
    chart.width, chart.height = width, height
    chart.y_axis.numFmt = RATE if percent else COUNT
    chart.x_axis.txPr = chart_font()
    chart.y_axis.txPr = chart_font()
    chart.y_axis.scaling.min = 0
    if ceiling is not None:
        chart.y_axis.scaling.max = ceiling
    chart.display_blanks = "gap"
    for column in value_cols:
        chart.add_data(Reference(sheet, min_col=column, min_row=start, max_row=end), titles_from_data=True)
    chart.set_categories(Reference(sheet, min_col=category_col, min_row=start + 1, max_row=end))
    chart.legend = None if len(value_cols) == 1 else chart.legend
    if chart.legend:
        chart.legend.position = "t"
    for series, color, column in zip(chart.series, PALETTE, value_cols):
        series.title = SeriesLabel(v=str(sheet.cell(start, column).value))
        series.graphicalProperties.line.solidFill = color
        series.graphicalProperties.line.width = 22000
        if kind == "line":
            series.smooth = False
        if kind != "line":
            series.graphicalProperties.solidFill = color
    sheet.add_chart(chart, position)


def prepare_data(reports):
    monthly = reports["monthly_overview"]["monthly_rows"]
    statuses = {row["month_start"]: row["period_status"] for row in monthly}
    item_rows = read_csv("product_item_audit.csv")
    baskets, item_counts, multi_counts = defaultdict(list), defaultdict(int), defaultdict(int)
    for item in item_rows:
        baskets[int(item["order_id"])].append(item)
        item_counts[item["month_start"]] += 1
    for group in baskets.values():
        if len({item["product_id"] for item in group}) > 1:
            multi_counts[group[0]["month_start"]] += 1
    month_data = []
    for row in monthly:
        month_data.append([
            date.fromisoformat(row["month_start"]), row["period_status"],
            *[row[key] for key in ("sessions", "converted_sessions", "session_orders", "orders")],
            *[number(row[key]) for key in ("session_sales_usd", "sales_usd", "cogs_usd")],
            item_counts[row["month_start"]], multi_counts[row["month_start"]],
        ])
    channels = read_csv("channel_monthly.csv")
    channel_data = [[date.fromisoformat(row["month_start"]), row["period_status"], row["channel"], row["device_type"],
                     *[int(row[key]) for key in ("sessions", "converted_sessions", "session_orders")],
                     *[number(row[key]) for key in ("session_sales_usd", "session_cogs_usd")]] for row in channels]
    funnel = read_csv("funnel_audit.csv")
    funnel_data = [[date.fromisoformat(row["month_start"]), row["period_status"], row["channel"], row["device_type"], row["landing_url"],
                    *[int(row[key]) for key in ("sessions", "pageview_sessions", "ordered_products", "ordered_detail", "ordered_cart", "ordered_shipping", "ordered_billing", "ordered_thank_you", "converted_sessions", "session_orders")], number(row["session_sales_usd"])] for row in funnel]
    products = reports["product_analysis"]["monthly_products"]
    product_data = [[date.fromisoformat(row["month_start"]), statuses[row["month_start"]], row["product_id"], row["product_name"],
                     row["items"], row["primary_items"], row["secondary_items"], number(row["sales_usd"]), number(row["cogs_usd"]),
                     number(row["observed_refund_usd"]), row["window_eligible_items"], row["window_refunded_items"],
                     number(row["window_refund_usd"]), row["window_ineligible_items"]] for row in products]
    require(sum(row[9] for row in month_data) == len(item_rows), "明细计数未对平。")
    return month_data, channel_data, funnel_data, product_data


def build_workbook(reports, data):
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.calculation = CalcProperties(calcMode="auto", fullCalcOnLoad=True, forceFullCalc=True)
    workbook.properties.title = "Maven Fuzzy Factory 电商经营分析"
    workbook.properties.description = "公开模拟数据。按可比月份选择期间，销售额未扣退款，退款窗口单列。"
    overview = base_sheet(workbook, VIEWS[0], "玩具电商经营总览", "公开模拟数据。金额为美元、未扣退款。请选择起止月份。", rows=61)
    channel_view = base_sheet(workbook, VIEWS[1], "渠道与设备", "按会话月份归属。候选来源分类不等于已经证实的流量性质。", rows=48)
    funnel_view = base_sheet(workbook, VIEWS[2], "购买漏斗", "按会话月份归属。推进率分母为同设备前一有效阶段的会话数。", rows=43)
    product_view = base_sheet(workbook, VIEWS[3], "商品与退款", "按订单月份归属。退款明细率使用完整窗口资格，销售减退款不代表净利润。", rows=43)
    monthly, channels, funnel, products = data
    data_sheet(workbook, "月度数据", ["月份", "月份状态", "会话数", "转化会话数", "会话订单数", "订单数", "会话收入", "销售额", "商品成本", "商品件数", "多商品订单数"], monthly,
               f"月度报告 {reports['monthly_overview']['generated_at']}；商品审计明细", "monthly_data", money_columns=(7, 8, 9))
    channel_source = data_sheet(workbook, "渠道数据", ["月份", "月份状态", "渠道分类", "设备", "会话数", "转化会话数", "会话订单数", "会话收入", "商品成本"], channels,
               f"渠道报告 {reports['channel_analysis']['generated_at']}", "channel_data", money_columns=(8, 9))
    channel_source.column_dimensions["C"].width = 35
    funnel_source = data_sheet(workbook, "漏斗数据", ["月份", "月份状态", "渠道分类", "设备", "落地页", "会话数", "有页面浏览", "有序商品列表", "有序详情", "有序购物车", "有序配送", "有序支付", "有序确认", "实际转化会话", "实际订单", "会话收入"], funnel,
               f"漏斗报告 {reports['funnel_analysis']['generated_at']}", "funnel_data", money_columns=(16,))
    funnel_source.column_dimensions["C"].width = 39
    funnel_source.column_dimensions["E"].width = 23
    product_source = data_sheet(workbook, "商品数据", ["月份", "月份状态", "商品编号", "商品名称", "件数", "主商品件数", "非主商品件数", "销售额", "商品成本", "已观测退款", "窗口合资格件数", "窗口退款件数", "窗口退款金额", "观察不足件数"], products,
               f"商品报告 {reports['product_analysis']['generated_at']}", "product_data", money_columns=(8, 9, 10, 13))
    product_source.column_dimensions["D"].width = 36
    limits = dict(zip(("月度数据", "渠道数据", "漏斗数据", "商品数据"), [len(rows) + 5 for rows in data]))
    month_end = limits["月度数据"]
    comparable = [(index + 6, row) for index, row in enumerate(monthly) if row[1] == "comparable_month"]
    workbook.defined_names.add(DefinedName("comparable_months", attr_text=f"'月度数据'!$A${comparable[0][0]}:$A${comparable[-1][0]}"))
    overview["A4"], overview["B4"] = "起始月份", date(2014, 1, 1)
    overview["D4"], overview["E4"] = "结束月份", date(2014, 12, 1)
    for address in ("B4", "E4"):
        overview[address].number_format = "yyyy-mm"
        overview[address].fill = PatternFill("solid", fgColor="FFF0CC")
        overview[address].font = Font(name=FONT, size=11, bold=True, color=BLUE)
        validation = DataValidation(type="list", formula1="comparable_months", allow_blank=False)
        validation.errorTitle, validation.error = "月份不在可比范围", "请选择下拉列表中的月份。"
        validation.showErrorMessage, validation.errorStyle = True, "stop"
        overview.add_data_validation(validation)
        validation.add(overview[address])
    overview["K4"] = "期间状态"
    overview["L4"] = f'=IF(OR(COUNTIFS(\'月度数据\'!$A$6:$A${month_end},B4,\'月度数据\'!$B$6:$B${month_end},"comparable_month")<>1,COUNTIFS(\'月度数据\'!$A$6:$A${month_end},E4,\'月度数据\'!$B$6:$B${month_end},"comparable_month")<>1,B4>E4),"请检查月份","可比月份")'
    overview.column_dimensions["L"].width = 21
    header(overview, 6, ["所选期间指标", "数值"])
    overview_metrics = [("会话数", "C", COUNT), ("转化会话数", "D", COUNT), ("订单数", "F", COUNT), ("销售额", "H", MONEY)]
    for row, (label, column, fmt) in enumerate(overview_metrics, 7):
        overview.cell(row, 1, label)
        overview.cell(row, 2, sum_formula("月度数据", month_end, column)).number_format = fmt
    for row, label, formula, fmt in (
        (11, "会话转化率", safe_rate("B8", "B7"), RATE),
        (12, "客单价", safe_rate("B10", "B9"), MONEY),
        (13, "每次访问收入", safe_rate("B16", "B7"), MONEY),
        (14, "每单件数", safe_rate("B17", "B9"), "0.0000"),
        (15, "多商品订单占比", safe_rate("B18", "B9"), RATE),
        (16, "按会话归属收入", sum_formula("月度数据", month_end, "G"), MONEY),
        (17, "商品件数", sum_formula("月度数据", month_end, "J"), COUNT),
        (18, "多商品订单数", sum_formula("月度数据", month_end, "K"), COUNT),
    ):
        overview.cell(row, 1, label)
        overview.cell(row, 2, formula).number_format = fmt
    overview["A21"] = "月度趋势包含全部 35 个可比月份，便于查看长期变化。"
    header(overview, 24, ["月份", "会话数", "订单数", "销售额", "会话转化率", "客单价", "每次访问收入", "每单件数", "多商品订单占比", "图表月份"])
    for output_row, (source_row, _) in enumerate(comparable, 25):
        refs = {key: f"'月度数据'!{key}{source_row}" for key in "CDFGHJK"}
        values = [f"='月度数据'!A{source_row}", f"={refs['C']}", f"={refs['F']}", f"={refs['H']}",
                  safe_rate(refs["D"], refs["C"]), safe_rate(refs["H"], refs["F"]), safe_rate(refs["G"], refs["C"]),
                  safe_rate(refs["J"], refs["F"]), safe_rate(refs["K"], refs["F"]), f'=TEXT(A{output_row},"yyyy-mm")']
        for col, value in enumerate(values, 1):
            overview.cell(output_row, col, value)
    table_formats(overview, 25, 59, {"A": "yyyy-mm", "B": COUNT, "C": COUNT, "D": MONEY, "E": RATE, "F": MONEY, "G": MONEY, "H": "0.0000", "I": RATE})
    overview.column_dimensions["A"].width = 28
    overview.column_dimensions["B"].width = 19
    add_chart(overview, "月度销售额（美元，全部可比月份）", 10, [4], 24, 59, "D6", kind="line", width=24, height=10)

    header(channel_view, 7, ["渠道分类", "设备", "会话数", "转化会话数", "订单数", "会话收入", "转化率", "每次访问收入", "会话占比"])
    keys = sorted({(row[2], row[3]) for row in channels})
    channel_view.column_dimensions["A"].width = 39
    for row_number, (channel, device) in enumerate(keys, 8):
        channel_view.cell(row_number, 1, channel)
        channel_view.cell(row_number, 2, device)
        for col, source in ((3, "E"), (4, "F"), (5, "G"), (6, "H")):
            channel_view.cell(row_number, col, sum_formula("渠道数据", limits["渠道数据"], source, (("C", channel), ("D", device))))
        for col, formula in ((7, safe_rate(f"D{row_number}", f"C{row_number}")), (8, safe_rate(f"F{row_number}", f"C{row_number}")), (9, safe_rate(f"C{row_number}", "'经营总览'!$B$7"))):
            channel_view.cell(row_number, col, formula)
    table_formats(channel_view, 8, len(keys) + 7, {"C": COUNT, "D": COUNT, "E": COUNT, "F": MONEY, "G": RATE, "H": MONEY, "I": RATE})
    header(channel_view, 29, ["设备", "会话数", "转化会话数", "转化率", "会话收入", "每次访问收入"])
    for row_number, device in enumerate(("desktop", "mobile"), 30):
        channel_view.cell(row_number, 1, device)
        for col, source in ((2, "E"), (3, "F"), (5, "H")):
            channel_view.cell(row_number, col, sum_formula("渠道数据", limits["渠道数据"], source, (("D", device),)))
        channel_view.cell(row_number, 4, safe_rate(f"C{row_number}", f"B{row_number}"))
        channel_view.cell(row_number, 6, safe_rate(f"E{row_number}", f"B{row_number}"))
    table_formats(channel_view, 30, 31, {"B": COUNT, "C": COUNT, "D": RATE, "E": MONEY, "F": MONEY})
    channel_view["A34"] = "candidate 类别为候选来源。缺少广告支出，不能据此计算投放回报。"
    add_chart(channel_view, "设备会话转化率", 1, [4], 29, 31, "H28", percent=True, width=18, height=9)

    header(funnel_view, 7, ["有效阶段", "整体会话数", "桌面会话数", "移动会话数", "整体推进率", "桌面推进率", "移动推进率", "移动减桌面（百分点）"])
    stages = [("全部会话", "F"), ("有页面浏览", "G"), ("商品列表", "H"), ("商品详情", "I"), ("购物车", "J"), ("配送", "K"), ("支付", "L"), ("确认页", "M")]
    for row_number, (label, column) in enumerate(stages, 8):
        funnel_view.cell(row_number, 1, label)
        for output_column, device in ((2, None), (3, "desktop"), (4, "mobile")):
            criteria = () if device is None else (("D", device),)
            funnel_view.cell(row_number, output_column, sum_formula("漏斗数据", limits["漏斗数据"], column, criteria))
        if row_number > 8:
            for result_column, count_col in ((5, "B"), (6, "C"), (7, "D")):
                funnel_view.cell(row_number, result_column, safe_rate(f"{count_col}{row_number}", f"{count_col}{row_number - 1}"))
            funnel_view.cell(row_number, 8, f'=IF(OR(F{row_number}="n.a.",G{row_number}="n.a."),"n.a.",(G{row_number}-F{row_number})*100)')
    table_formats(funnel_view, 8, 15, {"B": COUNT, "C": COUNT, "D": COUNT, "E": RATE, "F": RATE, "G": RATE, "H": '+0.00;-0.00;0.00'})
    funnel_view.column_dimensions["H"].width = 25
    funnel_view["A17"] = "各阶段分母不同，百分点差值不能相加；观察差异不证明页面故障。"
    add_chart(funnel_view, "有序购买路径的会话数", 1, [2], 7, 15, "A20", width=20, height=11)
    add_chart(funnel_view, "桌面与移动阶段推进率", 1, [6, 7], 7, 15, "H20", percent=True, width=20, height=11, ceiling=1)

    window = reports["product_analysis"]["window_days"]
    cutoff = reports["product_analysis"]["refund_observation_cutoff_at"].replace("T", " ")
    product_view["F4"], product_view["G4"] = "退款窗口", f"{window} 天"
    header(product_view, 7, ["商品", "销售额", "销售占比", "件数", "主商品件数", "非主商品件数", "合资格件数", "窗口退款件数", "窗口退款率", "已观测退款", "销售减退款", "观察不足件数"])
    names = {1: "Original Mr. Fuzzy", 2: "Forever Love Bear", 3: "Birthday Sugar Panda", 4: "Hudson River Mini bear"}
    for row_number, catalog in enumerate(reports["product_analysis"]["catalog"], 8):
        identifier = catalog["product_id"]
        product_view.cell(row_number, 1, names.get(identifier, catalog["product_name"]))
        for col, source in ((2, "H"), (4, "E"), (5, "F"), (6, "G"), (7, "K"), (8, "L"), (10, "J"), (12, "N")):
            product_view.cell(row_number, col, sum_formula("商品数据", limits["商品数据"], source, (("C", identifier),)))
        product_view.cell(row_number, 3, safe_rate(f"B{row_number}", "'经营总览'!$B$10"))
        product_view.cell(row_number, 9, safe_rate(f"H{row_number}", f"G{row_number}"))
        product_view.cell(row_number, 11, f"=B{row_number}-J{row_number}")
    table_formats(product_view, 8, 11, {"B": MONEY, "C": RATE, "D": COUNT, "E": COUNT, "F": COUNT, "G": COUNT, "H": COUNT, "I": RATE, "J": MONEY, "K": MONEY, "L": COUNT})
    for col in "BJK":
        product_view.column_dimensions[col].width = 19
    product_view.column_dimensions["A"].width = 32
    product_view["A13"] = f"截止代理：{cutoff}。最后一次退款事件不能证明此前每天均完整采集。"
    product_view["A14"] = "窗口退款率 = 完整窗口内发生正金额退款的明细数 / 合资格明细数。观察不足的明细不计入两者。"
    product_view["A15"] = "未纳入退款后成本冲回与其他经营费用。商品上市时长、季节及流量构成不同。"
    add_chart(product_view, "商品销售额（美元）", 1, [2], 7, 11, "A19", width=20, height=11)
    add_chart(product_view, f"商品 {window} 天退款明细率", 1, [9], 7, 11, "H19", percent=True, width=20, height=11)
    return workbook


def expected_cases(reports, data):
    cases = []
    for label, start, end in (("year_2014", "2014-01-01", "2014-12-01"), ("year_2013", "2013-01-01", "2013-12-01"), ("dec_2014", "2014-12-01", "2014-12-01"), ("all_comparable", "2012-04-01", "2015-02-01")):
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        rows = [row for row in data[0] if first <= row[0] <= last and row[1] == "comparable_month"]
        totals = {index: sum((Decimal(str(row[index])) for row in rows), Decimal(0)) for index in range(2, 11)}
        expected = {
            "经营总览!B7": totals[2], "经营总览!B8": totals[3], "经营总览!B9": totals[5], "经营总览!B10": totals[7],
            "经营总览!B11": ratio(totals[3], totals[2]), "经营总览!B12": ratio(totals[7], totals[5]),
            "经营总览!B13": ratio(totals[6], totals[2]), "经营总览!B14": ratio(totals[9], totals[5]),
            "经营总览!B15": ratio(totals[10], totals[5]),
        }
        funnel_rows = [row for row in data[2] if first <= row[0] <= last and row[1] == "comparable_month"]
        expected["购买漏斗!B15"] = sum(row[12] for row in funnel_rows)
        for row_number, product in enumerate(reports["product_analysis"]["catalog"], 8):
            selected = [row for row in data[3] if first <= row[0] <= last and row[1] == "comparable_month" and row[2] == product["product_id"]]
            sales = sum((Decimal(str(row[7])) for row in selected), Decimal(0))
            eligible, refunded = sum(row[10] for row in selected), sum(row[11] for row in selected)
            expected[f"商品退款!B{row_number}"] = sales
            expected[f"商品退款!G{row_number}"] = eligible
            expected[f"商品退款!I{row_number}"] = ratio(refunded, eligible) if eligible else "n.a."
        for row_number, device in enumerate(("desktop", "mobile"), 30):
            selected = [row for row in data[1] if first <= row[0] <= last and row[1] == "comparable_month" and row[3] == device]
            expected[f"渠道设备!D{row_number}"] = ratio(sum(row[5] for row in selected), sum(row[4] for row in selected))
        cases.append({"case": label, "start": start, "end": end, "expected": expected})
    return cases


def main():
    require(sys.prefix != sys.base_prefix and Path(sys.prefix).resolve().is_relative_to(ROOT / "env"), "使用项目 env/python/bin/python。")
    reports, fingerprints = load_inputs()
    data = prepare_data(reports)
    workbook = build_workbook(reports, data)
    path = OUTPUT / "ecommerce_dashboard.xlsx"
    workbook.save(path)
    saved = load_workbook(path, data_only=False)
    require(saved.sheetnames == [*VIEWS, "月度数据", "渠道数据", "漏斗数据", "商品数据"], "工作表缺失。")
    require(sum(len(sheet._charts) for sheet in saved) == 6, "图表数量不正确。")
    require(len(saved[VIEWS[0]].data_validations.dataValidation) == 2, "月份选择器缺失。")
    for sheet in saved:
        for row in sheet:
            for cell in row:
                require(cell.data_type != "e" and "#REF!" not in str(cell.value), "出现错误或无效引用。")
    qa = {
        "build_status": "BUILT", "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "workbook": path.name, "workbook_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "sources_sha256": fingerprints,
        "source_rows": dict(zip(("monthly", "channel", "funnel", "product"), map(len, data))),
        "sheet_names": saved.sheetnames, "charts": 6,
        "formula_calculation": "Excel 首次打开自动重算；构建核验不等同于 Excel 引擎验证。",
        "cases": expected_cases(reports, data),
        "limits": ["构建记录仅覆盖文件结构；公式计算和月份切换需另行验证。", "固定源数据快照，源结果更新后重新运行构建脚本，不支持手工追加源数据自动扩展。"],
    }
    (OUTPUT / "excel_validation_report.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2, default=json_value) + "\n", encoding="utf-8")
    now = qa["generated_at"]
    with (ROOT / "docs" / "project_process.md").open("a", encoding="utf-8") as handle:
        handle.write(f"\n\n### {now}：Excel 展示构建\n\n- 内容：仅读取已复核结果，生成 4 个展示视图、4 份粒度明确的汇总数据和 6 张原生图表。\n- 命令：env/python/bin/python -B src/build_excel_dashboard.py。\n- 验证：源指纹、工作表、图表、月份验证和无效引用检查通过；公式实际计算与原生 Excel 月份切换另行记录。\n- 问题与处理：openpyxl 不执行 Excel 公式，设置首次打开自动重算；不把保存成功当作原生 Excel 验证。\n")
    print(json.dumps({key: qa[key] for key in ("build_status", "workbook", "source_rows", "sheet_names", "charts", "formula_calculation")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        (OUTPUT / "excel_validation_report.json").write_text(json.dumps({
            "build_status": "FAIL", "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
            "note": "本次构建未完成。已有工作簿可能来自较早运行，请先处理终端报错。",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise
