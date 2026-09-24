#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor/cost.py —— Cost · 推进成本：推高/砸低价格要吃掉多少换手（单日、不分方向）。

口径见 doc/model.md §3.2：

    AbsCost = 当日换手率 ÷ |当日涨跌幅|
    板块 RelCost = AbsCost ÷ Median(过去 20 日 AbsCost，不含当天)    —— 相对自身历史
    个股 RelCost = AbsCost ÷ Median(同板块其余个股当日 AbsCost)       —— 相对同伴

它本身不带方向（用 |涨跌幅|），方向由 participation 的「chg」单独看。
只读本地数据库（board_daily / stock_daily / concept_member），不联网。
"""

from __future__ import annotations

from datasource.store import store as default_store

RELBASE = 20    # RelCost 的基准窗口（过去 20 日，不含当天）
DAYS = 10       # debug 显示最近多少天


def _median(values) -> float | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    n = len(values)
    mid = n // 2
    return values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2


def _chg(r) -> float | None:
    """涨跌幅（%）：板块表叫 change_pct，个股表叫 pct_chg。"""
    v = r.get("change_pct")
    return r.get("pct_chg") if v is None else v


class Cost:
    """推进成本计算。db 默认 data/raw/raw.sqlite。"""

    def __init__(self, db=None):
        self.db = db if db is not None else default_store

    # -- 市值（快照）------------------------------------------------------
    def _board_cap(self, concept) -> float | None:
        row = self.db.query(
            "SELECT SUM(mktcap) AS s FROM concept_member WHERE concept = ?",
            (concept,))[0]
        return row["s"] or None

    def _stock_cap(self, code) -> float | None:
        rows = self.db.query(
            "SELECT mktcap FROM concept_member "
            "WHERE code = ? AND mktcap IS NOT NULL LIMIT 1", (code,))
        return rows[0]["mktcap"] if rows else None

    # -- 入口 -------------------------------------------------------------
    def compute_board(self, concept, days=DAYS) -> list[dict] | None:
        """算一个板块（一级或二级）最近 days 天的推进成本；没数据返回 None。"""
        concept = str(concept).strip().upper()
        rows = self.db.load_board_daily(concept, days=days + RELBASE)
        if not rows:
            return None
        return self._series(code=concept,
                            name=self.db.name_of("board", concept) or "",
                            kind="board", rows=rows,
                            cap=self._board_cap(concept), days=days)

    def compute_stock(self, code, days=DAYS) -> list[dict] | None:
        """算一只个股最近 days 天的推进成本；没数据返回 None。

        个股的 RelCost = AbsCost ÷ 同板块其余个股当日 AbsCost 的中位数（横向比同伴，
        不是比自身历史）。
        """
        code = str(code).strip().zfill(6)
        own = self.db.load_stock_daily(codes=[code])[-days:]
        if not own:
            return None
        cap = self._stock_cap(code)
        if not cap:
            return None

        # 该股所属的二级板块 + 其余成员
        board = None
        for b in self.db.boards_of(code):
            if self.db.board_level(b) == 2:
                board = b
                break
        members = self.db.load_board_members_weighted(board) if board else []
        cap_map = {m["code"]: m["mktcap"] for m in members if m.get("mktcap")}
        peer_codes = [m["code"] for m in members if m["code"] != code]

        # 同伴每日 AbsCost（换手率 ÷ |涨跌幅|）
        peer_abs: dict[int, list[float]] = {}
        if peer_codes:
            peer_rows = self.db.load_stock_daily(
                codes=peer_codes, start=own[0]["trade_date"], end=own[-1]["trade_date"])
            for r in peer_rows:
                pc = r.get("pct_chg")
                pcap = cap_map.get(r["code"])
                if pc is None or pc == 0 or not pcap:
                    continue
                peer_abs.setdefault(r["trade_date"], []).append(
                    (r.get("amount") or 0.0) / pcap / (abs(pc) / 100.0))

        out = []
        for r in own:
            chg = r.get("pct_chg")
            abs_cost = None
            if chg is not None and chg != 0:
                abs_cost = (r.get("amount") or 0.0) / cap / (abs(chg) / 100.0)
            med = _median(peer_abs.get(r["trade_date"], []))
            rel = (abs_cost / med) if (abs_cost is not None and med) else None
            out.append({"date": r["trade_date"], "abs_cost": abs_cost, "rel_cost": rel})

        result = []
        for s in out:
            result.append({
                "code": code, "name": self.db.name_of("stock", code) or "",
                "kind": "stock", "date": s["date"],
                "abs_cost": round(s["abs_cost"], 4) if s["abs_cost"] is not None else None,
                "rel_cost": round(s["rel_cost"], 4) if s["rel_cost"] is not None else None,
            })
        return result or None

    # -- 核心 -------------------------------------------------------------
    def _series(self, *, code, name, kind, rows, cap, days) -> list[dict] | None:
        if not cap:
            return None

        # 1) 每日 AbsCost = 换手率 ÷ |涨跌幅|（涨跌幅为 0 记 None）
        daily: list[dict] = []
        for r in rows:
            chg = _chg(r)
            if chg is None or chg == 0:
                abs_cost = None
            else:
                turn = (r.get("amount") or 0.0) / cap
                abs_cost = turn / (abs(chg) / 100.0)
            daily.append({"date": r["trade_date"], "abs_cost": abs_cost})

        # 2) RelCost = AbsCost ÷ 过去 20 日中位数（不含当天）
        out = []
        for i in range(len(daily)):
            baseline = [d["abs_cost"] for d in daily[max(0, i - RELBASE):i]]
            base_med = _median(baseline)
            abs_cost = daily[i]["abs_cost"]
            rel = (abs_cost / base_med) if (abs_cost is not None and base_med) else None
            out.append({"date": daily[i]["date"], "abs_cost": abs_cost, "rel_cost": rel})

        # 3) 取最近 days 天
        result = []
        for s in out[-days:]:
            result.append({
                "code": code, "name": name, "kind": kind, "date": s["date"],
                "abs_cost": round(s["abs_cost"], 4) if s["abs_cost"] is not None else None,
                "rel_cost": round(s["rel_cost"], 4) if s["rel_cost"] is not None else None,
            })
        return result or None
