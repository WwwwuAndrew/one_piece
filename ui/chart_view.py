#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chart_view.py —— 板块热力图：行=板块、列=交易日，点击行出右侧明细。

    gui_hot(part, cost, codes, parent_of=None)   一张大热力图

每个板块占 3 行（上→下：P=Part(RelPart)、涨=涨跌幅、C=Cost(RelCost)），板块之间粗线分隔。
红=高/涨/费劲、绿=低/跌/省力，颜色上限钳在 P95。
悬浮格子看数值，点击某行三行联动高亮 + 右侧出明细（二级板块还带它的一级）。
纯手写 SVG + 原生 JS，不依赖第三方图表库。
"""

from __future__ import annotations

import json
import webbrowser

from datasource.store import date_to_str

from .show_data import _CSS, _VIEW_FILE


def _fmt(v, digits=2) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def _fmt_pct(v) -> str:
    return "—" if v is None else f"{v * 100:+.2f}%"


def _esc(s: str) -> str:
    return s.replace("'", "&#39;").replace('"', "&quot;")


# ---------------------------------------------------------------------------
# 数据准备
# ---------------------------------------------------------------------------

def _p95(vals) -> float | None:
    vs = sorted(v for v in vals if v is not None)
    if not vs:
        return None
    return vs[min(int(len(vs) * 0.95), len(vs) - 1)]


def _merged_rows(part_rows, cost_rows) -> list[dict]:
    cmap = {r["date"]: r for r in (cost_rows or [])}
    out = []
    for p in (part_rows or []):
        c = cmap.get(p["date"]) or {}
        out.append({
            "date": p["date"], "chg": p.get("chg"),
            "abs_part": p.get("abs_part"), "rel_part": p.get("rel_part"),
            "abs_cost": c.get("abs_cost"), "rel_cost": c.get("rel_cost"),
        })
    return out


def _hot_data(part, cost, codes: list[str], parent_of: dict | None) -> list[dict]:
    parent_of = parent_of or {}
    boards = []
    for code in codes:
        prows = part.compute_board(code)
        if not prows:
            continue
        b = {"code": code, "name": prows[0]["name"] or code,
             "rows": _merged_rows(prows, cost.compute_board(code)),
             "parent": None, "parent_name": None, "parent_rows": None}
        pcode = parent_of.get(code)
        if pcode:
            pp = part.compute_board(pcode)
            if pp:
                b["parent"] = pcode
                b["parent_name"] = pp[0]["name"] or pcode
                b["parent_rows"] = _merged_rows(pp, cost.compute_board(pcode))
        boards.append(b)
    return boards


# ---------------------------------------------------------------------------
# 颜色
# ---------------------------------------------------------------------------

def _heat_color(v, center, lo, hi, pos=(215, 48, 39), neg=(26, 127, 69)) -> str:
    if v is None:
        return "#eef1f5"
    if v >= center:
        t = min(max((v - center) / max(hi - center, 1e-9), 0.0), 1.0)
        return f"rgba({pos[0]},{pos[1]},{pos[2]},{t:.2f})"
    t = min(max((center - v) / max(center - lo, 1e-9), 0.0), 1.0)
    return f"rgba({neg[0]},{neg[1]},{neg[2]},{t:.2f})"


def _heat_ranges(boards):
    rel_parts = [r["rel_part"] for b in boards for r in b["rows"] if r["rel_part"] is not None]
    rel_costs = [r["rel_cost"] for b in boards for r in b["rows"] if r["rel_cost"] is not None]
    chgs = [abs(r["chg"]) for b in boards for r in b["rows"] if r["chg"] is not None]
    part_hi = max(_p95([v for v in rel_parts if v > 1]) or 1.3, 1.3)
    part_lo = min((min(rel_parts) if rel_parts else 0.6), 0.6)
    cost_hi = max(_p95([v for v in rel_costs if v > 1]) or 1.3, 1.3)
    cost_lo = min((min(rel_costs) if rel_costs else 0.6), 0.6)
    chg_hi = _p95(chgs) or 1.0
    return (part_hi, part_lo), (cost_hi, cost_lo), chg_hi


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------

_HOT_W = 900
_HNAME_W = 140
_HTAG_W = 24
_HLABEL_W = _HNAME_W + _HTAG_W
_HPLOT_W = _HOT_W - _HLABEL_W
_HCOL_W = _HPLOT_W / 10


def _heat_svg(boards, dates) -> str:
    """一张大热力图：每个板块 3 行（P / 涨 / C），板块之间粗分隔线。"""
    n = len(boards)
    row_h = 16 if n <= 40 else 10
    group_h = 3 * row_h
    gap = 6
    total_h = n * group_h + (n - 1) * gap + 28

    (part_hi, part_lo), (cost_hi, cost_lo), chg_hi = _heat_ranges(boards)
    RED, GREEN = (215, 48, 39), (26, 127, 69)
    factors = [
        ("P", "rel_part", 1.0, part_lo, part_hi),
        ("涨", "chg", 0.0, -chg_hi, chg_hi),
        ("C", "rel_cost", 1.0, cost_lo, cost_hi),
    ]

    p = []
    for i, b in enumerate(boards):
        gtop = i * (group_h + gap)
        p.append(f'<text class="hname" x="{_HNAME_W - 6}" y="{gtop + group_h / 2 + 4:.1f}" '
                 f'text-anchor="end">{(b["name"] or "")[:8]}</text>')
        for k, (tag, key, center, lo, hi) in enumerate(factors):
            y = gtop + k * row_h
            p.append(f'<text class="htag" x="{_HLABEL_W - 6}" y="{y + row_h / 2 + 3:.1f}" '
                     f'text-anchor="end">{tag}</text>')
            for j, r in enumerate(b["rows"]):
                v = r.get(key)
                col = _heat_color(v, center, lo, hi, RED, GREEN)
                x = _HLABEL_W + j * _HCOL_W
                tip = (f'<b>{b["name"]} {b["code"]}</b><br>{date_to_str(r["date"])}<br>'
                       f'AbsPart {_fmt(r["abs_part"], 3)} · RelPart {_fmt(r["rel_part"], 3)}<br>'
                       f'涨幅 {_fmt_pct(r["chg"])}<br>'
                       f'AbsCost {_fmt(r["abs_cost"])} · RelCost {_fmt(r["rel_cost"])}')
                p.append(f'<rect data-board="{b["code"]}" data-tip="{_esc(tip)}" '
                         f'x="{x:.1f}" y="{y:.1f}" width="{_HCOL_W}" height="{row_h}" '
                         f'fill="{col}" onmouseover="onPoint(event,this)" onmouseout="offPoint()" '
                         f'onclick="pick(\'{b["code"]}\')"/>')
        if i < n - 1:
            dy = gtop + group_h + gap / 2
            p.append(f'<line class="bdiv" x1="0" y1="{dy:.1f}" x2="{_HOT_W}" y2="{dy:.1f}"/>')

    for j, d in enumerate(dates):
        x = _HLABEL_W + j * _HCOL_W + _HCOL_W / 2
        p.append(f'<text class="date" x="{x:.1f}" y="{total_h - 8}" text-anchor="middle">'
                 f'{(date_to_str(d) or "")[5:]}</text>')

    return (f'<svg class="heat" width="{_HOT_W}" height="{total_h}" '
            f'viewBox="0 0 {_HOT_W} {total_h}" xmlns="http://www.w3.org/2000/svg">'
            + "".join(p) + "</svg>")


def _detail_html(b) -> str:
    head = ["日期", "AbsPart", "RelPart", "涨幅", "AbsCost", "RelCost"]

    def span_ratio(v, digits):
        if v is None:
            return '<span class="flat">—</span>'
        cls = "up" if v > 1 else ("down" if v < 1 else "flat")
        return f'<span class="{cls}">{v:.{digits}f}</span>'

    def span_pct(v):
        if v is None:
            return '<span class="flat">—</span>'
        cls = "up" if v > 0 else ("down" if v < 0 else "flat")
        return f'<span class="{cls}">{v * 100:+.2f}%</span>'

    def tbl(rows):
        trs = []
        for r in rows:
            d = date_to_str(r['date'])
            trs.append(
                f'<tr data-date="{d}" onclick="hlDate(\'{d}\')">'
                f"<td>{d}</td>"
                f"<td>{span_ratio(r['abs_part'], 3)}</td>"
                f"<td>{span_ratio(r['rel_part'], 3)}</td>"
                f"<td>{span_pct(r['chg'])}</td>"
                f"<td>{span_ratio(r['abs_cost'], 2)}</td>"
                f"<td>{span_ratio(r['rel_cost'], 2)}</td>"
                "</tr>")
        thead = "".join(f"<th>{h}</th>" for h in head)
        return f'<table><thead><tr>{thead}</tr></thead><tbody>{"".join(trs)}</tbody></table>'

    html = f'<div class="d-title">{b["name"]} {b["code"]}</div>' + tbl(b["rows"])
    if b.get("parent_rows"):
        html += (f'<div class="d-sub">← 一级 {b["parent_name"]} {b["parent"]}</div>'
                 + tbl(b["parent_rows"]))
    return html


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------

_HOT_CSS = """
.hot-wrap { display: flex; gap: 16px; align-items: flex-start; }
.hot-left { flex: 2 2 0; min-width: 0; background: #fff; border: 1px solid #e2e6ec;
            border-radius: 10px; padding: 10px; }
