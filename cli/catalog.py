#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
catalog.py —— 本地「目录」类数据：板块层级表、个股名字字典。

    这几样东西都是**定义**，不是行情：板块属于哪一级、代码叫什么名字。
    改一次能用很久，所以单独拿出来，和每天要刷的行情分开。

顺带放着「有层级表之后才能做」的两件事：按层级取板块、按父级分组。
"""

from __future__ import annotations

from datasource.base import is_board_code
from datasource.board_tree import BoardTree, prune_dict, used_boards
from datasource.store import today_int

from .base import Command, Context


def boards_at_level(db, level: int) -> list[str]:
    """层级表里某一级的板块代码（一级 31 个 / 二级 128 个）。"""
    return [r["concept"] for r in db.query(
        "SELECT concept FROM board_tree WHERE level = ? ORDER BY concept", (level,))]


def board_groups(db) -> list[tuple[str, list[str]]]:
    """[(一级代码, [二级代码…]), …] —— 从层级表来。"""
    rows = db.load_board_tree()
    kids: dict[str, list[str]] = {}
    for r in rows:
        if r["parent"]:
            kids.setdefault(r["parent"], []).append(r["concept"])
    return [(r["concept"], sorted(kids.get(r["concept"], [])))
            for r in rows if r["level"] == 1]


def followed_boards(db, watched: list[str]) -> list[str]:
    """
    「我在跟的板块」= 有行情的 ∪ 有成分股的 ∪ 自选里的。

    故意**不含** board_list 里纯粹是字典条目的板块 —— update tree 会把 159 个都写进字典，
    要是都算成「在跟的」，update member all 一下就成了上百个请求。
    """
    rows = db.query("SELECT concept FROM board_daily "
                    "UNION SELECT concept FROM concept_member ORDER BY concept")
    codes = [r["concept"] for r in rows]
    for c in watched:
        if c not in codes:
            codes.append(c)
    return sorted(codes)


class UpdateStockList(Command):
    """
    update stock —— 同步个股字典（代码 → 名字），1 个请求拿全市场。

    名字属于字典不属于行情：按只查要 5500 个请求，一次拉全量只要 1 个。
    """

    name = "update stock"

    def run(self) -> int:
        try:
            src = self.ctx.tushare
        except Exception as exc:
            self.console.failed(exc)
            return 1

        self.console.stock_list_start()
        try:
            rows = src.fetch_stock_list()
        except Exception as exc:
            self.console.failed(exc)
            return 1

        now = today_int()
        old = {r["code"]: r["name"] for r in self.ctx.db.load_stock_list()}
        n = self.ctx.db.upsert_stock_list([{**r, "updated_at": now} for r in rows])

        new = {r["code"]: r["name"] for r in rows}
        added = sorted(set(new) - set(old))
        renamed = sorted(c for c in set(new) & set(old)
                         if new[c] and old[c] and new[c] != old[c])

        self.console.stock_list_done(len(rows), n, len(old), added, renamed, new, old, now)
        return 0


class UpdateTree(Command):
    """
    update tree —— 重建一级/二级板块表，并清掉字典里不属于这两级的多余条目。

    层级来自申万分类（东财接口不给层级），东财只用来拉板块字典。
    """

    name = "update tree"

    def run(self) -> int:
        tree = BoardTree(db=self.ctx.db)
        self.console.tree_start()
        try:
            res = tree.build()
        except Exception as exc:
            self.console.failed(exc)
            return 1

        if not res.nodes:
            self.console.err("❌ 一个节点都没建出来，先看看上面的报错")
            return 1

        n = tree.save(res)
        keep = {x.bk for x in res.nodes}
        protected = used_boards(self.ctx.db) | set(self.ctx.watched("board"))
        dropped = prune_dict(self.ctx.db, keep, protected)
        kept = sorted(protected - keep)
        self.console.tree_done(n, res.counts, dropped, kept)
        return 0


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

            before = self.ctx.boards.what_would_drop(code)
            if not any(before.values()):
                self.console.drop_nothing(code)
                continue

            self.console.drop_report(code, before)
            gone = self.ctx.boards.drop(code)
            self.console.drop_done(code, gone, code in watched)

        if rc == 0 and self.codes:
            self.console.board_state()
        return rc
