#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_direct.py —— 东方财富延时行情数据源（板块快照 + 板块成分股）。

    src = DirectSource()
    src.fetch_board("BK1201")              # 板块当天快照
    src.fetch_board_members("BK1201")      # 成分股名单（分页，约 6 个请求）
    src.fetch_industry_boards()            # 全部行业板块（约 5 个请求）

⚠️ 宿主是 **push2delay**（延时行情）那个集群：`push2` / `push2his` / `7.push2his` /
   `29.push2` 这几个在本机是被拒的（RemoteDisconnected），别改回去。
   `push2delay` 只服务 clist / ulist，**没有 kline** —— 所以板块历史走 akshare。

⚠️ 单页上限 100：传 pz=500 也只会返回 100 条，**必须翻页**，
   而且拿不全要报错而不是静默少返回（`_clist` 里那条 total 检查就是干这个的）。

限速：单个请求失败重试 2 次，请求之间隔 INTERVAL 秒；批量操作由调用方再拉开间隔。
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

    # 去掉 None，入库更干净
    return {k: v for k, v in rec.items() if v is not None}


# ---------------------------------------------------------------------------
# 数据源
# ---------------------------------------------------------------------------

class DirectSource(DataSource):
    """
    东方财富延时行情数据源（板块快照 / 成分股 / 板块清单）。

    自带 requests.Session：连接复用，更快也更像正常浏览器。
    """

    # 入库时写进记录的 source 字段。**必须显式声明** ——
    # 基类给的是 "base"，不写就会把 "base" 当成「某某数据源」存进去。
    name = "direct"

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
        # 累计发了多少个请求（**含重试**，因为这正是对方看到的数）。
        # 批量同步板块成分股时把它打出来，心里才有底。
        self.requests_made = 0

    # -- 底层请求 ---------------------------------------------------------
    def _get(self, path: str, params: dict) -> dict:
        """带少量重试的一次 GET（走 self.session）。"""
        url = f"https://{HOST}{path}"
        last_err = None
        for attempt in range(1, self.max_retry + 1):
            self.requests_made += 1
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                last_err = exc
                if attempt < self.max_retry:
                    time.sleep(2)
        raise RuntimeError(f"请求失败（已重试 {self.max_retry} 次）：{last_err}")

    def _clist(self, fs: str, page_size: int = 100, max_pages: int = 12,
               fields: str | None = None) -> list[dict]:
        """
        取一个列表，按需翻页。

        ⚠️ 东财服务端**单页上限就是 100**（传 pz=500 也只返回 100 条，多的被静默截断），
        所以必须翻页；拿不全就报错，不能悄悄少返回。
        """
        rows: list[dict] = []
        total = None
        for page in range(1, max_pages + 1):
            if page > 1:
                time.sleep(self.interval)
            payload = self._get("/api/qt/clist/get", {
                "pn": page, "pz": page_size, "po": 1, "np": 1, "ut": UT,
                "fltt": 2, "invt": 2, "fid": "f3", "fs": fs,
                "fields": fields or FIELDS,
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

    # -- 对外接口：行情（两个）--------------------------------------------
    def fetch_board(self, code: str, days: int | None = None) -> list[dict]:
        """
        拉板块**当天快照**（1 个请求，精准查询，不是扫列表）。
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

    # -- 板块成分股（板块字典用）------------------------------------------
    def fetch_industry_boards(self, fields: str | None = None) -> list[dict]:
        """
        拉**全部行业板块**（一次扫全市场板块）：`fs = "m:90 t:2 f:!50"`，约 496 个 / 5 个请求。

        ⚠️ 这 496 个是**扁平的混合表**：既有电子(BK1201) 这种一级，也有电池(BK1033) 这种二级，
        接口本身**不给层级**。所以这里原样返回，层级关系见 board_tree.py。
        """
        return self._clist(FS_INDUSTRY, fields=fields)

    def fetch_board_members(self, code: str) -> list[dict]:
        """
        拉某个板块的**成分股名单**：`fs = "b:BK1201 f:!50"`。

        ⚠️ 请求量 = 成员数 ÷ 100 向上取整（单页上限 100）：BK1201 有 521 只 -> 6 个请求。
        所以**绝不能按板块逐个去拉**（500 个板块 ≈ 3000 个请求）；日常行情走 fetch market。
        """
        code = code.strip().upper()
        rows = self._clist(f"b:{code} f:!50")
        if not rows:
            raise LookupError(f"{code} 没拉到任何成分股（板块代码可能不对）")

        out, seen = [], set()
        for r in rows:
            c = str(r.get("f12") or "").strip()
            if not c or c in seen:
                continue
            seen.add(c)
            out.append({"code": c, "name": str(r.get("f14") or "")})
        return out

    def fetch_board_name(self, code: str) -> str | None:
        """取板块名称（1 个请求）。

        成分股接口不返回板块名（它只给股票名），所以名字要单独取一次。
        """
        code = code.strip().upper()
        rows = self._ulist([f"90.{code}"])
        if rows and rows[0].get("f14"):
            return str(rows[0]["f14"])
        return None

    @staticmethod
    def _reject_history(what: str, days: int | None) -> None:
        """这个数据源只有当天快照。要求历史就明确报错，不要静默只返回 1 条。"""
        if days is not None and days > 1:
            raise NotImplementedError(
                f"DirectSource 只有{what}的当天快照（1 条），拿不到 {days} 天历史；"
                f"历史请把配置切到 akshare / tushare 数据源")

