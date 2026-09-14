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
   当天通常 1 条，历史多天多条。统一成列表，Fetcher 才能无差别地比对入库。

2. **`days` 的含义**
   `days=None` -> 只要最新的那一条（当天快照）
   `days=N`    -> 最近 N 个交易日

3. **金额 / 成交量一律给「原始单位」**
   金额 = **元**，成交量 = **股**。
   （tushare 的「千元」「手」要在数据源里先换算成 元 / 股。）
   入库时**原样存**，不做任何缩放 —— 这样以后换数据源，库里也不会多出一层换算错。

4. ★ **字段名一律用这一套「对外名」**（板块 / 个股都一样，别各叫各的）：

       日期 date · 代码 code · 名字 name
       价格 price · 涨跌幅 change_pct · 涨跌额 change
       开 open · 高 high · 低 low · 昨收 pre_close · 量 volume · 额 amount(元)

   为什么必须统一：**两张表的列名并不一样**
   （板块是 `price` / `change_pct`，个股是 `close` / `pct_chg` —— 后者是 tushare 的叫法）。
   数据源只管按上面这套名字给，翻成各表的列名是 `Fetcher._to_row` **一个地方**的事。
   ⚠️ 那里漏翻一个字段不会报错，只会让那一列静默变成 NULL（价格、涨跌幅全空）。

5. **字段可以少给**（拿不到的就不给），写库时缺的自动是 NULL：
   **列一定在，只是没值**。列的集合由数据库的建表语句（store.py）唯一决定，
   不再由代码里的一份字段清单维护 —— 表结构就是契约，改了表就一定会被发现。

6. **拿不到的能力要明确报错**，绝不能静默少返回数据。

======================================================================
数据层：数据库是唯一的落盘格式
======================================================================

`data/raw/raw.sqlite` 里的表就是唯一真相（早期用过 jsonl，已彻底退役）：

    stock_daily    (code, trade_date, open, high, low, close, pre_close,
                    change, pct_chg, volume, amount)          amount=元 volume=股
    board_daily    (concept, trade_date, source, price, change_pct, ...)
    board_list     (concept, name, member_updated_at)
    concept_member (concept, code)

所以：
  · 「同类记录长得一样」由**表结构**保证，不需要代码再拼一份固定字段；
  · 内存里的记录 = 数据库的一行（列名、单位全都跟库里一致）；
  · 换算成「亿 / 万」只发生在展示那一步（fmt_amount / show_data）。
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

    # ⚠️ 这是**数据源自己的名字**（写进记录的 source 字段），不是「标的的名字」。
    #    子类必须显式声明；实例里也不要再写 self.name = ...（会把数据源名字顶掉）。
    #    （忘了声明就会继承成 "base"，那是最难查的一类错：数据看着都对，来源是假的。）
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


def today_str() -> str:
    """今天的 "YYYY-MM-DD"（北京时区）。

    只用来当**查询参数**（该去问哪一天），不参与交易日的判断 ——
    哪天真有数据，永远由接口返回的内容决定。
    """
    return datetime.now(BEIJING).strftime("%Y-%m-%d")


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
# 金额的「展示单位」
# ---------------------------------------------------------------------------
# ⚠️ 数据层（数据库）一律存**原始单位：元**。这两个只是「显示成什么单位好看」：
#    板块成交额动辄几千亿 -> 用亿
#    个股成交额可能只有几千万 -> 用万（够 1 亿时自动换成亿）
#
# 换算只该发生在**展示**那一步（fmt_amount / show_data），绝不要在读写路径上做。

AMOUNT_UNIT = {"board": "亿", "stock": "万"}    # 各自「默认」显示成什么单位
YI = 1e8                                        # 亿
WAN = 1e4                                       # 万


def fmt_amount(kind: str, yuan) -> str:
    """元 -> 给人看的字符串。

    板块：4,886.2 亿
    个股：够 1 亿就显示 亿（99.8 亿），不够就显示 万（1,234.5 万）
    """
    v = to_num(yuan)
    if v is None:
        return "—"
    if kind == "stock" and abs(v) < YI:
        return f"{v / WAN:,.1f} 万"
    return f"{v / YI:,.1f} 亿"
