#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
show_data.py —— 读取本地已存数据，展示成人类可读的列表 / 图形化表格。

只读本地数据库，不联网。对外：

    gui(codes, days=15)        把数据渲染成图形化表格，浏览器弹窗打开（hunter show 走这里）
    print_snapshot(rec)        打印单条「今天」快照（板块/个股自动识别）
    show(code, days=15)        打印某个代码最近 N 个交易日的列表（程序化用）
    show_many(codes, days=15)  打印多个代码（板块/个股可混在一起）

一个代码可以是板块（801080.SI，申万代码）或个股（300308）。

★ 单位：**记录里拿到的成交额是「元」**（数据层一律存原始单位），
  「亿 / 万」是这一层负责换算的（fmt_amount）。展示层不要再假设别的单位。
"""

from __future__ import annotations

from pathlib import Path

from .base import AMOUNT_UNIT, WAN, YI, fmt_amount
from .fetch import Fetcher, is_board_code
from .store import date_to_str

# 图形化表格的落点：固定写在项目 data/ 下（不能用 /tmp —— snap 版浏览器读不到 /tmp，
# 会报 ERR_FILE_NOT_FOUND）。每次 show 都覆盖这一个文件。
_VIEW_FILE = Path(__file__).resolve().parent.parent / "data" / "view.html"


# ---------------------------------------------------------------------------
# 从记录里取字段
# ---------------------------------------------------------------------------
# ⚠️ 两张表的**列名不一样**，展示层必须在这一层统一掉，别散到各处去：
#      board_daily: concept · change_pct · amount/volume · up/down/flat
#      stock_daily: code    · close      · pct_chg     · amount/volume
#    所以下面这几个小函数就是那座「桥」。展示层**不要直接写 rec.get("close")**——
#    读错表不会报错，只会静默显示成 "—"（数据没问题、页面是空的，最难发现的一类错）。

def _code(rec: dict) -> str:
    """记录里的代码：板块表是 concept，个股表是 code。"""
    return str(rec.get("concept") or rec.get("code") or "")


def _day(rec: dict) -> str:
    """记录里的交易日 -> "YYYY-MM-DD"（库里存的是整数 20260911）。"""
    return date_to_str(rec.get("trade_date")) or ""


def _price(rec: dict):
    """价格：板块是点位（price），个股是收盘价（close）。"""
    v = rec.get("price")
    return rec.get("close") if v is None else v


def _chg(rec: dict):
    """涨跌幅：板块叫 change_pct，个股叫 pct_chg。"""
    v = rec.get("change_pct")
    return rec.get("pct_chg") if v is None else v


# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------

def _num(v, digits: int = 2) -> str:
    return "—" if v is None else f"{v:,.{digits}f}"


def _pct(v, digits: int = 2) -> str:
    """带正负号的百分比"""
    return "—" if v is None else f"{v:+.{digits}f}%"


def _vol(v) -> str:
    return "—" if v is None else f"{v:,.0f} 股"


def _kind(code: str) -> str:
    return "板块" if is_board_code(code) else "个股"


def _kind_key(code: str) -> str:
    """代码 -> 'board' | 'stock'（base 里那套格式用这个 key）。"""
    return "board" if is_board_code(code) else "stock"


# ---------------------------------------------------------------------------
# 单条快照
# ---------------------------------------------------------------------------

def print_snapshot(rec: dict) -> None:
    """打印单条快照，板块/个股自动识别，逐行展示。"""
    code = _code(rec)
    name = rec.get("name") or "—"
    kind = _kind(code)
    key = _kind_key(code)
    line = "=" * 58

    print(f"\n{line}")
    print(f"  {code}  {name}   [{kind}]")
    print(f"  交易日：{_day(rec) or '—'}")
    print(line)

    if kind == "板块":
        up, dn, fl = rec.get("up"), rec.get("down"), rec.get("flat")
        updown = "—"
        if up is not None:
            updown = f"涨 {up} / 跌 {dn} / 平 {fl}   （共 {up + dn + fl} 只）"
        rows = [
            ("名字", name),
            ("成交额", fmt_amount(key, rec.get("amount"))),
            ("涨跌幅", _pct(_chg(rec))),
            ("涨跌家数", updown),
            ("成交量", _vol(rec.get("volume"))),
        ]
    else:
        rows = [
            ("名字", name),
            ("成交额", fmt_amount(key, rec.get("amount"))),
            ("价格", _num(_price(rec))),
            ("涨跌幅", _pct(_chg(rec))),
            ("成交量", _vol(rec.get("volume"))),
        ]

    for label, value in rows:
        print(f"    {label:<12}{value}")
    print(line)


# ---------------------------------------------------------------------------
# 历史列表
# ---------------------------------------------------------------------------

def _table(records: list[dict]) -> str:
    """把一批记录排成表格（用 tabulate 对齐，requirements 里已含）。"""
    from tabulate import tabulate

    if not records:
        return "（无数据）"

    kind = _kind(_code(records[0]))
    key = _kind_key(_code(records[0]))
    if kind == "板块":
        header = ["日期", "涨跌幅", "成交额", "涨/跌/平", "成交量"]
        rows = []
        for r in records:
            up, dn, fl = r.get("up"), r.get("down"), r.get("flat")
            udf = f"{up}/{dn}/{fl}" if up is not None else "—"
            rows.append([
                _day(r), _pct(_chg(r)), fmt_amount(key, r.get("amount")),
                udf, _vol(r.get("volume")),
            ])
    else:
        header = ["日期", "价格", "涨跌幅", "成交额", "成交量"]
        rows = []
        for r in records:
            rows.append([
                _day(r), _num(_price(r)), _pct(_chg(r)),
                fmt_amount(key, r.get("amount")), _vol(r.get("volume")),
            ])
    # disable_numparse：格子里的字符串已经是这一层格式化好的（"12,730.57" / "+1.25%"），
    # 不让 tabulate 再"聪明地"当成数字重排一遍 —— 否则千分位和小数位会被它吃掉。
    return tabulate(rows, headers=header, tablefmt="simple", stralign="right",
                    disable_numparse=True)


def show(code: str, days: int = 15) -> None:
    """展示某个代码最近 N 个交易日的本地数据。"""
    code = str(code).strip().upper()
    records = Fetcher().history(code, days=days)
    kind = _kind(code)

    if not records:
        print(f"⚠️  {code} 没有本地数据。先执行：python3 hunter.py fetch {code}")
        return

    name = records[-1].get("name") or "—"
    line = "=" * 78
    print(f"\n{line}")
    print(f"  {code}  {name}   [{kind}]  最近 {len(records)} 个交易日")
    print(line)
    print(_table(records))
    print()


def show_many(codes: list[str], days: int = 15) -> None:
    """展示多个代码（板块/个股可混）的本地数据。"""
    for c in codes:
        show(c, days=days)


# ---------------------------------------------------------------------------
# 图形化表格（浏览器弹窗）
# ---------------------------------------------------------------------------

_CSS = """
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 28px; background: #eef1f5; color: #1f2430;
       font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
