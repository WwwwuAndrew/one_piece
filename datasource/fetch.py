#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch.py —— 数据获取主接口：按配置选数据源 + 联网拉取 + 按交易日落盘。

    from datasource.fetch import Fetcher
    f = Fetcher()
    f.fetch("BK1201")             -> FetchResult  板块当天快照
    f.fetch("BK1201", days=15)    -> FetchResult  板块最近 15 个交易日
    f.fetch("300308")             -> FetchResult  个股最新一天
    f.fetch("300308", days=15)    -> FetchResult  个股最近 15 个交易日
    f.history("BK1201", days=15)  -> list[dict]   读本地（不联网）
    f.load("BK1201")              -> list[dict]   读本地全部
    f.cached_codes("board")       -> list[str]    本地已缓存了哪些板块

数据源路由（在 config/system.yaml 里配）：
    board_today    板块「当天」用哪个数据源   （默认 direct）
    board_history  板块「历史」用哪个数据源   （默认 akshare，数据同样来自东财）
    stock          个股（当天 + 历史）        （默认 tushare）
    可选名字见下面的 SOURCE_FACTORIES：direct / akshare / tushare

fetch 的流程：
    1. 按上面的配置挑一个数据源；
    2. 联网拉取（days=None 只要最新一条，days=N 要最近 N 个交易日）；
    3. 拿回来的每条记录按交易日与本地比对；
    4. 本地没有的才追加落盘（同一交易日不覆盖）。

存什么永远由数据源返回的交易日决定，**不做任何「今天是哪天」的判断**，
所以周末、法定节假日都不会算错。

落盘格式：
    data/raw/board/BK1201.jsonl   板块，每个代码一个文件
    data/raw/stock/300308.jsonl   个股，每个代码一个文件
    一行 = 一个交易日的一条快照（JSON），追加式；读单只标的只读它那一个文件。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from config.config import config

from .base import DataSource, FIELDS, format_record, is_board_code, is_stock_code
from .fetch_akshare import AkshareSource
from .fetch_direct import DirectSource
from .fetch_tushare import TushareSource

DEFAULT_RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

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
    added: list[dict] = field(default_factory=list)    # 其中本地原本没有、已落盘的
    total: int = 0                                     # 落盘后本地总条数

    def __repr__(self) -> str:
        return (f"<FetchResult {self.code} 拉到 {len(self.fetched)} 条，"
                f"新增 {len(self.added)} 条，本地共 {self.total} 条>")


# ---------------------------------------------------------------------------
# 数据获取器
# ---------------------------------------------------------------------------

