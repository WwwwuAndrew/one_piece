#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
base.py —— 命令行的通用件。

    Target      一个标的（代码 + 类型），代码怎么规范化只在这里定义
    Freshness   「本地是不是最新」的判据（交易日钟 / --force / --days）
    Context     一次命令执行的依赖装配点：库、自选、数据源、输出
    Command     子命令基类，run() 返回退出码

依赖全部由 Context 装配，命令里不出现 Fetcher() / DirectSource() / store 这类全局。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from config.config import config

from datasource.boards import Boards
from datasource.fetch import Fetcher, is_board_code, is_stock_code
from datasource.fetch_direct import DirectSource
from datasource.fetch_tushare import TushareSource
from datasource.stock import Stocks
from datasource.store import local, store

from .console import Console

KINDS = ("board", "stock")

# 出现这些字样，基本就是「被对方拒了」，不是「代码写错了」
BLOCKED_HINTS = ("remotedisconnected", "connection aborted", "connection reset",
                 "connectionerror", "max retries", "timed out", "403", "429", "502", "503")


def looks_blocked(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(h in text for h in BLOCKED_HINTS)


@dataclass
class Target:
    """一个标的：代码 + 类型。代码规范化（板块转大写、个股补零）只在这里做。"""

    code: str
    kind: str

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind 只能是 {KINDS}，收到：{self.kind!r}")
        text = str(self.code).strip()
        self.code = text.upper() if self.kind == "board" else text.zfill(6)

    @classmethod
    def parse(cls, raw) -> "Target | None":
        """从用户输入认出一个标的；认不出来返回 None。"""
        text = str(raw).strip()
        if is_board_code(text):
            return cls(text, "board")
        if is_stock_code(text):
            return cls(text, "stock")
        return None

    @property
    def is_board(self) -> bool:
        return self.kind == "board"

    def __str__(self) -> str:
        return self.code


class Freshness:
    """
    「本地是不是最新」的判据：本地最后一天 >= 交易日钟。

    交易日钟 = 全市场日线里最新的那一天，不猜「今天几号」（节假日会猜错）。
    --force 或 --days（你要的是历史）直接绕过跳过逻辑。
    """

    def __init__(self, fetcher: Fetcher, force: bool = False, days: int | None = None):
        self._fetcher = fetcher
        self.bypass = bool(force) or days is not None
        self.ref = None if self.bypass else fetcher.db.last_trade_day()

    def split(self, codes: list[str]) -> tuple[list[str], list[str]]:
        """分成 (要拉的, 本地已经是最新的)。交易日钟一批只查一次。"""
        if self.bypass:
            return list(codes), []
        todo: list[str] = []
        fresh: list[str] = []
        for c in codes:
            (fresh if self._fetcher.is_up_to_date(c, ref_day=self.ref) else todo).append(c)
        return todo, fresh


class Context:
    """一次命令执行期间共享的依赖。

    所有 new 都发生在这里 —— 要换库、换数据源、换输出，只动这一处。
    """

    def __init__(self, *, db=None, watch=None, fetcher=None, boards=None, stocks=None,
                 direct=None, tushare=None, console=None):
        self.db = db if db is not None else store
        self.watch = watch if watch is not None else local
        self.console = console if console is not None else Console(self.db)
        self.fetcher = fetcher if fetcher is not None else Fetcher(db=self.db)
        self.boards = boards if boards is not None else Boards(self.fetcher)
        self.stocks = stocks if stocks is not None else Stocks(self.fetcher)
        self._direct = direct
        self._tushare = tushare

    @property
    def direct(self) -> DirectSource:
        """东财直连源。用到才建（不联网的命令不该构造网络对象）。"""
        if self._direct is None:
            self._direct = DirectSource()
        return self._direct

    @property
    def tushare(self) -> TushareSource:
        if self._tushare is None:
            self._tushare = TushareSource()
        return self._tushare

    @property
    def interval(self) -> float:
        """两个请求之间等几秒（config/system.yaml 的 fetch_interval）。"""
        return float(config.system.get("fetch_interval", 1.5) or 0)

    def watched(self, kind: str | None = None) -> list[str]:
        """当前自选里的代码（kind 为空 = 板块 + 个股都要）。"""
        return [w["code"] for w in self.watch.load_watchlist(kind)]

    def fetch_one(self, code: str, days: int | None = None):
        """按代码类型分派到 Boards / Stocks。"""
        box = self.boards if is_board_code(code) else self.stocks
        return box.fetch(code, days=days)


class Command(ABC):
    """一个子命令。run() 返回进程退出码（0 = 成功）。"""

    name = ""

    def __init__(self, ctx: Context):
        self.ctx = ctx

    @property
    def console(self) -> Console:
        return self.ctx.console

    @abstractmethod
    def run(self) -> int:
        """执行，返回退出码。"""
