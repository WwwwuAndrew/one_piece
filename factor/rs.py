#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor/rs.py —— 相对板块强度（Relative Strength）。

    RS     = 个股当日涨跌幅 − 二级板块当日涨跌幅         （单日跑赢多少，%）
    RS偏离  = RS − Median(过去 20 日 RS)                 （跑赢在加强还是衰减）

个股层因子之一（见 doc/model.md 个股层）。只读本地数据库，不联网。
用差值（不用比值）：从「跑输」转「跑赢」时比值 ÷ 负数会符号反；差值恒正确。
基准用中位数（典型水平），不被个别爆拉日拉偏。
"""

from __future__ import annotations

from datasource.store import store as default_store

WINDOW = 20     # 基准窗口（过去 20 日）
DAYS = 10       # debug 显示最近多少天


def _median(values) -> float | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    n = len(values)
    mid = n // 2
    return values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2


class RS:
    """相对板块强度计算。db 默认 data/raw/raw.sqlite。"""

    def __init__(self, db=None):
        self.db = db if db is not None else default_store

    def compute_stock(self, code, days=DAYS) -> list[dict] | None:
        """算一只个股最近 days 天的相对板块强度；没数据返回 None。"""
        code = str(code).strip().zfill(6)
        need = WINDOW + days
        own = self.db.load_stock_daily(codes=[code])[-need:]
        if not own:
            return None

        # 该股所属的二级板块
        board = None
        for b in self.db.boards_of(code):
            if self.db.board_level(b) == 2:
                board = b
                break
        board_rows = self.db.load_board_daily(board, days=need) if board else []

        own_map = {r["trade_date"]: r["pct_chg"] for r in own}
        board_map = {r["trade_date"]: r["change_pct"] for r in board_rows}

        # 逐日 RS（个股涨跌幅 − 板块涨跌幅），转成小数
        rs_map: dict[int, float] = {}
        for d, oc in own_map.items():
            bc = board_map.get(d)
            if oc is None or bc is None:
                continue
            rs_map[d] = (oc - bc) / 100.0

        dates = sorted(rs_map)[-days:]
        out = []
        for d in dates:
            rs = rs_map[d]
            hist = [rs_map[x] for x in sorted(rs_map) if x <= d][-WINDOW:]
            med = _median(hist)
            out.append({
                "code": code,
                "name": self.db.name_of("stock", code) or "",
                "kind": "stock",
                "date": d,
                "rs": round(rs, 6),
                "rs_dev": round(rs - med, 6) if (rs is not None and med is not None) else None,
            })
        return out or None