class Fetcher:
    """按配置挑数据源，把「拉取 -> 比对交易日 -> 落盘」编排起来。

    sources : 名字 -> 数据源实例，用来覆盖默认工厂（测试、临时换源用）
    raw_dir : 落盘根目录，默认 data/raw，测试时可以指向临时目录
    """

    def __init__(self,
                 sources: dict[str, DataSource] | None = None,
                 raw_dir: str | Path | None = None):
        self.raw_dir = Path(raw_dir) if raw_dir is not None else DEFAULT_RAW_DIR
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

    # -- 本地读写 ---------------------------------------------------------
    def _sub_dir(self, kind: str) -> Path:
        return self.raw_dir / ("board" if kind == "board" else "stock")

    def _path(self, code: str) -> Path:
        """代码 -> 本地文件路径（含目录）。无法识别时抛错。"""
        code = str(code).strip().upper()
        if is_board_code(code):
            return self._sub_dir("board") / f"{code}.jsonl"
        if is_stock_code(code):
            return self._sub_dir("stock") / f"{code}.jsonl"
        raise ValueError(f"无法识别的代码：{code}（板块形如 BK1201，个股形如 300308）")

    def cached_codes(self, kind: str) -> list[str]:
        """本地已经缓存了哪些代码（看有哪些 jsonl 文件）。kind: 'board' | 'stock'。"""
        if kind not in ("board", "stock"):
            raise ValueError(f"kind 只能是 'board' 或 'stock'，收到：{kind!r}")
        d = self._sub_dir(kind)
        if not d.exists():
            return []
        return sorted(p.stem for p in d.glob("*.jsonl"))

    def load(self, code: str) -> list[dict]:
        """读本地文件全部记录（按日期升序）。文件不存在返回空列表。"""
        path = self._path(code)
        if not path.exists():
            return []
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
        records.sort(key=lambda r: r.get("date", ""))
        return records

    def history(self, code: str, days: int | None = None) -> list[dict]:
        """本地已存的记录（升序）；days 非空则只取最近 N 个交易日。"""
        records = self.load(code)
        if days:
            records = records[-days:]
        return records

    def _save(self, code: str, records: list[dict]) -> None:
        """以交易日为准去重（同一交易日保留最先出现的一条、不覆盖），升序写回 jsonl。

        写入时先写临时文件再原子替换，避免中途崩溃损坏原文件。
        """
        path = self._path(code)
        path.parent.mkdir(parents=True, exist_ok=True)

        dedup: dict[str, dict] = {}
        for r in records:
            d = r.get("date")
            if d and d not in dedup:
                dedup[d] = r  # 同一交易日首次出现才保留，不覆盖
        ordered = [dedup[d] for d in sorted(dedup)]

        tmp = path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            for r in ordered:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, path)

    @staticmethod
    def _fill_name(records: list[dict], local: list[dict]) -> None:
        """数据源没给名字时，从本地已有记录里补上（就地改 records）。

        最典型的是 akshare 的板块历史：那个接口根本不返回板块名称。
        补不到就算了，不报错、也不编造。
        """
        name = next((r.get("name") for r in reversed(local) if r.get("name")), None)
        if not name:
            return
        for r in records:
            if not r.get("name"):
                r["name"] = name

    # -- 对外主接口 -------------------------------------------------------
    def fetch(self, code: str, days: int | None = None) -> FetchResult:
        """联网拉取（days=None 只要最新，days=N 要最近 N 个交易日）并落盘。

        拿回来的记录会先**统一格式化成本地格式**（见 base.format_record），
        再按交易日与本地比对，本地没有的才追加；返回 FetchResult。
        """
        code = str(code).strip().upper()
        source = self._pick_source(code, days)      # 顺带校验代码是否认识
        kind = "board" if is_board_code(code) else "stock"

        if kind == "board":
            raw = source.fetch_board(code, days=days)
        else:
            raw = source.fetch_stock(code, days=days)
        if not raw:
            raise LookupError(f"{code} 没拉到任何记录（数据源 {source.name}）")

        # 不管数据源给的是什么排列、什么字段，先统一成本地格式再往下走
        records = [format_record(kind, r, source=source.name) for r in raw]

        local = self.load(code)
        self._fill_name(records, local)

        have = {r.get("date") for r in local}
        added = [r for r in records if r.get("date") and r["date"] not in have]
        if added:
            self._save(code, local + added)  # 本地没有的交易日才追加落盘

        return FetchResult(code=code, fetched=records, added=added,
                           total=len(local) + len(added))

    # -- 旧数据迁移 -------------------------------------------------------
    def rewrite_local(self, kind: str | None = None) -> dict[str, int]:
        """把本地已存的旧格式记录，重写成当前统一落盘格式。

        判定「旧格式」的依据：**没有 source 字段**（那时成交额还是「元」）。
        已经有 source 的记录视为已迁移，只重排字段、**不再做单位换算**，
        所以本方法是可重复执行的，不会把亿/万又当成元再除一次。

        旧记录一律标成 source="direct" —— 在引入多数据源之前，只有 DirectSource
        这一个数据源，所以这是准确的。

        返回 {kind: 重写的文件数}。
        """
        kinds = [kind] if kind else ["board", "stock"]
        done: dict[str, int] = {}
        for k in kinds:
            if k not in FIELDS:
                raise ValueError(f"kind 只能是 'board' 或 'stock'，收到：{k!r}")
            n = 0
            for code in self.cached_codes(k):
                fixed = []
                for r in self.load(code):
                    if "source" in r:      # 已是新格式：只重排，不再换算
                        fixed.append(format_record(k, r, source=r["source"],
                                                   already_formatted=True))
                    else:                  # 旧格式：需要把「元」换算成 亿/万
                        fixed.append(format_record(k, r, source="direct"))
                self._save(code, fixed)
                n += 1
            done[k] = n
        return done
