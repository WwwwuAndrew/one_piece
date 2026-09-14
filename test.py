#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test.py —— 探针脚本：能不能拿到某个板块的**成分股名单**。

    python3 test.py              # 默认 BK1201
    python3 test.py BK1033       # 换一个板块

用的是 datasource/fetch_direct.py 的同一套机制（同一个 push2delay 集群、
同一个 requests.Session、同一套翻页和「取不全就报错」的检查），
只是把筛选条件从「全部行业板块」换成「某个板块的成分股」：

    全部行业板块    fs = "m:90 t:2 f:!50"
    某板块的成分股  fs = "b:BK1201 f:!50"      <- 就是这个

为什么要测这个：model.md §3 的架构要求「东方财富只做板块字典」，
而板块字典 = 板块列表 + 每个板块的成分股映射。
所以能不能稳定拿到成分股，是后面所有 Breadth 计算的前提。

⚠️ 请求量：BK1201 有 500+ 只成分股，而东财单页上限是 100 条，
   所以一次要翻 6 页 = 6 个请求（脚本会把每个请求都打出来给你看）。
   这正是「不能按板块逐个拉成分股」的原因 —— 板块一多请求量就爆了。
"""

from __future__ import annotations

import sys

from datasource.fetch_direct import FIELDS, HOST, DirectSource

DEFAULT_BOARD = "BK1201"


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _fmt_amount(v) -> str:
    v = _num(v)
    return "—" if v is None else f"{v / 1e8:,.2f} 亿"


def _fmt_pct(v) -> str:
    v = _num(v)
    return "—" if v is None else f"{v:+.2f}%"


def main() -> int:
    board = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BOARD).strip().upper()
    if not board.startswith("BK"):
        print(f"❌ 板块代码应形如 BK1201，收到：{board}")
        return 1

    fs = f"b:{board} f:!50"        # 板块成分股的筛选条件

    print("=" * 74)
    print(f"  拉取板块 {board} 的成分股")
    print("=" * 74)
    print(f"  集群   : {HOST}")
    print(f"  筛选   : fs = {fs!r}")
    print(f"  字段   : {len(FIELDS.split(','))} 个")
    print("=" * 74)

    src = DirectSource()

    # 把每个底层请求都打出来，方便你判断请求量（反爬风险就看这个数）
    calls = {"n": 0}
    original_get = src._get

    def counting_get(path: str, params: dict):
        calls["n"] += 1
        print(f"  请求 {calls['n']}: {path}  "
              f"pn={params.get('pn')} pz={params.get('pz')} fs={params.get('fs')!r}")
        return original_get(path, params)

    src._get = counting_get

    # ---- 1) 取成分股（复用 fetch_direct 的翻页 + 少数据告警）----
    print("\n【1】按页拉取成分股…")
    try:
        rows = src._clist(fs)
    except Exception as exc:
        print(f"\n❌ 拉取失败：{type(exc).__name__}: {exc}")
        print("\n如果报 RemoteDisconnected / ConnectionError：")
        print("  · 说明本机 IP 被这个集群拒了，或该集群对 b:<板块> 这种筛选不响应；")
        print("  · 别反复重跑（每次重跑都会重置限流计时）。")
        return 1

    print(f"\n✅ 拿到 {len(rows)} 只成分股，共 {calls['n']} 个请求\n")

    if not rows:
        print(f"⚠️  一条都没拿到：可能这个板块代码不存在，"
              f"或接口对 fs={fs!r} 不返回数据。")
        return 1

    # ---- 2) 打印名单 ----
    print("=" * 74)
    print(f"  {board}  成分股 {len(rows)} 只")
    print("=" * 74)
    print(f"{'代码':<8}{'名称':<12}{'最新价':>10}{'涨跌幅':>10}{'成交额':>14}")

    for r in rows:
        name = str(r.get("f14") or "")
        # 中文名按显示宽度补齐，避免列错位
        pad = 12 - sum(2 if ord(c) > 127 else 1 for c in name)
        print(f"{str(r.get('f12') or ''):<8}{name}{' ' * max(pad, 1)}"
              f"{_num(r.get('f2'), 0):>10,.2f}{_fmt_pct(r.get('f3')):>10}"
              f"{_fmt_amount(r.get('f6')):>14}")

    # ---- 3) 自己统计涨跌家数 ----
    up = sum(1 for r in rows if (_num(r.get("f3")) or 0) > 0)
    down = sum(1 for r in rows if (_num(r.get("f3")) or 0) < 0)
    flat = sum(1 for r in rows if _num(r.get("f3")) == 0)
    unknown = len(rows) - up - down - flat
    total_amount = sum(_num(r.get("f6"), 0) or 0 for r in rows)

    print("=" * 74)
    print("  【自己按成分股统计】")
    print(f"    涨 {up} / 跌 {down} / 平 {flat}"
          + (f" / 无数据 {unknown}" if unknown else "")
          + f"   （共 {up + down + flat + unknown} 只）")
    print(f"    成分股成交额合计：{total_amount / 1e8:,.2f} 亿")

    # ---- 4) 和板块自己的快照对一下 ----
    print("\n【2】取板块自己的快照，和上面统计对一下（再 1 个请求）…")
    try:
        board_rec = src.fetch_board(board)[0]
    except Exception as exc:
        print(f"  ⚠️  板块快照没取到（不影响成分股结果）：{type(exc).__name__}: {exc}")
        return 0

    b_up, b_dn, b_fl = board_rec.get("up"), board_rec.get("down"), board_rec.get("flat")
    print(f"    板块快照说：涨 {b_up} / 跌 {b_dn} / 平 {b_fl}"
          f"   （共 {(b_up or 0) + (b_dn or 0) + (b_fl or 0)} 只）")
    print(f"    成分股数量：{len(rows)}")
    # 注意：数据源返回的是原始记录，amount 单位是「元」
    #（换算成 亿/万 是落盘时 Fetcher 做的），所以这里直接用 _fmt_amount 自己除 1e8。
    print(f"    板块成交额：{_fmt_amount(board_rec.get('amount'))}"
          f"   成分股合计：{total_amount / 1e8:,.2f} 亿")

    print()
    if b_up == up and b_dn == down and b_fl == flat and b_up is not None:
        print("  ✅ 完全对得上 —— 说明「成分股名单」和「板块涨跌家数」口径一致，")
        print("     后面就能用成分股自己聚合出 Breadth（model.md §3 / §4.2）。")
    else:
        print("  ⚠️  对不上。可能原因：板块快照把停牌/无涨跌幅的股票也算进了某一边，")
        print("     或者成分股集合与统计口径略有差异 —— 把两行数字发我看看。")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断")
        sys.exit(130)
