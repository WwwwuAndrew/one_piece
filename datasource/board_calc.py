#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
board_calc.py —— 板块聚合日线：从「全市场个股日线 + 成分股」本地算出板块指标。

    calc = BoardCalc()
    r = calc.compute()            # 增量：只算 board_daily 里还没有的交易日
    r = calc.compute(force=True)  # 全量重算：先清空 board_daily 再从头算

一句话原理
    板块不是独立行情，它的「成交额 / 涨跌家数 / 涨跌幅」都是成分股对应指标的聚合。
    所以只要手里有「每只股票每天的行情」+「每个板块有哪些股票」，就能在本地算出
    任何板块、任何历史交易日的板块指标 —— 不需要东财 / 申万给板块行情。

计算公式（对某个板块、某个交易日，对其全部成分股求和）：

    amount      成交额 = Σ 成分股当日成交额 amount_i            （单位：元）
    volume      成交量 = Σ 成分股当日成交量 volume_i            （单位：股）
    up          上涨家数 = #{ i | pct_chg_i > 0 }
    down        下跌家数 = #{ i | pct_chg_i < 0 }
    flat        平盘家数 = #{ i | pct_chg_i = 0 }
    change_pct  涨跌幅   = Σ (pct_chg_i × w_i) / Σ w_i          （市值加权，单位：%）

    权重 w_i = 成分股总市值 mktcap_i（concept_member.mktcap，单位元）。
    这个市值是**快照**：update board 时由乐咕写入，之后每天 fetch market 用
    tushare daily_basic 刷一次，所以它总是「最近同步那天」的市值。
    某只成分股缺市值时，用**本板块有市值成员的中位数**补它的权重（见下面的 ⚠️）。

举例（只讲 change_pct，其它都是直接求和/计数）：
    某板块某日 3 只成分股：
        股 A：pct_chg = +10%，市值 100 亿
        股 B：pct_chg =  -5%，市值 300 亿
        股 C：pct_chg =  +2%，市值 100 亿
    市值加权涨跌幅：
        = (10 × 100 + (-5) × 300 + 2 × 100) / (100 + 300 + 100)
        = (1000 - 1500 + 200) / 500
        = -300 / 500 = -0.6%
    而等权平均是 (10 - 5 + 2) / 3 = +2.33%。两者差这么多，正是「大票 B 下跌拖累板块」
    这件事被市值加权体现出来了 —— 这就是为什么不做简单平均。

⚠️ 边界：
    · 停牌 / 当天没行情的股票，tushare daily 里没有那行，直接跳过（不参与任何求和）。
    · 新股没有 pct_chg（无昨收）：只计它的成交额/成交量，不计涨跌家数和涨跌幅。
    · **缺市值的股票，权重取「本板块有市值成员的中位数」** —— 不能像以前那样给 w=1：
      别的成员是 1e10 量级，给 1 等于把这只票**整个踢出**加权平均（那不是「退化为等权」，
      而是「当它不存在」，还会让剩余成员的权重悄悄变大）。本板块一个市值都没有时，
      才真的退化成等权（所有 w 都相等）。
    · 板块当天一只成分股都没有成交 -> 不写 board_daily 那一行（避免「全 0」误导）。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .store import store as default_store


def _median(values) -> float | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    n = len(values)
    mid = n // 2
    return values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2


@dataclass
class BoardResult:
    """一次 compute 的结果，方便上层报告。"""

    days: int = 0            # 本次算了几个交易日
    boards: int = 0          # 涉及几个板块（有成分股的）
    rows: int = 0            # 入库几行
    skipped_days: int = 0    # 本地已有、跳过的交易日数
    missing_caps: int = 0    # 多少条成分股关系缺市值（权重按中位数补的）


