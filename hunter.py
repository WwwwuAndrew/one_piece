#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hunter.py —— 整个项目唯一的执行入口。

用法：
    # 拉取（并落盘）板块 / 个股
    python3 hunter.py fetch BK1201                 # 板块当天快照（direct）
    python3 hunter.py fetch 300308                 # 个股最新一天（tushare）
    python3 hunter.py fetch BK1201 --days 15       # 板块最近 15 个交易日（akshare）
    python3 hunter.py fetch 300308 --days 15       # 个股最近 15 个交易日（tushare）
    python3 hunter.py fetch BK1201 300308          # 一次拉多个

    # 批量：把本地已缓存过的板块全部刷一遍最新（两个之间会自动间隔，避免被反爬）
    python3 hunter.py fetch board

    # 展示本地已存的最近 N 天数据（默认 15 天，浏览器图形化表格 + 柱状图）
    python3 hunter.py show BK1201
    python3 hunter.py show 300308 --days 30
    python3 hunter.py show BK1201 300308 BK1036
    python3 hunter.py show board                 # 展示本地已缓存的所有板块

说明：
    fetch  = 数据获取主接口（datasource/fetch.py）：按 config/system.yaml 配的
             数据源联网拉取，再按交易日与本地比对，缺了才落盘到 data/raw/。
    show   = 只读本地 data/raw/，渲染成图形化页面（成交额/涨跌幅柱状图 + 明细表）
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
from datasource import show_data


def _print_result(result) -> None:
    """把一次 fetch 的结果打给用户看。"""
    rec = result.fetched[-1]
    name = rec.get("name") or ""
    if len(result.fetched) == 1:
        # 只要最新一条：直接展示快照
        show_data.print_snapshot(rec)
        return
    print(f"✅ {result.code} {name}  拉到 {len(result.fetched)} 条"
          f"（新增 {len(result.added)} 条，本地共 {result.total} 条）"
          f"  {result.fetched[0].get('date')} ~ {rec.get('date')}")


def _fetch_all_boards(boards: Boards, days: int | None) -> int:
    """把本地已缓存的板块逐个刷一遍最新。"""
    if days:
        print("⚠️  fetch board 是批量刷最新快照，忽略 --days")

    codes = boards.cached_codes()
    if not codes:
        print("⚠️  本地还没有任何板块数据。先拉一个具体的，例如：")
        print("      python3 hunter.py fetch BK1201")
        return 1

    interval = float(config.system.get("fetch_interval", 1.0) or 0)
    print(f"本地已缓存 {len(codes)} 个板块，逐个拉最新（每个之间间隔 {interval} 秒）…")

    ok = 0
    for i, code in enumerate(codes, 1):
        if i > 1 and interval:
            time.sleep(interval)          # 间隔一下，别把对方惹毛
        try:
            result = boards.fetch(code)
        except Exception as exc:
            print(f"  [{i}/{len(codes)}] ❌ {code}：{exc}", file=sys.stderr)
            continue
        rec = result.fetched[-1]
        mark = "新增" if result.added else "本地已有"
        print(f"  [{i}/{len(codes)}] ✅ {code} {rec.get('name') or '':<8} "
              f"{rec.get('date')}  {mark}")
        ok += 1

    print(f"\n完成：{ok}/{len(codes)} 个成功")
    return 0 if ok == len(codes) else 1


def cmd_fetch(codes: list[str], days: int | None) -> int:
    # 一个命令里只用一套 Fetcher / Boards / Stocks：
    # 这样数据源实例是复用的（DirectSource 的连接池才有效）。
    fetcher = Fetcher()
    boards = Boards(fetcher)
    stocks = Stocks(fetcher)

    rc = 0
    for raw in codes:
        code = raw.strip()
        if code.lower() == "board":
            rc |= _fetch_all_boards(boards, days)
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


def cmd_show(codes: list[str], days: int) -> int:
    # 关键字 board：把本地已缓存的所有板块一起展示出来
    expanded: list[str] = []
    for raw in codes:
        c = raw.strip()
        if c.lower() == "board":
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
               "  python3 hunter.py fetch BK1201\n"
               "  python3 hunter.py fetch 300308\n"
               "  python3 hunter.py fetch BK1201 --days 15\n"
               "  python3 hunter.py fetch 300308 --days 15\n"
               "  python3 hunter.py fetch board            # 批量刷本地所有板块\n"
               "  python3 hunter.py show BK1201\n"
               "  python3 hunter.py show 300308 --days 30\n"
               "  python3 hunter.py show board             # 展示本地所有板块",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser(
        "fetch", help="拉取板块/个股并落盘（--days 拉历史；codes 给 board 则批量刷本地板块）")
    p_fetch.add_argument(
        "codes", nargs="+",
        help="板块代码 BK1201 / 个股代码 300308 / 关键字 board（批量刷本地已缓存的板块）")
    p_fetch.add_argument(
        "--days", type=int, default=None,
        help="拉最近 N 个交易日；不给则只拉最新一条")

    p_show = sub.add_parser(
        "show", help="展示本地数据（成交额/涨跌幅柱状图 + 明细表，浏览器打开）")
    p_show.add_argument(
        "codes", nargs="+",
        help="板块代码 / 个股代码 / 关键字 board（展示本地已缓存的所有板块）")
    p_show.add_argument("--days", type=int, default=15, help="最近多少个交易日，默认 15")

    args = ap.parse_args()

    if args.cmd == "fetch":
        return cmd_fetch(args.codes, args.days)
    return cmd_show(args.codes, args.days)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断")
        sys.exit(130)
