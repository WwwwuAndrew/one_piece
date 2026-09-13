#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_direct.py —— 直连东方财富「延时行情」的数据源（当天快照）。

    from datasource.fetch_direct import DirectSource
    src = DirectSource()                          # 也可以注入自己的 session
    src.fetch_board("BK1201")  -> list[dict]      板块当天快照（1 条）
    src.fetch_stock("300308")  -> list[dict]      个股当天快照（1 条）

继承 datasource/base.py 的 DataSource；记录结构、方法契约见 base.py 的文档。
**这个数据源只有「当天」**，要历史请用 akshare（板块）/ tushare（个股）。

（方法统一返回 list[dict]：当天就是 1 条，和别的数据源保持同一个契约，
 这样 Fetcher 才能无差别地合并落盘。）

为什么是延时行情集群 push2delay：
    AKShare 的板块/个股接口固定访问 push2.eastmoney.com / push2his.eastmoney.com，
    这两个域名在本机网络下会被服务器直接断开；push2delay.eastmoney.com 可正常访问。

请求量（每个标的）：
    板块：1 个请求。板块的 secid 前缀固定是 90（90.BK1201），直接精准取，
          不遍历、不翻页。
    个股：用代码推断市场号精确查询，最多 2 个请求（沪/深/北 需要猜一次）。

设计约定：
    **有状态的放类里**（连接、超时、重试、间隔 -> DirectSource）；
    **无状态的纯转换保持函数**（原始行 -> 统一结构 _normalize）；
    **全项目共用的纯函数放 base.py**（代码识别、to_num、日期窗口等）。
