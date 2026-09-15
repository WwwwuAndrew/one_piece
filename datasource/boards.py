#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
boards.py —— 板块数据封装接口（内部调用 fetch.py）。

    b = Boards()
    b.fetch("BK1201")            -> FetchResult   联网拉取并入库
    b.history("BK1201", days=15) -> list[dict]    读本地（不联网）
    b.drop("BK1201")             -> dict          删掉这个板块的本地数据

用哪个数据源由 config/system.yaml 的 board_today / board_history 决定。
后续其他功能（指标、策略）统一从这里拿板块数据，不要直接碰具体数据源。
"""

from __future__ import annotations

from .fetch import Fetcher, FetchResult, is_board_code


class Boards:
    """板块数据封装接口。"""

    def __init__(self, fetcher: Fetcher | None = None):
        self.fetcher = fetcher or Fetcher()

    def fetch(self, code: str, days: int | None = None) -> FetchResult:
        """联网拉取板块数据并入库（数据源 / 存储位置都由 fetch.py 决定）。

        days=None -> 当天快照；days=N -> 最近 N 个交易日（会自动换到历史数据源）。
        """
        return self.fetcher.fetch(self._check(code), days=days)

    def get(self, code: str) -> dict:
        """取板块当天快照（联网拉取；这个交易日本地已有就不重复写）。"""
        return self.fetch(code).fetched[-1]

    def history(self, code: str, days: int | None = None) -> list[dict]:
        """本地已存的板块记录（按日期升序）；days 可截取最近 N 个交易日。"""
        return self.fetcher.history(self._check(code), days=days)

    def cached_codes(self) -> list[str]:
        """本地已经缓存了哪些板块代码。"""
        return self.fetcher.cached_codes("board")

    def drop(self, code: str) -> dict[str, int]:
        """**删掉**这个板块的本地数据（行情 + 成员 + 字典那行）。

        和「移出自选」不是一回事：移出自选只是不再关注（数据留着），
        drop 是把数据本身删掉。想重新要回来得再 fetch + update member。
        """
        return self.fetcher.db.drop_board(self._check(code))

    def what_would_drop(self, code: str) -> dict[str, int]:
        """先看看删的话会删掉多少（不真删），用来删之前给你看一眼。"""
        code = self._check(code)
        return {
            "board_daily": len(self.fetcher.db.load_board_daily(code)),
            "concept_member": len(self.fetcher.db.load_board_members(code)),
            "board_list": 1 if self.fetcher.db.name_of("board", code) else 0,
        }

    @staticmethod
    def _check(code: str) -> str:
        code = str(code).strip().upper()
        if not is_board_code(code):
            raise ValueError(f"板块代码应形如 BK1201，收到：{code}")
        return code
