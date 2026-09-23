#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
debug.py —— 因子 debug：在最终展示形态定下来之前，先肉眼看一下算出来的数字。

    debug show board            一级一个标签，参与度 + 推进成本 一起看（浏览器）
    debug show 801080.SI        看某个板块 / 个股的参与度 + 成本（控制台）

计算在 factor/（participation + cost），分组的图形化在 ui/board_view.py。
"""

from __future__ import annotations

from datasource.base import is_board_code
from factor.cost import Cost
from factor.participation import Participation
from ui import board_view

from .base import Command, Context, Target
from .catalog import board_groups


class Debug(Command):
    """debug show <board|代码…> —— 临时查看因子数字。"""

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

        want_grouped = False
        codes: list[str] = []
        for raw in self.targets:
            text = raw.strip()
            if text.lower() == "board":
                want_grouped = True
            elif Target.parse(text):
                codes.append(text)
            else:
                self.console.show_unknown(text)
                return 1

        try:
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
                prows = part.compute_board(code) if is_board else part.compute_stock(code)
                crows = cost.compute_board(code) if is_board else cost.compute_stock(code)
                if not prows:
                    self.console.debug_no_data(code)
                    continue
                self.console.board_series(prows, crows)
        except Exception as exc:
            self.console.failed(exc)
            return 1
        return 0
