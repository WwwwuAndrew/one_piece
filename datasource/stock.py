#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock.py —— 个股数据封装接口（内部调用 fetch.py）。

    from datasource.stock import Stocks
    s = Stocks()
    s.fetch("300308")             -> FetchResult  最新一天（走 tushare）
    s.fetch("300308", days=15)    -> FetchResult  最近 15 个交易日（走 tushare）
    s.get("300308")               -> dict         最新一天本身
    s.history("300308", days=15)  -> list[dict]   本地已存的（不联网）
    s.cached_codes()              -> list[str]    本地已缓存了哪些个股

用哪个数据源由 config/system.yaml 的 stock 决定（默认 tushare）。

后续其他功能（指标、策略等）统一从这里拿个股数据，不要直接碰具体数据源。
"""

from __future__ import annotations

from .fetch import Fetcher, FetchResult, is_stock_code


class Stocks:
    """个股数据封装接口。"""

    def __init__(self, fetcher: Fetcher | None = None):
        self.fetcher = fetcher or Fetcher()

    def fetch(self, code: str, days: int | None = None) -> FetchResult:
        """联网拉取个股数据并入库（数据源 / 存储位置都由 fetch.py 决定）。

        days=None -> 只要最新一条；days=N -> 最近 N 个交易日。
        """
        return self.fetcher.fetch(self._check(code), days=days)

    def get(self, code: str) -> dict:
        """取个股最新一天（联网拉取；这个交易日本地已有就不重复写）。"""
        return self.fetch(code).fetched[-1]

    def history(self, code: str, days: int | None = None) -> list[dict]:
        """本地已存的个股记录（按日期升序）；days 可截取最近 N 个交易日。"""
        return self.fetcher.history(self._check(code), days=days)

    def cached_codes(self) -> list[str]:
        """本地已经缓存了哪些个股代码。"""
        return self.fetcher.cached_codes("stock")

    @staticmethod
    def _check(code: str) -> str:
        code = str(code).strip().zfill(6)
        if not is_stock_code(code):
            raise ValueError(f"个股代码应形如 300308，收到：{code}")
        return code
