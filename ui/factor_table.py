#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_table.py —— 板块 / 个股的因子数字表：行 = 标的、列 = 交易日，**全部用数字说话**。

    gui_boards(part, cost, codes, parent_of, good=False, days=10)   show board [--good]
    gui_stocks(part, cost, rs, code, good=False, days=10)           show board --code … [--good]

一个标的占固定的几行，每格写这个因子当天的数字（不再用热力图的颜色深浅）：

    板块（4 行）  AbsPart/RelPart · 涨跌幅 · AbsCost/RelCost · 涨跌家数
    个股（3 行）  AbsPart/RelPart · RS/RS偏移 · AbsCost/RelCost

数字按各自的特点上色：**涨 / 高 = 红，跌 / 低 = 绿**
（比值类以 1 为界：`>1` 红 —— 钱在来、更费劲；`<1` 绿；家数则是涨红、跌绿、平灰）。

交互（原生 JS，不依赖第三方库）：

    · 点某个标的的某一天 → 这格高亮、这个标的整组高亮
    · 右侧明细表里，**同一天那一行**跟着高亮（一级和二级看的是同一天）
    · 右侧明细表**不带日期列** —— 日期看左边那几列，行按同一批交易日、同样升序排，位置一一对应
    · 板块视图：右侧换成这个二级板块的**一级母板块**（二级数据左边已经有了，不再重复）
    · 个股视图：右侧就是所在板块（固定的，不跟着点换）
    · 悬浮任意格子看这一天的全部因子（全精度）

