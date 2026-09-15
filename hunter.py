#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hunter.py —— 整个项目唯一的执行入口。
"""

from __future__ import annotations

import argparse
import sys
import time

from config.config import config

from datasource.boards import Boards
from datasource.stock import Stocks
from datasource.fetch import Fetcher, is_board_code
from datasource.fetch_direct import DirectSource
from datasource.fetch_tushare import TushareSource
from datasource.store import date_to_str, local, store, today_int
from datasource import show_data


def _print_result(result) -> None:
    """把一次 fetch 的结果打给用户看。"""
    rec = result.fetched[-1]
    name = rec.get("name") or ""
    if len(result.fetched) == 1:
        show_data.print_snapshot(rec)
        return
    first = date_to_str(result.fetched[0].get("trade_date"))
    print(f"✅ {result.code} {name}  拉到 {len(result.fetched)} 条"
          f"（新增 {len(result.added)} 条，本地共 {result.total} 条）"
          f"  {first} ~ {date_to_str(rec.get('trade_date'))}")


def _split_fresh(fetcher: Fetcher, codes: list[str], days: int | None,
                 force: bool) -> tuple[list[str], list[str], int | None]:
    """
    把代码分成 (需要拉的, 本地已经是最新的)。

    判据是「本地最后一天 >= 交易日钟」，不猜今天几号，详见 Fetcher.is_up_to_date。
    给了 --days 说明你要的是历史，那就不跳过。
    """
    if force or days is not None:
        return list(codes), [], None
    # 交易日钟要从**这个 fetcher 自己的库**取，别去摸模块级的 store
    ref = fetcher.db.last_trade_day()     # 一批只查一次
    todo, fresh = [], []
    for c in codes:
        (fresh if fetcher.is_up_to_date(c, ref_day=ref) else todo).append(c)
    return todo, fresh, ref


def _report_plan(label: str, todo: list[str], fresh: list[str],
                 interval: float, ref: int | None) -> None:
    """批量拉之前，先说清楚「谁要拉、谁跳过、为什么」。"""
    if fresh:
        print(f"  本地已是最新（交易日钟 = {date_to_str(ref)}）跳过 {len(fresh)} 个：")
        print("      " + ", ".join(fresh[:12]) + (" …" if len(fresh) > 12 else ""))
        print("      要强制重拉：加 --force")
    if not todo:
        print(f"  ✅ {label}全都已经是最新的了，一个请求都不用发")
        return
    print(f"  需要拉 {len(todo)} 个（每个之间间隔 {interval:g} 秒）：")
    print("      " + ", ".join(todo[:12]) + (" …" if len(todo) > 12 else ""))
    print()


def _fetch_batch(fetcher: Fetcher, boxes, codes: list[str], days: int | None,
                 force: bool = False, label: str = "") -> int:
    """
    批量刷一批标的：本地已是最新的跳过，其余逐个拉（带间隔）。fetch board / fetch watch 共用。
    """
    if not codes:
        return 1

    interval = float(config.system.get("fetch_interval", 1.0) or 0)
    todo, fresh, ref = _split_fresh(fetcher, codes, days, force)

    print(f"{label}{len(codes)} 个" + (f"（最近 {days} 个交易日）" if days else "，只要最新"))
    _report_plan(label, todo, fresh, interval, ref)
    if not todo:
        return 0

    rc = 0
    ok = 0
    for i, code in enumerate(todo, 1):
        if i > 1 and interval:
            time.sleep(interval)          # 间隔一下，别把对方惹毛
        kind = _kind_of_code(code) or "stock"
        try:
            result = _fetch_one(boxes, code, days)
        except Exception as exc:
            rc = 1
            print(f"  [{i}/{len(todo)}] ❌ {code}：{exc}")
            continue
        rec = result.fetched[-1]
        mark = f"新增 {len(result.added)}" if result.added else "本地已有"
        print(f"  [{i}/{len(todo)}] ✅ {_label(kind, code):<14}"
              f"{date_to_str(rec.get('trade_date'))}  {mark}")
        ok += 1

    print(f"\n完成：{ok}/{len(todo)} 个成功"
          + (f"（另有 {len(fresh)} 个本来就已经是最新的，跳过）" if fresh else ""))
    return rc if rc else (0 if ok == len(todo) else 1)


def _boards_by_level(level: int) -> list[str]:
    """层级表里某一级的板块代码（一级 31 个 / 二级 128 个）。"""
    return [r["concept"] for r in store.query(
        "SELECT concept FROM board_tree WHERE level = ? ORDER BY concept", (level,))]


def _fetch_all_boards(fetcher: Fetcher, boxes, days: int | None,
                      force: bool = False, level: int | None = None) -> int:
    """
    fetch board —— 一级和二级板块的行情一起更新（31 + 128 个，每个 1 个请求）。

    本地已经有当天的会跳过；--level 1 / 2 可以只刷一层。
    """
    lv1, lv2 = _boards_by_level(1), _boards_by_level(2)
    if not lv1 and not lv2:
        print("⚠️  还没有板块层级表。先跑一次（东财 5 + 乐咕 3 个请求）：")
        print("      python3 hunter.py update tree")
        return 1

    if level == 2:
        codes = lv2
    elif level == 1:
        codes = lv1
    else:
        codes = lv1 + lv2          # 一级在前，二级在后（报告看起来是分组的）
    if not codes:
        print(f"⚠️  层级表里没有 {level} 级的板块")
        return 1

    if days:
        print("⚠️  fetch board 是批量刷最新快照，忽略 --days")

    rc = _fetch_batch(fetcher, boxes, codes, days, force,
                      label=f"板块（一级 {len(lv1)} + 二级 {len(lv2)}）")
    if rc == 0:
        _print_board_state()
    return rc


def _print_db_state() -> None:
    """库里现在有多少全市场数据。"""
    r = store.query("SELECT COUNT(*) AS n, COUNT(DISTINCT trade_date) AS d, "
                    "MIN(trade_date) AS a, MAX(trade_date) AS b FROM stock_daily")[0]
    if not r["n"]:
        print("   库里 stock_daily：空")
        return
    print(f"   库里 stock_daily：{r['n']:,} 行 / {r['d']} 个交易日"
          f"（{date_to_str(r['a'])} ~ {date_to_str(r['b'])}）")


def _print_day_health() -> None:
    """
    体检：每个交易日的行数都该接近全市场只数，明显偏少就是那天没取全。
    """
    rows = store.query("SELECT trade_date, COUNT(*) AS n FROM stock_daily "
                       "GROUP BY trade_date ORDER BY trade_date")
    if len(rows) < 2:
        return

    counts = {r["trade_date"]: r["n"] for r in rows}
    full = max(counts.values())
    thin = [(d, n) for d, n in counts.items() if n < full * 0.95]

    print(f"   体检：{len(counts)} 个交易日，每天 {min(counts.values()):,} ~ {full:,} 行")
    if not thin:
        return
    print(f"   ⚠️  有 {len(thin)} 天明显偏少（不到全市场的 95%）：")
    for d, n in thin[:8]:
        print(f"        {date_to_str(d)}  只有 {n:,} 行（少了 {full - n:,}）")
    if len(thin) > 8:
        print(f"        … 还有 {len(thin) - 8} 天")
    print("      这些天大概没取全，可以用 backfill market --days <覆盖到这些天> --force 重拉")


def _fmt_eta(n: int, interval: float) -> str:
    """N 个请求按 interval 间隔大约要多久。"""
    sec = max(n - 1, 0) * interval
    return f"{sec:.0f} 秒" if sec < 90 else f"{sec / 60:.1f} 分钟"


def _fetch_market(fetcher: Fetcher, trade_date: str | None) -> int:
    """
    fetch market —— 拉**一天**的全市场日线入库（1 个请求，约 5500 只）。

    这是整套架构的地基，同时是全系统的「交易日钟」：其余「本地是不是最新」都以它为准。
    """
    print("拉全市场日线（1 个请求）…")
    try:
        r = fetcher.market(trade_date)
    except Exception as exc:
        print(f"\n❌ {exc}", file=sys.stderr)
        return 1

    if r.skipped:
        print(f"⏭  {r.day} 本地已有 {r.total:,} 行，跳过（要重拉：backfill market "
              f"--days 1 --force）")
    elif r.empty:
        print(f"⚠️  {r.day or trade_date} 没有数据（非交易日，或数据还没发布）")
        return 1
    else:
        print(f"✅ {r.day}  全市场 {r.fetched:,} 只")
        print(f"   入库 {r.added:,} 行"
              + (f"，{r.fetched - r.added:,} 行本地已有（跳过）"
                 if r.fetched > r.added else ""))
        if not trade_date:
            print("   （没指定日期，自动往回找到最近有数据的一天）")

    _print_db_state()
    _print_day_health()
    return 0


def _backfill_market(fetcher: Fetcher, days: int,
                     end: str | None = None, force: bool = False) -> int:
    """
    backfill market --days N —— 补最近 N 个交易日的全市场日线。

    一天 1 个请求，本地已有的不重复拉，所以 Ctrl-C 之后重跑会接着补。
    """
    try:
        plan = fetcher.market_plan(days, end=end)
    except Exception as exc:
        print(f"\n❌ {exc}", file=sys.stderr)
        return 1
    if not plan:
        print("⚠️  没排到任何交易日，无事可做")
        return 1

    have = [p for p in plan if p["rows"]]
    todo = plan if force else [p for p in plan if not p["rows"]]
    interval = float(config.system.get("fetch_interval", 1.5) or 0)

    print(f"计划：最近 {len(plan)} 个交易日  {plan[-1]['day']} ~ {plan[0]['day']}")
    if have:
        print(f"      本地已有 {len(have)} 天："
              + ", ".join(p["day"] for p in have))
    if force and have:
        print("      ⚠️  --force：上面这些天也重拉（先删后写，只影响它们）")
    if not todo:
        print("      ✅ 全都已经有了，不用拉（要重拉加 --force）")
        _print_db_state()
        _print_day_health()
        return 0
    print(f"      需要拉 {len(todo)} 天（每天 1 个请求，间隔 {interval:g} 秒，"
          f"约 {_fmt_eta(len(todo), interval)}）")
    if len(todo) > 45 and interval < 1.2:
        print("      ⚠️  fetch_interval < 1.2 秒：tushare 限 50 次/分钟，可能被限流")
    print()

    done = empty = failed = 0
    added_total = 0
    for i, p in enumerate(todo, 1):
        if i > 1 and interval:
            time.sleep(interval)          # 间隔一下，别触发对方的频率限制
        try:
            r = fetcher.market(p["day"], force=force)
        except Exception as exc:
            failed += 1
            # 走 stdout：它是进度的一部分（日志里编号不能缺一块）
            print(f"  [{i}/{len(todo)}] ❌ {p['day']}  {exc}")
            continue

        if r.skipped:
            continue
        if r.empty:
            empty += 1
            print(f"  [{i}/{len(todo)}] ⏭  {p['day']}  那天没数据"
                  f"（非交易日 / 还没发布）")
            continue

        done += 1
        added_total += r.added
        print(f"  [{i}/{len(todo)}] ✅ {p['day']}  {r.fetched:,} 只"
              + (f"（重拉，新增 {r.added:,}）" if force else ""))

    print(f"\n完成：{done} 天入库"
          + (f"，{empty} 天没数据" if empty else "")
          + (f"，{failed} 天失败" if failed else "")
          + f"（本次新增 {added_total:,} 行）")
    _print_db_state()
    _print_day_health()
    if failed:
        print("   失败的天重跑一次同样的命令就会接着补（本地已有的会跳过）")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# 自选（watchlist）
# ---------------------------------------------------------------------------
# 自选存在 data/local.sqlite（**不可重建**：既下不到也算不到，所以单独一个文件）。
# 板块和个股共用一张表，kind 是 'board' / 'stock'。
#
# ⚠️ watch / unwatch 只改本地状态，**一个请求都不发**。要拉数据是下一步的事
#    （fetch watch / update member watch / show watch），网络动作永远由你显式触发。

def _kind_of_code(code: str) -> str | None:
    """代码 -> 'board' | 'stock'；认不出来返回 None。"""
    code = str(code).strip().upper()
    if is_board_code(code):
        return "board"
    if code.isdigit() and len(code) == 6:
        return "stock"
    return None


def _normalize(code: str, kind: str) -> str:
    return code.strip().upper() if kind == "board" else code.strip().zfill(6)


def _label(kind: str, code: str) -> str:
    """「代码 名字」——名字从字典表取，取不到就只显示代码。"""
    name = store.name_of(kind, code)
    return f"{code} {name}".strip()


def watched_codes(kind: str | None = None) -> list[str]:
    """当前自选里的代码（kind 为空 = 板块 + 个股都要）。"""
    return [w["code"] for w in local.load_watchlist(kind)]


def cmd_watch(codes: list[str]) -> int:
    """watch <代码...> —— 加入自选（纯本地，不联网）。"""
    if not codes:
        return cmd_watchlist()

    rc = 0
    for raw in codes:
        kind = _kind_of_code(raw)
        if not kind:
            print(f"❌ 认不出的代码：{raw.strip()}（板块形如 BK1201，个股形如 300308）",
                  file=sys.stderr)
            rc = 1
            continue
        code = _normalize(raw, kind)
        if local.is_watched(kind, code):
            print(f"· 已在自选：{_label(kind, code)}")
            local.watch(kind, code, name=store.name_of(kind, code))   # 只刷新时间
            continue
        local.watch(kind, code, name=store.name_of(kind, code))
        print(f"✅ 加入自选：{_label(kind, code)}  [{'板块' if kind == 'board' else '个股'}]")

    if rc == 0:
        print("   （只改了本地状态，没联网。要看数据：python3 hunter.py fetch watch)"
              if codes else "")
    return rc


def cmd_unwatch(codes: list[str]) -> int:
    """unwatch <代码...> —— 移出自选（纯本地）。行会保留，只是不在列表里。"""
    if not codes:
        print("❌ unwatch 要跟代码，例如：python3 hunter.py unwatch BK1201", file=sys.stderr)
        return 1

    rc = 0
    for raw in codes:
        kind = _kind_of_code(raw)
        if not kind:
            print(f"❌ 认不出的代码：{raw.strip()}", file=sys.stderr)
            rc = 1
            continue
        code = _normalize(raw, kind)
        if not local.is_watched(kind, code):
            print(f"· 本来就不在自选：{_label(kind, code)}")
            continue
        local.unwatch(kind, code)
        print(f"✅ 移出自选：{_label(kind, code)}")
    return rc


def cmd_watchlist() -> int:
    """watch（不带参数）—— 看当前自选。"""
    items = local.load_watchlist()
    if not items:
        print("自选是空的。看到感兴趣的加进来：")
        print("      python3 hunter.py watch BK1201        # 板块")
        print("      python3 hunter.py watch 300308        # 个股")
        return 0

    boards = [w for w in items if w["kind"] == "board"]
    stocks = [w for w in items if w["kind"] == "stock"]
    print(f"自选 {len(items)} 个（板块 {len(boards)} / 个股 {len(stocks)}）\n")
    for title, group in (("板块", boards), ("个股", stocks)):
        if not group:
            continue
        print(f"  [{title}]")
        for w in group:
            name = w["name"] or store.name_of(w["kind"], w["code"]) or "—"
            # 有没有本地数据，一眼看出来
            n = len(Fetcher(db=store).load(w["code"]))
            mark = f"{n} 个交易日" if n else "⚠️ 本地还没有数据"
            note = f"   {w['note']}" if w["note"] else ""
            print(f"    {w['code']}  {name:<8}  {date_to_str(w['watched_at'])} 起   {mark}{note}")
    print("\n  拉数据：python3 hunter.py fetch watch")
    print("  看数据：python3 hunter.py show watch")
    print("  移出：  python3 hunter.py unwatch <代码>")
    return 0


def _fetch_one(boxes, code: str, days: int | None):
    """按代码类型分派到 Boards / Stocks。"""
    boards, stocks = boxes
    return (boards.fetch(code, days=days) if is_board_code(code)
            else stocks.fetch(code, days=days))


def _fetch_watch(fetcher: Fetcher, boxes, days: int | None,
                 force: bool = False) -> int:
    """
    fetch watch —— 只刷自选里的标的。
    """
    codes = watched_codes()
    if not codes:
        print("⚠️  自选是空的。先加一个：python3 hunter.py watch BK1201")
        return 1
    return _fetch_batch(fetcher, boxes, codes, days, force, label="自选 ")


def cmd_fetch(codes: list[str], days: int | None,
              trade_date: str | None = None, force: bool = False) -> int:
    # 一个命令里只用一套 Fetcher（数据源实例复用，连接池才有效）
    fetcher = Fetcher()
    boards = Boards(fetcher)
    stocks = Stocks(fetcher)

    rc = 0
    for raw in codes:
        code = raw.strip()
        if code.lower() == "market":
            rc |= _fetch_market(fetcher, trade_date)
            continue
        if code.lower() == "board":
            rc |= _fetch_all_boards(fetcher, (boards, stocks), days, force)
            continue
        if code.lower() == "watch":
            rc |= _fetch_watch(fetcher, (boards, stocks), days, force)
            continue
        try:
            if is_board_code(code):
                result = boards.fetch(code, days=days)
            else:
                result = stocks.fetch(code, days=days)
        except Exception as exc:
            print(f"\n❌ {code}：{exc}", file=sys.stderr)
            rc |= 1
            continue
        _print_result(result)
    return rc


def _member_sync(code: str, src: DirectSource, now: int, brief: bool = False) -> dict:
    """
    同步一个板块的成分股并写库。失败就抛出来，由调用方决定继续还是停。
    """
    old = set(store.load_board_members(code))
    members = src.fetch_board_members(code)          # 分页拉，可能好几个请求
    codes = [m["code"] for m in members]
    new = set(codes)

    # 板块名：本地字典里已经有就**不再请求**（批量同步时这一条能省掉 21 个请求）
    name = store.name_of("board", code)
    if not name:
        try:
            name = src.fetch_board_name(code)        # 1 个请求
        except Exception:
            name = None                              # 拿不到不算失败

    store.replace_board_members(code, codes, updated_at=now)
    if name:
        store.upsert_board_list([{"concept": code, "name": name}])

    info = {"code": code, "name": name, "total": len(codes),
            "added": sorted(new - old), "removed": sorted(old - new), "old": len(old)}

    if brief:
        diff = ""
        if info["added"] or info["removed"]:
            diff = f"  (+{len(info['added'])}/-{len(info['removed'])})"
        print(f"  ✅ {code}  {(name or '?'):<8} {len(codes):>4} 只{diff}")
    return info


def _update_member(code: str) -> int:
    """
    update member BK1201 —— 同步**一个**板块的成分股名单。
    """
    code = code.strip().upper()
    if not is_board_code(code):
        print(f"\n❌ 板块代码应形如 BK1201，收到：{code}", file=sys.stderr)
        return 1

    now = today_int()
    src = DirectSource()
    print(f"同步板块成分股 {code} …")
    try:
        info = _member_sync(code, src, now)
    except Exception as exc:
        _print_member_error(exc)
        return 1

    added, removed = info["added"], info["removed"]
    label = f"{code} {info['name'] or ''}".strip()
    print(f"✅ {label}")
    print(f"   成分股 {info['total']} 只（本地原有 {info['old']} 只）")
    if info["old"]:
        print(f"   相比本地：新增 {len(added)} 只，移除 {len(removed)} 只")
        if added:
            print(f"     新增：{', '.join(added[:10])}" + (" …" if len(added) > 10 else ""))
        if removed:
            print(f"     移除：{', '.join(removed[:10])}" + (" …" if len(removed) > 10 else ""))
    else:
        print("   本地首次建立（原为空）")
    print(f"   同步时间记为 {date_to_str(now)}")
    return 0


def _print_member_error(exc: Exception) -> None:
    """成分股同步失败时把「是不是被拒了」说清楚。"""
    print(f"\n❌ 拉成分股失败：{exc}", file=sys.stderr)
    if _looks_blocked(exc):
        print("   ⚠️ 看着像被东财拒了（连接被掐）。**别立刻重跑** —— 越敲锁得越久，"
              "等一会儿再说。", file=sys.stderr)


# 出现这些字样，基本就是「被对方拒了」，不是「代码写错了」
_BLOCKED_HINTS = ("remotedisconnected", "connection aborted", "connection reset",
                  "connectionerror", "max retries", "timed out", "403", "429", "502", "503")


def _looks_blocked(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(h in text for h in _BLOCKED_HINTS)


def _all_board_codes() -> list[str]:
    """
    「我在跟的板块」= 有行情的 ∪ 有成分股的 ∪ 自选里的。

    故意**不含** board_list 里纯粹是字典条目的板块 —— update tree 会把 159 个都写进字典，
    要是都算成「在跟的」，update member all 一下就成了上百个请求。
    """
    rows = store.query("SELECT concept FROM board_daily "
                       "UNION SELECT concept FROM concept_member ORDER BY concept")
    codes = [r["concept"] for r in rows]
    for c in watched_codes("board"):
        if c not in codes:
            codes.append(c)
    return sorted(codes)


def _update_member_watch(force: bool = False) -> int:
    """
    update member watch —— 只同步自选里那些板块的成分股（个股没有成分股，忽略）。
    """
    codes = [c for c in watched_codes("board")]
    if not codes:
        print("⚠️  自选里没有板块。先加一个：python3 hunter.py watch BK1201")
        return 1
    return _sync_members(codes, force=force)


def _update_member_all(force: bool = False) -> int:
    """
    update member all —— 同步库里所有相关板块的成分股。

    防反爬：板块之间按 fetch_interval 间隔；今天同步过的跳过（顺带可中断续跑）；
    一旦被拒立刻停下 —— 继续敲只会让 IP 被锁更久。
    """
    return _sync_members(_all_board_codes(), force=force)


def _sync_members(codes: list[str], force: bool = False) -> int:
    """把给定的这些板块的成分股同步一遍（防反爬 / 可续跑的逻辑都在这里）。"""
    if not codes:
        print("⚠️  库里还没有任何板块。先拉一个，例如：python3 hunter.py fetch BK1201")
        return 1

    now = today_int()
    synced_at = {r["concept"]: r["member_updated_at"] for r in store.load_board_list()}
    todo = [c for c in codes if force or synced_at.get(c) != now]
    skip = [c for c in codes if c not in todo]

    names = {r["concept"]: r["name"] for r in store.load_board_list()}
    interval = float(config.system.get("fetch_interval", 1.5) or 0)

    print(f"库里共 {len(codes)} 个板块")
    if skip:
        print(f"  今天已经同步过 {len(skip)} 个，跳过（要重来加 --force）："
              + ", ".join(skip[:8]) + (" …" if len(skip) > 8 else ""))
    if not todo:
        print("  ✅ 全都同步过了，不用再拉")
        _print_member_state()
        return 0
    print(f"  需要同步 {len(todo)} 个（每个之间间隔 {interval:g} 秒）：")
    print("      " + ", ".join(f"{c} {names.get(c) or ''}".strip() for c in todo))
    print()

    src = DirectSource()
    done = failed = 0
    got = 0
    members_total = 0
    t0 = time.time()
    for i, code in enumerate(todo, 1):
        if i > 1 and interval:
            time.sleep(interval)          # 间隔一下，别把对方惹毛
        try:
            info = _member_sync(code, src, now, brief=True)
        except Exception as exc:
            failed += 1
            print(f"  [{i}/{len(todo)}] ❌ {code}  {exc}")
            if _looks_blocked(exc):
                print("\n⚠️  看着像被东财拒了（连接被掐）。**就此停下**，不再继续敲。")
                print(f"      已经同步好 {done} 个（今天不会重复拉）。")
                print("      别马上重跑 —— 越敲锁得越久；等一会儿再跑一次，会接着补。")
                _print_member_state()
                return 1
            continue
        done += 1
        got += 1
        members_total += info["total"]

    cost = time.time() - t0
    print(f"\n完成：{done}/{len(todo)} 个板块"
          + (f"，{failed} 个失败" if failed else "")
          + f"；共 {members_total:,} 条成员关系")
    # 请求数只有 DirectSource 记，没有就不报这一句
    made = getattr(src, "requests_made", 0)
    print(f"   用了 {cost:.0f} 秒" + (f" / {made} 个请求" if made else "")
          + (f"（平均 {cost / made:.1f} 秒一个）" if made else ""))
    _print_member_state()
    if failed:
        print("   失败的重跑一次同样的命令就会接着补（同步过的会跳过）")
    return 1 if failed else 0


def _print_member_state() -> None:
    """库里现在有多少板块成员。"""
    r = store.query("SELECT COUNT(*) AS n, COUNT(DISTINCT concept) AS c "
                    "FROM concept_member")[0]
    print(f"   库里 concept_member：{r['n']:,} 条 / {r['c']} 个板块有成员名单")


def _update_stock_list() -> int:
    """
    update stock —— 同步个股字典（代码 → 名字），1 个请求拿全市场。

    名字属于字典不属于行情：按只查要 5500 个请求，一次拉全量只要 1 个。
    """
    try:
        src = TushareSource()
    except Exception as exc:
        print(f"\n❌ {exc}", file=sys.stderr)
        return 1

    print("拉全市场个股名字（1 个请求）…")
    try:
        rows = src.fetch_stock_list()
    except Exception as exc:
        print(f"\n❌ {exc}", file=sys.stderr)
        return 1

    now = today_int()
    old = {r["code"]: r["name"] for r in store.load_stock_list()}
    n = store.upsert_stock_list([{**r, "updated_at": now} for r in rows])

    new = {r["code"]: r["name"] for r in rows}
    added = sorted(set(new) - set(old))
    renamed = sorted(c for c in set(new) & set(old)
                     if new[c] and old[c] and new[c] != old[c])

    print(f"✅ 拿到 {len(rows):,} 只股票的名字")
    print(f"   入库 {n:,} 行（本地原有 {len(old):,} 只）")
    if added:
        print(f"   新出现 {len(added)} 只："
              + ", ".join(f"{c} {new[c]}" for c in added[:6])
              + (" …" if len(added) > 6 else ""))
    if renamed:
        print(f"   改名 {len(renamed)} 只："
              + ", ".join(f"{c} {old[c]}→{new[c]}" for c in renamed[:6])
              + (" …" if len(renamed) > 6 else ""))
    print(f"   同步时间记为 {date_to_str(now)}")
    return 0


def _update_tree() -> int:
    """
    update tree —— 重建一级/二级板块表，并清掉字典里不属于这两级的多余条目。

    层级来自申万分类（东财接口不给层级），东财只用来拉板块字典。
    详见 datasource/board_tree.py。
    """
    from datasource.board_tree import BoardTree, prune_dict, used_boards

    tree = BoardTree()
    print("重建板块层级 …")
    try:
        res = tree.build()
    except Exception as exc:
        print(f"\n❌ {exc}", file=sys.stderr)
        return 1

    if not res.nodes:
        print("❌ 一个节点都没建出来，先看看上面的报错", file=sys.stderr)
        return 1

    n = tree.save(res)
    counts = res.counts
    print(f"\n✅ 一级/二级板块表已入库：{n} 个"
          f"（一级 {counts.get(1, 0)} / 二级 {counts.get(2, 0)}）")

    keep = {x.bk for x in res.nodes}
    protected = used_boards(store) | set(watched_codes("board"))
    dropped = prune_dict(store, keep, protected)
    if dropped:
        print(f"   板块字典清掉 {len(dropped)} 个不属于一级/二级的条目"
              f"（三级板块那些；行情和成分股都没动）")
    kept = sorted(protected - keep)
    if kept:
        print(f"   有 {len(kept)} 个不在层级里、但你抓过数据 —— 保留了："
              + "、".join(kept[:8]))
        print("      要连数据一起删：python3 hunter.py drop board " + " ".join(kept[:3]))

    print(f"\n   fetch board 现在会更新这 {n} 个板块（一级在前、二级在后）")
    print("   show board 会按一级分标签，每个一级下面依次列出它的二级")
    return 0


def cmd_update(what: str, codes: list[str], force: bool = False) -> int:
    """update member <codes|all> / update stock。"""
    if what == "stock":
        return _update_stock_list()
    if what == "tree":
        return _update_tree()
    if what != "member":
        print(f"\n❌ 暂不支持 update {what}（只有 member / stock / tree）", file=sys.stderr)
        return 1
    if not codes:
        print("\n❌ update member 要跟板块代码或 all，例如：\n"
              "      python3 hunter.py update member BK1201\n"
              "      python3 hunter.py update member all", file=sys.stderr)
        return 1

    if any(c.strip().lower() == "all" for c in codes):
        if len(codes) > 1:
            print("⚠️  给了 all 就忽略其它代码了")
        return _update_member_all(force=force)
    if any(c.strip().lower() == "watch" for c in codes):
        if len(codes) > 1:
            print("⚠️  给了 watch 就忽略其它代码了")
        return _update_member_watch(force=force)

    rc = 0
    for c in codes:
        rc |= _update_member(c)
    return rc


def _print_board_state() -> None:
    """库里现在有多少板块数据。"""
    r = store.query("SELECT COUNT(*) AS n, COUNT(DISTINCT concept) AS c, "
                    "COUNT(DISTINCT trade_date) AS d, MIN(trade_date) AS a, "
                    "MAX(trade_date) AS b FROM board_daily")[0]
    if not r["n"]:
        print("   库里 board_daily：空")
        return
    print(f"   库里 board_daily：{r['n']:,} 行 / {r['c']} 个板块 / {r['d']} 个交易日"
          f"（{date_to_str(r['a'])} ~ {date_to_str(r['b'])}）")


def cmd_drop(what: str, codes: list[str]) -> int:
    """
    drop board <代码…> —— 删掉板块的本地数据（行情 + 成分股 + 字典那行）。

    和 unwatch 不是一回事：unwatch 只是不再关注，数据留着。
    个股没有 drop —— 它的行情是全市场一次拉进来的，删了明天也会回来。
    """
    if what != "board":
        print(f"❌ drop 只支持 board（个股行情是全市场一次拉的，删了也会回来；"
              f"不想要就 unwatch）", file=sys.stderr)
        return 1
    if not codes:
        print("❌ drop board 要跟板块代码，例如：python3 hunter.py drop board BK1201",
              file=sys.stderr)
        return 1

    # ⚠️ 用 hunter.Fetcher 造 Boards，别写 Boards() —— 那会用 datasource.boards
    #    里 import 进去的 Fetcher（真实库）。全部经由 hunter.Fetcher，「换掉它」才有唯一口子。
    boards = Boards(Fetcher())
    watched = set(watched_codes("board"))
    rc = 0
    for raw in codes:
        code = raw.strip().upper()
        if not is_board_code(code):
            print(f"❌ 板块代码应形如 BK1201，收到：{raw.strip()}", file=sys.stderr)
            rc = 1
            continue

        before = boards.what_would_drop(code)
        if not any(before.values()):
            print(f"· 本地本来就没有 {code} 的任何数据")
            continue

        label = _label("board", code)
        print(f"删除 {label}：")
        print(f"    行情 {before['board_daily']} 个交易日"
              f" / 成员 {before['concept_member']} 只"
              + ("  / 板块字典那行" if before["board_list"] else ""))
        gone = boards.drop(code)
        print(f"  ✅ 已删（board_daily {gone['board_daily']} 行，"
              f"concept_member {gone['concept_member']} 行，"
              f"board_list {gone['board_list']} 行）")
        if code in watched:
            print(f"  ⚠️  它还在自选里 —— 数据没了但标记还在。要一起清掉："
                  f"python3 hunter.py unwatch {code}")
        print(f"  拿回来：python3 hunter.py fetch {code} --days 20"
              f" + python3 hunter.py update member {code}")
    if rc == 0 and codes:
        _print_board_state()
    return rc


def _board_groups() -> list[tuple[str, list[str]]]:
    """[(一级代码, [二级代码…]), …] —— 从层级表来。"""
    rows = store.load_board_tree()
    kids: dict[str, list[str]] = {}
    for r in rows:
        if r["parent"]:
            kids.setdefault(r["parent"], []).append(r["concept"])
    return [(r["concept"], sorted(kids.get(r["concept"], [])))
            for r in rows if r["level"] == 1]


def cmd_show(codes: list[str], days: int) -> int:
    """show —— board = 一级分标签（下面排二级）；watch = 只看自选；也可以给具体代码。"""
    expanded: list[str] = []
    want_grouped = False
    for raw in codes:
        c = raw.strip()
        if c.lower() == "board":
            want_grouped = True
        elif c.lower() == "watch":
            w = watched_codes()
            if not w:
                print("⚠️  自选是空的。先加一个：python3 hunter.py watch BK1201")
                return 1
            print(f"自选 {len(w)} 个，全部展示：" + ", ".join(w))
            expanded.extend(w)
        elif is_board_code(c) or (c.isdigit() and len(c) == 6):
            expanded.append(c)
        else:
            print(f"❌ 认不出的写法：{c}（板块形如 BK1201，个股形如 300308，"
                  f"或关键字 board / watch）", file=sys.stderr)
            return 1

    try:
        if want_grouped:
            groups = _board_groups()
            if not groups:
                print("⚠️  还没有一级/二级板块表。先跑一次：")
                print("      python3 hunter.py update tree")
                return 1
            n2 = sum(len(k) for _, k in groups)
            print(f"一级 {len(groups)} 个标签，每个下面排它自己的二级（共 {n2} 个）")
            show_data.gui_grouped(groups, days=days)
        if expanded:
            show_data.gui(expanded, days=days)
        return 0
    except Exception as exc:
        print(f"\n❌ {exc}", file=sys.stderr)
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="hunter.py",
        description="去追寻资金的脚印吧",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例（完整用法见 README.md）：\n"
               "  python3 hunter.py fetch market     # 每天收盘后：全市场日线\n"
               "  python3 hunter.py fetch board      # 一级+二级板块行情\n"
               "  python3 hunter.py update tree      # 建一级/二级板块表\n"
               "  python3 hunter.py watch BK1036     # 加自选\n"
               "  python3 hunter.py show board       # 一级分标签看行情\n"
               "  python3 hunter.py show watch       # 只看自选",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser(
        "fetch", help="拉取并入库（codes 给 market=全市场 / board=批量刷本地板块）")
    p_fetch.add_argument(
        "codes", nargs="+",
        help="关键字 market（全市场日线）/ board（本地已有数据的板块）"
             " / watch（自选里的标的） / 板块代码 BK1201 / 个股代码 300308")
    p_fetch.add_argument(
        "--days", type=int, default=None,
        help="拉最近 N 个交易日；不给则只拉最新一条")
    p_fetch.add_argument(
        "--date", default=None, metavar="YYYYMMDD",
        help="配合 market：指定要拉的交易日；不给则自动往回找最近有数据的一天")
    p_fetch.add_argument(
        "--force", action="store_true",
        help="配合 board / watch：本地已是最新的也重拉（默认会跳过，一个请求都不发）")

    p_back = sub.add_parser(
        "backfill", help="补历史空档（按交易日逐天往前拉；本地已有的自动跳过，可中断续跑）")
    p_back.add_argument("what", choices=["market"], help="补什么：market = 全市场日线")
    p_back.add_argument("--days", type=int, required=True,
                        help="最近多少个交易日，如 30")
    p_back.add_argument("--end", default=None, metavar="YYYYMMDD",
                        help="补到这个交易日为止，默认到今天")
    p_back.add_argument("--force", action="store_true",
                        help="本地已有的也重拉（先删后写），用来修「那天没取全」")

    p_drop = sub.add_parser(
        "drop", help="删掉某个板块的本地数据（行情 + 成分股 + 字典那行）")
    p_drop.add_argument("what", choices=["board"], help="删什么：board = 板块")
    p_drop.add_argument("codes", nargs="+", help="板块代码，如 BK1201（可给多个）")

    p_watch = sub.add_parser(
        "watch", help="加入自选（不给代码 = 看当前自选）；只改本地状态，不联网")
    p_watch.add_argument("codes", nargs="*",
                         help="板块代码 BK1201 / 个股代码 300308；不给则列出自选")

    p_unwatch = sub.add_parser("unwatch", help="移出自选（只改本地状态，不联网）")
    p_unwatch.add_argument("codes", nargs="+", help="板块 / 个股代码")

    p_update = sub.add_parser(
        "update", help="更新本地缓存的「定义类」数据（板块成分股名单）")
    p_update.add_argument(
        "what", choices=["member", "stock", "tree"],
        help="更新什么：member = 板块成分股 / stock = 个股字典 / tree = 一级二级板块表")

    p_update.add_argument("codes", nargs="*",
                          help="板块代码（如 BK1201），或关键字 all（库里所有板块）"
                               " / watch（只在自选里的板块）")
    p_update.add_argument("--force", action="store_true",
                          help="配合 all：今天同步过的也重新拉一遍")

    p_show = sub.add_parser(
        "show", help="展示本地数据（成交额/涨跌幅柱状图 + 明细表，浏览器打开）")
    p_show.add_argument(
        "codes", nargs="+",
        help="板块代码 / 个股代码 / 关键字 board（一级+二级，按一级分标签）"
             " / watch（自选）")
    p_show.add_argument("--days", type=int, default=15, help="最近多少个交易日，默认 15")

    args = ap.parse_args()

    if args.cmd == "fetch":
        return cmd_fetch(args.codes, args.days, args.date, force=args.force)
    if args.cmd == "backfill":
        return _backfill_market(Fetcher(), args.days, end=args.end, force=args.force)
    if args.cmd == "drop":
        return cmd_drop(args.what, args.codes)
    if args.cmd == "watch":
        return cmd_watch(args.codes)
    if args.cmd == "unwatch":
        return cmd_unwatch(args.codes)
    if args.cmd == "update":
        return cmd_update(args.what, args.codes, force=args.force)
    return cmd_show(args.codes, args.days)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断")
        sys.exit(130)
