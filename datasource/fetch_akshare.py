#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_akshare.py —— 基于 AKShare 的**板块历史**数据源。

    from datasource.fetch_akshare import AkshareSource
    src = AkshareSource()
    src.fetch_board("BK1201", days=15)       # -> [最近 15 个交易日]

只支持板块历史。当天快照请用 DirectSource（更快、1 个请求），个股请用 TushareSource。

用到的 AKShare 接口：
    ak.stock_board_industry_hist_em(symbol, start_date, end_date, period="日k", adjust="")
        东方财富网-沪深板块-行业板块-历史行情。

        ⚠️ docstring 写的是「symbol: 板块名称」，但**看实现才知道可以直接传 BK 代码**：
             if re.match(r"^BK\\d+", symbol):  em_code = symbol
             else:                             # 传名称才去查一遍板块列表
        所以传 "BK1201" 就行，不需要先查名字、也不会多一个请求。

        实际请求：
            http://7.push2his.eastmoney.com/api/qt/stock/kline/get?secid=90.BK1201&...
        ⚠️ 这是 push2his 集群、且是 http —— 就是本机之前被 RemoteDisconnected 的那个集群。
            （push2delay 只提供 clist/快照，不提供 kline 历史，所以历史只能走这里。）

    ⚠️ 返回的历史数据**没有涨跌家数**：
        涨跌家数是横截面统计量，历史接口只给价格序列（11 列）：
        日期/开盘/收盘/最高/最低/成交量/成交额/振幅/涨跌幅/涨跌额/换手率。
        所以板块的历史记录里不会带 up/down/flat，只有当天快照（DirectSource）才有。

    ⚠️ 也**没有 name**：
        这个接口不返回板块名称。所以名字只能靠本地已有记录补（Fetcher 会做这件事），
        或者你先前用 direct 拉过一次当天快照。
"""

from __future__ import annotations

from .base import DataSource, date_window, to_date, to_num

# 这个接口必须有的列，缺了就立刻报错（接口改版要能马上发现）
_REQUIRED = ("日期", "收盘", "涨跌幅", "成交额")


class AkshareSource(DataSource):
    """AKShare 数据源（板块历史，数据来自东方财富）。"""

    name = "akshare"

    def fetch_board(self, code: str, days: int | None = None) -> list[dict]:
        """取板块最近 N 个交易日的历史。**1 个请求**。"""
        import akshare as ak          # 放在这里 import：不用它时不必加载 akshare

        code = str(code).strip().upper()
        n = max(days or 1, 1)
        start, end = date_window(n)

        try:
            df = ak.stock_board_industry_hist_em(
                symbol=code, start_date=start, end_date=end,
                period="日k", adjust="")
        except Exception as exc:
            raise RuntimeError(
                f"ak.stock_board_industry_hist_em 调用失败：{type(exc).__name__}: {exc}\n"
                f"    参数: symbol={code!r}, start_date={start!r}, end_date={end!r}\n"
                f"    该接口走 http://7.push2his.eastmoney.com（不是 push2delay），\n"
                f"    如果报 RemoteDisconnected，就是本机 IP 被这个集群拒了。") from exc

        if df is None or len(df) == 0:
            raise LookupError(
                f"AKShare 在 {start} ~ {end} 没有 {code} 的历史数据"
                f"（板块代码可能不对，或区间内无交易日）")

        missing = [c for c in _REQUIRED if c not in df.columns]
        if missing:
            raise RuntimeError(
                f"stock_board_industry_hist_em 返回的列和预期不一致，缺少 {missing}；"
                f"实际列：{list(df.columns)}")

        records = []
        for _, row in df.tail(n).iterrows():
            rec: dict = {
                "code": code,
                "date": to_date(row.get("日期")),
                "price": to_num(row.get("收盘")),
                "change_pct": to_num(row.get("涨跌幅")),
                "change": to_num(row.get("涨跌额")),
                "amount": to_num(row.get("成交额")),
                "open": to_num(row.get("开盘")),
                "high": to_num(row.get("最高")),
                "low": to_num(row.get("最低")),
                "turnover_pct": to_num(row.get("换手率")),
                "amplitude_pct": to_num(row.get("振幅")),
            }
            # 注意：这里**故意不存 volume**。
            # 东财 kline 的成交量单位（手/股）没有实测确认，板块的点位又不是元，
            # 无法反推校验；存错了就是 100 倍的静默错误，不如先不存。
            # 需要的话等你实测确认单位后再加一行。
            records.append({k: v for k, v in rec.items() if v is not None})

        if not records:
            raise LookupError(f"AKShare 没有返回 {code} 的任何记录")
        return records

    def fetch_stock(self, code: str, days: int | None = None) -> list[dict]:
        raise NotImplementedError(
            "AkshareSource 只提供板块历史；个股请用 TushareSource")
