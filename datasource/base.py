#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
base.py —— 数据源基类 + 全项目共用的代码/数值工具。

所有数据源都继承 `DataSource`，并**只覆盖自己支持的方法**：

    class DirectSource(DataSource):
        def fetch_board(self, code, days=None) -> list[dict]: ...
        def fetch_stock(self, code, days=None) -> list[dict]: ...

    class AkshareSource(DataSource):
        def fetch_board(self, code, days=None) -> list[dict]: ...   # 只支持板块

    class TushareSource(DataSource):
        def fetch_stock(self, code, days=None) -> list[dict]: ...   # 只支持个股

这里**故意不用 abc.ABC**：因为不同数据源支持的范围不一样（direct 当天有、
akshare 只有板块、tushare 只有个股），基类给每个方法一个「不支持就报错」的
默认实现，比强制子类实现一个假的空方法更诚实。

======================================================================
方法契约（所有数据源必须遵守）
======================================================================

1. **统一返回 `list[dict]`**
   当天通常 1 条，历史多天多条。统一成列表，Fetcher 才能无差别地合并落盘。

2. **`days` 的含义**
   `days=None` -> 只要最新的那一条（当天快照）
   `days=N`    -> 最近 N 个交易日

3. **金额 / 成交量一律给「原始单位」**
   金额 = **元**，成交量 = **股**。
   （tushare 的「千元」「手」要在数据源里先换算成 元 / 股。）
   换算成「落盘单位」由 `format_record()` 统一做，数据源不要自己换算 —— 
   这样以后换数据源，落盘格式也不会变。

4. **字段可以少给**（拿不到的就不给），`format_record()` 会把缺的补成 `None`：
   **属性一定在，只是没值**。落盘格式由本文件的 BOARD_FIELDS / STOCK_FIELDS 唯一决定。

5. **拿不到的能力要明确报错**，绝不能静默少返回数据。

======================================================================
本地统一落盘格式
======================================================================

不管数据来自 direct / akshare / tushare，落盘前都要过一遍 `format_record()`：

    · 字段固定、顺序固定  -> 同类记录（board 之间 / stock 之间）长得完全一样；
    · 缺的字段补 None      -> 「拉不到涨跌家数」也照样有这个属性，只是值为 None；
    · 多余的字段丢掉；
    · 成交额换算成落盘单位并保留 1 位小数：
        board -> 亿
        stock -> 万（展示时如果够 1 亿会自动显示成亿）
    · board 和 stock 的格式**可以不一样**（板块有涨跌家数、个股有上市日期）。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))


# ---------------------------------------------------------------------------
# 基类
# ---------------------------------------------------------------------------

class DataSource:
    """数据源基类。子类覆盖自己支持的方法，不支持的直接抛 NotImplementedError。"""

    name = "base"

    def fetch_board(self, code: str, days: int | None = None) -> list[dict]:
        raise NotImplementedError(f"{type(self).__name__} 不支持板块数据")

    def fetch_stock(self, code: str, days: int | None = None) -> list[dict]:
        raise NotImplementedError(f"{type(self).__name__} 不支持个股数据")

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"


# ---------------------------------------------------------------------------
# 代码识别 / 转换（无状态纯函数，全项目共用）
# ---------------------------------------------------------------------------

def is_board_code(code: str) -> bool:
    return bool(re.fullmatch(r"BK\d{4}", str(code).strip().upper()))


def is_stock_code(code: str) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(code).strip()))


def exchange_prefix(code: str) -> str:
    """6 位代码 -> sh / sz / bj。注意 92 段（北交所新代码）要先于 9 段判断。"""
    c = str(code).zfill(6)
    if c.startswith(("4", "8", "92")):
        return "bj"
    if c.startswith(("60", "68", "69", "9", "5", "11", "13")):
        return "sh"
    if c.startswith(("00", "30", "20", "12", "15", "16", "18")):
        return "sz"
    return "sh"


def to_ts_code(code: str) -> str:
    """6 位代码 -> tushare 的 ts_code，如 300308 -> 300308.SZ。"""
    c = str(code).strip().zfill(6)
    suffix = {"sh": "SH", "sz": "SZ", "bj": "BJ"}[exchange_prefix(c)]
    return f"{c}.{suffix}"


def limit_rate(code: str, name: str = "") -> float:
    """涨跌停幅度：主板 10% / 创业·科创 20% / 北交所 30% / ST 主板 5%。"""
    c = str(code).zfill(6)
    n = (name or "").upper().replace(" ", "")
    is_st = "ST" in n[:4]
    if c.startswith(("300", "301", "688", "689")):
        return 0.20
    if c.startswith(("4", "8", "92")):
        return 0.30
    return 0.05 if is_st else 0.10