排序：板块按最近一天涨跌幅递减；个股按最近一天 RS（0,1,2,3,4,-1,-2,-3,-4）。
页面落在 data/view.html（每次覆盖），只读本地，不联网。
"""

from __future__ import annotations

import html
import json
import webbrowser
from pathlib import Path

from datasource.store import date_to_str
from factor import screen

DAYS = 10       # 默认展示最近多少个交易日（和 cli/app.py 的 --days 默认值保持一致）

# 页面落点：固定写项目 data/ 下（不能用 /tmp —— snap 版浏览器读不到 /tmp）。
_VIEW_FILE = Path(__file__).resolve().parent.parent / "data" / "view.html"

# 每个标的占哪几行：(行标签, 这一行怎么渲染)。涨跌幅 / RS 的单位 % 写在标签上。
_ROWS_BOARD = (("AbsPart/RelPart", "part"), ("涨跌幅 %", "chg"),
               ("AbsCost/RelCost", "cost"), ("涨跌家数", "breadth"))
_ROWS_STOCK = (("AbsPart/RelPart", "part"), ("RS/RS偏移 %", "rs"),
               ("AbsCost/RelCost", "cost"))


# ---------------------------------------------------------------------------
# 数字 -> 带颜色的 <span>
# ---------------------------------------------------------------------------

def _n(v, digits: int = 2) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def _p(v, digits: int = 2) -> str:
    """小数（0.0123）-> "+1.23%"。"""
    return "—" if v is None else f"{v * 100:+.{digits}f}%"


def _na() -> str:
    return '<span class="na">—</span>'


def _ratio(v, digits: int = 2) -> str:
    """比值类因子（Part / Cost）：>1 红、<1 绿、=1 灰。"""
    if v is None:
        return _na()
    cls = "up" if v > 1 else ("down" if v < 1 else "flat")
    return f'<span class="{cls}">{v:.{digits}f}</span>'


def _pct(v, digits: int = 2, unit: str = "%") -> str:
    """涨跌幅 / RS：正红、负绿、零灰。

    unit="" 时不带单位 —— 格子里的 % 写在行标签上，一格能省两个字符（十几列省不少宽度）；
    悬浮提示和右侧明细表仍然带 %。
    """
    if v is None:
        return _na()
    cls = "up" if v > 0 else ("down" if v < 0 else "flat")
    return f'<span class="{cls}">{v * 100:+.{digits}f}{unit}</span>'


def _breadth(r) -> str:
    """涨跌家数：涨红 / 跌绿 / 平灰。"""
    up, down, flat = r.get("up"), r.get("down"), r.get("flat")
    if up is None or down is None:
        return _na()
    return (f'<span class="up">{up}</span>/<span class="down">{down}</span>'
            f'/<span class="flat">{flat or 0}</span>')


_GRID_CELL = {
    "part":    lambda r: f'{_ratio(r.get("abs_part"))}/{_ratio(r.get("rel_part"))}',
    "chg":     lambda r: _pct(r.get("chg"), unit=""),
    "cost":    lambda r: f'{_ratio(r.get("abs_cost"))}/{_ratio(r.get("rel_cost"))}',
    "breadth": _breadth,
    "rs":      lambda r: (f'{_pct(r.get("rs"), unit="")}'
                          f'/{_pct(r.get("rs_dev"), unit="")}'),
}


# ---------------------------------------------------------------------------
# 因子序列 -> 展示用的行（只做拼接，不改口径）
# ---------------------------------------------------------------------------

def _merge_board(part_rows, cost_rows, board_rows) -> list[dict]:
    cmap = {r["date"]: r for r in (cost_rows or [])}
    bmap = {r["trade_date"]: r for r in (board_rows or [])}
    out = []
    for p in part_rows or []:
        c = cmap.get(p["date"]) or {}
        b = bmap.get(p["date"]) or {}
        out.append({
            "date": p["date"], "chg": p.get("chg"),
            "abs_part": p.get("abs_part"), "rel_part": p.get("rel_part"),
            "abs_cost": c.get("abs_cost"), "rel_cost": c.get("rel_cost"),
            "up": b.get("up"), "down": b.get("down"), "flat": b.get("flat"),
        })
    return out


def _merge_stock(part_rows, cost_rows, rs_rows) -> list[dict]:
    cmap = {r["date"]: r for r in (cost_rows or [])}
    rmap = {r["date"]: r for r in (rs_rows or [])}
    out = []
    for p in part_rows or []:
        c = cmap.get(p["date"]) or {}
        s = rmap.get(p["date"]) or {}
        out.append({
            "date": p["date"],
            "abs_part": p.get("abs_part"), "rel_part": p.get("rel_part"),
            "rs": s.get("rs"), "rs_dev": s.get("rs_dev"),
            "abs_cost": c.get("abs_cost"), "rel_cost": c.get("rel_cost"),
        })
    return out


def _board_rows(part, cost, code, days) -> list[dict] | None:
    prows = part.compute_board(code, days=days)
    if not prows:
        return None
    return _merge_board(prows, cost.compute_board(code, days=days),
                        part.db.load_board_daily(code, days=days))


def _stock_rows(part, cost, rs, code, days) -> list[dict] | None:
    prows = part.compute_stock(code, days=days)
    if not prows:
        return None
    return _merge_stock(prows, cost.compute_stock(code, days=days),
                        rs.compute_stock(code, days=days))


def _module(db, code: str, kind: str, rows: list[dict], tag: str = "") -> dict:
    return {"code": code, "name": db.name_of(kind, code) or code,
            "tag": tag, "rows": rows}


def _date_axis(modules: list[dict], days: int) -> list[int]:
    """横轴 = 所有标的的交易日并集里最近 days 天（某天缺数的格子留空）。"""
    seen: set[int] = set()
    for m in modules:
        seen.update(r["date"] for r in m["rows"])
    return sorted(seen)[-days:]


# ---------------------------------------------------------------------------
# 排序
# ---------------------------------------------------------------------------

def _last_row(m: dict, date: int | None = None) -> dict | None:
    """排序看的那一行：默认是「最后一天」（表格最后一列）那一行，标的缺这天就用它自己的最后一行。"""
    if date is not None:
        return {r["date"]: r for r in m["rows"]}.get(date)
    return m["rows"][-1] if m["rows"] else None


def _chg_key(m: dict, date: int | None = None) -> float:
    """板块：按最后一天涨跌幅排序（递减）。"""
    r = _last_row(m, date)
    v = r.get("chg") if r else None
    return float("-inf") if v is None else v


def _rs_key(m: dict, date: int | None = None) -> tuple[int, float]:
    """个股：以 RS=0 起，先正数递增（0,1,2,3,4），再接负数递减（-1,-2,-3,-4）。

    = 先看「跑赢的、超额少的在前」，再看「跑输的、跑输少的在前」。
    """
    r = _last_row(m, date)
    v = r.get("rs") if r else None
    if v is None:
        return (2, 0.0)
    return (0, v) if v >= 0 else (1, -v)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_CSS = """
