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

from datasource import show_data
from datasource.store import date_to_str, store


def fmt_eta(n: int, interval: float) -> str:
    """N 个请求按 interval 间隔大约要多久。"""
    sec = max(n - 1, 0) * interval
    return f"{sec:.0f} 秒" if sec < 90 else f"{sec / 60:.1f} 分钟"


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

    # -- 一批标的逐个拉 -----------------------------------------------------
    def batch_header(self, label: str, n: int, days: int | None) -> None:
        self.say(f"{label}{n} 个" + (f"（最近 {days} 个交易日）" if days else "，只要最新"))

    def batch_plan(self, label: str, todo: list[str], fresh: list[str],
                   interval: float, ref: int | None) -> None:
        if fresh:
            self.say(f"  本地已是最新（交易日钟 = {date_to_str(ref)}）跳过 {len(fresh)} 个：")
            self.say("      " + self.span(fresh))
            self.say("      要强制重拉：加 --force")
        if not todo:
            self.say(f"  ✅ {label}全都已经是最新的了，一个请求都不用发")
            return
        self.say(f"  需要拉 {len(todo)} 个（每个之间间隔 {interval:g} 秒）：")
        self.say("      " + self.span(todo))
        self.say()

    def batch_step(self, i: int, n: int, code: str, kind: str, day: str, added: int) -> None:
        mark = f"新增 {added}" if added else "本地已有"
        self.say(f"  [{i}/{n}] ✅ {self.label(code, kind):<14}{day}  {mark}")

    def batch_step_failed(self, i: int, n: int, code: str, exc: Exception) -> None:
        self.say(f"  [{i}/{n}] ❌ {code}：{exc}")

    def batch_done(self, ok: int, total: int, fresh: int) -> None:
        self.say(f"\n完成：{ok}/{total} 个成功"
                 + (f"（另有 {fresh} 个本来就已经是最新的，跳过）" if fresh else ""))

    # -- 全市场日线 ---------------------------------------------------------
    def market_start(self) -> None:
        self.say("拉全市场日线（1 个请求）…")

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
        # 走 stdout：它是进度的一部分（日志里编号不能缺一块）
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

    # -- 自选 ---------------------------------------------------------------
    def unknown_code(self, raw: str, hint: bool = True) -> None:
        tail = "（板块形如 BK1201，个股形如 300308）" if hint else ""
        self.err(f"❌ 认不出的代码：{raw.strip()}{tail}")

    def unwatch_needs_codes(self) -> None:
        self.err("❌ unwatch 要跟代码，例如：python3 hunter.py unwatch BK1201")

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
        self.say("   （只改了本地状态，没联网。要看数据：python3 hunter.py fetch watch)")

    def no_watch(self, boards_only: bool = False) -> None:
        what = "自选里没有板块" if boards_only else "自选是空的"
        self.say(f"⚠️  {what}。先加一个：python3 hunter.py watch BK1201")

    def watchlist_empty(self) -> None:
        self.say("自选是空的。看到感兴趣的加进来：")
        self.say("      python3 hunter.py watch BK1201        # 板块")
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
        self.say("\n  拉数据：python3 hunter.py fetch watch")
        self.say("  看数据：python3 hunter.py show watch")
        self.say("  移出：  python3 hunter.py unwatch <代码>")

    # -- 板块成分股 ---------------------------------------------------------
    def member_error(self, exc: Exception, blocked: bool) -> None:
        self.err(f"\n❌ 拉成分股失败：{exc}")
        if blocked:
            self.err("   ⚠️ 看着像被东财拒了（连接被掐）。**别立刻重跑** —— 越敲锁得越久，"
                     "等一会儿再说。")

    def member_brief(self, diff) -> None:
        mark = ""
        if diff.added or diff.removed:
            mark = f"  (+{len(diff.added)}/-{len(diff.removed)})"
        self.say(f"  ✅ {diff.code}  {(diff.name or '?'):<8} {diff.total:>4} 只{mark}")

    def member_detail(self, diff, now: int) -> None:
        self.say(f"✅ {diff.code} {diff.name or ''}".strip())
        self.say(f"   成分股 {diff.total} 只（本地原有 {diff.old} 只）")
        if not diff.old:
            self.say("   本地首次建立（原为空）")
        else:
            self.say(f"   相比本地：新增 {len(diff.added)} 只，移除 {len(diff.removed)} 只")
            if diff.added:
                self.say("     新增：" + self.span(diff.added, 10))
            if diff.removed:
                self.say("     移除：" + self.span(diff.removed, 10))
        self.say(f"   同步时间记为 {date_to_str(now)}")

    def member_plan(self, total: int, todo: list[str], skip: list[str],
                    names: dict[str, str], interval: float) -> bool:
        """报告成分股同步计划；返回「有没有活要干」。"""
        self.say(f"库里共 {total} 个板块")
        if skip:
            self.say(f"  今天已经同步过 {len(skip)} 个，跳过（要重来加 --force）："
                     + self.span(skip, 8))
        if not todo:
            self.say("  ✅ 全都同步过了，不用再拉")
            return False
        self.say(f"  需要同步 {len(todo)} 个（每个之间间隔 {interval:g} 秒）：")
        self.say("      " + ", ".join(f"{c} {names.get(c) or ''}".strip() for c in todo))
        self.say()
        return True

    def member_step_failed(self, i: int, n: int, code: str, exc: Exception) -> None:
        self.say(f"  [{i}/{n}] ❌ {code}  {exc}")

    def member_blocked(self, done: int) -> None:
        self.say("\n⚠️  看着像被东财拒了（连接被掐）。**就此停下**，不再继续敲。")
        self.say(f"      已经同步好 {done} 个（今天不会重复拉）。")
        self.say("      别马上重跑 —— 越敲锁得越久；等一会儿再跑一次，会接着补。")

    def member_start(self, code: str) -> None:
        self.say(f"同步板块成分股 {code} …")

    def member_done(self, done: int, total: int, failed: int, members: int,
                    cost: float, requests: int) -> None:
        self.say(f"\n完成：{done}/{total} 个板块"
                 + (f"，{failed} 个失败" if failed else "")
                 + f"；共 {members:,} 条成员关系")
        # 请求数只有 DirectSource 记，没有就不报这一句
        self.say(f"   用了 {cost:.0f} 秒"
                 + (f" / {requests} 个请求" if requests else "")
                 + (f"（平均 {cost / requests:.1f} 秒一个）" if requests else ""))

    def member_retry_hint(self) -> None:
        self.say("   失败的重跑一次同样的命令就会接着补（同步过的会跳过）")

    def no_boards_in_db(self) -> None:
        self.say("⚠️  库里还没有任何板块。先拉一个，例如：python3 hunter.py fetch BK1201")

    def member_needs_codes(self) -> None:
        self.err("\n❌ update member 要跟板块代码或 all，例如：\n"
                 "      python3 hunter.py update member BK1201\n"
                 "      python3 hunter.py update member all")

    def ignore_extra(self, keyword: str) -> None:
        self.say(f"⚠️  给了 {keyword} 就忽略其它代码了")

    def bad_board_code(self, text: str, blank_line: bool = False) -> None:
        self.err(("\n" if blank_line else "") + f"❌ 板块代码应形如 BK1201，收到：{text}")

    # -- 个股字典 / 板块层级表 ----------------------------------------------
    def stock_list_start(self) -> None:
        self.say("拉全市场个股名字（1 个请求）…")

    def stock_list_done(self, rows: int, upserted: int, old: int,
                        added: list[str], renamed: list[str],
                        new: dict[str, str], old_names: dict[str, str], now: int) -> None:
        self.say(f"✅ 拿到 {rows:,} 只股票的名字")
        self.say(f"   入库 {upserted:,} 行（本地原有 {old:,} 只）")
        if added:
            self.say(f"   新出现 {len(added)} 只："
                     + self.span([f"{c} {new[c]}" for c in added], 6))
        if renamed:
            self.say(f"   改名 {len(renamed)} 只："
                     + self.span([f"{c} {old_names[c]}→{new[c]}" for c in renamed], 6))
        self.say(f"   同步时间记为 {date_to_str(now)}")

    def need_tree(self) -> None:
        """fetch board 发现还没有层级表。"""
        self.say("⚠️  还没有板块层级表。先跑一次（东财 5 + 乐咕 3 个请求）：")
        self.say("      python3 hunter.py update tree")

    def need_tree_for_show(self) -> None:
        """show board 发现还没有层级表。"""
        self.say("⚠️  还没有一级/二级板块表。先跑一次：")
        self.say("      python3 hunter.py update tree")

    def no_boards_at_level(self, level: int) -> None:
        self.say(f"⚠️  层级表里没有 {level} 级的板块")

    def ignore_days(self) -> None:
        self.say("⚠️  fetch board 是批量刷最新快照，忽略 --days")

    def tree_start(self) -> None:
        self.say("重建板块层级 …")

    def tree_done(self, n: int, counts: dict[int, int], dropped: list[str],
                  kept: list[str]) -> None:
        self.say(f"\n✅ 一级/二级板块表已入库：{n} 个"
                 f"（一级 {counts.get(1, 0)} / 二级 {counts.get(2, 0)}）")
        if dropped:
            self.say(f"   板块字典清掉 {len(dropped)} 个不属于一级/二级的条目"
                     f"（三级板块那些；行情和成分股都没动）")
        if kept:
            self.say(f"   有 {len(kept)} 个不在层级里、但你抓过数据 —— 保留了："
                     + "、".join(kept[:8]))
            self.say("      要连数据一起删：python3 hunter.py drop board " + " ".join(kept[:3]))
        self.say(f"\n   fetch board 现在会更新这 {n} 个板块（一级在前、二级在后）")
        self.say("   show board 会按一级分标签，每个一级下面依次列出它的二级")

    # -- 删板块 -------------------------------------------------------------
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
        self.say(f"  拿回来：python3 hunter.py fetch {code} --days 20"
                 f" + python3 hunter.py update member {code}")

    # -- 展示 ---------------------------------------------------------------
    def show_watch_all(self, codes: list[str]) -> None:
        self.say(f"自选 {len(codes)} 个，全部展示：" + ", ".join(codes))

    def show_unknown(self, raw: str) -> None:
        self.err(f"❌ 认不出的写法：{raw}（板块形如 BK1201，个股形如 300308，"
                 f"或关键字 board / watch）")

    def show_grouped_plan(self, groups: list[tuple[str, list[str]]]) -> None:
        n2 = sum(len(k) for _, k in groups)
        self.say(f"一级 {len(groups)} 个标签，每个下面排它自己的二级（共 {n2} 个）")

    def fetch_result(self, result) -> None:
        """单个标的拉完：只有一条就明细展示，多条就一行摘要。"""
        rec = result.fetched[-1]
        if len(result.fetched) == 1:
            show_data.print_snapshot(rec)
            return
        self.say(f"✅ {result.code} {rec.get('name') or ''}  拉到 {len(result.fetched)} 条"
                 f"（新增 {len(result.added)} 条，本地共 {result.total} 条）"
                 f"  {date_to_str(result.fetched[0].get('trade_date'))}"
                 f" ~ {date_to_str(rec.get('trade_date'))}")
