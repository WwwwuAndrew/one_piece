#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
base.py —— 数据源基类 + 全项目共用的工具函数。

所有数据源继承 DataSource，只覆盖自己支持的方法，不支持的直接抛 NotImplementedError。
（故意不用 abc：三个来源支持的范围本来就不一样，给个「不支持就报错」的默认实现更诚实。）

方法契约
    · 统一返回 list[dict]；days=None 只要最新一条，days=N 要最近 N 个交易日。
    · 金额一律给**元**、成交量给**股**（tushare 的「千元 / 手」要在数据源里先换算）。
    · 字段名一律用这套对外名：
          date · code · name · price · change_pct · change
          open · high · low · pre_close · volume · amount
      两张表的列名**并不一样**（板块 price/change_pct，个股 close/pct_chg），
      翻成各表的列名是 Fetcher._to_row 一个地方的事 —— 漏翻不会报错，
      只会让那一列静默变 NULL，所以那条路必须有测试盯着。
    · 字段可以少给（拿不到的就不给），入库时缺的自动是 NULL。
    · 拿不到的能力要明确报错，绝不静默少返回数据。

数据层
    data/raw/raw.sqlite 里的表是唯一真相（早期用过 jsonl，已退役）。
    库里存**原始单位**（元 / 股），换算成「亿 / 万」只发生在展示那一步。
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
