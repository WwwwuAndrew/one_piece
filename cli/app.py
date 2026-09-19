#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py —— 命令行长什么样，以及怎么变成命令对象。

这里是**唯一**懂 argv 的地方：解析、分派、退出码。命令本身不知道命令行怎么拼。
"""

from __future__ import annotations

import argparse

from .base import Command, Context
from .catalog import DropBoard, UpdateStockList, UpdateTree
from .fetch import BackfillMarket, Fetch
from .members import UpdateMembers
from .show import Show
from .watch import Unwatch, Watch


def build_parser() -> argparse.ArgumentParser:
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
    p_fetch.add_argument(
        "--level", type=int, choices=[1, 2], default=None, metavar="{1,2}",
        help="配合 board：只刷一级（--level 1）或只刷二级（--level 2），默认两层都刷")

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

    return ap


def build_command(args, ctx: Context) -> Command:
    """把解析出来的参数翻成一个命令对象。"""
    if args.cmd == "fetch":
        return Fetch(ctx, args.codes, args.days, args.date,
                     force=args.force, level=args.level)
    if args.cmd == "backfill":
        return BackfillMarket(ctx, args.days, end=args.end, force=args.force)
    if args.cmd == "drop":
        return DropBoard(ctx, args.codes)
    if args.cmd == "watch":
        return Watch(ctx, args.codes)
    if args.cmd == "unwatch":
        return Unwatch(ctx, args.codes)
    if args.cmd == "update":
        return build_update(args, ctx)
    return Show(ctx, args.codes, days=args.days)


def build_update(args, ctx: Context) -> Command:
    """update <member|stock|tree> 三选一。"""
    if args.what == "stock":
        return UpdateStockList(ctx)
    if args.what == "tree":
        return UpdateTree(ctx)
    return UpdateMembers(ctx, args.codes, force=args.force)


def main(argv: list[str] | None = None, ctx: Context | None = None) -> int:
    args = build_parser().parse_args(argv)
    return build_command(args, ctx if ctx is not None else Context()).run()
