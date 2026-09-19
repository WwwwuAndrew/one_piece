#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
base.py —— 命令行的通用件。

    Target      一个标的（代码 + 类型），代码怎么规范化只在这里定义
    Context     一次命令执行的依赖装配点：库、自选、输出
    Command     子命令基类，run() 返回退出码

依赖全部由 Context 装配，命令里不出现 Fetcher() / store 这类全局。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from config.config import config

from datasource.base import is_board_code, is_stock_code
from datasource.fetch import Fetcher
from datasource.store import local, store

from .console import Console

KINDS = ("board", "stock")


@dataclass
class Target:
    """一个标的：代码 + 类型。代码规范化（板块转大写、个股补零）只在这里做。

    板块代码是申万行业代码 + .SI 后缀，形如 801080.SI；个股是 6 位数字，形如 300308。
    """

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
        text = str(raw).strip().upper()
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


class Context:
    """一次命令执行期间共享的依赖。所有 new 都发生在这里。"""

    def __init__(self, *, db=None, watch=None, fetcher=None, console=None):
        self.db = db if db is not None else store
        self.watch = watch if watch is not None else local
        self.console = console if console is not None else Console(self.db)
        self.fetcher = fetcher if fetcher is not None else Fetcher(db=self.db)

    @property
    def interval(self) -> float:
        """两个请求之间等几秒（config/system.yaml 的 fetch_interval）。"""
        return float(config.system.get("fetch_interval", 1.5) or 0)

    @property
    def legu_interval(self) -> float:
        """乐咕拉成分股时的间隔（乐咕限流严，要比 tushare 慢）。"""
        return float(config.system.get("legu_interval", 10.0) or 0)

    def watched(self, kind: str | None = None) -> list[str]:
        """当前自选里的代码（kind 为空 = 板块 + 个股都要）。"""
        return [w["code"] for w in self.watch.load_watchlist(kind)]


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
