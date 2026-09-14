#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hunter.py —— 整个项目唯一的执行入口。

用法：
    # 拉取（并入库）板块 / 个股
    python3 hunter.py fetch market                 # ★ 全市场日线入库（1 个请求）
    python3 hunter.py fetch market --date 20260911 # 指定交易日
    python3 hunter.py fetch BK1201                 # 板块当天快照（direct）
    python3 hunter.py fetch 300308                 # 个股最新一天（tushare）
    python3 hunter.py fetch BK1201 --days 15       # 板块最近 15 个交易日（akshare）
    python3 hunter.py fetch 300308 --days 15       # 个股最近 15 个交易日（tushare）
    python3 hunter.py fetch BK1201 300308          # 一次拉多个

    # 批量：把本地已缓存过的板块全部刷一遍最新（两个之间会自动间隔，避免被反爬）
    python3 hunter.py fetch board

    # 补历史空档（新装时跑一次；一天 1 个请求，本地已有的自动跳过、可中断续跑）
    python3 hunter.py backfill market --days 30    # 最近 30 个交易日的全市场日线
    python3 hunter.py backfill market --days 30 --force   # 已有的也重拉（修没取全的天）

    # 自选：关注不过来那么多板块，看到感兴趣的加进来，势能走完再移出去
    python3 hunter.py watch BK1201 300308          # 加入自选（纯本地，不联网）
    python3 hunter.py watch                        # 看当前自选
    python3 hunter.py unwatch BK1201               # 移出自选（纯本地）

    # 关键字 watch：只对「自选里的标的」做同一件事
    python3 hunter.py fetch watch                  # 刷自选（已是最新的跳过，0 请求）
    python3 hunter.py fetch watch --days 15        # 拉自选的最近 15 个交易日
    python3 hunter.py show watch                   # 只看自选
    python3 hunter.py update member watch          # 只同步自选里板块的成分股

    # 同步「定义类」数据 —— 都不常做（只在上市/改名/换成分股时才需要）
    python3 hunter.py update member BK1201         # 一个板块的成分股（走东财，分页拉）
    python3 hunter.py update member all            # ★ 库里所有板块，带防反爬 + 可续跑
    python3 hunter.py update stock                 # 个股字典：代码→名字（1 个请求）

    # 展示本地已存的最近 N 天数据（默认 15 天，浏览器图形化表格 + 柱状图）
    python3 hunter.py show BK1201
    python3 hunter.py show 300308 --days 30
    python3 hunter.py show BK1201 300308 BK1036
    python3 hunter.py show board                 # 展示本地已缓存的所有板块

说明：
    watch    = 自选（存 data/local.sqlite，**不可重建**所以单独一个文件）。
               板块和个股共用一张表；watch / unwatch 只改本地状态，不联网。
               fetch / update / show 都认关键字 watch = 只操作自选里的标的。
    fetch    = 数据获取主接口（datasource/fetch.py）：按 config/system.yaml 配的
               数据源联网拉取，再按交易日与本地比对，缺了才入库（data/raw/raw.sqlite）。
               fetch market 入 stock_daily；fetch 板块入 board_daily。
               ★ fetch board / fetch watch 会先看本地是不是已经到「最新交易日」了，
                 是就跳过、一个请求都不发（判据见 Fetcher.is_up_to_date）；加 --force 可强制重拉。
    backfill = 补历史空档：按交易日逐天往前拉（fetch 只管最新那一天）。
    update   = 更新「定义类」数据（都不常做）：
               update member <代码|all>  板块成分股，走东财（all = 库里所有板块）
               update stock              个股字典（代码→名字），Tushare 1 个请求拿全市场
    show     = 只读本地数据库，渲染成图形化页面（成交额/涨跌幅柱状图 + 明细表）
               并在浏览器打开，不联网。
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
        # 只要最新一条：直接展示快照
        show_data.print_snapshot(rec)
        return
    first = date_to_str(result.fetched[0].get("trade_date"))
    print(f"✅ {result.code} {name}  拉到 {len(result.fetched)} 条"
          f"（新增 {len(result.added)} 条，本地共 {result.total} 条）"
          f"  {first} ~ {date_to_str(rec.get('trade_date'))}")


