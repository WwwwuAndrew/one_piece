#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
catalog.py —— 板块定义：层级表 + 成分股。

    update board   拉最新申万一/二级板块 + 成分股（乐咕），更新数据库
    drop board     删掉某个板块的本地数据
"""

from __future__ import annotations

import time

from datasource.base import is_board_code
from datasource.board_tree import BoardTree
from datasource.fetch_legu import LeguSource
from datasource.store import today_int

from .base import Command, Context


class UpdateBoard(Command):
    """
    update board —— 拉最新申万一/二级板块 + 成分股，更新数据库。

    一次做三件事：
        1. 拉乐咕层级 -> 整块重建 board_tree（快照，增/删/改都反映在这一次覆盖里）；
        2. 逐个板块拉成分股 -> 整块替换 concept_member（顺带存市值权重）；
        3. 清掉「这次树里没有」的旧板块（申万删掉的板块，连同它的行情/成员/字典一起删）。

    幂等：今天同步过的板块跳过（--force 才重拉），板块之间按 fetch_interval 间隔。
    """

    name = "update board"

    def __init__(self, ctx: Context, force: bool = False):
        super().__init__(ctx)
        self.force = force

    def run(self) -> int:
        tree = BoardTree(db=self.ctx.db)
        self.console.update_board_start()
        try:
            res = tree.build()
        except Exception as exc:
            self.console.failed(exc)
            return 1
        if not res.nodes:
            self.console.err("❌ 一个板块都没建出来，先看看上面的报错")
            return 1

        # 1) 层级表整块重建（快照）
        n = tree.save(res)
        # 名字也写进 board_list（字典），这样 show/自选能取到板块名
        self.ctx.db.upsert_board_list([{"concept": x.code, "name": x.name}
                                       for x in res.nodes])

        # 2) 成分股
        synced, failed, skipped = self._sync_members(res.nodes)

        # 3) 删掉这次树里没有的旧板块
        removed = self._prune_removed(res.nodes)

        self.console.update_board_done(n, res.counts, synced, failed, skipped, removed)
        self.console.member_state()
        return 1 if failed else 0

    # -- 成分股 -----------------------------------------------------------
    def _sync_members(self, nodes) -> tuple[int, int, int]:
        src = LeguSource()
        now = today_int()
        synced_at = {r["concept"]: r["member_updated_at"]
                     for r in self.ctx.db.load_board_list()}
        interval = self.ctx.legu_interval          # 乐咕限流严，用更慢的间隔

        synced = failed = skipped = 0
        total = len(nodes)
        for i, node in enumerate(nodes, 1):
            # 幂等：今天已同步的跳过（断点续跑 / 避免同一天重复拉）。
            # 注意 member_updated_at 存的是「日期」—— 所以明天再跑 = 全部重新拉最新；
            # 当天想强制全量重拉 = 加 --force。
            if not self.force and synced_at.get(node.code) == now:
                skipped += 1
                self.console.member_skip(i, total, node.code, node.name)
                continue
            if i > 1 and interval:
                time.sleep(interval)
            try:
                members = src.fetch_members(node.code)
            except Exception as exc:
                failed += 1
                self.console.member_step_failed(i, total, node.code, exc)
                continue
            old = set(self.ctx.db.load_board_members(node.code))
            new = {m["code"] for m in members}
            self.ctx.db.replace_board_members(node.code, members, updated_at=now)
            synced += 1
            self.console.member_step(i, total, node.code, node.name,
                                     len(members), len(new - old), len(old - new))
        return synced, failed, skipped

    # -- 删掉申万已经移除的板块 -------------------------------------------
    def _prune_removed(self, nodes) -> list[str]:
        keep = {x.code for x in nodes}
        existing = {r["concept"] for r in self.ctx.db.query(
            "SELECT DISTINCT concept FROM board_list")}
        removed = sorted(existing - keep)
        for c in removed:
            self.ctx.db.drop_board(c)          # 行情 + 成员 + 字典一起删
        return removed


class DropBoard(Command):
    """
    drop board <代码…> —— 删掉板块的本地数据（行情 + 成分股 + 字典那行）。

    和 unwatch 不是一回事：unwatch 只是不再关注，数据留着。
    个股没有 drop —— 它的行情是全市场一次拉进来的，删了明天也会回来。
    """

    name = "drop board"

    def __init__(self, ctx: Context, codes: list[str]):
        super().__init__(ctx)
        self.codes = codes

    def run(self) -> int:
        watched = set(self.ctx.watched("board"))
        rc = 0
        for raw in self.codes:
            code = raw.strip().upper()
            if not is_board_code(code):
                self.console.bad_board_code(raw.strip())
                rc = 1
                continue

            before = {
                "board_daily": len(self.ctx.db.load_board_daily(code)),
                "concept_member": len(self.ctx.db.load_board_members(code)),
                "board_list": 1 if self.ctx.db.name_of("board", code) else 0,
            }
            if not any(before.values()):
                self.console.drop_nothing(code)
                continue

            self.console.drop_report(code, before)
            gone = self.ctx.db.drop_board(code)
            self.console.drop_done(code, gone, code in watched)

        if rc == 0 and self.codes:
            self.console.board_state()
        return rc