class BoardCalc:
    """板块聚合计算。db 默认 data/raw/raw.sqlite。"""

    def __init__(self, db=None):
        self.db = db if db is not None else default_store

    # -- 主入口 -----------------------------------------------------------
    def compute(self, force: bool = False) -> BoardResult:
        """算板块日线并入库。

        force=False：增量 —— 只算 stock_daily 里有、board_daily 里还没有的交易日；
        force=True ：全量 —— 先清空 board_daily，再对所有交易日重算。
        """
        stock_days = [r["trade_date"] for r in self.db.query(
            "SELECT DISTINCT trade_date FROM stock_daily ORDER BY trade_date")]

        if force:
            self.db.delete_board_daily()
            todo = stock_days
            skipped = 0
        else:
            have = {r["trade_date"] for r in self.db.query(
                "SELECT DISTINCT trade_date FROM board_daily")}
            todo = [d for d in stock_days if d not in have]
            skipped = len(stock_days) - len(todo)

        if not todo:
            return BoardResult(skipped_days=skipped)

        members_by_concept = self._load_members()
        boards = len(members_by_concept)
        missing = sum(1 for ms in members_by_concept.values()
                      for _, cap in ms if not (cap and cap > 0))

        result = BoardResult(boards=boards, skipped_days=skipped, missing_caps=missing)
        for day in todo:
            rows = self._aggregate_day(day, members_by_concept)
            if rows:
                self.db.save_board_daily(rows)
                result.rows += len(rows)
            result.days += 1
        return result

    # -- 数据准备 ---------------------------------------------------------
    def _load_members(self) -> dict[str, list[tuple[str, float | None]]]:
        """全部成分股，按板块分组：concept -> [(code, mktcap), …]。

        mktcap 是总市值（元），市值加权权重；None 表示拿不到、按等权处理。
        """
        by_concept: dict[str, list[tuple[str, float | None]]] = defaultdict(list)
        for m in self.db.load_all_members():
            by_concept[m["concept"]].append((m["code"], m.get("mktcap")))
        return dict(by_concept)

    def _aggregate_day(self, trade_date: int,
                       members_by_concept: dict[str, list[tuple[str, float | None]]]
                       ) -> list[dict]:
        """算一个交易日、所有板块的聚合行（不含 trade_date/concept，调用方再补）。

        先把这个交易日的全市场个股日线读进内存 dict（code -> row），
        再对每个板块迭代它的成分股做聚合 —— 一次读全市场，复用给所有板块。
        """
        day = {r["code"]: r for r in self.db.query(
            "SELECT code, pct_chg, amount, volume FROM stock_daily "
            "WHERE trade_date = ?", (trade_date,))}

        out: list[dict] = []
        for concept, members in members_by_concept.items():
            agg = self._aggregate(members, day)
            if agg is None:
                continue
            out.append({"concept": concept, "trade_date": trade_date, **agg})
        return out

    @staticmethod
    def _aggregate(members: list[tuple[str, float | None]],
                   day: dict) -> dict | None:
        """对一个板块的成分股做聚合，返回一行（不含 concept/trade_date）。

        返回 None 表示「这个板块当天没有任何成分股有成交」，不写库。
        各字段算法见模块 docstring 的公式。
        """
        amount = 0.0
        volume = 0.0
        up = down = flat = 0
        w_sum = 0.0     # 权重和
        w_chg = 0.0     # 权重 × 涨跌幅 之和

        # 缺市值的股票拿什么当权重：本板块**有市值成员的中位数**。
        # 给 w=1 是错的 —— 别的成员是 1e10 量级，w=1 等于把这只票踢出去（还顺手放大了别人的权重）。
        # 全班都没有市值时才落到 1.0，那时所有票同权，是真的等权。
        fallback_w = _median([cap for _, cap in members if cap and cap > 0]) or 1.0

        for code, mktcap in members:
            row = day.get(code)
            if row is None:
                continue                     # 停牌 / 当天无行情：跳过
            if row["amount"]:
                amount += row["amount"]
            if row["volume"]:
                volume += row["volume"]

            chg = row["pct_chg"]
            if chg is None:
                continue                     # 新股无涨跌幅：只计成交，不计涨跌
            if chg > 0:
                up += 1
            elif chg < 0:
                down += 1
            else:
                flat += 1

            w = mktcap if (mktcap and mktcap > 0) else fallback_w
            w_chg += chg * w
            w_sum += w

        if not amount:
            return None                       # 无成交，不写「全 0」的假行

        return {
            "source": "local",
            "change_pct": round(w_chg / w_sum, 4) if w_sum else None,
            "amount": amount,
            "volume": volume,
            "up": up,
            "down": down,
            "flat": flat,
        }