.hot-left svg { width: 100%; height: auto; display: block; }
.hot-right { flex: 1 1 0; min-width: 0; position: sticky; top: 10px; background: #fff;
             border: 1px solid #e2e6ec; border-radius: 10px; padding: 14px;
             max-height: 92vh; overflow: auto; }
.heat rect { cursor: pointer; }
.heat rect.dimmed { opacity: .08; }
.heat rect.active { stroke: #111; stroke-width: 1.8; }
.hname { font-size: 13px; fill: #1f2430; font-weight: 600; }
.htag { font-size: 9px; fill: #98a0ad; }
.bdiv { stroke: #c3cad4; stroke-width: 1.5; }
.date { font-size: 11px; fill: #7b8494; }
.d-title { font-size: 15px; font-weight: 600; margin-bottom: 8px; }
.d-sub { font-size: 13px; color: #2f6fed; margin: 14px 0 6px; border-left: 3px solid #2f6fed;
         padding-left: 6px; }
.d-hint { color: #98a0ad; font-size: 14px; line-height: 1.7; }
.hot-right table { font-size: 12px; width: 100%; border-collapse: collapse; }
.hot-right th, .hot-right td { padding: 4px 7px; text-align: right; border-bottom: 1px solid #eef1f5; }
.hot-right th:first-child, .hot-right td:first-child { text-align: left; }
.hot-right th { background: #f4f6fa; color: #5a6270; }
.hot-right tbody tr { cursor: pointer; }
.hot-right tr.d-row-on td { background: #fff3cd; }
#tooltip { display: none; position: absolute; z-index: 20; background: rgba(30,34,44,.94); color: #fff;
           font-size: 12px; line-height: 1.6; padding: 7px 10px; border-radius: 7px; pointer-events: none;
           max-width: 260px; }
"""

_HOT_JS = """
var cur = null;
var curDate = null;
function onPoint(evt, el){
  var t = document.getElementById('tooltip');
  t.innerHTML = el.getAttribute('data-tip');
  t.style.display = 'block';
  t.style.left = (evt.pageX + 16) + 'px';
  t.style.top = (evt.pageY + 16) + 'px';
}
function offPoint(){ document.getElementById('tooltip').style.display = 'none'; }
function hlDate(date){
  if (curDate === date) { curDate = null; } else { curDate = date; }
  document.querySelectorAll('.hot-right tr[data-date]').forEach(function(e){
    e.classList.toggle('d-row-on', e.getAttribute('data-date') === curDate);
  });
}
function pick(code){
  var cells = document.querySelectorAll('.heat rect[data-board]');
  if (cur === code) { cur = null; } else { cur = code; }
  cells.forEach(function(e){
    var on = e.getAttribute('data-board') === cur;
    e.classList.toggle('dimmed', cur !== null && !on);
    e.classList.toggle('active', on);
  });
  curDate = null;
  document.querySelectorAll('.hot-right tr[data-date]').forEach(function(e){
    e.classList.remove('d-row-on');
  });
  var d = document.getElementById('detail');
  if (cur && DETAIL[cur]) { d.innerHTML = DETAIL[cur]; }
  else { d.innerHTML = '<div class="d-hint">点击某个板块行，这里显示它的 10 天明细（二级板块还会带上它的一级）</div>'; }
}
"""


def _render_hot(boards, dates) -> str:
    svg = _heat_svg(boards, dates)
    detail = json.dumps({b["code"]: _detail_html(b) for b in boards}, ensure_ascii=False)

    hint = ('<div class="legend" style="margin-top:10px"><span class="hint" '
            'style="font-size:13px;color:#5a6270">'
            '每板块 3 行（上→下）：P=Part(RelPart)、涨=涨跌幅、C=Cost(RelCost)，粗线分隔板块，'
            '行按最近一天涨跌幅从高到低排。'
            '红=高/涨/费劲，绿=低/跌/省力，颜色上限钳在 P95。'
            '悬浮看数值，点击某行三行联动高亮 + 右侧出明细。</span></div>')

    html = (
        '<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">'
        "<title>板块势能 · 热力图</title><style>" + _CSS + _HOT_CSS + "</style></head><body>"
        f'<h1>板块热力图（{len(boards)} 个 · {date_to_str(dates[0])} ~ {date_to_str(dates[-1])}）</h1>'
        + hint + '<div class="hot-wrap"><div class="hot-left">' + svg + "</div>"
        + '<div class="hot-right" id="detail"><div class="d-hint">点击某个板块行，'
          '这里显示它的 10 天明细（二级板块还会带上它的一级）</div></div></div>'
        + '<div id="tooltip"></div>'
        + "<script>var DETAIL = " + detail + ";" + _HOT_JS + "</script></body></html>"
    )
    return html


def gui_hot(part, cost, codes: list[str], parent_of: dict | None = None) -> None:
    """热力图：行=板块、列=天。"""
    boards = _hot_data(part, cost, codes, parent_of)
    if not boards:
        print("⚠️ 没有可展示的板块数据（先跑：python3 hunter.py fetch board）")
        return
    # 行排序：最近一天涨跌幅从高到低
    boards.sort(key=lambda b: (b["rows"][-1]["chg"] or 0), reverse=True)
    dates = [r["date"] for r in boards[0]["rows"]]
    if not dates:
        print("⚠️ 没有数据")
        return
    html = _render_hot(boards, dates)
    _VIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    _VIEW_FILE.write_text(html, encoding="utf-8")
    webbrowser.open(_VIEW_FILE.as_uri())
    print(f"已在浏览器打开：{_VIEW_FILE}")
