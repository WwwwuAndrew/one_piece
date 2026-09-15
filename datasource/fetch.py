#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch.py —— 数据获取主接口：按配置选数据源 + 联网拉取 + 按交易日入库。

    f = Fetcher()
    f.fetch("BK1201")               -> FetchResult  板块当天快照
    f.fetch("BK1201", days=15)      -> FetchResult  板块最近 15 个交易日
    f.fetch("300308")               -> FetchResult  个股最新一天
    f.history("BK1201", days=15)    -> list[dict]   读本地（不联网）
    f.cached_codes("board")         -> list[str]    本地已有哪些板块
    f.market("20260915")            -> MarketDay    全市场某一天（1 个请求）
    f.market_plan(30)               -> list[dict]   最近 30 个交易日的补数计划
    f.is_up_to_date("BK1201")       -> bool         本地是不是已经最新（0 个请求）

流程：挑数据源 → 联网拉 → 对齐成库的列（单位原样）→ 按交易日与本地比对 → 缺的才写。
存什么永远由数据源返回的交易日决定，**不做「今天是哪天」的判断**，所以周末节假日不会算错。

数据源路由（config/system.yaml）：board_today / board_history / stock / market
（market 不配就复用 stock）。

记录的形状 = **数据库的一行**（列名和单位都跟库里一致），调用方不需要知道数据存在哪儿。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from config.config import config

from .base import DataSource, is_board_code, is_stock_code, to_date, today_str
from .fetch_akshare import AkshareSource
from .fetch_direct import DirectSource
from .fetch_tushare import TushareSource
from .store import Store, date_to_int, date_to_str, store as default_store

# 两个 kind 各自的表列定义。列取自数据库，所以「表加了列」不会被漏掉。
_TABLE_COLS: dict[str, tuple] = {
    "board": Store.TABLE_COLS["board_daily"],
    "stock": Store.TABLE_COLS["stock_daily"],
}

# 数据源工厂：名字 -> 造实例的可调用对象。
#
# 做成「工厂 + 用到了才创建」，而不是启动就把三个都建好，原因有二：
#   1. tushare 没填 token 时会构造失败，只拉板块的人不该被它拦住；
#   2. akshare / tushare 的 import 都很重，不用就不该加载。
SOURCE_FACTORIES: dict[str, type[DataSource]] = {
    "direct": DirectSource,
    "akshare": AkshareSource,
    "tushare": TushareSource,
}


@dataclass
class FetchResult:
    """一次 fetch 的结果（方便上层报告「拉到几条、新增几条」）。"""

    code: str
    fetched: list[dict] = field(default_factory=list)  # 这次从数据源拿到的
    added: list[dict] = field(default_factory=list)    # 其中本地原本没有、已入库的
    total: int = 0                                     # 入库后本地总条数

    def __repr__(self) -> str:
        return (f"<FetchResult {self.code} 拉到 {len(self.fetched)} 条，"
                f"新增 {len(self.added)} 条，本地共 {self.total} 条>")


@dataclass
class MarketDay:
    """
    全市场「某一个交易日」的拉取结果。

    skipped = 本地已有这天，一个请求都没发；empty = 联网问了但那天没数据
    （非交易日或还没发布），都不是错误。
    """

    day: str | None = None      # 对应的交易日 "YYYY-MM-DD"
    fetched: int = 0            # 数据源这次返回的行数
    added: int = 0              # 新增入库的行数
    total: int = 0              # 这一交易日在库里的总行数
    skipped: bool = False       # 本地已有，没联网

    @property
    def empty(self) -> bool:
        return not self.skipped and self.fetched == 0

    def __repr__(self) -> str:
        if self.skipped:
            return f"<MarketDay {self.day} 本地已有 {self.total} 行，跳过>"
        if self.empty:
            return f"<MarketDay {self.day} 没有数据>"
        return f"<MarketDay {self.day} 拉到 {self.fetched} 行，新增 {self.added} 行>"


