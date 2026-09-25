#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch.py —— 数据获取主接口：tushare 全市场行情 + 本地读写。

    f = Fetcher()
    f.market("20260915")         -> MarketDay   全市场某一天（1 个请求）
    f.market_plan(30)            -> list[dict]  最近 30 个交易日的补数计划
    f.load("801080.SI")          -> list[dict]  读本地记录（板块/个股，不联网）
    f.dict_name("board", code)   -> str|None    名字（从字典表）
    f.ensure_stock_names()       -> int         补个股名字字典（缺了才拉，1 请求）

行情只有一条路：tushare 按交易日拉全市场，存 stock_daily。
板块指标不在这里算 —— 由 board_calc.BoardCalc 本地聚合（见 cli/fetch.py 的 fetch board）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .base import is_board_code, is_stock_code, to_date, today_str
from .store import date_to_int, date_to_str, store as default_store


@dataclass
class MarketDay:
    """
    全市场「某一个交易日」的拉取结果。

    skipped = 本地已有这天，一个请求都没发；empty = 联网问了但那天没数据
    （非交易日或还没发布），都不是错误。
    """

    day: str | None = None      # 对应的交易日 "YYYY-MM-DD"
    fetched: int = 0            # 数据源这次返回的行数
    added: int = 0              # 新增入库的行数
    total: int = 0              # 这一交易日在库里的总行数
    skipped: bool = False       # 本地已有，没联网

    @property
    def empty(self) -> bool:
        return not self.skipped and self.fetched == 0

    def __repr__(self) -> str:
        if self.skipped:
            return f"<MarketDay {self.day} 本地已有 {self.total} 行，跳过>"
        if self.empty:
            return f"<MarketDay {self.day} 没有数据>"
        return f"<MarketDay {self.day} 拉到 {self.fetched} 行，新增 {self.added} 行>"


def _day_span(end_day: str, back: int) -> list[str]:
    """从 end_day 往回 back 个自然日，**新的在前**（"YYYY-MM-DD"）。

    注意这只是「候选日期」，不判断哪天是交易日 —— 交易日由交易日历 / 接口返回决定。
    """
    end = datetime.strptime(end_day, "%Y-%m-%d").date()
    return [(end - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(back + 1)]


class Fetcher:
    """tushare 全市场行情 + 本地读写。db 默认 data/raw/raw.sqlite。"""

    def __init__(self, db=None):
        self.db = db if db is not None else default_store
        self._tushare = None

    # -- tushare 源（惰性创建，不用就不加载 tushare）----------------------
    def _market_source(self):
        if self._tushare is None:
            from .fetch_tushare import TushareSource
            self._tushare = TushareSource()
        return self._tushare

    # -- 本地读写（全部走数据库）------------------------------------------
    @staticmethod
    def _kind_of(code: str) -> str:
        """代码 -> 'board' | 'stock'（无法识别时抛错）。"""
        if is_board_code(code):
            return "board"
        if is_stock_code(code):
            return "stock"
        raise ValueError(f"无法识别的代码：{code}（板块形如 801080.SI，个股形如 300308）")

    def load(self, code: str) -> list[dict]:
        """读本地记录（升序）。记录 = 数据库一行 + `name`（名字在字典表里，不在行情表）。"""
        code = str(code).strip().upper()
        kind = self._kind_of(code)
        if kind == "board":
            rows = self.db.load_board_daily(code)
        else:
            code = code.zfill(6)
            rows = self.db.load_stock_daily(codes=[code])
        name = self.dict_name(kind, code)
        for r in rows:
            r["name"] = name
        return rows

    def dict_name(self, kind: str, code: str) -> str | None:
        """从字典表取名字（板块 board_list / 个股 stock_list）。"""
        return self.db.name_of(kind, code)

    def cached_codes(self, kind: str) -> list[str]:
        """本地已经有哪些代码。"""
        if kind not in ("board", "stock"):
            raise ValueError(f"kind 只能是 'board' 或 'stock'，收到：{kind!r}")
        table, col = (("board_daily", "concept") if kind == "board"
                      else ("stock_daily", "code"))
        return [r[col] for r in self.db.query(
            f"SELECT DISTINCT {col} FROM {table} ORDER BY {col}")]

    # -- 个股名字字典 -----------------------------------------------------
    def ensure_stock_names(self) -> int:
        """补全个股名字字典。

        tushare daily 不返回名字，名字只在 stock_basic 里。这里发现 stock_daily
        里有「stock_list 没有的代码」时，才拉一次 stock_basic 全量补上（1 个请求）。
        没有缺就不用拉，返回 0。
        """
        missing = self.db.query(
            "SELECT 1 FROM stock_daily s LEFT JOIN stock_list l ON s.code = l.code "
            "WHERE l.code IS NULL LIMIT 1")
        if not missing:
            return 0
        rows = self._market_source().fetch_stock_list()
        return self.db.upsert_stock_list(rows)

    # -- 全市场日线（按交易日，一天 1 个请求）-----------------------------
    def market(self, trade_date: str | None = None, force: bool = False) -> MarketDay:
        """拉**全市场某一天**的日线并入库。

        缓存优先：本地已经有这天、又没 force，就一个请求都不发。
        force = **先拉、拉到了才删旧的再写** —— 写入是 INSERT OR IGNORE，不先删就盖不掉脏行；
        反过来先删后拉，网络一失败就把好数据删了。
        """
        src = self._market_source()

        if trade_date:
            day = to_date(trade_date)
            if not day:
                raise ValueError(f"看不懂的日期：{trade_date!r}（要 YYYYMMDD 或 YYYY-MM-DD）")
            if not force:
                n = self.db.stock_day_counts().get(date_to_int(day), 0)
                if n:
                    return MarketDay(day=day, total=n, skipped=True)   # 没联网
            rows = src.fetch_market_on(day)
        else:
            day, rows = src.fetch_market_daily()

        if not rows:
            return MarketDay(day=day)          # 那天没数据，本地一行没动

        if force:
            self.db.delete_stock_days([day])
        added = self.db.save_stock_daily(rows)
        return MarketDay(day=day, fetched=len(rows), added=added,
                         total=self.db.stock_day_counts().get(date_to_int(day), 0))

    def market_plan(self, days: int, end: str | None = None) -> list[dict]:
        """最近 days 个交易日的**补数计划**：[{"day", "rows"}, …]，新的在前。

        rows = 本地已有多少行（0 = 要联网拉）。「哪天是交易日」优先问数据源的交易日历，
        问不到就退回按自然日多排一些 —— 没数据的那天会被跳过，不影响正确性。
        """
        days = int(days)
        if days < 1:
            raise ValueError(f"days 要 >= 1，收到：{days}")

        src = self._market_source()
        end_day = to_date(end) if end else today_str()
        if not end_day:
            raise ValueError(f"看不懂的日期：{end!r}（要 YYYYMMDD 或 YYYY-MM-DD）")

        # 交易日历只覆盖「最近 days 个交易日」所需的自然日（N 个交易日约 1.4N 天，留一倍余量）
        cal = src.trade_days(_day_span(end_day, days * 2 + 15)[-1], end_day)
        if cal:
            picked = cal[-days:]
        else:
            picked = _day_span(end_day, int(days * 1.5) + 7)

        have = {date_to_str(d): n for d, n in self.db.stock_day_counts().items()}
        return [{"day": d, "rows": have.get(d, 0)} for d in reversed(picked)]
