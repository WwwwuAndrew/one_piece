#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch.py —— 把行情拉进来。

    FetchMarket      一天的全市场日线（tushare，1 个请求），全系统的「交易日钟」
    BackfillMarket   按交易日往前补历史全市场（tushare）
    FetchBoard       板块聚合日线（**纯本地计算**，board_calc.BoardCalc）
    Fetch            按命令行参数分派到上面三个

板块行情不再联网：它从「全市场个股日线 + 成分股」本地算出来（见 datasource/board_calc.py）。
"""

from __future__ import annotations

import time

from datasource.board_calc import BoardCalc

from .base import Command, Context


class FetchMarket(Command):
    """
    fetch market —— 拉**一天**的全市场日线入库，顺手刷市值快照（2 个请求）。

    这是整套架构的地基，同时是全系统的「交易日钟」：其余「本地是不是最新」都以它为准。
    顺手做两件小事（都不影响主流程）：

        1. 个股名字字典：daily 不返回名字，缺了才拉一次 stock_basic；
        2. 市值快照（tushare daily_basic，1 个请求）：板块涨跌幅是市值加权，
           权重不能停在几周前那次 update board 上 —— 每天刷一次。**失败了只警告**，
           行情照常入库（daily_basic 限速很严，一天只该跑一次）。
    """

    name = "fetch market"

    def __init__(self, ctx: Context, trade_date: str | None = None,
                 force: bool = False):
        super().__init__(ctx)
        self.trade_date = trade_date
        self.force = force

    def run(self) -> int:
        self.console.market_start()
        try:
            r = self.ctx.fetcher.market(self.trade_date, force=self.force)
        except Exception as exc:
            self.console.failed(exc)
            return 1

        if r.skipped:
            self.console.market_skipped(r.day, r.total)
        elif r.empty:
            self.console.market_empty(r.day or self.trade_date)
            return 1
        else:
            self.console.market_done(r.day, r.fetched, r.added, not self.trade_date)

        # 个股名字字典：stock_daily 里有「stock_list 没有」的代码才拉一次，否则 0 请求。
        # 它是「字典」不是「行情」：刷不上只警告 —— 否则会出现「行情明明入库了，
        # 却因为名字没刷上而显示失败」这种误导（app 的弹框会把它放大）。
        try:
            self.ctx.fetcher.ensure_stock_names()
        except Exception as exc:
            self.console.names_failed(exc)

        # 市值快照：板块市值加权的权重。失败**不算错**（行情已经入库了），只提示
        if r.day:
            try:
                self.console.mktcap_done(self.ctx.fetcher.refresh_mktcap(r.day))
            except Exception as exc:
                self.console.mktcap_failed(exc)

        self.console.db_state()
        self.console.day_health()
        return 0


class BackfillMarket(Command):
    """
    backfill market --days N —— 补最近 N 个交易日的全市场日线。

    一天 1 个请求，本地已有的不重复拉，所以 Ctrl-C 之后重跑会接着补。
    """

    name = "backfill market"

    def __init__(self, ctx: Context, days: int, end: str | None = None,
                 force: bool = False):
        super().__init__(ctx)
        self.days = days
        self.end = end
        self.force = force

    def run(self) -> int:
        try:
            plan = self.ctx.fetcher.market_plan(self.days, end=self.end)
        except Exception as exc:
            self.console.failed(exc)
            return 1
        if not plan:
            self.console.backfill_nothing()
            return 1

        have = [p for p in plan if p["rows"]]
        todo = plan if self.force else [p for p in plan if not p["rows"]]
        interval = self.ctx.interval

        if not self.console.backfill_plan(plan, have, todo, interval, self.force):
            self.console.db_state()
            self.console.day_health()
            return 0

        done = empty = failed = 0
        added_total = 0
        for i, p in enumerate(todo, 1):
            if i > 1 and interval:
                time.sleep(interval)          # 间隔一下，别触发 tushare 频率限制
            try:
                r = self.ctx.fetcher.market(p["day"], force=self.force)
            except Exception as exc:
                failed += 1
                self.console.backfill_failed(i, len(todo), p["day"], exc)
                continue

            if r.skipped:
                continue
            if r.empty:
                empty += 1
                self.console.backfill_empty(i, len(todo), p["day"])
                continue

            done += 1
            added_total += r.added
            self.console.backfill_step(i, len(todo), p["day"], r.fetched, r.added, self.force)

        self.console.backfill_done(done, empty, failed, added_total)
        # 补完历史顺手把个股名字字典补上（缺了才拉）；同样：刷不上只警告
        try:
            self.ctx.fetcher.ensure_stock_names()
        except Exception as exc:
            self.console.names_failed(exc)
        self.console.db_state()
        self.console.day_health()
        if failed:
            self.console.backfill_retry_hint()
        return 1 if failed else 0


class FetchBoard(Command):
    """
    fetch board —— 本地计算板块聚合日线（**0 个网络请求**）。

    对 stock_daily 里每个交易日、board_daily 里还没有的，算一遍入库（幂等，可中断续跑）。
    --force 先清空 board_daily 再全量重算。
    """

    name = "fetch board"

    def __init__(self, ctx: Context, force: bool = False):
        super().__init__(ctx)
        self.force = force

    def run(self) -> int:
        self.console.board_calc_start(self.force)
        try:
            res = BoardCalc(db=self.ctx.db).compute(force=self.force)
        except Exception as exc:
            self.console.failed(exc)
            return 1
        self.console.board_calc_done(res)
        self.console.board_state()
        return 0


class Fetch(Command):
    """fetch <market|board> —— 分派。"""

    name = "fetch"

    def __init__(self, ctx: Context, what: str, date: str | None = None,
                 force: bool = False):
        super().__init__(ctx)
        self.what = what
        self.date = date
        self.force = force

    def run(self) -> int:
        if self.what == "market":
            return FetchMarket(self.ctx, self.date, self.force).run()
        return FetchBoard(self.ctx, self.force).run()