"""

from __future__ import annotations

import re
import time
from datetime import datetime

import requests

from .base import (BEIJING, DataSource, exchange_prefix, is_board_code,
                   is_stock_code, to_num)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

HOST = "push2delay.eastmoney.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}

UT = "bd1d9ddb04089700cf9c27f6f7426281"  # 东方财富要求的固定参数
TIMEOUT = 15                             # 单次请求超时（秒）
MAX_RETRY = 2                            # 最多重试 2 次（避免加重限流）
INTERVAL = 0.5                           # 多个请求之间的间隔（秒）

# 过滤器：取「全部行业板块」用。fetch_board 现在走精准查询用不到它，
# 但以后做「板块之间相对强弱」（模型里的相对走强）需要一次扫全部板块，先保留。
FS_INDUSTRY = "m:90 t:2 f:!50"           # 全部行业板块

# 字段 -> 含义（f 编号是东方财富的原始字段号）
FIELDS = ",".join([
    "f2",    # 最新价 / 板块点位
    "f3",    # 涨跌幅 %
    "f4",    # 涨跌额
    "f5",    # 成交量（个股=手，板块=股）
    "f6",    # 成交额（元）
    "f7",    # 振幅 %
    "f8",    # 换手率 %
    "f9",    # 市盈率(动)
    "f10",   # 量比
    "f12",   # 代码
    "f13",   # 市场：0=深/北 1=沪
    "f14",   # 名称
    "f15",   # 最高
    "f16",   # 最低
    "f17",   # 开盘
    "f18",   # 昨收
    "f20",   # 总市值
    "f21",   # 流通市值
    "f23",   # 市净率
    "f24",   # 60日涨跌幅 %
    "f25",   # 年初至今涨跌幅 %
    "f26",   # 上市日期
    "f104",  # 上涨家数（板块才有）
    "f105",  # 下跌家数（板块才有）
    "f106",  # 平盘家数（板块才有）
    "f124",  # 行情时间戳
])

# ---------------------------------------------------------------------------
# 原始行 -> 统一结构（无状态纯函数）
# 公共工具（代码识别 / to_num / BEIJING）已挪到 base.py，全项目共用一份
# ---------------------------------------------------------------------------


def _normalize(row: dict, kind: str) -> dict:
    """东财原始行 -> 统一结构。kind: 'board' | 'stock'。"""
    code = str(row.get("f12") or "")
    name = row.get("f14")
    list_date = row.get("f26")

    rec: dict = {
        "code": code,
        "name": str(name) if name else "",
        "price": to_num(row.get("f2")),
        "change_pct": to_num(row.get("f3")),
        "change": to_num(row.get("f4")),
        "amount": to_num(row.get("f6")),
        "amplitude_pct": to_num(row.get("f7")),
        "turnover_pct": to_num(row.get("f8")),
        "pe": to_num(row.get("f9")),
        "volume_ratio": to_num(row.get("f10")),
        "open": to_num(row.get("f17")),
        "high": to_num(row.get("f15")),
        "low": to_num(row.get("f16")),
        "pre_close": to_num(row.get("f18")),
        "total_mv": to_num(row.get("f20")),
        "float_mv": to_num(row.get("f21")),
        "pb": to_num(row.get("f23")),
        "chg_60d": to_num(row.get("f24")),
        "chg_ytd": to_num(row.get("f25")),
    }

    # 成交量：个股 f5 是「手」（1 手 = 100 股），板块 f5 是「股」，统一存成「股」
    vol = to_num(row.get("f5"))
    if vol is not None:
        rec["volume"] = vol if kind == "board" else vol * 100

    # 涨跌家数：只有板块有
    if kind == "board":
        up, dn, fl = to_num(row.get("f104")), to_num(row.get("f105")), to_num(row.get("f106"))
        if up is not None:
            rec["up"] = int(up)
        if dn is not None:
            rec["down"] = int(dn)
        if fl is not None:
            rec["flat"] = int(fl)

    # 上市日期：仅个股有，必须是 8 位数字
    if isinstance(list_date, str) and re.fullmatch(r"\d{8}", list_date):
        rec["list_date"] = list_date

    # 行情时间戳 -> 日期 + 时间
    ts = to_num(row.get("f124"))
    if ts:
        dt = datetime.fromtimestamp(ts, BEIJING)
        rec["quote_time"] = dt.strftime("%Y-%m-%d %H:%M:%S")
        rec["date"] = dt.strftime("%Y-%m-%d")
    else:
        rec["date"] = datetime.now(BEIJING).strftime("%Y-%m-%d")

    # 去掉 None，落盘更干净
    return {k: v for k, v in rec.items() if v is not None}


# ---------------------------------------------------------------------------
# 数据源
# ---------------------------------------------------------------------------

class DirectSource(DataSource):
    """东方财富延时行情数据源。

    持有自己的 requests.Session：**连接复用**，不必每次请求都重新做
    TCP + TLS 握手（更快，也更像正常浏览器，不容易被当成脚本）。
    """

    def __init__(self,
                 session: requests.Session | None = None,
                 timeout: int = TIMEOUT,
                 max_retry: int = MAX_RETRY,
                 interval: float = INTERVAL):
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        self.timeout = timeout
        self.max_retry = max_retry
        self.interval = interval

    # -- 底层请求 ---------------------------------------------------------
    def _get(self, path: str, params: dict) -> dict:
        """带少量重试的一次 GET（走 self.session）。"""
        url = f"https://{HOST}{path}"
        last_err = None
        for attempt in range(1, self.max_retry + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                last_err = exc
                if attempt < self.max_retry:
                    time.sleep(2)
        raise RuntimeError(f"请求失败（已重试 {self.max_retry} 次）：{last_err}")

    def _clist(self, fs: str, page_size: int = 100, max_pages: int = 12) -> list[dict]:
        """取一个列表，按需翻页。

        目前 fetch_board 已改为精准查询（1 个请求），不再走这里；
        保留它是为了以后「一次扫全部板块」的场景（板块相对强弱要用）。

        ⚠️ 东方财富服务端**单页上限就是 100 条**（传 pz=500 也只会返回 100 条，
        多余的被静默截断）。所以列表必须翻页，否则会悄悄少拿数据。
        行业板块共 496 个 -> 5 页。
        """
        rows: list[dict] = []
        total = None
        for page in range(1, max_pages + 1):
            if page > 1:
                time.sleep(self.interval)
            payload = self._get("/api/qt/clist/get", {
                "pn": page, "pz": page_size, "po": 1, "np": 1, "ut": UT,
                "fltt": 2, "invt": 2, "fid": "f3", "fs": fs, "fields": FIELDS,
            })
            data = payload.get("data") or {}
            diff = data.get("diff") or []
            if total is None:
                total = data.get("total")
            if not diff:
                break
            rows.extend(diff)
            if total is not None and len(rows) >= total:
                break
            if len(diff) < page_size:
                break

        if total is not None and len(rows) < total:
            raise RuntimeError(
                f"列表未取全：接口报 total={total}，实际只拿到 {len(rows)} 条"
                f"（已翻满 {max_pages} 页）")
        return rows

    def _ulist(self, secids: list[str]) -> list[dict]:
        """按 secid 精确取行情（secid 形如 0.300308 / 1.600000 / 90.BK1201）。"""
        payload = self._get("/api/qt/ulist.np/get", {
            "fltt": 2, "invt": 2, "ut": UT,
            "secids": ",".join(secids), "fields": FIELDS,
        })
        return list((payload.get("data") or {}).get("diff") or [])

    # -- 对外接口（两个）--------------------------------------------------
    def fetch_board(self, code: str, days: int | None = None) -> list[dict]:
        """查板块**当天**快照。**1 个请求**：板块 secid 前缀固定是 90，直接精准取。

        与个股不同（个股市场号要猜，最多 2 次），板块不用遍历全部板块、也不用翻页。
        这个数据源只有「当天」，要历史请用 akshare 数据源。
        """
        self._reject_history("板块", days)
        code = code.strip().upper()
        rows = self._ulist([f"90.{code}"])
        if not rows or not rows[0].get("f12"):
            raise LookupError(
                f"{code} 精准查询没返回数据（secid=90.{code}），该板块代码可能不存在")

        rec = _normalize(rows[0], "board")

        if "up" not in rec:
            raise RuntimeError(
                f"{code} 精准查询有数据，但没有涨跌家数（f104/f105/f106）——"
                f"说明 ulist 接口不提供板块涨跌家数，需要改回「取全部板块再筛」的方式。")
        return [rec]

    def fetch_stock(self, code: str, days: int | None = None) -> list[dict]:
        """查个股**当天**快照。用代码推断市场号精确查询，最多 2 个请求。"""
        self._reject_history("个股", days)
        code = code.strip().zfill(6)
        first = 1 if exchange_prefix(code) == "sh" else 0
        last_exc = None
        for market in (first, 1 - first):
            try:
                rows = self._ulist([f"{market}.{code}"])
            except Exception as exc:
                last_exc = exc
                continue
            if rows and rows[0].get("f12"):
                return [_normalize(rows[0], "stock")]
        if last_exc is not None:
            raise last_exc
        raise LookupError(f"{code} 没查到数据（代码可能不存在）")

    @staticmethod
    def _reject_history(what: str, days: int | None) -> None:
        """这个数据源只有当天快照。要求历史就明确报错，不要静默只返回 1 条。"""
        if days is not None and days > 1:
            raise NotImplementedError(
                f"DirectSource 只有{what}的当天快照（1 条），拿不到 {days} 天历史；"
                f"历史请把配置切到 akshare / tushare 数据源")