# ---------------------------------------------------------------------------
# 数值 / 日期工具
# ---------------------------------------------------------------------------

def to_num(value, default=None):
    """把接口里的占位符（"-" / "--" / ""）统一转成 None，其余转 float。"""
    if value in (None, "-", "--", ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def to_date(value) -> str | None:
    """把各种日期写法统一成 "YYYY-MM-DD"。认不出来返回 None。

    能吃下：datetime/date、"20260911"、"2026-09-11"、"2026/09/11"。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if hasattr(value, "strftime"):          # datetime.date 等
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    m = re.fullmatch(r"(\d{4})[-/](\d{2})[-/](\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def date_window(days: int) -> tuple[str, str]:
    """N 个交易日大约对应 N*1.7 个自然日，再加点缓冲。

    返回 (start, end)，格式 YYYYMMDD，给各接口当查询区间用。
    注意：这只是「查询窗口」，不判断哪天是交易日 —— 交易日由接口返回的数据决定。
    """
    end = datetime.now(BEIJING).date()
    # 最少往回 15 天：够覆盖周末 + 春节/国庆这种长休市
    span = max(int(max(days, 1) * 1.7) + 7, 15)
    start = end - timedelta(days=span)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# 本地统一落盘格式（schema）
# ---------------------------------------------------------------------------
# 字段顺序 = 落盘 jsonl 里每行的字段顺序。同类记录必须完全一致：
#   · 属性一定在（拿不到就是 None），不再把 None 丢掉；
#   · 顺序固定，方便肉眼比对和 diff。

BOARD_FIELDS = (
    "date", "code", "name", "source",
    "price", "change_pct", "change",
    "amount", "volume",
    "up", "down", "flat",                       # 板块独有：涨跌家数
    "open", "high", "low", "pre_close",
    "amplitude_pct", "turnover_pct",
    "total_mv", "float_mv",
    "pe", "volume_ratio", "pb", "chg_60d", "chg_ytd",
    "quote_time",
)

STOCK_FIELDS = (
    "date", "code", "name", "source",
    "price", "change_pct", "change",
    "amount", "volume",
    "open", "high", "low", "pre_close",
    "amplitude_pct", "turnover_pct",
    "total_mv", "float_mv",
    "pe", "volume_ratio", "pb", "chg_60d", "chg_ytd",
    "list_date",                                # 个股独有：上市日期
    "quote_time",
)

FIELDS = {"board": BOARD_FIELDS, "stock": STOCK_FIELDS}

# 成交额的「落盘单位」：板块存亿，个股存万（个股金额小，存亿会丢精度）
AMOUNT_UNIT = {"board": "亿", "stock": "万"}
_AMOUNT_DIVISOR = {"board": 1e8, "stock": 1e4}


def store_amount(kind: str, yuan) -> float | None:
    """成交额：元 -> 落盘单位（board 亿 / stock 万），保留 1 位小数。"""
    v = to_num(yuan)
    if v is None:
        return None
    return round(v / _AMOUNT_DIVISOR[kind], 1)


def fmt_amount(kind: str, stored) -> str:
    """把落盘的成交额显示成人看的样子。

    板块：4886.2 亿
    个股：存的单位是万；够 1 亿就换成亿显示（299740.0 万 -> 299.7 亿），否则 1234.5 万。
    """
    v = to_num(stored)
    if v is None:
        return "—"
    if kind == "stock" and abs(v) >= 1e4:
        return f"{v / 1e4:,.1f} 亿"
    return f"{v:,.1f} {AMOUNT_UNIT[kind]}"


def format_record(kind: str, rec: dict, source: str | None = None,
                  already_formatted: bool = False) -> dict:
    """把任意数据源给的记录，格式化成该 kind 的本地统一格式。

    kind              : 'board' | 'stock'
    source            : 数据源名字，写进 source 字段（None 则保留原值）
    already_formatted : True 表示 rec 已经是本地格式（成交额已是 亿/万），
                        **不再做单位换算**。迁移旧数据时用它避免重复换算。
    """
    fields = FIELDS[kind]
    out = {f: rec.get(f) for f in fields}

    if source is not None:
        out["source"] = source

    if not already_formatted:
        raw = to_num(rec.get("amount"))
        out["amount"] = None if raw is None else store_amount(kind, raw)

    return out
