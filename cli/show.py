#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
show.py —— 展示本地数据（成交额/涨跌幅柱状图 + 明细表，浏览器打开）。

三种看法：
    show board          一级一个标签，面板里一级在上、它的二级依次在下
    show watch          只看自选（板块和个股混在一个页面里）
    show BK1201 300308  给具体代码，平铺

真正的画图在 datasource/show_data.py，这里只决定「展示哪些、怎么分组」。
"""

from __future__ import annotations

from datasource import show_data

from .base import Command, Context, Target
from .catalog import board_groups


class Show(Command):
    """show —— board / watch / 具体代码。"""

    name = "show"

    def __init__(self, ctx: Context, codes: list[str], days: int = 15):
        super().__init__(ctx)
        self.codes = codes
        self.days = days

    def run(self) -> int:
        expanded, want_grouped = self._expand()
        if expanded is None:
            return 1

        try:
            if want_grouped:
                groups = board_groups(self.ctx.db)
                if not groups:
                    self.console.need_tree_for_show()
                    return 1
                self.console.show_grouped_plan(groups)
                show_data.gui_grouped(groups, days=self.days)
            if expanded:
                show_data.gui(expanded, days=self.days)
        except Exception as exc:
            self.console.failed(exc)
            return 1
        return 0

    def _expand(self) -> tuple[list[str] | None, bool]:
        """把命令行参数翻成 (要展示的代码, 要不要按一级分组)；认不出来返回 (None, False)。"""
        expanded: list[str] = []
        want_grouped = False
        for raw in self.codes:
            text = raw.strip()
            keyword = text.lower()
            if keyword == "board":
                want_grouped = True
            elif keyword == "watch":
                watched = self.ctx.watched()
                if not watched:
                    self.console.no_watch()
                    return None, False
                self.console.show_watch_all(watched)
                expanded.extend(watched)
            elif Target.parse(text):
                expanded.append(text)
            else:
                self.console.show_unknown(text)
                return None, False
        return expanded, want_grouped
