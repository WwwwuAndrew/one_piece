#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py —— 命令行长什么样，以及怎么变成命令对象。

这里是**唯一**懂 argv 的地方：解析、分派、退出码。命令本身不知道命令行怎么拼。
"""

from __future__ import annotations

import argparse

from .base import Command, Context
from .catalog import DropBoard, UpdateBoard
from .fetch import BackfillMarket, Fetch
from .show import Show
from .watch import Unwatch, Watch


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="hunter.py",
        description="去追寻资金的脚印吧",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例（完整用法见 README.md）：\n"
               "  python3 hunter.py backfill market --days 30   # 首次：补 30 天全市场\n"
               "  python3 hunter.py update board                # 首次/成分变动：拉申万板块定义\n"
               "  python3 hunter.py fetch market                 # 每天收盘：全市场日线\n"
               "  python3 hunter.py fetch board                  # 每天收盘：本地算板块指标\n"
               "  python3 hunter.py show board                   # 全部二级板块的因子表\n"
               "  python3 hunter.py show board --code 801081.SI  # 板块内个股的因子表\n"
               "  python3 hunter.py watch 801080.SI              # 加自选\n",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser(
        "fetch", help="拉取并入库（market=全市场 / board=本地计算板块）")
    p_fetch.add_argument(
        "what", choices=["market", "board"],
        help="market = 全市场个股日线（tushare）；board = 本地聚合板块指标")
    p_fetch.add_argument(
        "--date", default=None, metavar="YYYYMMDD",
        help="配合 market：指定要拉的交易日；不给则自动往回找最近有数据的一天")
    p_fetch.add_argument(
        "--force", action="store_true",
        help="market：重拉某天；board：全量重算（先清空 board_daily 再算）")

    p_back = sub.add_parser(
        "backfill", help="补历史空档（按交易日逐天往前拉全市场；本地已有的自动跳过）")
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
    p_drop.add_argument("codes", nargs="+", help="板块代码，如 801080.SI（可给多个）")

    p_watch = sub.add_parser(
        "watch", help="加入自选（不给代码 = 看当前自选）；只改本地状态，不联网")
    p_watch.add_argument("codes", nargs="*",
                         help="板块代码 801080.SI / 个股代码 300308；不给则列出自选")

    p_unwatch = sub.add_parser("unwatch", help="移出自选（只改本地状态，不联网）")
    p_unwatch.add_argument("codes", nargs="+", help="板块 / 个股代码")

    p_update = sub.add_parser(
        "update", help="更新板块定义（申万一级/二级 + 成分股，来自乐咕）")
    p_update.add_argument(
        "what", choices=["board"],
        help="更新什么：board = 拉最新一级/二级板块 + 成分股，更新数据库")
    p_update.add_argument("--force", action="store_true",
                          help="今天同步过的板块也重新拉一遍")

    p_show = sub.add_parser(
        "show", help="展示板块因子表：show board [--code 板块代码] [--good]")
    p_show.add_argument(
        "what", help="board —— 因子数字表（每板块 4 行 / 每只个股 3 行）")
    p_show.add_argument("rest", nargs="*", help=argparse.SUPPRESS)
    p_show.add_argument(
        "--code", default=None, metavar="板块代码",
        help="看这个板块内所有个股（右侧固定这个板块的表格），如 801081.SI")
    p_show.add_argument(
        "--good", action="store_true",
        help="只留还在跑的：近 3 日累计涨跌幅 < 0（个股看近 3 日累积 RS < -1%%）、"
             "或 AbsCost 连续 3 天下跌且 < 1 的，都不展示")
    p_show.add_argument("--days", type=int, default=10, help="最近多少个交易日，默认 10")

    return ap


def build_command(args, ctx: Context) -> Command:
    """把解析出来的参数翻成一个命令对象。"""
    if args.cmd == "fetch":
        return Fetch(ctx, args.what, date=args.date, force=args.force)
    if args.cmd == "backfill":
        return BackfillMarket(ctx, args.days, end=args.end, force=args.force)
    if args.cmd == "drop":
        return DropBoard(ctx, args.codes)
    if args.cmd == "watch":
        return Watch(ctx, args.codes)
    if args.cmd == "unwatch":
        return Unwatch(ctx, args.codes)
    if args.cmd == "update":
        return UpdateBoard(ctx, force=args.force)
    return Show(ctx, args.what, args.rest, code=args.code, good=args.good, days=args.days)


def main(argv: list[str] | None = None, ctx: Context | None = None) -> int:
    args = build_parser().parse_args(argv)
    return build_command(args, ctx if ctx is not None else Context()).run()
