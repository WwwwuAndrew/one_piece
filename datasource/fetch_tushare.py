#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_tushare.py —— 基于 tushare 的个股数据源（当天 + 历史）。

    from datasource.fetch_tushare import TushareSource
    src = TushareSource()                    # token 从 config/system.yaml 的 tushare_token 读
    src.fetch_stock("300308")                # -> [最新一天]
    src.fetch_stock("300308", days=15)       # -> [最近 15 个交易日]

只支持个股；`fetch_board` 会明确报错（板块走 direct / akshare，都是东财的数据）。

用到的 tushare 接口：
    pro.daily(ts_code, start_date, end_date)
        ts_code 形如 "300308.SZ"（不是 300308），所以要用 base.to_ts_code() 转。
        返回字段：ts_code, trade_date, open, high, low, close,
                  pre_close, change, pct_chg, vol, amount

    pro.stock_basic(ts_code, fields="ts_code,name")
        daily 不返回股票名字，名字只能从这个接口取。按代码缓存，一只股票只查一次；
        查不到不报错，只是这条记录没有 name（不会因此丢掉行情数据）。

⚠️ 单位换算（tushare 和东财不一样，必须换，否则存进去就是错的）：
    vol    tushare 是「手」   -> 统一存「股」：× 100
    amount tushare 是「千元」 -> 统一存「元」：× 1000

⚠️ 拿不到的东西（tushare daily 本身不提供，不在同一次请求里）：
    换手率、振幅、量比、总市值 —— 这些在 pro.daily_basic 接口里，是**另一次请求**。
    目前不取，所以历史记录里没有这几个字段；需要的话再加。
"""

from __future__ import annotations

from config.config import config

from .base import DataSource, date_window, to_date, to_num, to_ts_code

# tushare daily 必须有的字段，缺了就说明接口变了，必须立刻报错而不是硬凑
_REQUIRED = ("trade_date", "open", "high", "low", "close",
             "pre_close", "change", "pct_chg", "vol", "amount")


class TushareSource(DataSource):
    """tushare 数据源（个股）。"""

    name = "tushare"

    def __init__(self, token: str | None = None, timeout: int = 30):
        token = token or config.system.get("tushare_token")
        if not token:
            raise ValueError(
                "缺少 tushare token：请在 config/system.yaml 里填 tushare_token")

        import tushare as ts          # 放在这里 import：只用板块时不必加载 tushare
        self.pro = ts.pro_api(token, timeout=timeout)
        self._names: dict[str, str | None] = {}   # ts_code -> name（None 表示查过但没查到）

    # -- 名字（best-effort，按代码缓存）-----------------------------------
    def _name(self, ts_code: str) -> str | None:
        if ts_code not in self._names:
            name = None
            try:
                df = self.pro.stock_basic(ts_code=ts_code, fields="ts_code,name")
                if df is not None and len(df) and "name" in df.columns:
                    name = str(df.iloc[0]["name"])
            except Exception as exc:
                print(f"⚠️  取 {ts_code} 的名字失败（不影响行情数据）：{exc}")
            self._names[ts_code] = name
        return self._names[ts_code]

    # -- 板块：不支持 -----------------------------------------------------
    def fetch_board(self, code: str, days: int | None = None) -> list[dict]:
        raise NotImplementedError(
            "TushareSource 只提供个股数据；板块请用 direct（当天）或 akshare（历史）")

    # -- 个股 -------------------------------------------------------------
    def fetch_stock(self, code: str, days: int | None = None) -> list[dict]:
        code = str(code).strip().zfill(6)
        ts_code = to_ts_code(code)
        n = max(days or 1, 1)
        start, end = date_window(n)

        try:
            df = self.pro.daily(ts_code=ts_code, start_date=start, end_date=end)
        except Exception as exc:
            raise RuntimeError(
                f"tushare daily 调用失败：{type(exc).__name__}: {exc}\n"
                f"    接口: pro.daily(ts_code={ts_code!r}, "
                f"start_date={start!r}, end_date={end!r})") from exc

        if df is None or len(df) == 0:
            raise LookupError(
                f"tushare 在 {start} ~ {end} 没有 {ts_code} 的数据"
                f"（代码可能不对，或区间内无交易日）")

        missing = [f for f in _REQUIRED if f not in df.columns]
        if missing:
            raise RuntimeError(
                f"tushare daily 返回的字段和预期不一致，缺少 {missing}；"
                f"实际字段：{list(df.columns)}")

        # tushare 默认按交易日倒序返回，统一成升序后取最近 n 条
        df = df.sort_values("trade_date")
        rows = df.tail(n)

        name = self._name(ts_code)
        records = []
        for _, row in rows.iterrows():
            rec: dict = {
                "code": code,
                "date": to_date(row.get("trade_date")),
                "price": to_num(row.get("close")),
                "change_pct": to_num(row.get("pct_chg")),
                "change": to_num(row.get("change")),
                "open": to_num(row.get("open")),
                "high": to_num(row.get("high")),
                "low": to_num(row.get("low")),
                "pre_close": to_num(row.get("pre_close")),
            }
            if name:
                rec["name"] = name

            # 单位换算：手 -> 股；千元 -> 元
            vol = to_num(row.get("vol"))
            if vol is not None:
                rec["volume"] = vol * 100
            amount = to_num(row.get("amount"))
            if amount is not None:
                rec["amount"] = amount * 1000

            records.append({k: v for k, v in rec.items() if v is not None})

        if not records:
            raise LookupError(f"tushare 没有返回 {ts_code} 的任何记录")
        return records
