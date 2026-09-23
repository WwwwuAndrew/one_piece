#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor/participation.py —— Participation · 资金参与度（AbsPart + RelPart）。

口径见 doc/model.md §3.1，两个维度：

    AbsPart = V ÷ MA20(V)
        钱有没有进来 —— 当日成交额相对自己过去 20 个交易日的放大倍数。

    RelPart = AbsPart ÷ AbsPart(基准)
        钱是不是冲它来 —— 相对「同级其余部分」的放大倍数。
        基准 = 父级 − 自身（剔除自身，避免大板块被自己的放量拖累而低估）：

            全A股市场
             └─ 一级板块  →  基准 = 全A股市场 − 该一级板块
                 └─ 二级板块  →  基准 = 一级母板块 − 该二级板块
                     └─ 个股  →  基准 = 二级板块 − 该个股

每个结果行还带「chg」（当日涨跌幅，小数），用来判断资金是在推涨还是推跌。
只读本地数据库（board_daily / stock_daily / board_tree / concept_member），不联网。
"""

from __future__ import annotations

from datasource.store import store as default_store

WINDOW = 20     # MA20 窗口
DAYS = 10       # debug 显示最近多少天


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _ratio(a, b) -> float | None:
    """a ÷ b；b 是 None 或 0 就返回 None。"""
    if a is None or b is None or b == 0:
        return None
    return a / b


def _chg(r) -> float | None:
    """涨跌幅（%）：板块表叫 change_pct，个股表叫 pct_chg。"""
    v = r.get("change_pct")
    return r.get("pct_chg") if v is None else v


class Participation:
    """资金参与度计算。db 默认 data/raw/raw.sqlite。"""

    def __init__(self, db=None):
        self.db = db if db is not None else default_store
        self._market_cache: list[dict] | None = None

    # -- 成交额序列（升序）-----------------------------------------------
    def _board_series(self, concept, window) -> list[dict]:
        return self.db.load_board_daily(concept, days=window)

    def _stock_series(self, code, window) -> list[dict]:
        return self.db.load_stock_daily(codes=[code])[-window:]

    def _market_series(self, window) -> list[dict]:
        """全A股市场逐日成交额（元）。全量缓存，按需切片。"""
        if self._market_cache is None:
            rows = self.db.query(
                "SELECT trade_date, SUM(amount) AS amount FROM stock_daily "
                "GROUP BY trade_date ORDER BY trade_date")
            self._market_cache = sorted((dict(r) for r in rows),
                                        key=lambda r: r["trade_date"])
        return self._market_cache[-window:]

    def _parent_of(self, concept) -> str | None:
        rows = self.db.query("SELECT parent FROM board_tree WHERE concept = ?",
                             (concept,))
        return rows[0]["parent"] if rows else None

    # -- 入口 -------------------------------------------------------------
    def compute_board(self, concept, days=DAYS) -> list[dict] | None:
        """算一个板块（一级或二级）最近 days 天的参与度；没数据返回 None。"""
        concept = str(concept).strip().upper()
        need = WINDOW + days
        self_rows = self._board_series(concept, need)
        if not self_rows:
            return None
        if self.db.board_level(concept) == 2:
            parent = self._parent_of(concept)
            container = self._board_series(parent, need) if parent else []
        else:
            container = self._market_series(need)
        return self._series(code=concept,
                            name=self.db.name_of("board", concept) or "",
                            kind="board", self_rows=self_rows,
                            container_rows=container, days=days)

    def compute_stock(self, code, days=DAYS) -> list[dict] | None:
        """算一只个股最近 days 天的参与度；没数据返回 None。"""
        code = str(code).strip().zfill(6)
        need = WINDOW + days
        self_rows = self._stock_series(code, need)
        if not self_rows:
            return None
        # 基准 = 该股所属的二级板块；取不到就只算 AbsPart、RelPart 记 None。
        container: list[dict] = []
        for b in self.db.boards_of(code):
            if self.db.board_level(b) == 2:
                container = self._board_series(b, need)
                break
        return self._series(code=code,
                            name=self.db.name_of("stock", code) or "",
                            kind="stock", self_rows=self_rows,
                            container_rows=container, days=days)

    # -- 核心 -------------------------------------------------------------
    def _series(self, *, code, name, kind, self_rows, container_rows, days) -> list[dict]:
        self_map = {r["trade_date"]: (r.get("amount") or 0.0) for r in self_rows}
        cont_map = {r["trade_date"]: (r.get("amount") or 0.0) for r in container_rows}
        chg_map = {r["trade_date"]: _chg(r) for r in self_rows}

        self_dates = sorted(self_map)
        target_dates = self_dates[-days:]

        out: list[dict] = []
        for d in target_dates:
            amount_today = self_map.get(d)
            amount_ma20 = _mean([self_map[x] for x in self_dates if x <= d][-WINDOW:])
            abs_part = _ratio(amount_today, amount_ma20)

            rel_part = None
            if cont_map:
                cont_dates = sorted(cont_map)
                window_days = [x for x in cont_dates if x <= d][-WINDOW:]
                bench_vals = [max(cont_map[x] - self_map.get(x, 0.0), 0.0)
                              for x in window_days]
                bench_today = max(cont_map.get(d, 0.0) - self_map.get(d, 0.0), 0.0)
                bench_ma20 = _mean(bench_vals)
                bench_abs = _ratio(bench_today, bench_ma20)
                rel_part = _ratio(abs_part, bench_abs)

            chg = chg_map.get(d)
            out.append({
                "code": code, "name": name, "kind": kind, "date": d,
                "chg": round(chg / 100.0, 6) if chg is not None else None,   # 小数
                "abs_part": round(abs_part, 4) if abs_part is not None else None,
                "rel_part": round(rel_part, 4) if rel_part is not None else None,
            })
        return out