def _day_span(end_day: str, back: int) -> list[str]:
    """从 end_day 往回 back 个自然日，**新的在前**（"YYYY-MM-DD"）。

    注意这只是「候选日期」，不判断哪天是交易日 —— 交易日由交易日历 / 接口返回决定。
    """
    end = datetime.strptime(end_day, "%Y-%m-%d").date()
    return [(end - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(back + 1)]


# ---------------------------------------------------------------------------
# 数据获取器
# ---------------------------------------------------------------------------

class Fetcher:
    """
    按配置挑数据源，把「拉取 → 比对交易日 → 入库」编排起来。

    sources : 名字 → 数据源实例，用来覆盖默认工厂（测试、临时换源用）
    db      : 数据落到哪个库，默认 data/raw/raw.sqlite
    """

    def __init__(self,
                 sources: dict[str, DataSource] | None = None,
                 db=None):
        self.db = db if db is not None else default_store
        self._injected = dict(sources or {})
        self._cache: dict[str, DataSource] = {}

    # -- 数据源 -----------------------------------------------------------
    def _source(self, name: str) -> DataSource:
        """按名字拿数据源。惰性创建 + 同一个 Fetcher 内复用实例
        （复用很关键：DirectSource 的连接池就是这样共享的）。"""
        if name in self._injected:
            return self._injected[name]
        if name not in self._cache:
            factory = SOURCE_FACTORIES.get(name)
            if factory is None:
                raise ValueError(f"未知数据源 {name!r}，可用：{sorted(SOURCE_FACTORIES)}")
            self._cache[name] = factory()
        return self._cache[name]

    def _pick_source(self, code: str, days: int | None) -> DataSource:
        """按「代码类型 + 有没有 days」去配置里挑数据源。"""
        if is_board_code(code):
            key = "board_history" if days else "board_today"
        elif is_stock_code(code):
            key = "stock"
        else:
            raise ValueError(f"无法识别的代码：{code}（板块形如 BK1201，个股形如 300308）")

        name = config.system.get(key)
        if not name:
            raise ValueError(f"config/system.yaml 里没配置 {key}（这个角色该用哪个数据源）")
        return self._source(name)

    # -- 本地读写（全部走数据库）------------------------------------------

    @staticmethod
    def _kind_of(code: str) -> str:
        """代码 -> 'board' | 'stock'（无法识别时抛错）。"""
        if is_board_code(code):
            return "board"
        if is_stock_code(code):
            return "stock"
        raise ValueError(f"无法识别的代码：{code}（板块形如 BK1201，个股形如 300308）")

    def cached_codes(self, kind: str) -> list[str]:
        """
        本地已经有哪些代码。个股是全市场入库的，所以通常返回几千个 —— 这是对的。
        """
        if kind not in ("board", "stock"):
            raise ValueError(f"kind 只能是 'board' 或 'stock'，收到：{kind!r}")
        table, col = (("board_daily", "concept") if kind == "board"
                      else ("stock_daily", "code"))
        return [r[col] for r in self.db.query(
            f"SELECT DISTINCT {col} FROM {table} ORDER BY {col}")]

    def load(self, code: str) -> list[dict]:
        """
        读本地记录（升序）。记录 = 数据库一行 + `name`（名字在字典表里，不在行情表）。
        """
        code = str(code).strip().upper()
        kind = self._kind_of(code)
        if kind == "board":
            rows = self.db.load_board_daily(code)
        else:
            code = code.zfill(6)
            rows = self.db.load_stock_daily(codes=[code])
        name = self.dict_name(kind, code)
        for r in rows:
            r["name"] = name
        return rows

    def history(self, code: str, days: int | None = None) -> list[dict]:
        """本地已存的记录（升序）；days 非空则只取最近 N 个交易日。"""
        rows = self.load(code)
        return rows[-days:] if days else rows

    def dict_name(self, kind: str, code: str) -> str | None:
        """从字典表取名字（板块 board_list / 个股 stock_list）。

        公开的：加自选、报告里都要靠它把「代码」显示成「代码 + 名字」。
        """
        return self.db.name_of(kind, code)

    def _save(self, code: str, rows: list[dict]) -> None:
        """入库。**同一交易日已存在的不覆盖**（这就是「以交易日为准」的落点）。

        顺手把名字写进字典表（数据源带了名字就带上，比单独再查一次省一个请求）。
        """
        if is_board_code(code):
            self.db.save_board_daily(rows)
            names = [{"concept": code, "name": r.get("name")} for r in rows if r.get("name")]
            if names:
                self.db.upsert_board_list(names)
            return

        self.db.save_stock_daily(rows)
        names = [{"code": r["code"], "name": r.get("name")} for r in rows if r.get("name")]
        if names:
            self.db.upsert_stock_list(names)

    def _fill_name(self, kind: str, code: str, rows: list[dict], local: list[dict]) -> None:
        """
        数据源没给名字时，从本地记录 / 字典表补上。补不到就算了，不报错也不编造。
        """
        name = (next((r.get("name") for r in reversed(local) if r.get("name")), None)
                or self.dict_name(kind, code))
        if not name:
            return
        for r in rows:
            if not r.get("name"):
                r["name"] = name

    # -- 本地是不是最新的（0 个请求就能判断）-------------------------------
    def is_up_to_date(self, code: str, ref_day: int | None = None) -> bool:
        """
        本地已经有「最新交易日」的数据了吗？—— **不用联网**。

        判据：这个标的本地最后一天 >= 全市场日线覆盖到的最后一天（交易日钟）。
        好处是**不需要判断「今天是哪天、今天开不开市」**（那种判断在节假日一定算错）。
        代价：还没跑 fetch market 就先跑 fetch watch 的话，它会以为已经最新而跳过（要盘中快照就 --force）。
        """
        kind = self._kind_of(str(code).strip().upper())
        code = str(code).strip().upper() if kind == "board" else str(code).strip().zfill(6)
        ref = self.db.last_trade_day() if ref_day is None else ref_day
        if not ref:
            return False                    # 库里还没有全市场日线 -> 无从判断，老实去拉
        last = self.db.last_day(kind, code)
        return bool(last) and last >= ref

    # -- 对外主接口 -------------------------------------------------------
    def fetch(self, code: str, days: int | None = None) -> FetchResult:
        """联网拉取（days=None 只要最新，days=N 要最近 N 个交易日）并入库。

        拿回来的记录先对齐成**数据库一行的形状**，再按交易日与本地比对，
        本地没有的才写；返回 FetchResult。
        """
        code = str(code).strip().upper()
        kind = self._kind_of(code)                  # 顺带校验代码是否认识
        source = self._pick_source(code, days)

        if kind == "board":
            raw = source.fetch_board(code, days=days)
        else:
            raw = source.fetch_stock(code, days=days)
        if not raw:
            raise LookupError(f"{code} 没拉到任何记录（数据源 {source.name}）")

        # 数据源给什么字段都行，这里对齐成库里的列（单位原样，不做缩放）
        rows = [self._to_row(kind, r, code, source.name) for r in raw]

        local = self.load(code)
        self._fill_name(kind, code, rows, local)

        have = {r["trade_date"] for r in local}
        new = [r for r in rows if r["trade_date"] and r["trade_date"] not in have]
        if new:
            self._save(code, new)
        return FetchResult(code=code, fetched=rows, added=new,
                           total=len(have) + len(new))

    # -- 数据源记录 -> 数据库一行（唯一的翻译层）---------------------------
    @staticmethod
    def _to_row(kind: str, rec: dict, code: str, source: str) -> dict:
        """
        把数据源给的一条记录对齐成库里的列 —— **这里是唯一的翻译层**。

        数据源按「对外字段名」给数据（见 base.py），两张表的列名却不一样，所以要翻：
        board 是 concept / price / change_pct，stock 是 code / close / pct_chg。
        ⚠️ 漏翻一个字段不会报错，只会让那一列静默变成 NULL，所以必须有测试盯着。
        """
        cols = _TABLE_COLS[kind]
        row = {c: rec.get(c) for c in cols}
        row["trade_date"] = date_to_int(rec.get("date") or rec.get("trade_date"))
        row["source"] = rec.get("source") or source    # 这条是哪来的，必须记下来
        if kind == "board":
            row["concept"] = code
        else:
            row["code"] = code
            row["close"] = rec.get("close", rec.get("price"))            # price -> close
            row["pct_chg"] = rec.get("pct_chg", rec.get("change_pct"))   # change_pct -> pct_chg
        row["name"] = rec.get("name")        # 不是行情表的列，留着给 _save 写字典 / 展示用
        return row

    # -- 全市场日线（按交易日）--------------------------------------------
    #
    # 全市场天生就是「一天一张横截面」，所以直接进 stock_daily 表：
    # 三千万行也就 2.6 GB，一张表比三千万个文件好伺候得多。

    def _market_source(self) -> DataSource:
        """全市场日线用哪个数据源：配了 market 就用它，没配就复用 stock。"""
        name = config.system.get("market") or config.system.get("stock")
        if not name:
            raise ValueError("config/system.yaml 里既没配 market 也没配 stock")
        src = self._source(name)
        if not hasattr(src, "fetch_market_daily"):
            raise ValueError(
                f"数据源 {name!r} 不支持全市场日线："
                f"market 这个角色需要能 fetch_market_daily 的数据源")
        return src

    def market(self, trade_date: str | None = None, force: bool = False) -> MarketDay:
        """
        拉**全市场某一天**的日线并入库（一天 1 个请求，约 5500 只）。

        缓存优先：本地已经有这天、又没 force，就一个请求都不发。
        force = **先拉、拉到了才删旧的再写** —— 写入是 INSERT OR IGNORE，不先删就盖不掉脏行；
        反过来先删后拉，网络一失败就把好数据删了。
        """
        src = self._market_source()

        if trade_date:
            day = to_date(trade_date)
            if not day:
                raise ValueError(f"看不懂的日期：{trade_date!r}（要 YYYYMMDD 或 YYYY-MM-DD）")
            if not force:
                n = self.db.stock_day_counts().get(date_to_int(day), 0)
                if n:
                    return MarketDay(day=day, total=n, skipped=True)   # 没联网
            rows = src.fetch_market_on(day)
        else:
            day, rows = src.fetch_market_daily()

        if not rows:
            return MarketDay(day=day)          # 那天没数据，本地一行没动

        if force:
            self.db.delete_stock_days([day])
        added = self.db.save_stock_daily(rows)
        return MarketDay(day=day, fetched=len(rows), added=added,
                         total=self.db.stock_day_counts().get(date_to_int(day), 0))

    def market_plan(self, days: int, end: str | None = None) -> list[dict]:
        """
        最近 days 个交易日的**补数计划**：[{"day", "rows"}, …]，新的在前。

        rows = 本地已有多少行（0 = 要联网拉）。「哪天是交易日」优先问数据源的交易日历，
        问不到就退回按自然日多排一些 —— 没数据的那天会被跳过，不影响正确性。
        """
        days = int(days)
        if days < 1:
            raise ValueError(f"days 要 >= 1，收到：{days}")

        src = self._market_source()
        end_day = to_date(end) if end else today_str()
        if not end_day:
            raise ValueError(f"看不懂的日期：{end!r}（要 YYYYMMDD 或 YYYY-MM-DD）")

        # 交易日历只覆盖「最近 days 个交易日」所需的自然日（N 个交易日约 1.4N 天，留一倍余量）
        cal = src.trade_days(_day_span(end_day, days * 2 + 15)[-1], end_day)
        if cal:
            picked = cal[-days:]
        else:
            picked = _day_span(end_day, int(days * 1.5) + 7)

        have = {date_to_str(d): n for d, n in self.db.stock_day_counts().items()}
        return [{"day": d, "rows": have.get(d, 0)} for d in reversed(picked)]
