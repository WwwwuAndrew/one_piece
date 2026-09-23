#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
board_view.py —— 参与度 + 推进成本 合并的 debug 展示（HTML 页面，浏览器打开）。

    gui_board_grouped(part, cost, groups)   一级一个标签，面板里一级在上、二级依次在下

计算在 factor/participation.py 与 factor/cost.py；复用 ui/show_data.py 的 CSS / JS。
每个板块展示最近 N 个交易日：AbsPart / RelPart / 涨幅 / AbsCost / RelCost。
"""

from __future__ import annotations

import webbrowser

from datasource.store import date_to_str

from .show_data import _CSS, _JS, _VIEW_FILE


_HINT = ('<div class="legend"><span class="hint">'
         'AbsPart = 成交额 ÷ MA20；RelPart = 相对同级（父级−自身）；涨幅 = 当日涨跌幅。'
         'AbsCost = 换手率 ÷ |涨跌幅|；RelCost = AbsCost ÷ 前 20 日中位数（&gt;1 = 比平时更费劲）。'
         '红 = 放量/涨，绿 = 缩量/跌。</span></div>')


def _ratio(v) -> str:
    """AbsPart / RelPart：>1 红、<1 绿、=1 灰。"""
    if v is None:
        return '<span class="flat">—</span>'
    cls = "up" if v > 1 else ("down" if v < 1 else "flat")
    return f'<span class="{cls}">{v:.3f}</span>'


def _pct(v) -> str:
    if v is None:
        return "—"
    cls = "up" if v > 0 else ("down" if v < 0 else "flat")
    return f'<span class="{cls}">{v * 100:+.2f}%</span>'


def _num(v, digits=2) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def _merged(part_rows, cost_rows) -> list[dict]:
    """按日期把参与度序列和成本序列拼成一张表。"""
    cmap = {r["date"]: r for r in (cost_rows or [])}
    out = []
    for p in (part_rows or []):
        c = cmap.get(p["date"]) or {}
        out.append({
            "date": p["date"], "chg": p.get("chg"),
            "abs_part": p.get("abs_part"), "rel_part": p.get("rel_part"),
            "abs_cost": c.get("abs_cost"),
            "rel_cost": c.get("rel_cost"),
        })
    return out


def _series_table(rows: list[dict]) -> str:
    if not rows:
        return ""
    head = ("<thead><tr>"
            "<th>日期</th><th>AbsPart</th><th>RelPart</th><th>涨幅</th>"
            "<th>AbsCost</th><th>RelCost</th>"
            "</tr></thead>")
    body = []
    for r in rows:
        body.append(
            "<tr>"
            f"<td>{date_to_str(r['date']) or '—'}</td>"
            f"<td>{_ratio(r['abs_part'])}</td>"
            f"<td>{_ratio(r['rel_part'])}</td>"
            f"<td>{_pct(r['chg'])}</td>"
            f"<td>{_num(r['abs_cost'])}</td>"
            f"<td>{_num(r['rel_cost'])}</td>"
            "</tr>")
    return (f'<div class="wrap"><table>{head}<tbody>{"".join(body)}</tbody></table></div>')


def _render_grouped(part, cost, groups) -> tuple[str, list[str]]:
    tabs: list[tuple[str, str, str]] = []
    missing: list[str] = []
    ref_dates: list[str] = []
    days = 0

    for parent, children in groups:
        prows = part.compute_board(parent)
        if not prows:
            missing.append(parent)
            continue
        days = max(days, len(prows))
        ref_dates += [date_to_str(r["date"]) for r in prows if r["date"]]
        pname = prows[0]["name"] or parent
        pcost = cost.compute_board(parent)

        parts = [f'<h3 class="sub">{pname}（一级）'
                 f'<span class="code">{parent}</span></h3>',
                 _series_table(_merged(prows, pcost))]

        child_blocks = []
        for child in children:
            crows = part.compute_board(child)
            if not crows:
                missing.append(child)
                continue
            cname = crows[0]["name"] or child
            ccost = cost.compute_board(child)
            child_blocks.append(f'<h3 class="sub">{cname}（二级）'
                                f'<span class="code">{child}</span></h3>'
                                + _series_table(_merged(crows, ccost)))
        if child_blocks:
            parts.append(f'<h2 class="sec">二级板块 {len(child_blocks)} 个</h2>')
            parts.extend(child_blocks)

        tabs.append((parent, f"{parent} {pname}".strip(), "".join(parts)))

    if not tabs:
        return "", missing

    buttons, panels = [], []
    for i, (key, label, body) in enumerate(tabs):
        active = " active" if i == 0 else ""
        buttons.append(f'<button class="tab{active}" data-tab="{key}" '
                       f'onclick="showTab(\'{key}\')">{label}</button>')
        panels.append(f'<div class="panel{active}" id="panel-{key}">{body}</div>')

    span = f"（{min(ref_dates)} ~ {max(ref_dates)}）" if ref_dates else ""
    title = f"参与度 + 推进成本（一级 / 二级）· 最近 {days} 个交易日{span}"
    inner = '<div class="tabs">' + "".join(buttons) + "</div>" + "".join(panels)
    return (
        '<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">'
        "<title>板块势能 · 参与度 + 成本</title><style>" + _CSS + "</style></head><body>"
        f"<h1>{title}</h1>{_HINT}{inner}"
        "<script>" + _JS + "</script></body></html>"
    ), missing


def gui_board_grouped(part, cost, groups) -> None:
    """一级一个标签，面板里一级在上、它的二级依次在下。"""
    html, missing = _render_grouped(part, cost, groups)
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