def _split_fresh(fetcher: Fetcher, codes: list[str], days: int | None,
                 force: bool) -> tuple[list[str], list[str], int | None]:
    """把这批代码分成 (需要拉的, 本地已经是最新的)。

    「已经是最新」= 本地数据 >= 全市场日线覆盖到的最后一天（交易日钟）。
    没联网、没猜「今天是哪天」—— 详见 Fetcher.is_up_to_date。

    只在「只要最新」时跳过（days 为空）：给了 --days N 说明你就是要历史，
    那就老老实实去拉，别自作聪明。
    """
    if force or days is not None:
        return list(codes), [], None
    # 交易日钟从**这个 fetcher 自己的库**取（不要去摸模块级的 store ——
    # 生产和测试里看着一样，但那是巧合，层级上 hunter 不该替 fetch 决定读哪个库）
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
    """批量刷一批标的：本地已是最新的跳过，其余逐个拉（带间隔）。

    fetch board 和 fetch watch 共用这一套 —— 这样「跳过」的规则只有一份，不会走岔。
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


def _fetch_all_boards(fetcher: Fetcher, boxes, days: int | None,
                      force: bool = False) -> int:
    """fetch board —— 把本地已有数据的板块都刷一遍最新。"""
    codes = boxes[0].cached_codes()
    if not codes:
        print("⚠️  本地还没有任何板块数据。先拉一个具体的，例如：")
        print("      python3 hunter.py fetch BK1201")
        return 1
    return _fetch_batch(fetcher, boxes, codes, days, force, label="本地已有的板块 ")


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
    """库内体检：每个交易日的行数都该接近「全市场只数」。

    明显偏少 = 那天没取全（接口截断 / 数据还没跑完就拉了）。
    行数只增不减（上市公司只会越来越多），所以「比最多的那天少 5% 以上」
    基本只有一个解释：那天缺数据。（30 个交易日里不可能新上市 270 只。）
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
    """fetch market —— 拉**一天**的全市场日线入库。1 个请求，约 5400 只。

    这是整套架构的地基：行情只拉一次，之后所有板块计算都在本地做。
    本地已经有的交易日不会重复拉（要重拉用 backfill --force）。
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
    """backfill market --days N —— 把最近 N 个交易日的全市场日线补齐。

    一次性动作：新装、或想回看更早的行情时跑一次；
    之后每天只要 fetch market 补当天那一根就够了。

    · 一天 1 个请求，**本地已有的不重复拉** -> 中途 Ctrl-C，重跑会接着补，不白费请求；
    · 请求之间按 config 的 fetch_interval 间隔（tushare 120 积分限 50 次/分钟）。
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
            # 走 stdout：这行是**进度**的一部分（否则日志里编号会缺一块，看着像 bug）。
            # 整个命令直接失败（比如 token 不对）才走 stderr。
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
            # 有没有本地数据，一眼能看出来（省得你去猜为什么 show 是空的）
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
    """fetch watch —— 把**自选里的**标的各刷一遍最新（本地已有当天数据的跳过）。

    自选通常只有几个，所以这个命令很轻；想全刷板块用 fetch board。
    """
    codes = watched_codes()
    if not codes:
        print("⚠️  自选是空的。先加一个：python3 hunter.py watch BK1201")
        return 1
    return _fetch_batch(fetcher, boxes, codes, days, force, label="自选 ")


