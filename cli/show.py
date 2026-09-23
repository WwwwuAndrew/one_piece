#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
show.py —— 展示。

    show hot board            热力图：全部二级板块（默认）
    show hot board --level 1  热力图：全部一级板块
    show hot 801080.SI        热力图：该一级板块下的二级板块
    show watch                只看自选（板块和个股混在一个页面里）
    show 801080.SI 300308     给具体代码，平铺

热力图在 ui/chart_view.py，行情图在 ui/show_data.py，这里只决定「展示哪些、怎么分组」。
"""

from __future__ import annotations

from datasource.base import is_board_code
from factor.cost import Cost
from factor.participation import Participation
from ui import chart_view, show_data

from .base import Command, Context, Target


class Show(Command):
    """show —— hot（热力图）/ watch / 具体代码。"""

    name = "show"

    def __init__(self, ctx: Context, codes: list[str], days: int = 15, level: int = 2):
        super().__init__(ctx)
        self.codes = codes
        self.days = days
        self.level = level

    def run(self) -> int:
        targets = [c.strip() for c in self.codes]
        if "hot" in [t.lower() for t in targets]:
            return self._run_hot([t for t in targets if t.lower() != "hot"])
        return self._run_plain(targets)

    # -- 热力图 -----------------------------------------------------------
    def _run_hot(self, targets: list[str]) -> int:
        part = Participation(db=self.ctx.db)
        cost = Cost(db=self.ctx.db)
        tree = self.ctx.db.load_board_tree()
        parents = {r["concept"]: r["parent"] for r in tree}

        try:
            if not targets or "board" in [t.lower() for t in targets]:
                # show hot board：默认二级，--level 1 转一级
                if self.level == 1:
                    codes = [r["concept"] for r in tree if r["level"] == 1]
                    parent_of = None
                else:
                    codes = [r["concept"] for r in tree if r["level"] == 2]
                    parent_of = {r["concept"]: r["parent"] for r in tree if r["level"] == 2}
                if not codes:
                    self.console.need_tree_for_show()
                    return 1
                chart_view.gui_hot(part, cost, codes, parent_of)
                return 0

            for raw in targets:
                t = Target.parse(raw)
                if not t or not is_board_code(t.code):
                    self.console.show_unknown(raw)
                    return 1
                code = t.code
                if self.ctx.db.board_level(code) == 1:
                    children = [r["concept"] for r in tree
                                if r["parent"] == code and r["level"] == 2]
                else:
                    children = [code]
                if not children:
                    self.console.debug_no_data(code)
                    return 1
                parent_of = {c: parents[c] for c in children if parents.get(c)}
                chart_view.gui_hot(part, cost, children, parent_of)
        except Exception as exc:
            self.console.failed(exc)
            return 1
        return 0

    # -- 行情图（watch / 具体代码）----------------------------------------
    def _run_plain(self, targets: list[str]) -> int:
        expanded: list[str] = []
        for raw in targets:
            text = raw.strip()
            keyword = text.lower()
            if keyword == "watch":
                watched = self.ctx.watched()
                if not watched:
                    self.console.no_watch()
                    return 1
                self.console.show_watch_all(watched)
                expanded.extend(watched)
            elif Target.parse(text):
                expanded.append(text)
            else:
                self.console.show_unknown(text)
                return 1
        if not expanded:
            self.console.err("❌ show 需要跟 hot / watch / 代码")
            return 1
        try:
            show_data.gui(expanded, days=self.days)
        except Exception as exc:
            self.console.failed(exc)
            return 1
        return 0