h1 { font-size: 18px; margin: 0 0 4px; }
.tabs { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0 14px; }
.tab { padding: 7px 14px; font-size: 13px; border: 1px solid #d5dae2; background: #fff;
       border-radius: 7px; cursor: pointer; color: #3a4150; }
.tab:hover { border-color: #2f6fed; color: #2f6fed; }
.tab.active { background: #2f6fed; color: #fff; border-color: #2f6fed; }
.panel { display: none; }
.panel.active { display: block; }
h2.sec { font-size: 15px; margin: 18px 0 8px; color: #1f2430; }
h2.sec .lv { font-size: 11px; color: #8a93a3; font-weight: 400; margin-left: 6px; }
h3.sub { font-size: 13px; margin: 16px 0 6px; color: #2f6fed;
         border-left: 3px solid #2f6fed; padding-left: 8px; }
h3.sub .code { color: #8a93a3; font-weight: 400; font-size: 11px; margin-left: 6px; }
.wrap { overflow: auto; max-height: 72vh; border: 1px solid #e2e6ec;
        border-radius: 10px; background: #fff; }
table { border-collapse: separate; border-spacing: 0; width: 100%; font-size: 13px; }
th, td { padding: 9px 16px; border-bottom: 1px solid #eef1f5; white-space: nowrap; text-align: right; }
th { position: sticky; top: 0; background: #f4f6fa; color: #5a6270; font-weight: 600; z-index: 2; }
th:first-child, td:first-child { position: sticky; left: 0; text-align: left; }
th:first-child { background: #f4f6fa; z-index: 3; }
td:first-child { background: #fff; font-weight: 500; }
tbody tr:hover td { background: #eef4ff; }
tbody tr:hover td:first-child { background: #eef4ff; }
.up { color: #d73027; font-weight: 600; }
.down { color: #1a7f45; font-weight: 600; }
.flat { color: #8a93a3; }

/* 图形化：成交额 + 涨跌幅 柱状图 */
.chart-card { background: #fff; border: 1px solid #e2e6ec; border-radius: 10px;
              padding: 14px 18px 8px; margin-bottom: 14px; overflow-x: auto; }
.legend { display: flex; flex-wrap: wrap; align-items: center; gap: 18px;
          font-size: 12px; color: #5a6270; margin-bottom: 10px; }
.legend i.sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px;
               margin-right: 5px; vertical-align: -1px; }
.sw.amt { background: #4e79d4; }
.sw.up { background: #d73027; }
.sw.down { background: #1a7f45; }
.legend .hint { color: #98a0ad; font-size: 11px; }
.chart-svg { display: block; }
.chart-svg .grid { stroke: #eef1f5; stroke-width: 1; }
.chart-svg .zero { stroke: #b9c0cc; stroke-width: 1; stroke-dasharray: 4 3; }
.chart-svg .axis { font-size: 10px; fill: #98a0ad; }
.chart-svg .val { font-size: 9px; }
.chart-svg .val.amt { fill: #4e79d4; }
.chart-svg .val.up { fill: #d73027; }
.chart-svg .val.down { fill: #1a7f45; }
.chart-svg .val.na { fill: #c2c8d2; }
.chart-svg .date { font-size: 10px; fill: #7b8494; }
"""

_JS = """
function showTab(id){
  var bs=document.querySelectorAll('.tab');
  for(var i=0;i<bs.length;i++){bs[i].classList.toggle('active',bs[i].getAttribute('data-tab')===id);}
  var ps=document.querySelectorAll('.panel');
  for(var i=0;i<ps.length;i++){ps[i].classList.toggle('active',ps[i].id==='panel-'+id);}
}
"""


def _h_num(v) -> str:
    return "—" if v is None else f"{v:,.2f}"


def _h_amt(kind: str, v) -> str:
    """成交额单元格：板块显示亿、个股按大小显示万/亿（单位写在格子里）。"""
    return fmt_amount(kind, v)


def _h_chg(v) -> str:
    if v is None:
        return "—"
    cls = "up" if v > 0 else ("down" if v < 0 else "flat")
    return f'<span class="{cls}">{v:+.2f}%</span>'


# ---------------------------------------------------------------------------
# 图形化：成交额 + 涨跌幅 分组柱状图（手写 SVG，不依赖任何前端库 / 网络）
# ---------------------------------------------------------------------------

_C_AMT = "#4e79d4"      # 成交额：蓝
_C_UP = "#d73027"       # 涨跌幅 正：红（A 股习惯）
_C_DOWN = "#1a7f45"     # 涨跌幅 负：绿


def _chart_amount_unit(key: str, amounts: list) -> tuple[str, float]:
    """整张图统一用一个成交额单位，避免同一张图里「万 / 亿」混着标。

    传进来的是**元**。板块一律用亿；个股够 1 亿就用亿，否则用万（不然小票的柱子
    标签会是一串 0）。
    """
    m = max([a for a in amounts if a is not None], default=0) or 0
    if key == "stock" and m < YI:
        return AMOUNT_UNIT["stock"], WAN        # 万
    return AMOUNT_UNIT["board"], YI             # 亿


def _chart_svg(key: str, records: list[dict]) -> str:
    """成交额 + 涨跌幅 的分组柱状图。

    横轴 = 交易日；每个交易日一组两根柱子：**左=成交额，右=涨跌幅**。
    两者量纲不同（亿 vs %），所以各有各的刻度：左轴给成交额、右轴给涨跌幅，
    柱子顶部标数值，鼠标悬停还有完整 tooltip。
    """
    if not records:
        return ""

    n = len(records)
    amounts = [r.get("amount") for r in records]
    pcts = [_chg(r) for r in records]

    unit, div = _chart_amount_unit(key, amounts)
    amt = [None if a is None else a / div for a in amounts]
    max_amt = max([a for a in amt if a is not None], default=0) or 1
    max_pct = max([abs(p) for p in pcts if p is not None], default=0) or 1

    # 几何参数
    pad_l, pad_r, pad_t, pad_b = 62, 56, 34, 54
    group_w, bar_w, bar_gap = 48, 15, 5
    plot_w = n * group_w
    width = pad_l + plot_w + pad_r
    height = 300
    base_y = height - pad_b              # 柱子底线
    plot_h = base_y - pad_t
    zero_y = base_y - plot_h * 0.34      # 涨跌幅的 0 线
    pct_h = plot_h * 0.34                # |涨跌幅| 最大值对应的高度

    p: list[str] = []

    # 背景横网格 + 左轴（成交额）
    for frac in (0.0, 0.5, 1.0):
        y = base_y - plot_h * frac
        p.append(f'<line class="grid" x1="{pad_l}" y1="{y:.1f}" '
                 f'x2="{pad_l + plot_w}" y2="{y:.1f}"/>')
    p.append(f'<text class="axis amt" x="{pad_l - 8}" y="{base_y + 4}" text-anchor="end">0</text>')
    p.append(f'<text class="axis amt" x="{pad_l - 8}" y="{pad_t + 4}" text-anchor="end">'
             f'{max_amt:,.1f}</text>')

    # 涨跌幅的 0 线 + 右轴
    p.append(f'<line class="zero" x1="{pad_l}" y1="{zero_y:.1f}" '
             f'x2="{pad_l + plot_w}" y2="{zero_y:.1f}"/>')
    p.append(f'<text class="axis pct" x="{pad_l + plot_w + 8}" y="{zero_y - pct_h + 4}">'
             f'+{max_pct:.1f}%</text>')
    p.append(f'<text class="axis pct" x="{pad_l + plot_w + 8}" y="{zero_y + 4}">0</text>')
    p.append(f'<text class="axis pct" x="{pad_l + plot_w + 8}" y="{zero_y + pct_h + 4}">'
             f'-{max_pct:.1f}%</text>')

    for i, r in enumerate(records):
        gx = pad_l + i * group_w
        cx = gx + group_w / 2
        date = _day(r)
        tip = f"{date}"

        # 左柱：成交额
        a = amt[i]
        ax = cx - bar_gap / 2 - bar_w
        if a is not None and a > 0:
            h = max(a / max_amt * plot_h, 1.0)
            y = base_y - h
            p.append(f'<rect x="{ax:.1f}" y="{y:.1f}" width="{bar_w}" height="{h:.1f}" '
                     f'rx="2" fill="{_C_AMT}"><title>{tip} 成交额 {a:,.1f} {unit}</title></rect>')
            p.append(f'<text class="val amt" x="{ax + bar_w / 2:.1f}" y="{y - 4:.1f}" '
                     f'text-anchor="middle">{a:,.1f}</text>')
        else:
            p.append(f'<text class="val na" x="{ax + bar_w / 2:.1f}" y="{base_y - 4:.1f}" '
                     f'text-anchor="middle">—</text>')

        # 右柱：涨跌幅（从 0 线向上/向下长）
        c = pcts[i]
        px = cx + bar_gap / 2
        if c is not None:
            h = max(abs(c) / max_pct * pct_h, 1.0)
            y = zero_y - h if c >= 0 else zero_y
            color = _C_UP if c >= 0 else _C_DOWN
            p.append(f'<rect x="{px:.1f}" y="{y:.1f}" width="{bar_w}" height="{h:.1f}" '
                     f'rx="2" fill="{color}"><title>{tip} 涨跌幅 {c:+.2f}%</title></rect>')
            ly = y - 4 if c >= 0 else y + h + 11
            cls = "up" if c >= 0 else "down"
            p.append(f'<text class="val {cls}" x="{px + bar_w / 2:.1f}" y="{ly:.1f}" '
                     f'text-anchor="middle">{c:+.1f}</text>')
        else:
            p.append(f'<text class="val na" x="{px + bar_w / 2:.1f}" y="{zero_y - 4:.1f}" '
                     f'text-anchor="middle">—</text>')

        # 日期（横轴）
        mmdd = date[5:] if len(date) >= 10 else date
        p.append(f'<text class="date" x="{cx:.1f}" y="{base_y + 16}" text-anchor="end" '
                 f'transform="rotate(-45 {cx:.1f} {base_y + 16})">{mmdd}</text>')

    return (f'<svg class="chart-svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
            + "".join(p) + "</svg>")


def _legend_html(key: str, records: list[dict]) -> str:
    unit, _ = _chart_amount_unit(key, [r.get("amount") for r in records])
    return (
        '<div class="legend">'
        f'<span><i class="sw amt"></i>成交额（{unit}）</span>'
        '<span><i class="sw up"></i>涨跌幅 正（%）</span>'
        '<span><i class="sw down"></i>涨跌幅 负（%）</span>'
        '<span class="hint">横轴=交易日；每组左柱=成交额、右柱=涨跌幅。'
        '两者量纲不同，各自按自身最大值缩放（左轴成交额 / 右轴涨跌幅）</span>'
        '</div>')


def _html_table(records: list[dict]) -> str:
    """一批记录 -> 一张 HTML 表格。板块/个股自动识别。"""
    kind = "板块" if is_board_code(_code(records[0])) else "个股"
    key = _kind_key(_code(records[0]))
    if kind == "板块":
        headers = ["日期", "涨跌幅", "成交额", "涨/跌/平", "成交量"]
        rows = []
        for r in records:
            up, dn, fl = r.get("up"), r.get("down"), r.get("flat")
            udf = f"{up}/{dn}/{fl}" if up is not None else "—"
            rows.append(
                "<tr>"
                f"<td>{_day(r)}</td>"
                f"<td>{_h_chg(_chg(r))}</td>"
                f"<td>{_h_amt(key, r.get('amount'))}</td>"
                f"<td>{udf}</td>"
                f"<td>{_vol(r.get('volume'))}</td>"
                "</tr>")
    else:
        headers = ["日期", "价格", "涨跌幅", "成交额", "成交量"]
        rows = []
        for r in records:
            rows.append(
                "<tr>"
                f"<td>{_day(r)}</td>"
                f"<td>{_h_num(_price(r))}</td>"
                f"<td>{_h_chg(_chg(r))}</td>"
                f"<td>{_h_amt(key, r.get('amount'))}</td>"
                f"<td>{_vol(r.get('volume'))}</td>"
                "</tr>")
    thead = "".join(f"<th>{h}</th>" for h in headers)
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _render(codes: list[str], days: int) -> tuple[str, list[str]]:
    """生成整页 HTML，返回 (html, 无数据的代码列表)。"""
    fetcher = Fetcher()          # 所有标的一次读下来，共用同一个读取器
    tabs: list[tuple[str, str, str]] = []
    missing: list[str] = []
    for raw in codes:
        code = str(raw).strip().upper()
        try:
            records = fetcher.history(code, days=days)
        except Exception as exc:
            missing.append(f"{code}（{exc}）")
            continue
        if not records:
            missing.append(code)
            continue
        label = f"{code} {records[-1].get('name', '')}".strip()
        key = _kind_key(code)
        # 每个标的一屏：上面是成交额/涨跌幅柱状图，下面是明细表
        body = (f'<div class="chart-card">{_legend_html(key, records)}'
                f'{_chart_svg(key, records)}</div>'
                f'<div class="wrap">{_html_table(records)}</div>')
        tabs.append((code, label, body))

    if not tabs:
        return "", missing

    buttons, panels = [], []
    for i, (code, label, body) in enumerate(tabs):
        active = " active" if i == 0 else ""
        buttons.append(
            f'<button class="tab{active}" data-tab="{code}" '
            f'onclick="showTab(\'{code}\')">{label}</button>')
        panels.append(
            f'<div class="panel{active}" id="panel-{code}">{body}</div>')

    html = (
        '<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">'
        "<title>板块势能 · 数据</title><style>" + _CSS + "</style></head><body>"
        f"<h1>板块势能 · 最近 {days} 个交易日</h1>"
        '<div class="tabs">' + "".join(buttons) + "</div>"
        + "".join(panels)
        + "<script>" + _JS + "</script></body></html>"
    )
    return html, missing


# ---------------------------------------------------------------------------
# 分组展示：一个一级板块 = 一个标签，面板里先是一级自己，再依次是它的二级
# ---------------------------------------------------------------------------

def _board_block(code: str, records: list[dict], days: int,
                 heading: str | None = None) -> str:
    """一个板块的一屏：小标题（可选）+ 柱状图 + 明细表。"""
    key = _kind_key(code)
    name = records[-1].get("name") or ""
    title = heading or f"{code} {name}".strip()
    return (f'<h3 class="sub">{title}<span class="code">{code}</span></h3>'
            f'<div class="chart-card">{_legend_html(key, records)}'
            f'{_chart_svg(key, records)}</div>'
            f'<div class="wrap">{_html_table(records)}</div>')


def _render_grouped(groups: list[tuple[str, list[str]]],
                    days: int) -> tuple[str, list[str]]:
    """一级一个标签；面板里一级的数据在最上面，下面依次排它的二级。

    groups : [(一级代码, [二级代码, …]), …]
    """
    fetcher = Fetcher()
    tabs: list[tuple[str, str, str]] = []
    missing: list[str] = []

    for parent, children in groups:
        try:
            prows = fetcher.history(parent, days=days)
        except Exception as exc:
            missing.append(f"{parent}（{exc}）")
            continue
        if not prows:
            missing.append(parent)
            continue

        pname = prows[-1].get("name") or parent
        parts = [_board_block(parent, prows, days,
                             heading=f"{pname}（一级）")]
        shown = 0
        for child in children:
            try:
                crows = fetcher.history(child, days=days)
            except Exception:
                continue
            if not crows:
                missing.append(child)
                continue
            cname = crows[-1].get("name") or child
            parts.append(_board_block(child, crows, days,
                                     heading=f"{cname}（二级）"))
            shown += 1
        if shown:
            # 二级那一堆前面加一个说明，免得看不清分组
            parts.insert(1, f'<h2 class="sec">二级板块 {shown} 个'
                            f'<span class="lv">（同一级的成分股之和，会大于一级自身）</span></h2>')
        tabs.append((parent, f"{parent} {pname}", "".join(parts)))

    if not tabs:
        return "", missing

    buttons, panels = [], []
    for i, (code, label, body) in enumerate(tabs):
        active = " active" if i == 0 else ""
        buttons.append(f'<button class="tab{active}" data-tab="{code}" '
                       f'onclick="showTab(\'{code}\')">{label}</button>')
        panels.append(f'<div class="panel{active}" id="panel-{code}">{body}</div>')

    html = (
        '<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">'
        "<title>板块势能 · 一级/二级</title><style>" + _CSS + "</style></head><body>"
        f"<h1>板块势能 · 一级 / 二级（最近 {days} 个交易日）</h1>"
        '<div class="tabs">' + "".join(buttons) + "</div>"
        + "".join(panels)
        + "<script>" + _JS + "</script></body></html>"
    )
    return html, missing


def gui_grouped(groups: list[tuple[str, list[str]]], days: int = 15) -> None:
    """一级一个标签，面板里一级在上、它的二级依次在下。"""
    import webbrowser

    html, missing = _render_grouped(groups, days)
    if missing:
        print(f"⚠️  {len(missing)} 个板块本地还没有数据，已跳过（先跑："
              f"python3 hunter.py fetch board）")
        if len(missing) <= 8:
            print("      " + ", ".join(missing))
    if not html:
        return

    _VIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    _VIEW_FILE.write_text(html, encoding="utf-8")
    webbrowser.open(_VIEW_FILE.as_uri())
    print(f"已在浏览器打开：{_VIEW_FILE}")


def gui(codes: list[str], days: int = 15) -> None:
    """把本地数据渲染成图形化表格，并在默认浏览器弹窗打开。"""
    import webbrowser

    html, missing = _render(codes, days)
    for m in missing:
        print(f"⚠️  {m} 没有本地数据，已跳过（先执行：python3 hunter.py fetch {m.split('（')[0]}）")
    if not html:
        return

    _VIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    _VIEW_FILE.write_text(html, encoding="utf-8")
    webbrowser.open(_VIEW_FILE.as_uri())
    print(f"已在浏览器打开：{_VIEW_FILE}")