* { box-sizing: border-box; }
body { margin: 0; padding: 16px 20px 40px; background: #eef1f5; color: #1f2430;
       font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
h1 { font-size: 19px; margin: 0 0 6px; }
.legend { font-size: 12.5px; color: #5a6270; line-height: 1.8; margin: 0 0 12px; }
.legend b { color: #1f2430; }
.wrap { display: flex; gap: 14px; align-items: flex-start; }
.left { flex: 1 1 auto; min-width: 0; background: #fff; border: 1px solid #e2e6ec;
        border-radius: 10px; padding: 6px 10px 10px; overflow: auto; max-height: 90vh;
        overscroll-behavior: contain; }
.right { flex: 0 0 420px; background: #fff; border: 1px solid #e2e6ec; border-radius: 10px;
         padding: 12px 14px 14px; max-height: 90vh; overflow: auto;
         position: sticky; top: 12px; }

table { border-collapse: separate; border-spacing: 0; }
.grid { font-size: 15px; font-variant-numeric: tabular-nums; }
.grid th, .grid td { padding: 3px 6px; text-align: right; white-space: nowrap; }
.grid thead th { position: sticky; top: 0; z-index: 2; background: #f4f6fa; color: #5a6270;
                 font-size: 12.5px; font-weight: 600; border-bottom: 1px solid #dfe4ec; }
.grid th.c-name, .grid td.c-name { position: sticky; left: 0; z-index: 3; text-align: left;
                 width: 128px; min-width: 128px; max-width: 128px; overflow: hidden;
                 text-overflow: ellipsis; background: #fff; border-right: 1px solid #eef1f5; }
.grid th.c-label, .grid td.c-label { position: sticky; left: 128px; z-index: 3; text-align: left;
                 width: 118px; min-width: 118px; max-width: 118px; background: #fff;
                 color: #98a0ad; font-size: 12px; border-right: 1px solid #eef1f5; }
.grid thead th.c-name, .grid thead th.c-label { z-index: 4; background: #f4f6fa; }
.grid td.c-name b { display: block; font-size: 15px; font-weight: 600; }
.grid .c-code { display: block; color: #98a0ad; font-size: 11px; }
.grid tbody tr.g-start td { border-top: 1px solid #cbd3e0; }
.grid tbody tr:hover td { background: #f7f9fd; }
.grid td.cell { cursor: pointer; }
.grid tr.on td, .grid tr.on td.c-name, .grid tr.on td.c-label { background: #eaf1ff; }
.grid td.sel { background: #ffe9a8 !important; box-shadow: inset 0 0 0 2px #e0a800; }

.right table { font-size: 14px; width: 100%; font-variant-numeric: tabular-nums; }
.right th, .right td { padding: 5px 6px; text-align: right; white-space: nowrap;
                       border-bottom: 1px solid #eef1f5; }
.right th:first-child, .right td:first-child { text-align: left; }
.right thead th { position: sticky; top: 0; background: #f4f6fa; color: #5a6270; font-weight: 600; }
.right tbody tr.on td { background: #fff3cd; }
.d-title { font-size: 15px; font-weight: 600; margin-bottom: 8px; }
.d-title .code { color: #8a93a3; font-weight: 400; font-size: 12px; margin-left: 6px; }
.d-title .tag { color: #2f6fed; font-weight: 400; font-size: 12px; margin-left: 6px; }
.hint { color: #98a0ad; font-size: 13.5px; line-height: 1.9; }
.up { color: #d73027; font-weight: 600; }
.down { color: #1a7f45; font-weight: 600; }
.flat { color: #8a93a3; }
.na { color: #c2c8d2; }
#tooltip { display: none; position: fixed; z-index: 30; background: rgba(30,34,44,.94); color: #fff;
           font-size: 12.5px; line-height: 1.75; padding: 8px 11px; border-radius: 7px;
           pointer-events: none; white-space: pre-line; max-width: 330px; }
"""

_JS = """
/* 因子表前端：悬浮提示 + 点选高亮 + 右侧联动。
   命令行生成的静态页和 app 用的是**同一份**（app 直接从 /static/table.js 取），
   所以两边行为永远一致，不会各写一套。 */

var FT = { cur: null, curDate: null, detail: null, parentOf: null, box: null, box0: '',
           names: {}, mode: 'boards' };

function ftCell(evt){ return evt.target.closest ? evt.target.closest('td.cell') : null; }
function ftShowTip(evt, td){
  var t = document.getElementById('tooltip');
  t.textContent = td.getAttribute('data-tip');
  t.style.display = 'block';
  ftMoveTip(evt);
}
function ftMoveTip(evt){
  var t = document.getElementById('tooltip');
  if (t.style.display !== 'block') { return; }
  t.style.left = Math.min(evt.clientX + 16, window.innerWidth - 300) + 'px';
  t.style.top = Math.min(evt.clientY + 14, window.innerHeight - 140) + 'px';
}
function ftHideTip(){ document.getElementById('tooltip').style.display = 'none'; }

/* 每次换表都要调一次（app 里表格内容会被替换）。
   事件只往 #grid 上绑一次（委派），不给上万个格子各写一份 onclick。 */
function initFactorTable(cfg){
  var grid = document.getElementById('grid');
  FT.detail = cfg.detail || null;
  FT.parentOf = (cfg.parentOf === undefined) ? null : cfg.parentOf;
  FT.names = cfg.names || {};
  FT.mode = cfg.mode || 'boards';
  FT.box = document.getElementById('detail');
  FT.box0 = FT.box.innerHTML;
  FT.cur = null; FT.curDate = null;
  if (grid && grid.getAttribute('data-bound') !== '1') {
    grid.setAttribute('data-bound', '1');
    grid.addEventListener('mouseover', function(e){
      var td = ftCell(e); if (td && td.getAttribute('data-tip')) { ftShowTip(e, td); }
    });
    grid.addEventListener('mousemove', ftMoveTip);
    grid.addEventListener('mouseout', function(e){ if (ftCell(e)) { ftHideTip(); } });
    grid.addEventListener('click', function(e){ var td = ftCell(e); if (td) { ftPick(td); } });
  }
  ftHideTip();
}

function ftPick(el){
  var b = el.getAttribute('data-b'), d = el.getAttribute('data-d');
  if (FT.cur === b && FT.curDate === d) { FT.cur = null; FT.curDate = null; }
  else { FT.cur = b; FT.curDate = d; }

  document.querySelectorAll('.grid tbody tr').forEach(function(tr){
    tr.classList.toggle('on', FT.cur !== null && tr.getAttribute('data-b') === FT.cur);
  });
  document.querySelectorAll('.grid td.cell').forEach(function(td){
    td.classList.toggle('sel', FT.cur !== null && td.getAttribute('data-b') === FT.cur
                                && td.getAttribute('data-d') === FT.curDate);
  });

  if (FT.cur === null) {
    FT.box.innerHTML = FT.box0;
  } else {
    if (FT.parentOf !== null) {          /* 板块视图：右侧换成它的一级母板块 */
      var p = FT.parentOf[FT.cur];
      FT.box.innerHTML = (p && FT.detail[p]) ? FT.detail[p]
                       : '<div class="hint">这个板块没有一级母板块</div>';
    }
    FT.box.querySelectorAll('tr[data-d]').forEach(function(tr){
      tr.classList.toggle('on', tr.getAttribute('data-d') === FT.curDate);
    });
  }
  /* app 的外壳用这个钩子显示「查看该板块个股」；命令行静态页没定义它，就是个空操作 */
  if (typeof onFactorPick === 'function') {
    onFactorPick(FT.cur, FT.curDate, FT.cur === null ? '' : (FT.names[FT.cur] || ''));
  }
}
"""

# app 服务要按文件发出去，取个明确的名字（单份来源，见 _JS 的注释）
TABLE_CSS = _CSS
TABLE_JS = _JS


def _tip(m: dict, r: dict, mode: str) -> str:
    """悬浮提示：纯文本多行（JS 用 textContent 塞进去，不解析 HTML）。"""
    lines = [f'{m["name"]} {m["code"]}   {date_to_str(r["date"])}']
    lines.append(f'AbsPart {_n(r.get("abs_part"), 3)} · RelPart {_n(r.get("rel_part"), 3)}')
    if mode == "boards":
        lines.append(f'涨跌幅 {_p(r.get("chg"))}')
        # 成本给全精度：规则②判定的是 AbsCost < 1，2 位小数会把 0.998 显示成 1.00
        lines.append(f'AbsCost {_n(r.get("abs_cost"), 4)} · '
                     f'RelCost {_n(r.get("rel_cost"), 4)}')
        lines.append(f'涨 {r.get("up")} / 跌 {r.get("down")} / 平 {r.get("flat")}')
    else:
        lines.append(f'RS {_p(r.get("rs"))} · RS偏移 {_p(r.get("rs_dev"))}')
        lines.append(f'AbsCost {_n(r.get("abs_cost"), 4)} · '
                     f'RelCost {_n(r.get("rel_cost"), 4)}')
    return "\n".join(lines)


def _grid(modules: list[dict], dates: list[int], rows_spec, mode: str) -> str:
    """左侧主表：一个标的 rows_spec 行，列 = 交易日。"""
    head = [f'<th class="c-name">{"板块" if mode == "boards" else "个股"}</th>',
            '<th class="c-label">指标</th>']
    head += [f'<th>{date_to_str(d)[5:]}</th>' for d in dates]

    body = []
    for m in modules:
        rmap = {r["date"]: r for r in m["rows"]}
        for k, (label, kind) in enumerate(rows_spec):
            tds = []
            if k == 0:
                tds.append(f'<td class="c-name" rowspan="{len(rows_spec)}">'
                           f'<b>{m["name"]}</b>'
                           f'<span class="c-code">{m["code"]}</span></td>')
            tds.append(f'<td class="c-label">{label}</td>')
            for d in dates:
                r = rmap.get(d)
                if r is None:
                    tds.append('<td class="cell"></td>')
                    continue
                tip = html.escape(_tip(m, r, mode), quote=True)
                tds.append(
                    f'<td class="cell" data-b="{m["code"]}" data-d="{d}" data-tip="{tip}">'
                    f'{_GRID_CELL[kind](r)}</td>')
            cls = ' class="g-start"' if k == 0 else ""
            body.append(f'<tr{cls} data-b="{m["code"]}">{"".join(tds)}</tr>')

    return ('<table class="grid"><thead><tr>' + "".join(head) + "</tr></thead><tbody>"
            + "".join(body) + "</tbody></table>")


def _detail_columns(mode: str):
    """右侧明细表除日期外的列：(表头, 单元格渲染)。"""
    cols = [("AbsPart", lambda r: _ratio(r.get("abs_part"), 3)),
            ("RelPart", lambda r: _ratio(r.get("rel_part"), 3))]
    if mode == "boards":
        cols += [("涨跌幅", lambda r: _pct(r.get("chg")))]
    else:
        cols += [("RS", lambda r: _pct(r.get("rs"))),
                 ("RS偏移", lambda r: _pct(r.get("rs_dev")))]
    cols += [("AbsCost", lambda r: _ratio(r.get("abs_cost"), 3)),
             ("RelCost", lambda r: _ratio(r.get("rel_cost"), 3))]
    if mode == "boards":
        cols += [("涨/跌/平", _breadth)]
    return cols


def _detail_table(m: dict, dates: list[int], mode: str) -> str:
    """右侧明细表：一行一个交易日（点某天时高亮的就是这一行）。

    **不带日期列** —— 左侧那张表已经有日期、点一下就高亮到这里对应的那一行；
    行和列是同一批交易日、同样升序，按位置就能对上。想确认是哪天，悬浮那一行看 title。
    """
    cols = _detail_columns(mode)
    rmap = {r["date"]: r for r in m["rows"]}
    head = "<tr>" + "".join(f"<th>{h}</th>" for h, _ in cols) + "</tr>"
    body = []
    for d in dates:
        r = rmap.get(d)
        tds = "".join(f"<td>{fn(r) if r else _na()}</td>" for _, fn in cols)
        body.append(f'<tr data-d="{d}" title="{date_to_str(d)}">{tds}</tr>')
    tag = f'<span class="tag">{m["tag"]}</span>' if m.get("tag") else ""
    return (f'<div class="d-title">{m["name"]}<span class="code">{m["code"]}</span>{tag}</div>'
            f'<table><thead>{head}</thead><tbody>{"".join(body)}</tbody></table>')


def _page(v: dict) -> str:
    """把一份「视图素材」（boards_fragment / stocks_fragment 的产物）拼成独立 HTML 页。"""
    cfg = {"detail": v["detail"], "parentOf": v["parent_of"],
           "names": v.get("names") or {}, "mode": v["mode"]}
    js = (f"var FT_CFG = {json.dumps(cfg, ensure_ascii=False)};\n"
          f"initFactorTable(FT_CFG);")
    return ('<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">'
            f"<title>{v['title']}</title><style>{_CSS}</style></head><body>"
            f"<h1>{v['title']}</h1>{v['legend']}"
            f'<div class="wrap"><div class="left" id="grid">{v["grid"]}</div>'
            f'<div class="right" id="detail">{v["right"]}</div></div>'
            f'<div id="tooltip"></div><script>{_JS}\n{js}</script></body></html>')


def _open(html_text: str) -> None:
    _VIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    _VIEW_FILE.write_text(html_text, encoding="utf-8")
    webbrowser.open(_VIEW_FILE.as_uri())
    print(f"已在浏览器打开：{_VIEW_FILE}")


def _span(dates: list[int]) -> str:
    return f"{date_to_str(dates[0])} ~ {date_to_str(dates[-1])}" if dates else "—"


def _empty(reason: str, total: int, missing: list) -> dict:
    return {"ok": False, "reason": reason, "grid": "", "detail": {}, "parent_of": None,
            "names": {}, "mode": "boards", "title": "", "legend": "", "right": "",
            "stats": {"total": total, "kept": 0, "missing": missing, "opened": False,
                      "span": ""}}


# ---------------------------------------------------------------------------
# 对外：视图素材（app 和命令行共用）
# ---------------------------------------------------------------------------

def boards_fragment(part, cost, codes: list[str], parent_of: dict | None = None,
                    good: bool = False, days: int = DAYS) -> dict:
    """show board [--good] 的**视图素材**：全部二级板块（每板块 4 行）+ 一级母板块明细表。

    返回的 dict 里就是渲染要用的东西（grid/legend/detail/names/stats…）；
    命令行拿它拼成独立页面（`_page`），app 拿它填进自己的外壳 —— 渲染只有这一份。
    没有可展示的数据时 `ok=False`，`reason` 说明原因。
    """
    db = part.db
    parent_of = parent_of or {}
    modules, parents, missing = [], {}, []

    for code in codes:
        rows = _board_rows(part, cost, code, days)
        if not rows:
            missing.append(code)
            continue
        modules.append(_module(db, code, "board", rows,
                               "二级" if db.board_level(code) == 2 else "一级"))
        pcode = parent_of.get(code)
        if pcode and pcode not in parents:
            prows = _board_rows(part, cost, pcode, days)
            if prows:
                parents[pcode] = _module(db, pcode, "board", prows, "一级")

    total = len(modules)
    if not modules:
        return _empty("本地还没有板块数据（先拉一次行情 + 计算板块）", total, missing)

    dates = _date_axis(modules, days)      # 横轴先定下来：排序和 --good 都按同一批交易日
    last = dates[-1] if dates else None
    modules.sort(key=lambda m: _chg_key(m, last), reverse=True)
    if good:
        modules = [m for m in modules
                   if not screen.board_rejected(m["rows"], dates=dates)]
    if not modules:
        return _empty("--good 之后一个板块都没剩下", total, missing)

    detail = {c: _detail_table(m, dates, "boards") for c, m in parents.items()}
    legend = (
        '<div class="legend">'
        f'<b>{len(modules)} 个板块</b> · {_span(dates)}'
        + (f'（--good：{total} → {len(modules)}，已去掉「近 3 日累计涨跌幅 &lt; 0」'
           f'或「AbsPart 连续 3 天下跌且 &lt; 1」的）' if good else "")
        + '<br>每板块 4 行：<b>AbsPart/RelPart</b>（左=绝对、右=相对，&gt;1 红=钱在来）、'
          '<b>涨跌幅</b>（%）、<b>AbsCost/RelCost</b>（&gt;1 红=更费劲）、'
          '<b>涨跌家数</b>（涨/跌/平，涨红跌绿）。'
          '排序 = 最近一天涨跌幅递减。'
          '格子里的数字是 2 位小数（颜色按原值上色），悬浮看全精度。<br>'
          '点某板块某一天 → 这格高亮，右侧出它的一级母板块并高亮同一天；'
          '再点一次取消。右侧表不带日期列，行和左边的日期列一一对应。'
          '悬浮看这一天全部因子。</div>')

    right = ('<div class="hint">点左侧某个板块的某一天：<br>'
             '· 这格高亮、这个板块整组高亮<br>'
             '· 右边换成它的<b>一级母板块</b>（二级数据左边已经有了）<br>'
             '· 一级表里同一天那一行跟着高亮（一行一天，按左到右的顺序排，不带日期列）</div>')
    return {
        "ok": True, "mode": "boards",
        "title": f"板块因子 · 最近 {len(dates)} 个交易日",
        "legend": legend, "right": right,
        "grid": _grid(modules, dates, _ROWS_BOARD, "boards"),
        "detail": detail, "parent_of": parent_of,
        "names": {m["code"]: m["name"] for m in modules},
        "stats": {"total": total, "kept": len(modules), "missing": missing,
                  "opened": True, "span": _span(dates)},
    }


def stocks_fragment(part, cost, rs, code: str, good: bool = False,
                    days: int = DAYS) -> dict:
    """show board --code 板块代码 [--good] 的视图素材：板块内所有个股（每股 3 行）。"""
    db = part.db
    members = db.load_board_members(code)
    if not members:
        return _empty(f"{code} 本地没有成分股名单（先跑：update board）", 0, [])

    modules, missing = [], []
    for mcode in members:
        rows = _stock_rows(part, cost, rs, mcode, days)
        if not rows:
            missing.append(mcode)
            continue
        modules.append(_module(db, mcode, "stock", rows))

    total = len(modules)
    if not modules:
        return _empty(f"{code} 内的个股本地都还没有行情", total, missing)

    dates = _date_axis(modules, days)      # 横轴先定下来：排序和 --good 都按同一批交易日
    last = dates[-1] if dates else None
    modules.sort(key=lambda m: _rs_key(m, last))
    if good:
        modules = [m for m in modules
                   if not screen.stock_rejected(m["rows"], dates=dates)]
    if not modules:
        return _empty("--good 之后一只都没剩下", total, missing)

    bname = db.name_of("board", code) or code
    btag = "二级" if db.board_level(code) == 2 else "一级"
    legend = (
        '<div class="legend">'
        f'<b>{bname} {code}</b> 内 {len(modules)} 只个股 · {_span(dates)}'
        + (f'（--good：{total} → {len(modules)}，已去掉「近 3 日累积 RS &lt; -1%」'
           f'或「AbsPart 连续 3 天下跌且 &lt; 1」的）' if good else "")
        + '<br>每只 3 行：<b>AbsPart/RelPart</b>（&gt;1 红=钱在来）、'
          '<b>RS/RS偏移</b>（%，正红=跑赢板块/跑赢在加强）、'
          '<b>AbsCost/RelCost</b>（&gt;1 红=更费劲）。<br>'
          '排序 = 最近一天 RS（0,1,2,3,4 → -1,-2,-3,-4）。'
          '点某只票某一天 → 这格高亮，右侧板块表里同一天那一行跟着高亮'
          '（一行一天，按左到右的顺序排，不带日期列）。悬浮看这一天全部因子。</div>')

    right = _detail_table(_module(db, code, "board", _board_rows(part, cost, code, days) or [],
                                  btag), dates, "boards")
    return {
        "ok": True, "mode": "stocks",
        "title": f"{bname} · 成分股因子 · 最近 {len(dates)} 个交易日",
        "legend": legend, "right": right,
        "grid": _grid(modules, dates, _ROWS_STOCK, "stocks"),
        "detail": {}, "parent_of": None,
        "names": {m["code"]: m["name"] for m in modules},
        "board": {"code": code, "name": bname},
        "stats": {"total": total, "kept": len(modules), "missing": missing,
                  "opened": True, "span": _span(dates)},
    }


# ---------------------------------------------------------------------------
# 命令行入口：素材 -> 独立页面 -> 打开浏览器
# ---------------------------------------------------------------------------

def gui_boards(part, cost, codes: list[str], parent_of: dict | None = None,
               good: bool = False, days: int = DAYS) -> dict:
    """show board [--good]：出页面。

    返回 {"total", "kept", "missing", "opened", "span"}，给命令层报告用。
    """
    v = boards_fragment(part, cost, codes, parent_of, good=good, days=days)
    if v["ok"]:
        _open(_page(v))
    return v["stats"]


def gui_stocks(part, cost, rs, code: str, good: bool = False, days: int = DAYS) -> dict:
    """show board --code 板块代码 [--good]：出页面。

    返回 {"total", "kept", "missing", "opened", "span"}，给命令层报告用。
    """
    v = stocks_fragment(part, cost, rs, code, good=good, days=days)
    if v["ok"]:
        _open(_page(v))
    return v["stats"]
