#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
console.py —— 所有给人看的输出。

命令只负责「发生了什么」，措辞、缩进、emoji 全归这里；要改文案只动这一个文件。

    c = Console(db)
    c.say("…")          打印到 stdout
    c.err("…")          打印到 stderr
    c.db_state()        库里的状态报告

db 只用来查名字和统计，全是只读。
"""

from __future__ import annotations

import sys

from datasource.store import date_to_str, store


def fmt_eta(n: int, interval: float) -> str:
    """N 个请求按 interval 间隔大约要多久。"""
    sec = max(n - 1, 0) * interval
    return f"{sec:.0f} 秒" if sec < 90 else f"{sec / 60:.1f} 分钟"


def _fmt_ratio(v) -> str:
    """因子比值（AbsFlow / RelFlow 这类），拿不到就是 —。"""
    return "—" if v is None else f"{v:.3f}"


def _fmt_cost(v) -> str:
    """推进成本这类无量纲数值，拿不到就是 —。"""
    return "—" if v is None else f"{v:.2f}"


def _fmt_pct(v) -> str:
    """把小数（0.0915）显示成带符号的百分比（+9.15%）。"""
    return "—" if v is None else f"{v * 100:+.2f}%"


class Console:
    """输出的唯一出口。"""

    def __init__(self, db=None):
        self.db = db if db is not None else store

    # -- 原语 ---------------------------------------------------------------
    def say(self, text: str = "") -> None:
        print(text)

    def err(self, text: str) -> None:
        print(text, file=sys.stderr)

    def failed(self, exc: Exception) -> None:
        self.err(f"\n❌ {exc}")

    def label(self, code: str, kind: str) -> str:
        """「代码 名字」——名字取不到就只留代码。"""
        return f"{code} {self.db.name_of(kind, code) or ''}".strip()

    def span(self, items: list[str], limit: int = 12) -> str:
        """一串代码/名称，超过 limit 个就用省略号收尾。"""
        return ", ".join(items[:limit]) + (" …" if len(items) > limit else "")

    # -- 库里有什么 ---------------------------------------------------------
    def db_state(self) -> None:
        r = self.db.query("SELECT COUNT(*) AS n, COUNT(DISTINCT trade_date) AS d, "
                          "MIN(trade_date) AS a, MAX(trade_date) AS b FROM stock_daily")[0]
        if not r["n"]:
            self.say("   库里 stock_daily：空")
            return
        self.say(f"   库里 stock_daily：{r['n']:,} 行 / {r['d']} 个交易日"
                 f"（{date_to_str(r['a'])} ~ {date_to_str(r['b'])}）")

    def day_health(self) -> None:
        """体检：每个交易日的行数都该接近全市场只数，明显偏少就是那天没取全。"""
        rows = self.db.query("SELECT trade_date, COUNT(*) AS n FROM stock_daily "
                             "GROUP BY trade_date ORDER BY trade_date")
        if len(rows) < 2:
            return

        counts = {r["trade_date"]: r["n"] for r in rows}
        full = max(counts.values())
        thin = [(d, n) for d, n in counts.items() if n < full * 0.95]

        self.say(f"   体检：{len(counts)} 个交易日，每天 {min(counts.values()):,} ~ {full:,} 行")
        if not thin:
            return
        self.say(f"   ⚠️  有 {len(thin)} 天明显偏少（不到全市场的 95%）：")
        for d, n in thin[:8]:
            self.say(f"        {date_to_str(d)}  只有 {n:,} 行（少了 {full - n:,}）")
        if len(thin) > 8:
            self.say(f"        … 还有 {len(thin) - 8} 天")
        self.say("      这些天大概没取全，可以用 backfill market --days <覆盖到这些天> --force 重拉")

    def board_state(self) -> None:
        r = self.db.query("SELECT COUNT(*) AS n, COUNT(DISTINCT concept) AS c, "
                          "COUNT(DISTINCT trade_date) AS d, MIN(trade_date) AS a, "
                          "MAX(trade_date) AS b FROM board_daily")[0]
        if not r["n"]:
            self.say("   库里 board_daily：空")
            return
        self.say(f"   库里 board_daily：{r['n']:,} 行 / {r['c']} 个板块 / {r['d']} 个交易日"
                 f"（{date_to_str(r['a'])} ~ {date_to_str(r['b'])}）")

    def member_state(self) -> None:
        r = self.db.query("SELECT COUNT(*) AS n, COUNT(DISTINCT concept) AS c "
                          "FROM concept_member")[0]
        self.say(f"   库里 concept_member：{r['n']:,} 条 / {r['c']} 个板块有成员名单")

    # -- 全市场日线 ---------------------------------------------------------
    def market_start(self) -> None:
        self.say("拉全市场日线（tushare，1 个请求）…")

    def market_skipped(self, day: str, total: int) -> None:
        self.say(f"⏭  {day} 本地已有 {total:,} 行，跳过（要重拉：backfill market "
                 f"--days 1 --force）")

    def market_empty(self, day: str | None) -> None:
        self.say(f"⚠️  {day} 没有数据（非交易日，或数据还没发布）")

    def market_done(self, day: str, fetched: int, added: int, auto_day: bool) -> None:
        self.say(f"✅ {day}  全市场 {fetched:,} 只")
        self.say(f"   入库 {added:,} 行"
                 + (f"，{fetched - added:,} 行本地已有（跳过）" if fetched > added else ""))
        if auto_day:
            self.say("   （没指定日期，自动往回找到最近有数据的一天）")

    def backfill_nothing(self) -> None:
        self.say("⚠️  没排到任何交易日，无事可做")

    def backfill_plan(self, plan: list[dict], have: list[dict], todo: list[dict],
                      interval: float, force: bool) -> bool:
        """报告补历史的计划；返回「有没有活要干」。"""
        self.say(f"计划：最近 {len(plan)} 个交易日  {plan[-1]['day']} ~ {plan[0]['day']}")
        if have:
            self.say(f"      本地已有 {len(have)} 天：" + ", ".join(p["day"] for p in have))
        if force and have:
            self.say("      ⚠️  --force：上面这些天也重拉（先删后写，只影响它们）")
        if not todo:
            self.say("      ✅ 全都已经有了，不用拉（要重拉加 --force）")
            return False
        self.say(f"      需要拉 {len(todo)} 天（每天 1 个请求，间隔 {interval:g} 秒，"
                 f"约 {fmt_eta(len(todo), interval)}）")
        if len(todo) > 45 and interval < 1.2:
            self.say("      ⚠️  fetch_interval < 1.2 秒：tushare 限 50 次/分钟，可能被限流")
        self.say()
        return True

    def backfill_failed(self, i: int, n: int, day: str, exc: Exception) -> None:
        self.say(f"  [{i}/{n}] ❌ {day}  {exc}")

    def backfill_empty(self, i: int, n: int, day: str) -> None:
        self.say(f"  [{i}/{n}] ⏭  {day}  那天没数据（非交易日 / 还没发布）")

    def backfill_step(self, i: int, n: int, day: str, fetched: int, added: int,
                      force: bool) -> None:
        self.say(f"  [{i}/{n}] ✅ {day}  {fetched:,} 只"
                 + (f"（重拉，新增 {added:,}）" if force else ""))

    def backfill_done(self, done: int, empty: int, failed: int, added: int) -> None:
        self.say(f"\n完成：{done} 天入库"
                 + (f"，{empty} 天没数据" if empty else "")
                 + (f"，{failed} 天失败" if failed else "")
                 + f"（本次新增 {added:,} 行）")

    def backfill_retry_hint(self) -> None:
        self.say("   失败的天重跑一次同样的命令就会接着补（本地已有的会跳过）")

    # -- 板块计算（fetch board，纯本地）------------------------------------
    def board_calc_start(self, force: bool) -> None:
        self.say("本地计算板块日线…" + ("（--force 全量重算）" if force else "（增量：只算缺的交易日）"))

    def board_calc_done(self, res) -> None:
        if not res.days:
            self.say(f"⏭  板块日线已是最新（{res.skipped_days} 个交易日都有），不用算")
            return
        self.say(f"✅ 算了 {res.days} 个交易日 / {res.boards} 个板块，入库 {res.rows:,} 行"
                 + (f"（另有 {res.skipped_days} 个交易日本地已有，跳过）" if res.skipped_days else ""))

    # -- 板块定义（update board，乐咕）-------------------------------------
    def update_board_start(self) -> None:
        self.say("拉申万一/二级板块 + 成分股（乐咕）…")
        self.say("   ⚠️ 乐咕限流约 6~7 请求/分钟，162 个板块约需 25~30 分钟。")
        self.say("      已同步的会跳过；失败的重跑同一命令会接着补（断点续跑）。")

    def update_board_done(self, n: int, counts: dict[int, int], synced: int,
                          failed: int, skipped: int, removed: list[str]) -> None:
        self.say(f"\n✅ 板块表已入库：一级 {counts.get(1, 0)} / 二级 {counts.get(2, 0)}"
                 f"（共 {n} 个）")
        self.say(f"   成分股：同步 {synced} 个"
                 + (f"，跳过 {skipped} 个（今天已同步）" if skipped else "")
                 + (f"，失败 {failed} 个" if failed else ""))
        if removed:
            self.say(f"   申万已删除的板块清理掉 {len(removed)} 个：{', '.join(removed[:8])}")
        if failed:
            self.say("   ⚠️ 有失败：重跑同一命令会接着补（已同步的今天会跳过；明天再跑则全部重拉最新）")
        self.say(f"\n   fetch board 现在会本地计算这 {n} 个板块的指标")
        self.say("   show board 会按一级分标签，每个一级下面依次列出它的二级")

    def member_step(self, i: int, n: int, code: str, name: str, count: int,
                    added: int, removed: int) -> None:
        diff = f"  (+{added}/-{removed})" if (added or removed) else ""
        label = f"{code} {(name or '').strip()}".strip()
        self.say(f"  [{i}/{n}] ✅ {label}  {count} 只{diff}")

    def member_skip(self, i: int, n: int, code: str, name: str) -> None:
        label = f"{code} {(name or '').strip()}".strip()
        self.say(f"  [{i}/{n}] ⏭  {label}  今天已同步，跳过")

    def member_step_failed(self, i: int, n: int, code: str, exc: Exception) -> None:
        self.say(f"  [{i}/{n}] ❌ {code}  {exc}")

    # -- 自选 ---------------------------------------------------------------
    def unknown_code(self, raw: str, hint: bool = True) -> None:
        tail = "（板块形如 801080.SI，个股形如 300308）" if hint else ""
        self.err(f"❌ 认不出的代码：{raw.strip()}{tail}")

    def unwatch_needs_codes(self) -> None:
        self.err("❌ unwatch 要跟代码，例如：python3 hunter.py unwatch 801080.SI")

    def watch_exists(self, code: str, kind: str) -> None:
        self.say(f"· 已在自选：{self.label(code, kind)}")

    def watch_added(self, code: str, kind: str) -> None:
        self.say(f"✅ 加入自选：{self.label(code, kind)}"
                 f"  [{'板块' if kind == 'board' else '个股'}]")

    def watch_removed(self, code: str, kind: str) -> None:
        self.say(f"✅ 移出自选：{self.label(code, kind)}")

    def watch_absent(self, code: str, kind: str) -> None:
        self.say(f"· 本来就不在自选：{self.label(code, kind)}")

    def watch_hint(self) -> None:
        self.say("   （只改了本地状态，没联网。要看数据：python3 hunter.py show watch）")

    def no_watch(self, boards_only: bool = False) -> None:
        what = "自选里没有板块" if boards_only else "自选是空的"
        self.say(f"⚠️  {what}。先加一个：python3 hunter.py watch 801080.SI")

    def watchlist_empty(self) -> None:
        self.say("自选是空的。看到感兴趣的加进来：")
        self.say("      python3 hunter.py watch 801080.SI     # 板块")
        self.say("      python3 hunter.py watch 300308        # 个股")

    def watchlist(self, items: list[dict], counts: dict[str, int]) -> None:
        boards = [w for w in items if w["kind"] == "board"]
        stocks = [w for w in items if w["kind"] == "stock"]
        self.say(f"自选 {len(items)} 个（板块 {len(boards)} / 个股 {len(stocks)}）\n")
        for title, group in (("板块", boards), ("个股", stocks)):
            if not group:
                continue
            self.say(f"  [{title}]")
            for w in group:
                name = w["name"] or self.db.name_of(w["kind"], w["code"]) or "—"
                n = counts.get(w["code"], 0)
                mark = f"{n} 个交易日" if n else "⚠️ 本地还没有数据"
                note = f"   {w['note']}" if w["note"] else ""
                self.say(f"    {w['code']}  {name:<8}  {date_to_str(w['watched_at'])} 起"
                         f"   {mark}{note}")
        self.say("\n  看数据：python3 hunter.py show watch")
        self.say("  移出：  python3 hunter.py unwatch <代码>")

    # -- 删板块 -------------------------------------------------------------
    def bad_board_code(self, text: str, blank_line: bool = False) -> None:
        self.err(("\n" if blank_line else "") + f"❌ 板块代码应形如 801080.SI，收到：{text}")

    def drop_nothing(self, code: str) -> None:
        self.say(f"· 本地本来就没有 {code} 的任何数据")

    def drop_report(self, code: str, before: dict[str, int]) -> None:
        self.say(f"删除 {self.label(code, 'board')}：")
        self.say(f"    行情 {before['board_daily']} 个交易日"
                 f" / 成员 {before['concept_member']} 只"
                 + ("  / 板块字典那行" if before["board_list"] else ""))

    def drop_done(self, code: str, gone: dict[str, int], still_watched: bool) -> None:
        self.say(f"  ✅ 已删（board_daily {gone['board_daily']} 行，"
                 f"concept_member {gone['concept_member']} 行，"
                 f"board_list {gone['board_list']} 行）")
        if still_watched:
            self.say("  ⚠️  它还在自选里 —— 数据没了但标记还在。要一起清掉："
                     f"python3 hunter.py unwatch {code}")
        self.say(f"  拿回来：python3 hunter.py update board + python3 hunter.py fetch board")

    # -- 展示 ---------------------------------------------------------------
    def need_tree_for_show(self) -> None:
        """show board 发现还没有层级表。"""
        self.say("⚠️  还没有一级/二级板块表。先跑一次：")
        self.say("      python3 hunter.py update board")

    def show_watch_all(self, codes: list[str]) -> None:
        self.say(f"自选 {len(codes)} 个，全部展示：" + ", ".join(codes))

    def show_unknown(self, raw: str) -> None:
        self.err(f"❌ 认不出的写法：{raw}（板块形如 801080.SI，个股形如 300308，"
                 f"或关键字 board / watch）")

    def show_grouped_plan(self, groups: list[tuple[str, list[str]]]) -> None:
        n2 = sum(len(k) for _, k in groups)
        self.say(f"一级 {len(groups)} 个标签，每个下面排它自己的二级（共 {n2} 个）")

    # -- debug（因子） -----------------------------------------------------
    def debug_no_data(self, code: str) -> None:
        self.say(f"⚠️  {code} 本地还没有数据（先跑：python3 hunter.py fetch market / fetch board）")

    def board_series(self, prows: list[dict], crows: list[dict] | None) -> None:
        """单个标的最近 N 天的「参与度 + 推进成本」合并序列（debug show <代码>）。"""
        if not prows:
            return
        r0 = prows[0]
        kind_cn = "板块" if r0["kind"] == "board" else "个股"
        cmap = {r["date"]: r for r in (crows or [])}
        line = "=" * 74
        self.say(f"\n{line}")
        self.say(f"  {r0['code']}  {r0['name'] or '—'}   [{kind_cn}]")
        self.say(f"  最近 {len(prows)} 个交易日（AbsPart=成交额÷MA20，RelPart=相对同级；"
                 f"AbsCost=换手÷|涨幅|，RelCost=AbsCost÷前20日中位）")
        self.say(line)
        from tabulate import tabulate
        table = []
        for p in prows:
            c = cmap.get(p["date"]) or {}
            table.append([
                date_to_str(p["date"]) or "",
                _fmt_ratio(p["abs_part"]),
                _fmt_ratio(p["rel_part"]),
                _fmt_pct(p["chg"]),
                _fmt_cost(c.get("abs_cost")),
                _fmt_cost(c.get("rel_cost")),
            ])
        self.say(tabulate(table, headers=["日期", "AbsPart", "RelPart", "涨幅",
                                          "AbsCost", "RelCost"],
                          tablefmt="simple", stralign="right", disable_numparse=True))
        self.say(line)

    def stock_series(self, prows: list[dict], crows: list[dict] | None,
                     rrows: list[dict] | None) -> None:
        """单个个股最近 N 天的 6 个因子序列（debug show <个股代码>）。"""
        if not prows:
            return
        r0 = prows[0]
        cmap = {r["date"]: r for r in (crows or [])}
        rmap = {r["date"]: r for r in (rrows or [])}
        line = "=" * 74
        self.say(f"\n{line}")
        self.say(f"  {r0['code']}  {r0['name'] or '—'}   [个股]")
        self.say(f"  最近 {len(prows)} 个交易日（AbsPart=成交额÷MA20，RelPart=相对同级；"
                 f"RS=相对板块强度=个股涨跌幅−板块涨跌幅，RS偏离=RS−Median20(RS)，正=跑赢加强；"
                 f"AbsCost=换手÷|涨幅|，RelCost=比同板块同伴）")
        self.say(line)
        from tabulate import tabulate
        table = []
        for p in prows:
            c = cmap.get(p["date"]) or {}
            rs = rmap.get(p["date"]) or {}
            table.append([
                date_to_str(p["date"]) or "",
                _fmt_ratio(p["abs_part"]),
                _fmt_ratio(p["rel_part"]),
                _fmt_pct(rs.get("rs")),
                _fmt_pct(rs.get("rs_dev")),
                _fmt_cost(c.get("abs_cost")),
                _fmt_cost(c.get("rel_cost")),
            ])
        self.say(tabulate(table, headers=["日期", "AbsPart", "RelPart", "RS", "RS偏离",
                                          "AbsCost", "RelCost"],
                          tablefmt="simple", stralign="right", disable_numparse=True))
        self.say(line)

    def stock_cross(self, rows: list[dict]) -> None:
        """一个板块内所有个股的最新一天 6 因子（debug show stock <板块代码>）。"""
        if not rows:
            return
        line = "=" * 74
        self.say(f"\n{line}")
        self.say(f"  板块内个股 {len(rows)} 只 · 最新一天（按 RS 从高到低排）")
        self.say(line)
        from tabulate import tabulate
        table = [[r["code"], (r["name"] or "")[:6],
                  _fmt_ratio(r["abs_part"]), _fmt_ratio(r["rel_part"]),
                  _fmt_pct(r["rs"]), _fmt_pct(r["rs_dev"]),
                  _fmt_cost(r["abs_cost"]), _fmt_cost(r["rel_cost"])]
                 for r in rows]
        self.say(tabulate(table, headers=["代码", "名称", "AbsPart", "RelPart", "RS",
                                          "RS偏离", "AbsCost", "RelCost"],
                          tablefmt="simple", stralign="right", disable_numparse=True))
        self.say(line)