def cmd_fetch(codes: list[str], days: int | None,
              trade_date: str | None = None, force: bool = False) -> int:
    # 一个命令里只用一套 Fetcher / Boards / Stocks：
    # 这样数据源实例是复用的（DirectSource 的连接池才有效）。
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
    """同步一个板块的成分股，返回 {"code","name","total","added","removed"}。

    做三件事：
      1. 拉成分股（分页，成员数 ÷ 100 个请求）
      2. 整块替换 concept_member（删掉的去除、新增的加上）
      3. 记下同步时间（+ 板块名，只在本地没有名字时才花那 1 个请求）

    失败就抛出来，由调用方决定「继续下一个」还是「赶紧停」。
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
    """update member BK1201 —— 同步**一个**板块的成分股名单。"""
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
    """库里「相关的」板块 = 有行情的（board_daily）∪ 字典里的（board_list）。

    前者 = 你抓过数据的，后者 = 你同步过成员 / 手动登记过的，合起来才是「我在跟的板块」。
    """
    rows = store.query("SELECT concept FROM board_daily "
                       "UNION SELECT concept FROM board_list ORDER BY concept")
    return [r["concept"] for r in rows]


def _update_member_watch(force: bool = False) -> int:
    """update member watch —— 只同步**自选里那些板块**的成分股。

    自选里的个股会被自动忽略（个股没有「成分股」这回事）。
    """
    codes = [c for c in watched_codes("board")]
    if not codes:
        print("⚠️  自选里没有板块。先加一个：python3 hunter.py watch BK1201")
        return 1
    return _sync_members(codes, force=force)


def _update_member_all(force: bool = False) -> int:
    """update member all —— 把库里**所有相关板块**的成分股都同步一遍。

    ★ 防反爬（这个命令是整套系统里请求最多的，必须小心）：

      1. **板块之间按 config 的 fetch_interval 间隔**（默认 2 秒），不连着打；
      2. **今天已经同步过的直接跳过** —— 成分股很少变（一个月才动几只），
         没必要天天重拉；顺带让这个命令**可以中断续跑**：Ctrl-C 之后重跑，
         同步好的会跳过，不会从头再来一遍；
      3. **一旦被拒（RemoteDisconnected 之类）立刻停下**，不再往下敲 ——
         继续敲只会让 IP 被锁更久。宁可只同步一半，也别把路走死。

    请求量：每个板块 = 成员数 ÷ 100 向上取整（东财单页上限 100），
    本地有名字就不再花那 1 个请求。21 个板块大约 60 个请求 / 2 分钟。
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
    # 请求数只有 DirectSource 记（别的数据源没有计数器，就不报这一句）
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
    """update stock —— 同步「个股字典」（代码 → 名字）。**1 个请求**，约 5500 只。

    为什么单独做一次：名字属于「字典」不属于行情，每天那份全市场日线里没有名字；
    而按只去查名字要 5500 个请求，一次拉全量只要 1 个。
    同步一次能用很久（只在上市 / 改名 / 退市时才需要再拉）。
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


def cmd_update(what: str, codes: list[str], force: bool = False) -> int:
    """update member <codes|all> / update stock。"""
    if what == "stock":
        return _update_stock_list()
    if what != "member":
        print(f"\n❌ 暂不支持 update {what}（只有 member / stock）", file=sys.stderr)
        return 1
    if not codes:
        print("\n❌ update member 要跟板块代码或 all，例如：\n"
              "      python3 hunter.py update member BK1201\n"
              "      python3 hunter.py update member all", file=sys.stderr)
        return 1

    # all：把库里所有相关板块（有行情 ∪ 在字典里）都同步一遍
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


def cmd_show(codes: list[str], days: int) -> int:
    # 关键字 board：把本地已缓存的所有板块一起展示出来
    expanded: list[str] = []
    for raw in codes:
        c = raw.strip()
        if c.lower() == "watch":
            w = watched_codes()
            if not w:
                print("⚠️  自选是空的。先加一个：python3 hunter.py watch BK1201")
                return 1
            print(f"自选 {len(w)} 个，全部展示：" + ", ".join(w))
            expanded.extend(w)
        elif c.lower() == "board":
            cached = Fetcher().cached_codes("board")
            if not cached:
                print("⚠️  本地还没有任何板块数据。先拉一个，例如：")
                print("      python3 hunter.py fetch BK1201")
                return 1
            print(f"本地已缓存 {len(cached)} 个板块，全部展示：{', '.join(cached)}")
            expanded.extend(cached)
        else:
            expanded.append(c)

    try:
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
        epilog="示例：\n"
               "  python3 hunter.py fetch market           # ★ 全市场日线入库（1 个请求）\n"
               "  python3 hunter.py fetch market --date 20260911\n"
               "  python3 hunter.py fetch BK1201\n"
               "  python3 hunter.py fetch 300308\n"
               "  python3 hunter.py fetch BK1201 --days 15\n"
               "  python3 hunter.py fetch 300308 --days 15\n"
               "  python3 hunter.py fetch board            # 批量刷本地所有板块\n"
               "  python3 hunter.py backfill market --days 30   # 补最近 30 个交易日\n"
               "  python3 hunter.py watch BK1201 300308          # 加入自选\n"
               "  python3 hunter.py fetch watch                  # 只刷自选\n"
               "  python3 hunter.py show watch                   # 只看自选\n"
               "  python3 hunter.py update member all            # 同步所有板块的成分股\n"
               "  python3 hunter.py backfill market --days 1 --force  # 重拉最新那天\n"
               "  python3 hunter.py show BK1201\n"
               "  python3 hunter.py show 300308 --days 30\n"
               "  python3 hunter.py show board             # 展示本地所有板块",
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

    p_watch = sub.add_parser(
        "watch", help="加入自选（不给代码 = 看当前自选）；只改本地状态，不联网")
    p_watch.add_argument("codes", nargs="*",
                         help="板块代码 BK1201 / 个股代码 300308；不给则列出自选")

    p_unwatch = sub.add_parser("unwatch", help="移出自选（只改本地状态，不联网）")
    p_unwatch.add_argument("codes", nargs="+", help="板块 / 个股代码")

    p_update = sub.add_parser(
        "update", help="更新本地缓存的「定义类」数据（板块成分股名单）")
    p_update.add_argument(
        "what", choices=["member", "stock"],
        help="更新什么：member = 板块成分股名单 / stock = 个股字典（代码→名字）")
    p_update.add_argument("codes", nargs="*",
                          help="板块代码（如 BK1201），或关键字 all（库里所有板块）"
                               " / watch（只在自选里的板块）")
    p_update.add_argument("--force", action="store_true",
                          help="配合 all：今天同步过的也重新拉一遍")

    p_show = sub.add_parser(
        "show", help="展示本地数据（成交额/涨跌幅柱状图 + 明细表，浏览器打开）")
    p_show.add_argument(
        "codes", nargs="+",
        help="板块代码 / 个股代码 / 关键字 board（本地所有板块） / watch（自选）")
    p_show.add_argument("--days", type=int, default=15, help="最近多少个交易日，默认 15")

    args = ap.parse_args()

    if args.cmd == "fetch":
        return cmd_fetch(args.codes, args.days, args.date, force=args.force)
    if args.cmd == "backfill":
        return _backfill_market(Fetcher(), args.days, end=args.end, force=args.force)
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
