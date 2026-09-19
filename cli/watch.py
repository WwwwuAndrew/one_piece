#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
watch.py —— 自选（watchlist）。

自选存在 data/local.sqlite（**不可重建**：既下不到也算不到，所以单独一个文件）。
板块和个股共用一张表，kind 是 'board' / 'stock'。

⚠️ watch / unwatch 只改本地状态，**一个请求都不发**。要拉数据是下一步的事
   （fetch watch / update member watch / show watch），网络动作永远由你显式触发。
"""

from __future__ import annotations

from .base import Command, Context, Target


class Watch(Command):
    """watch <代码…> —— 加入自选；不给代码就列出自选。"""

    name = "watch"

    def __init__(self, ctx: Context, codes: list[str]):
        super().__init__(ctx)
        self.codes = codes

    def run(self) -> int:
        if not self.codes:
            return ListWatch(self.ctx).run()

        rc = 0
        for raw in self.codes:
            target = Target.parse(raw)
            if target is None:
                self.console.unknown_code(raw)
                rc = 1
                continue
            self._add(target)
        if rc == 0:
            self.console.watch_hint()
        return rc

    def _add(self, target: Target) -> None:
        name = self.ctx.db.name_of(target.kind, target.code)
        if self.ctx.watch.is_watched(target.kind, target.code):
            self.console.watch_exists(target.code, target.kind)
            self.ctx.watch.watch(target.kind, target.code, name=name)   # 只刷新时间
            return
        self.ctx.watch.watch(target.kind, target.code, name=name)
        self.console.watch_added(target.code, target.kind)


class Unwatch(Command):
    """unwatch <代码…> —— 移出自选。行会保留，只是不在列表里。"""

    name = "unwatch"

    def __init__(self, ctx: Context, codes: list[str]):
        super().__init__(ctx)
        self.codes = codes

    def run(self) -> int:
        if not self.codes:
            self.console.unwatch_needs_codes()
            return 1

        rc = 0
        for raw in self.codes:
            target = Target.parse(raw)
            if target is None:
                self.console.unknown_code(raw, hint=False)
                rc = 1
                continue
            if not self.ctx.watch.is_watched(target.kind, target.code):
                self.console.watch_absent(target.code, target.kind)
                continue
            self.ctx.watch.unwatch(target.kind, target.code)
            self.console.watch_removed(target.code, target.kind)
        return rc


class ListWatch(Command):
    """watch（不带代码）—— 看当前自选，顺带告诉你每个标的本地有没有数据。"""

    name = "watch"

    def run(self) -> int:
        items = self.ctx.watch.load_watchlist()
        if not items:
            self.console.watchlist_empty()
            return 0

        counts = {w["code"]: len(self.ctx.fetcher.load(w["code"])) for w in items}
        self.console.watchlist(items, counts)
        return 0
