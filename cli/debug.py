#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
debug.py —— 因子 debug：在最终展示形态定下来之前，先肉眼看一下算出来的数字。

    debug show board                一级一个标签，参与度 + 推进成本 一起看（浏览器）
    debug show stock 801080.SI      该板块内所有个股的最新一天 6 因子（控制台）
    debug show 801080.SI            看某个板块的参与度 + 成本（控制台）
    debug show 300308               看某只个股的 6 个因子（控制台）

计算在 factor/（participation + cost + rs），分组的图形化在 ui/board_view.py。
"""

from __future__ import annotations

from datasource.base import is_board_code
from factor.cost import Cost
from factor.participation import Participation
from factor.rs import RS
from ui import board_view

from .base import Command, Context, Target
from .catalog import board_groups


class Debug(Command):
    """debug show <board|stock|代码…> —— 临时查看因子数字。"""

    name = "debug"

    def __init__(self, ctx: Context, what: str, targets: list[str]):
        super().__init__(ctx)
        self.what = what
        self.targets = targets

    def run(self) -> int:
        if self.what == "show":
            return self._debug_show()
        self.console.err(f"❌ 未知的 debug 方式：{self.what}（当前支持：show）")
        return 1

    def _debug_show(self) -> int:
        part = Participation(db=self.ctx.db)
        cost = Cost(db=self.ctx.db)
        rs = RS(db=self.ctx.db)

        targets = [t.strip() for t in self.targets]
        want_grouped = False
        stock_board = None
        codes: list[str] = []
        i = 0
        while i < len(targets):
            t = targets[i]
            lt = t.lower()
            if lt == "board":
                want_grouped = True
            elif lt == "stock":
                nxt = Target.parse(targets[i + 1]) if i + 1 < len(targets) else None
                if nxt and nxt.kind == "board":
                    stock_board = nxt.code
                    i += 1
                else:
                    self.console.err("❌ debug show stock 需要跟板块代码，"
                                     "例如：python3 hunter.py debug show stock 801080.SI")
                    return 1
            elif Target.parse(t):
                codes.append(t)
            else:
                self.console.show_unknown(t)
                return 1
            i += 1

        try:
            if stock_board:
                self._stock_cross(part, cost, rs, stock_board)
                return 0

            if want_grouped:
                groups = board_groups(self.ctx.db)
                if not groups:
                    self.console.need_tree_for_show()
                    return 1
                self.console.show_grouped_plan(groups)
                board_view.gui_board_grouped(part, cost, groups)

            for raw in codes:
                code = raw.strip().upper()
                is_board = is_board_code(code)
                if is_board:
                    prows = part.compute_board(code)
                    crows = cost.compute_board(code)
                    if not prows:
                        self.console.debug_no_data(code)
                        continue
                    self.console.board_series(prows, crows)
                else:
                    prows = part.compute_stock(code)
                    crows = cost.compute_stock(code)
                    rrows = rs.compute_stock(code)
                    if not prows:
                        self.console.debug_no_data(code)
                        continue
                    self.console.stock_series(prows, crows, rrows)
        except Exception as exc:
            self.console.failed(exc)
            return 1
        return 0

    def _stock_cross(self, part, cost, rs, board_code: str) -> None:
        """一个板块内所有个股的最新一天 6 因子，按 RS 从高到低排。"""
        members = self.ctx.db.load_board_members(board_code)
        rows = []
        for code in members:
            p = part.compute_stock(code)
            if not p:
                continue
            c = (cost.compute_stock(code) or [{}])[-1]
            r = (rs.compute_stock(code) or [{}])[-1]
            rows.append({
                "code": code, "name": p[-1]["name"] or "",
                "abs_part": p[-1]["abs_part"], "rel_part": p[-1]["rel_part"],
                "rs": r.get("rs"), "rs_dev": r.get("rs_dev"),
                "abs_cost": c.get("abs_cost"), "rel_cost": c.get("rel_cost"),
            })
        rows.sort(key=lambda x: (x["rs"] if x["rs"] is not None else -999.0), reverse=True)
        self.console.stock_cross(rows)
