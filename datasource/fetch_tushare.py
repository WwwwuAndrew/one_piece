#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_tushare.py —— 基于 tushare 的数据源（全市场 + 单只个股）。

    from datasource.fetch_tushare import TushareSource
    src = TushareSource()                    # token 从 config/system.yaml 的 tushare_token 读

    # ★ 全市场：1 个请求拿全部 ~5400 只（这是架构的地基）
    day, rows = src.fetch_market_daily()              # 自动往回找到最近有数据的一天
    day, rows = src.fetch_market_daily("20260911")    # 指定交易日
    rows = src.fetch_market_on("2026-09-10")          # 严格只要这天，没有就 []（补历史用）
    days = src.trade_days("2026-08-01", "2026-09-11") # 区间里开市的日子（补历史用）

    # 单只个股
    src.fetch_stock("300308")                # -> [最新一天]
    src.fetch_stock("300308", days=15)       # -> [最近 15 个交易日]

    # 个股字典（代码 -> 名字）：1 个请求拿全部
    src.fetch_stock_list()                   # -> [{"code": "000001", "name": "平安银行"}, …]

只支持个股；`fetch_board` 会明确报错（板块走 direct / akshare，都是东财的数据）。

用到的 tushare 接口：
    pro.daily(trade_date)          ★ 按**交易日**取全市场（推荐）
        tushare 官方明确建议：**循环日期提取全市场，不要循环 ts_code**。
        单次最多 6000 条，而全市场约 5400 只 —— 所以**一天 1 个请求就够**。
        这也正是「东财只做板块字典、行情只拉一次」那套架构能成立的前提。

    pro.trade_cal(exchange, start_date, end_date, is_open="1")
        交易日历：一次请求问清「哪几天开市」，补历史时就不用按自然日瞎猜 ——
        周末和长假一天都不浪费。拿不到会自动退回按自然日排（见 trade_days）。

    pro.daily(ts_code, start_date, end_date)
        ts_code 形如 "300308.SZ"（不是 300308），所以要用 base.to_ts_code() 转。
        返回字段：ts_code, trade_date, open, high, low, close,
                  pre_close, change, pct_chg, vol, amount

    pro.stock_basic(fields="ts_code,name")          ★ 一次拿全市场名字
        daily **不返回股票名字**，所以名字单独拉一次全量（约 5500 只 = 1 个请求），
        存进 stock_list 字典表。这比「每只股票查一次名字」省 5500 倍请求。

⚠️ 单位换算（tushare 和东财不一样，必须换，否则存进去就是错的）：
    vol    tushare 是「手」   -> 统一存「股」：× 100
    amount tushare 是「千元」 -> 统一存「元」：× 1000
    （换算后正好和 store.stock_daily 的列单位一致：volume=股、amount=元）

⚠️ 拿不到的东西（tushare daily 本身不提供，不在同一次请求里）：
    换手率、振幅、量比、总市值 —— 这些在 pro.daily_basic 接口里，是**另一次请求**。
    目前不取，所以历史记录里没有这几个字段；需要的话再加。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from config.config import config

from .base import BEIJING, DataSource, date_window, to_date, to_num, to_ts_code

# tushare daily 必须有的字段，缺了就说明接口变了，必须立刻报错而不是硬凑
_REQUIRED = ("trade_date", "open", "high", "low", "close",
             "pre_close", "change", "pct_chg", "vol", "amount")

# 取全市场时额外要求 ts_code（要靠它才知道每行是哪只股票）
_MARKET_REQUIRED = ("ts_code",) + _REQUIRED

# tushare 单次最多返回 6000 行。全市场约 5400 只，离上限不远 ——
# 一旦哪天真的顶到上限，说明**很可能被截断了**，必须吼一声（宁可吵也不要静静少一半）。
_MARKET_ROW_LIMIT = 5900


