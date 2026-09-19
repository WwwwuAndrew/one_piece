#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch.py —— 把行情拉进来（fetch market / backfill market / fetch board / fetch watch）。

共同点：都是「联网 → 入库」，区别只在标的和粒度。

    FetchMarket     一天的全市场日线（1 个请求），全系统的交易日钟
    BackfillMarket  按交易日往前补历史，本地已有的跳过，可中断续跑
    FetchBoards     一级 + 二级板块的当天快照（每个板块 1 个请求）
    FetchWatch      只刷自选
    Fetch           把上面几个按命令行参数分派出去

BatchFetcher 是 FetchBoards / FetchWatch 共用的那段「逐个拉 + 间隔 + 计数」。
"""

from __future__ import annotations

import time

from datasource.store import date_to_str

from .base import Command, Context, Freshness, Target
from .catalog import boards_at_level


class BatchFetcher:
    """把一批标的刷到最新：本地已是最新的跳过，其余带间隔逐个拉。"""

    def __init__(self, ctx: Context):
        self.ctx = ctx

    def run(self, codes: list[str], days: int | None = None,
            force: bool = False, label: str = "") -> int:
        if not codes:
            return 1

        interval = self.ctx.interval
        plan = Freshness(self.ctx.fetcher, force=force, days=days)
        todo, fresh = plan.split(codes)

        self.ctx.console.batch_header(label, len(codes), days)
        self.ctx.console.batch_plan(label, todo, fresh, interval, plan.ref)
        if not todo:
            return 0

        rc = 0
        ok = 0
        for i, code in enumerate(todo, 1):
            if i > 1 and interval:
                time.sleep(interval)          # 间隔一下，别把对方惹毛
            try:
                result = self.ctx.fetch_one(code, days=days)
            except Exception as exc:
                rc = 1
                self.ctx.console.batch_step_failed(i, len(todo), code, exc)
                continue
            rec = result.fetched[-1]
            parsed = Target.parse(code)
            self.ctx.console.batch_step(i, len(todo), code, parsed.kind if parsed else "stock",
                                        date_to_str(rec.get("trade_date")), len(result.added))
            ok += 1

        self.ctx.console.batch_done(ok, len(todo), len(fresh))
        return rc if rc else (0 if ok == len(todo) else 1)


class FetchMarket(Command):
    """
    fetch market —— 拉**一天**的全市场日线入库（1 个请求，约 5500 只）。

    这是整套架构的地基，同时是全系统的「交易日钟」：其余「本地是不是最新」都以它为准。
    """

    name = "fetch market"

    def __init__(self, ctx: Context, trade_date: str | None = None):
        super().__init__(ctx)
        self.trade_date = trade_date

    def run(self) -> int:
        self.console.market_start()
        try:
            r = self.ctx.fetcher.market(self.trade_date)
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
                time.sleep(interval)          # 间隔一下，别触发对方的频率限制
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
        self.console.db_state()
        self.console.day_health()
        if failed:
            self.console.backfill_retry_hint()
        return 1 if failed else 0


class FetchBoards(Command):
    """
    fetch board —— 一级和二级板块的行情一起更新（31 + 128 个，每个 1 个请求）。

    本地已经有当天的会跳过；level 可以只刷一层。
    """

    name = "fetch board"

    def __init__(self, ctx: Context, days: int | None = None,
                 force: bool = False, level: int | None = None):
        super().__init__(ctx)
        self.days = days
        self.force = force
        self.level = level

    def run(self) -> int:
        lv1 = boards_at_level(self.ctx.db, 1)
        lv2 = boards_at_level(self.ctx.db, 2)
        if not lv1 and not lv2:
            self.console.need_tree()
            return 1

        if self.level == 2:
            codes, what = lv2, f"二级 {len(lv2)}"
        elif self.level == 1:
            codes, what = lv1, f"一级 {len(lv1)}"
        else:
            # 一级在前，二级在后（报告看起来是分组的）
            codes, what = lv1 + lv2, f"一级 {len(lv1)} + 二级 {len(lv2)}"
        if not codes:
            self.console.no_boards_at_level(self.level)
            return 1

        if self.days:
            self.console.ignore_days()

        rc = BatchFetcher(self.ctx).run(codes, days=self.days, force=self.force,
                                        label=f"板块（{what}）")
        if rc == 0:
            self.console.board_state()
        return rc


class FetchWatch(Command):
    """fetch watch —— 只刷自选里的标的。"""

    name = "fetch watch"

    def __init__(self, ctx: Context, days: int | None = None, force: bool = False):
        super().__init__(ctx)
        self.days = days
        self.force = force

    def run(self) -> int:
        codes = self.ctx.watched()
        if not codes:
            self.console.no_watch()
            return 1
        return BatchFetcher(self.ctx).run(codes, days=self.days, force=self.force,
                                          label="自选 ")


class Fetch(Command):
    """fetch <market|board|watch|代码…> —— 按顺序处理每个参数。"""

    name = "fetch"

    def __init__(self, ctx: Context, codes: list[str], days: int | None = None,
                 trade_date: str | None = None, force: bool = False,
                 level: int | None = None):
        super().__init__(ctx)
        self.codes = codes
        self.days = days
        self.trade_date = trade_date
        self.force = force
        self.level = level

    def run(self) -> int:
        rc = 0
        for raw in self.codes:
            rc |= self._one(raw)
        return rc

    def _one(self, raw: str) -> int:
        code = raw.strip()
        keyword = code.lower()
        if keyword == "market":
            return FetchMarket(self.ctx, self.trade_date).run()
        if keyword == "board":
            return FetchBoards(self.ctx, self.days, self.force, self.level).run()
        if keyword == "watch":
            return FetchWatch(self.ctx, self.days, self.force).run()

        try:
            result = self.ctx.fetch_one(code, days=self.days)
        except Exception as exc:
            self.console.err(f"\n❌ {code}：{exc}")
            return 1
        self.console.fetch_result(result)
        return 0
