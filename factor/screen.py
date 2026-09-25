#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
screen.py —— `show board --good` 的筛选：把「已经不成立」的标的先摘掉，只留还在跑的那批。

两条否掉规则，**或**的关系（命中任意一条就不展示）：

    ① 最近 3 个交易日累计涨幅 < 0
       板块：近 3 日累计涨跌幅 < 0      —— 方向已经不在了
       个股：近 3 日累积 RS < -1%       —— 相对板块已经跑输

    ② AbsCost 连续 3 天下跌、且最新一天 < 1
       —— 推进一天比一天省力、而且已经省到「几乎没有成本」：既没有抛压、也没有资金
          在往上推。势能已经走完，不值得再盯。

口径：

    · 「最近 3 天」= 表格里最后 3 列（默认 15 天窗口的尾巴）。调用方把窗口的交易日传进来
      （dates），这样所有标的都按**同一批交易日**判定；传 None 就退回用序列自己的最后 3 行。
    · 累计涨跌幅用**复利** `∏(1 + pct) − 1` —— 收益率类一律复利（见 README 的复权纪律）。
    · RS 是「百分点差值」不是收益率，3 日累积**直接相加**。
    · 数据不够（这几天里缺任何一天，比如停牌）时**不否掉** —— 宁可多展示一个，
      也不因为缺数把它藏起来。
    · 只看 AbsCost（原始成本），不看 RelCost：这条规则问的是「绝对地费不费劲」。

只读内存里的因子序列（factor 算好的行），不碰数据库。
"""

from __future__ import annotations

DAYS = 3            # 「最近几天」：连续下跌 / 累计涨幅都看这 3 天
CHG_FLOOR = 0.0     # 近 3 日累计涨跌幅 < 0 就否掉
RS_FLOOR = -0.01    # 近 3 日累积 RS < -1% 就否掉
COST_FLOOR = 1.0    # 最新一天 AbsCost < 1 才可能触发第 ② 条


def _window(rows, days: int, dates=None) -> list[dict] | None:
    """取「最近 days 天」的行；凑不齐返回 None（判定不了）。

    dates 给了就按这些交易日取（表格的最后 days 列）—— 缺任何一天都算判定不了；
    没给就按序列自己的最后 days 行。
    """
    if dates is not None:
        want = list(dates)[-days:]
        if len(want) < days:
            return None
        rows_by_date = {r.get("date"): r for r in (rows or [])}
        got = [rows_by_date[d] for d in want if d in rows_by_date]
        return got if len(got) == days else None
    tail = list(rows or [])[-days:]
    return tail if len(tail) == days else None


def _values(rows, key: str, days: int, dates=None) -> list | None:
    """最近 days 天的某个字段；凑不齐 days 天、或者中间有 None，就返回 None。"""
    window = _window(rows, days, dates)
    if window is None:
        return None
    vals = [r.get(key) for r in window]
    return None if any(v is None for v in vals) else vals


def cum_change(rows, days: int = DAYS, dates=None) -> float | None:
    """近 days 个交易日的累计涨跌幅（复利，小数）；算不出来返回 None。

    行里的 chg 已经是小数（0.0123 = +1.23%）。
    """
    vals = _values(rows, "chg", days, dates)
    if vals is None:
        return None
    out = 1.0
    for v in vals:
        out *= 1.0 + v
    return out - 1.0


def cum_rs(rows, days: int = DAYS, dates=None) -> float | None:
    """近 days 个交易日的累积 RS（小数，百分点相加）；算不出来返回 None。"""
    vals = _values(rows, "rs", days, dates)
    return None if vals is None else sum(vals)


def cost_easing(rows, days: int = DAYS, floor: float = COST_FLOOR, dates=None) -> bool:
    """AbsCost 连续 days 天**严格下跌**、且最新一天 < floor —— 成本越来越省、已经省到没有。

    任何一天缺 AbsCost（比如当天涨跌幅为 0，成本算不出来）都算判定不了，返回 False。
    """
    vals = _values(rows, "abs_cost", days, dates)
    if vals is None or vals[-1] >= floor:
        return False
    return all(vals[i] < vals[i - 1] for i in range(1, len(vals)))


def board_rejected(rows, days: int = DAYS, dates=None) -> bool:
    """板块：近 3 日累计涨跌幅 < 0 **或** AbsCost 连续 3 天下跌且 < 1。"""
    chg = cum_change(rows, days, dates)
    return (chg is not None and chg < CHG_FLOOR) or cost_easing(rows, days, dates=dates)


def stock_rejected(rows, days: int = DAYS, dates=None) -> bool:
    """个股：近 3 日累积 RS < -1% **或** AbsCost 连续 3 天下跌且 < 1。"""
    rs = cum_rs(rows, days, dates)
    return (rs is not None and rs < RS_FLOOR) or cost_easing(rows, days, dates=dates)