def _recent_days(n: int) -> list[str]:
    """从今天往回 n 个自然日，新的在前（"YYYY-MM-DD"）。

    注意：这里的「今天」只是**当查询参数**用（该去问哪一天），
    **不是**判断哪天是交易日 —— 到底哪一天真有数据，由 tushare 返回的内容决定。
    所以周末、节假日都不会算错，只是多问一两次。
    """
    today = datetime.now(BEIJING).date()
    return [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


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

    # -- 板块：不支持 -----------------------------------------------------
    def fetch_board(self, code: str, days: int | None = None) -> list[dict]:
        raise NotImplementedError(
            "TushareSource 只提供个股数据；板块请用 direct（当天）或 akshare（历史）")

    # -- 交易日历 ---------------------------------------------------------
    def trade_days(self, start, end) -> list[str]:
        """区间里**开市**的交易日（升序，"YYYY-MM-DD"）。

        补历史时用它，比按自然日一天天问省事也省请求：一次问清哪几天开市，
        周末和长假一个请求都不浪费。

        拿不到就返回空列表（由调用方退回「按自然日排」），**不抛错** ——
        「交易日历取不到」不该让整个补数据失败，那只是少了点优化。
        """
        s, e = to_date(start), to_date(end)
        if not s or not e:
            return []
        try:
            df = self.pro.trade_cal(exchange="SSE", start_date=s.replace("-", ""),
                                    end_date=e.replace("-", ""), is_open="1")
        except Exception as exc:
            print(f"⚠️  取交易日历失败，退回按自然日逐天问：{type(exc).__name__}: {exc}")
            return []
        if df is None or len(df) == 0 or "cal_date" not in df.columns:
            print("⚠️  交易日历返回为空，退回按自然日逐天问")
            return []
        return sorted(d for d in (to_date(x) for x in df["cal_date"]) if d)

    # -- ★ 全市场日线（架构的地基：1 个请求拿全市场）----------------------
    def fetch_market_on(self, day) -> list[dict]:
        """**严格**拉某一天的全市场日线，只认这一天。

        那天没数据（非交易日 / 数据还没发布）就返回 []，**不往回找、不抛错** ——
        补历史时要的就是「这天到底有没有」，替它做主反而会算错。

        记录列表的列名已经对齐 store.stock_daily（volume=股、amount=元）。
        """
        day = to_date(day)
        ds = (day or "").replace("-", "")
        try:
            df = self.pro.daily(trade_date=ds)
        except Exception as exc:
            raise RuntimeError(
                f"tushare daily(trade_date={ds!r}) 调用失败："
                f"{type(exc).__name__}: {exc}\n"
                f"    · 若提示权限/积分不足 -> 是 token 或积分档位的问题；\n"
                f"    · 若是日期格式问题 -> trade_date 要 YYYYMMDD。") from exc

        if df is None or len(df) == 0:
            return []
        if len(df) >= _MARKET_ROW_LIMIT:
            print(f"⚠️  {day} 返回 {len(df)} 行，已顶到接口单次上限（约 6000）——"
                  f"这天很可能被截断了")
        return self._market_records(df)

    def fetch_market_daily(self, trade_date=None, max_back: int = 10):
        """拉全市场**最近有数据的**一天。**1 个请求**（约 5400 条）。

        trade_date : "20260911" / "2026-09-11"，给了就只要这一天（没有则报错）；
                     不给则从今天往回找，找到第一个有数据的日子为止
                     （周末 / 节假日 / 今天还没发布，都会自动往前退）。
        max_back   : 最多往回找多少个自然日（防止死循环）。

        返回 (实际拿到的交易日 "YYYY-MM-DD", 记录列表)。
        """
        days = [to_date(trade_date)] if trade_date else _recent_days(max_back)
        days = [d for d in days if d]
        tried: list[str] = []

        for day in days:
            tried.append(day)
            rows = self.fetch_market_on(day)
            if rows:
                return day, rows

        raise LookupError(
            f"从 {tried[0]} 往回 {len(tried)} 天都没拉到全市场数据 "
            f"（最近的都没数据，可能这段都不是交易日）。")

    def _market_records(self, df) -> list[dict]:
        """tushare 全市场 DataFrame -> 入库用的 dict 列表（对齐 store.stock_daily）。"""
        missing = [c for c in _MARKET_REQUIRED if c not in df.columns]
        if missing:
            raise RuntimeError(
                f"tushare daily(全市场) 返回的字段和预期不一致，缺少 {missing}；"
                f"实际字段：{list(df.columns)}")

        out: list[dict] = []
        # to_dict("records") 比逐行 iterrows 快很多（这里一次 5400 行）
        for r in df.to_dict("records"):
            code = str(r.get("ts_code") or "").split(".")[0]     # 000001.SZ -> 000001
            vol = to_num(r.get("vol"))
            amount = to_num(r.get("amount"))
            out.append({
                "code": code,
                "trade_date": to_date(r.get("trade_date")),
                "open": to_num(r.get("open")),
                "high": to_num(r.get("high")),
                "low": to_num(r.get("low")),
                "close": to_num(r.get("close")),
                "pre_close": to_num(r.get("pre_close")),
                "change": to_num(r.get("change")),
                "pct_chg": to_num(r.get("pct_chg")),
                # 单位换算：手 -> 股；千元 -> 元
                "volume": vol * 100 if vol is not None else None,
                "amount": amount * 1000 if amount is not None else None,
            })
        return out

    # -- 个股字典（代码 → 名字）------------------------------------------
    def fetch_stock_list(self) -> list[dict]:
        """拉**全部**股票的代码 + 名字。**1 个请求**（约 5500 只）。

        名字属于「字典」不属于行情，所以它单独一次拉全量，落在 `stock_list` 表里；
        `daily` 是不返回名字的，所以拿行情的时候顺手带不上。

        返回 [{"code": "000001", "name": "平安银行"}, …]。
        """
        try:
            df = self.pro.stock_basic(fields="ts_code,name")
        except Exception as exc:
            raise RuntimeError(
                f"tushare stock_basic 调用失败：{type(exc).__name__}: {exc}\n"
                f"    · 若提示权限/积分不足 -> stock_basic 需要 120 积分（和 daily 同档）。"
            ) from exc

        if df is None or len(df) == 0 or "name" not in df.columns:
            raise LookupError("tushare stock_basic 没返回任何股票名字")

        out = []
        for r in df.to_dict("records"):
            code = str(r.get("ts_code") or "").split(".")[0]
            name = r.get("name")
            if code:
                out.append({"code": code, "name": str(name) if name else None})
        return out

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
