#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
members.py —— 板块成分股名单的同步（update member）。

名单是**定义**类数据：一个板块有哪些股票。变一次管一阵子，所以单独一个动作。

防反爬的三条都在 MemberSync 里：
    · 板块之间按 fetch_interval 间隔；
    · 今天同步过的跳过（顺带可中断续跑）；
    · 一旦被拒立刻停下 —— 继续敲只会让 IP 被锁更久。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from datasource.base import is_board_code
from datasource.store import today_int

from .base import Command, Context, looks_blocked
from .catalog import followed_boards


@dataclass
class MemberDiff:
    """一个板块成分股同步前后的差异。"""

    code: str
    name: str | None
    total: int                       # 这次拿到的成分股只数
    old: int                         # 同步前本地有几只
    added: list[str]
    removed: list[str]


class MemberSync:
    """同步板块成分股并写库。"""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.console = ctx.console

    def one(self, code: str, now: int) -> MemberDiff:
        """同步一个板块。失败就抛出来，由调用方决定继续还是停。"""
        old = set(self.ctx.db.load_board_members(code))
        members = self.ctx.direct.fetch_board_members(code)      # 分页拉，可能好几个请求
        codes = [m["code"] for m in members]
        new = set(codes)

        # 板块名：本地字典里已经有就**不再请求**（批量同步时这一条能省掉 21 个请求）
        name = self.ctx.db.name_of("board", code)
        if not name:
            try:
                name = self.ctx.direct.fetch_board_name(code)    # 1 个请求
            except Exception:
                name = None                                      # 拿不到不算失败

        self.ctx.db.replace_board_members(code, codes, updated_at=now)
        if name:
            self.ctx.db.upsert_board_list([{"concept": code, "name": name}])

        return MemberDiff(code=code, name=name, total=len(codes), old=len(old),
                          added=sorted(new - old), removed=sorted(old - new))

    def many(self, codes: list[str], force: bool = False) -> int:
        """把给定的这些板块同步一遍。"""
        if not codes:
            self.console.no_boards_in_db()
            return 1

        now = today_int()
        board_list = self.ctx.db.load_board_list()
        synced_at = {r["concept"]: r["member_updated_at"] for r in board_list}
        todo = [c for c in codes if force or synced_at.get(c) != now]
        skip = [c for c in codes if c not in todo]

        if not self.console.member_plan(len(codes), todo, skip, self._names(board_list),
                                        self.ctx.interval):
            self.console.member_state()
            return 0

        src = self.ctx.direct
        interval = self.ctx.interval
        done = failed = 0
        members_total = 0
        t0 = time.time()
        for i, code in enumerate(todo, 1):
            if i > 1 and interval:
                time.sleep(interval)          # 间隔一下，别把对方惹毛
            try:
                diff = self.one(code, now)
            except Exception as exc:
                failed += 1
                self.console.member_step_failed(i, len(todo), code, exc)
                if looks_blocked(exc):
                    self.console.member_blocked(done)
                    self.console.member_state()
                    return 1
                continue
            done += 1
            members_total += diff.total
            self.console.member_brief(diff)

        self.console.member_done(done, len(todo), failed, members_total,
                                 time.time() - t0, getattr(src, "requests_made", 0))
        self.console.member_state()
        if failed:
            self.console.member_retry_hint()
        return 1 if failed else 0

    @staticmethod
    def _names(board_list: list[dict]) -> dict[str, str]:
        return {r["concept"]: r["name"] for r in board_list}


class UpdateMembers(Command):
    """update member <代码…|all|watch> —— 同步板块成分股名单。"""

    name = "update member"

    def __init__(self, ctx: Context, codes: list[str], force: bool = False):
        super().__init__(ctx)
        self.codes = codes
        self.force = force
        self.sync = MemberSync(ctx)

    def run(self) -> int:
        if not self.codes:
            self.console.member_needs_codes()
            return 1

        words = [c.strip().lower() for c in self.codes]
        if "all" in words:
            if len(self.codes) > 1:
                self.console.ignore_extra("all")
            followed = followed_boards(self.ctx.db, self.ctx.watched("board"))
            return self.sync.many(followed, force=self.force)
        if "watch" in words:
            if len(self.codes) > 1:
                self.console.ignore_extra("watch")
            watched = self.ctx.watched("board")          # 个股没有成分股，忽略
            if not watched:
                self.console.no_watch(boards_only=True)
                return 1
            return self.sync.many(watched, force=self.force)

        rc = 0
        for c in self.codes:
            rc |= self._one(c)
        return rc

    def _one(self, code: str) -> int:
        code = code.strip().upper()
        if not is_board_code(code):
            self.console.bad_board_code(code, blank_line=True)
            return 1

        now = today_int()
        self.console.member_start(code)
        try:
            diff = self.sync.one(code, now)
        except Exception as exc:
            self.console.member_error(exc, looks_blocked(exc))
            return 1
        self.console.member_detail(diff, now)
        return 0
