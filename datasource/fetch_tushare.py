#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_tushare.py —— tushare 数据源（全市场日线 + 个股字典 + 交易日历）。

    src = TushareSource()                    # token 从 config/system.yaml 读
    day, rows = src.fetch_market_daily()     # ★ 全市场最近有数据的一天（1 个请求）
    rows = src.fetch_market_on("2026-09-15") # 严格只这一天，没有就 []
    rows = src.fetch_mktcap("2026-09-15")    # 全市场总市值（1 个请求，板块加权用）
    rows = src.fetch_stock_list()            # 全市场名字（1 个请求）
    days = src.trade_days("2026-08-01", "2026-09-15")   # 交易日历

只做「全市场个股行情 + 个股字典」；板块定义走 fetch_legu.py，
板块指标由本地从个股聚合（board_calc.py）。

★ 关键：tushare 官方建议**循环日期取全市场，不要循环 ts_code**。
  单次上限 6000 条而全市场约 5500 只，所以**一天 1 个请求**。

⚠️ 单位换算（tushare 用的是「手 / 千元 / 万元」，入库前必须换成「股 / 元」）：
      vol        手   -> 股   × 100
      amount     千元 -> 元   × 1000
      total_mv   万元 -> 元   × 1e4      （daily_basic，市值快照）
⚠️ `daily` 不返回名字（也不返回换手率/振幅/量比/市值，那些在 daily_basic）。
   名字用 fetch_stock_list() 一次拉全量，别按只查。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from config.config import config

from .base import BEIJING, DataSource, to_date, to_num

# 取全市场时要求这些字段（缺了就说明接口变了，必须立刻报错而不是硬凑）
_MARKET_REQUIRED = ("ts_code", "trade_date", "open", "high", "low", "close",
                    "pre_close", "change", "pct_chg", "vol", "amount")

# tushare 单次最多返回 6000 行。全市场约 5400 只，离上限不远 ——
# 一旦哪天真的顶到上限，说明**很可能被截断了**，必须吼一声（宁可吵也不要静静少一半）。
_MARKET_ROW_LIMIT = 5900


def _recent_days(n: int) -> list[str]:
    """
    从今天往回 n 个自然日，新的在前。

    「今天」只是查询参数（该去问哪一天），**不是**判断哪天是交易日 ——
    哪天真有数据由 tushare 返回的内容决定，所以周末节假日都不会算错。
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

    # -- 交易日历 ---------------------------------------------------------
    def trade_days(self, start, end) -> list[str]:
        """
        区间里**开市**的交易日（升序）。补历史时用它，比按自然日一天天问省请求。

        拿不到就返回空列表（调用方退回按自然日排），**不抛错** ——
        交易日历取不到只是少了点优化，不该让整个补数失败。
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
        """
        **严格**拉某一天的全市场日线，只认这一天。

        那天没数据（非交易日 / 还没发布）就返回 []，不往回找也不抛错 ——
        补历史时要的就是「这天到底有没有」，替它做主反而会算错。
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
        """
        拉全市场**最近有数据的**一天（1 个请求，约 5500 条）。

        trade_date 给了就只要这一天（没有则报错）；不给就从今天往回找，
        周末 / 节假日 / 今天还没发布都会自动往前退。
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

    # -- 市值快照（代码 → 总市值）-----------------------------------------
    def fetch_mktcap(self, day) -> list[dict]:
        """拉某一天的全市场**总市值**（1 个请求，约 5500 只）。

        板块涨跌幅是市值加权，权重就是它 —— 所以每天刷一次（fetch market 顺手带上），
        免得权重停留在「上次 update board 那天」慢慢失真。

        返回 [{"code": "300308", "mktcap": 8.76e10}, …]，单位**元**。

        ⚠️ 单位换算：daily_basic 的 total_mv 是**万元**，入库前 ×1e4 换成元。
        ⚠️ 这个接口限速比 daily 严（实测 1 次/小时档），所以只做**一天一次**，
           别拿去回补历史（回补几十天会一直撞限速）。
        同一个请求里其实还带 circ_mv（流通市值），以后若要看「自由流通」口径可以一起取。
        """
        day = to_date(day)
        ds = (day or "").replace("-", "")
        if not ds:
            raise ValueError(f"看不懂的日期：{day!r}")
        try:
            df = self.pro.daily_basic(trade_date=ds, fields="ts_code,total_mv")
        except Exception as exc:
            raise RuntimeError(
                f"tushare daily_basic(trade_date={ds!r}) 调用失败："
                f"{type(exc).__name__}: {exc}\n"
                f"    · 若提示频率超限 -> 该接口限速很严（实测 1 次/小时），过一小时再跑；\n"
                f"    · 若提示权限/积分不足 -> 是 token 或积分档位的问题。") from exc

        if df is None or len(df) == 0:
            return []
        if "total_mv" not in df.columns:
            raise RuntimeError(
                f"tushare daily_basic 返回的字段和预期不一致：{list(df.columns)}")

        out = []
        for r in df.to_dict("records"):
            code = str(r.get("ts_code") or "").split(".")[0]
            mv = to_num(r.get("total_mv"))
            if code and mv:
                out.append({"code": code, "mktcap": mv * 1e4})     # 万元 -> 元
        return out

    # -- 个股字典（代码 → 名字）------------------------------------------
    def fetch_stock_list(self) -> list[dict]:
        """
        拉**全部**股票的代码 + 名字（1 个请求，约 5500 只）。

        名字属于「字典」不属于行情，所以单独拉一次全量存进 stock_list；
        `daily` 不返回名字，按只查要 5500 个请求。
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
