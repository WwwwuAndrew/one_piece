#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
board_tree.py —— 板块层级（一级 / 二级）。

    t = BoardTree()
    res = t.build()      # 从乐咕拉分类，算出这棵树（code 就是申万代码 801080.SI）
    t.save(res)          # 写进 board_tree 表（整块快照）

层级直接来自申万分类（乐咕乐股，见 fetch_legu.py）：
一级 31 个、二级 131 个。乐咕的二级表里已经带了「上级行业」名称，
所以这里只做一件小事：把「上级名称」映射回「上级代码」。
不再需要旧版那套「申万名字 ↔ 东财 BK 代码」的名称匹配（东财已移除）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .store import store as default_store


@dataclass
class Node:
    """树上的一个板块。"""

    code: str                       # 801080.SI
    name: str                       # 申万行业名称（可能带 Ⅱ/Ⅲ 后缀）
    level: int                      # 1 / 2
    parent: str | None = None       # 上级板块代码；一级为 None

    def as_row(self) -> dict:
        return {"concept": self.code, "name": self.name,
                "level": self.level, "parent": self.parent}


@dataclass
class BuildResult:
    """一次构建的结果。"""

    nodes: list[Node] = field(default_factory=list)

    @property
    def counts(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for n in self.nodes:
            out[n.level] = out.get(n.level, 0) + 1
        return out


class BoardTree:
    """板块层级：构建 / 保存 / 读取。db 默认 data/raw/raw.sqlite。"""

    def __init__(self, db=None):
        self.db = db if db is not None else default_store

    # -- 构建 -------------------------------------------------------------
    def build(self, levels: dict[int, list[dict]] | None = None) -> BuildResult:
        """算出整棵树。

        levels : {1: [{"code", "name"}], 2: [{"code", "name", "parent_name"}]}；
                 不给就联网拉乐咕（2 个请求）。
        """
        levels = levels if levels is not None else self._fetch_levels()

        # 二级的 parent_name 是「名称」，映射回一级 code 作为 parent
        name_to_code = {r["name"]: r["code"] for r in levels.get(1, [])}

        nodes: list[Node] = []
        for r in levels.get(1, []):
            nodes.append(Node(code=r["code"], name=r["name"], level=1))
        for r in levels.get(2, []):
            parent = name_to_code.get(r.get("parent_name") or "")
            nodes.append(Node(code=r["code"], name=r["name"], level=2,
                              parent=parent))

        res = BuildResult(nodes=sorted(nodes, key=lambda n: (n.level, n.code)))
        return res

    @staticmethod
    def _fetch_levels() -> dict[int, list[dict]]:
        from .fetch_legu import LeguSource
        return LeguSource().fetch_levels()

    # -- 保存 / 读取 ------------------------------------------------------
    def save(self, res: BuildResult, updated_at=None) -> int:
        return self.db.save_board_tree([n.as_row() for n in res.nodes],
                                       updated_at=updated_at)

    def load(self) -> list[Node]:
        return [Node(code=r["concept"], name=r["name"], level=r["level"],
                     parent=r["parent"]) for r in self.db.load_board_tree()]
