#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
show.py —— 展示：板块 / 个股的**因子数字表**（全部用数字，不用热力图）。

    show board                    全部二级板块（每板块 4 行：AbsPart/RelPart ·
                                  涨跌幅 · AbsCost/RelCost · 涨跌家数）；
                                  点某板块某一天 → 右侧出它的**一级母板块**并高亮同一天
    show board --code 801081.SI   该板块内所有个股（每只 3 行：AbsPart/RelPart ·
                                  RS/RS偏移 · AbsCost/RelCost），右侧固定该板块
    show board --good             show board + 筛选
    show board --code … --good    show board --code + 筛选

筛选口径（或的关系，见 factor/screen.py）：

    板块  近 3 日累计涨跌幅 < 0            ／ AbsPart 连续 3 天下跌且 < 1（钱在撤）
    个股  近 3 日累积 RS < -1%             ／ AbsPart 连续 3 天下跌且 < 1（钱在撤）

渲染在 ui/factor_table.py，因子在 factor/。这里只决定「展示哪些、筛不筛、看多少天」。
"""

from __future__ import annotations

from datasource.base import is_board_code
from factor.cost import Cost
from factor.participation import Participation
from factor.rs import RS
from ui import factor_table

from .base import Command, Context

DAYS = 10       # 默认展示最近多少个交易日（和 cli/app.py 的 --days 默认值保持一致）


class Show(Command):
    """show board [--code 板块代码] [--good] —— 板块 / 个股的因子数字表。"""

    name = "show"

    def __init__(self, ctx: Context, what: str, rest: list[str] | None = None,
                 code: str | None = None, good: bool = False, days: int = DAYS):
        super().__init__(ctx)
        self.what = what
        self.rest = list(rest or [])
        self.code = code
        self.good = good
        self.days = days

    def run(self) -> int:
        if str(self.what).strip().lower() != "board" or self.rest:
            self.console.show_only_board(" ".join([self.what, *self.rest]))
            return 1
        if self.code:
            return self._run_stocks()
        return self._run_boards()

    # -- show board [--good]：全部二级板块 ---------------------------------
    def _run_boards(self) -> int:
        tree = self.ctx.db.load_board_tree()
        codes = [r["concept"] for r in tree if r["level"] == 2]
        if not codes:
            self.console.need_tree_for_show()
            return 1
        parent_of = {r["concept"]: r["parent"] for r in tree if r["level"] == 2}

        part = Participation(db=self.ctx.db)
        cost = Cost(db=self.ctx.db)
        try:
            stats = factor_table.gui_boards(part, cost, codes, parent_of,
                                            good=self.good, days=self.days)
        except Exception as exc:
            self.console.failed(exc)
            return 1
        self.console.show_report(stats, "二级板块")
        return 0 if stats["opened"] else 1

    # -- show board --code <板块> [--good]：板块内的个股 -------------------
    def _run_stocks(self) -> int:
        code = str(self.code).strip().upper()
        if not is_board_code(code):
            self.console.bad_board_code(self.code)
            return 1
        if self.ctx.db.board_level(code) is None:
            self.console.show_no_board(code)
            return 1
        members = self.ctx.db.load_board_members(code)
        if not members:
            self.console.show_no_members(code)
            return 1

        part = Participation(db=self.ctx.db)
        cost = Cost(db=self.ctx.db)
        rs = RS(db=self.ctx.db)
        try:
            stats = factor_table.gui_stocks(part, cost, rs, code,
                                            good=self.good, days=self.days)
        except Exception as exc:
            self.console.failed(exc)
            return 1
        self.console.show_report(stats, f"{self.console.label(code, 'board')} 内的个股")
        return 0 if stats["opened"] else 1
