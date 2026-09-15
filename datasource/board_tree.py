#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
board_tree.py —— 板块层级（一级 / 二级）。

    t = BoardTree()
    t.build()   # 拉板块字典 + 申万分类，算出这棵树（联网：东财 5 + 乐咕 3 个请求）
    t.save()    # 写进库
    t.load()    # 从库读（不联网）

为什么绕这一道：东财的 clist 接口**不给层级** —— 它把一级/二级/三级共约 496 个行业板块
混在一条扁平列表里返回，没有任何字段表示上下级（实测 f207/f208/f209/f222 是领涨股名/
代码/标记/涨跌幅，跟层级无关）。但那 496 个名字就是**申万行业分类**：

    · 申万 2021 版 31 个一级行业，在东财列表里**同名命中 31/31**；
    · 82 个板块带 Ⅱ/Ⅲ 后缀（白酒Ⅱ、证券Ⅱ…）—— 正是申万区分同名层级的记号。

所以层级用**申万公开的分类表**拿（akshare 的 sw_index_first/second_info()，
源站是乐咕乐股，不是东财）。我们只要一级 + 二级（三级 337 个太细，不要）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .store import store as default_store

# ---------------------------------------------------------------------------
# 手工补丁：自动匹配对不上的那几个（各附原因，不猜）
# ---------------------------------------------------------------------------

# 自动匹配对不上的，手工补 —— 只有一处（二级层面）：
#   申万把「银行」直接拆成 4 个二级（国有大型银行Ⅱ / 股份制银行Ⅱ / 城商行Ⅱ / 农商行Ⅱ），
#   **东财没有这一层**，它用的是「银行Ⅱ」这个二级。所以：
#     · 东财的 BK0475 银行Ⅱ 手工挂到 银行 下面；
#     · 申万那 4 个二级在东财找不到对应，忽略。
EXTRA_NODES: dict[str, tuple[int, str]] = {
    "BK0475": (2, "BK1283"),      # 银行Ⅱ -> 银行
}
BANK_L2_NAMES = ("国有大型银行Ⅱ", "股份制银行Ⅱ", "城商行Ⅱ", "农商行Ⅱ")


@dataclass
class Node:
    """树上的一个板块。"""

    bk: str
    name: str
    level: int
    parent: str | None = None
    sw_code: str | None = None
    note: str | None = None
    _parent_name: str | None = field(default=None, repr=False)   # 上级的申万名字（临时）

    def as_row(self) -> dict:
        return {"concept": self.bk, "name": self.name, "level": self.level,
                "parent": self.parent, "sw_code": self.sw_code}


@dataclass
class BuildResult:
    """一次构建的结果，方便报告「匹配上多少、手工补了几个」。"""

    nodes: list[Node] = field(default_factory=list)
    unmatched_sw: list[dict] = field(default_factory=list)   # 申万有、东财没对上
    orphan_bk: list[str] = field(default_factory=list)       # 东财有、没进树
    patched: int = 0
    unmatched_bank: bool = False                             # 银行那 4 个二级对不上（正常）

    @property
    def counts(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for n in self.nodes:
            out[n.level] = out.get(n.level, 0) + 1
        return out


class BoardTree:
    """板块层级：构建 / 保存 / 查询。

    db : 落到哪个库，默认 data/raw/raw.sqlite
    """

    def __init__(self, db=None, fetcher=None):
        self.db = db if db is not None else default_store
        self._fetcher = fetcher

    # -- 构建 -------------------------------------------------------------
    def build(self, boards: list[dict] | None = None,
              sw: dict[int, "object"] | None = None) -> BuildResult:
        """
        算出整棵树。

        boards : 东财全板块清单 [{f12, f14}, …]；不给就联网拉（本地够 490 个则跳过）
        sw     : 申万分类 {1: df, 2: df}；不给就联网拉（3 个请求）
        """
        boards = boards if boards is not None else self._fetch_boards()
        bk_by_name = {str(b.get("f14")).strip(): str(b.get("f12")) for b in boards}
        bk_by_name_rev = {v: k for k, v in bk_by_name.items()}
        if sw is None:
            sw = self._fetch_sw()

        res = BuildResult()
        nodes: dict[str, Node] = {}

        # ★ 只要一级和二级：三级板块有 337 个，粒度太细（33~80 只成分股），
        #   而且用户明确说不需要 —— 所以这里直接不看三级。
        for level in (1, 2):
            df = sw.get(level)
            if df is None:
                continue
            n_col, c_col, p_col = _cols(df)
            if not n_col:
                continue
            for _, r in df.iterrows():
                name = str(r[n_col]).strip()
                parent_name = str(r[p_col]).strip() if p_col else None
                sw_code = str(r[c_col]).strip() if c_col else None

                bk = bk_by_name.get(name)
                if not bk:
                    res.unmatched_sw.append({"level": level, "name": name,
                                             "parent": parent_name})
                    continue
                nodes[bk] = Node(bk=bk, name=bk_by_name_rev.get(bk) or name,
                                 level=level, sw_code=sw_code,
                                 _parent_name=parent_name)

        # 上级：名字 -> BK 代码
        by_name = {n.name: n.bk for n in nodes.values()}
        for n in nodes.values():
            pn = n._parent_name
            if pn:
                n.parent = by_name.get(pn)
                if n.parent is None and pn in BANK_L2_NAMES:
                    res.unmatched_bank = True          # 申万那 4 个银行二级，东财没有

        # 东财多出来的板块：手工补
        for bk, (level, parent) in EXTRA_NODES.items():
            if bk in nodes:
                continue
            name = bk_by_name_rev.get(bk)
            if not name:
                continue
            nodes[bk] = Node(bk=bk, name=name, level=level, parent=parent,
                             note="东财有、申万没有：手工补的")
            res.patched += 1
        res.orphan_bk = sorted(set(bk_by_name.values()) - set(nodes))
        res.nodes = sorted(nodes.values(), key=lambda n: (n.level, n.bk))
        return res

    def _fetch_boards(self) -> list[dict]:
        """全板块清单。本地已经有 490+ 个就不联网了（少 5 个请求）。"""
        have = len(self.db.load_board_list())
        if have >= 490:
            rows = self.db.load_board_list()
            print(f"   本地已有 {have} 个板块，直接用（不发请求）")
            return [{"f12": r["concept"], "f14": r["name"]} for r in rows]

        from .fetch_direct import DirectSource
        src = self._fetcher or DirectSource()
        print(f"   拉全板块字典…（本地只有 {have} 个，需要联网 5 个请求）")
        rows = src.fetch_industry_boards(fields="f12,f14")
        out = [{"f12": str(r.get("f12")), "f14": str(r.get("f14"))} for r in rows]
        # 顺手把板块字典补全（名字 + 代码，没有成员同步时间）
        self.db.upsert_board_list([{"concept": r["f12"], "name": r["f14"]} for r in out])
        return out

    @staticmethod
    def _fetch_sw() -> dict:
        """申万一级/二级/三级分类（3 个请求，源站是乐咕乐股，不是东财）。"""
        import akshare as ak
        out = {}
        for level, fn in ((1, "sw_index_first_info"), (2, "sw_index_second_info"),
                          (3, "sw_index_third_info")):
            f = getattr(ak, fn, None)
            if f is None:
                print(f"   ⚠️ akshare 没有 {fn}()，跳过 {level} 级")
                continue
            print(f"   拉申万 {level} 级分类 … {fn}()")
            try:
                out[level] = f()
            except Exception as exc:
                print(f"   ❌ {fn}() 失败：{type(exc).__name__}: {exc}")
        return out

    # -- 保存 / 读取 ------------------------------------------------------
    def save(self, res: BuildResult, updated_at=None) -> int:
        return self.db.save_board_tree([n.as_row() for n in res.nodes],
                                       updated_at=updated_at)

    def load(self) -> list[Node]:
        return [Node(bk=r["concept"], name=r["name"], level=r["level"],
                     parent=r["parent"], sw_code=r["sw_code"]) for r in
                self.db.load_board_tree()]

    def children(self, bk: str, level: int | None = None) -> list[Node]:
        return [Node(bk=r["concept"], name=r["name"], level=r["level"],
                     parent=r["parent"], sw_code=r["sw_code"])
                for r in self.db.child_boards(bk, level=level)]


# ---------------------------------------------------------------------------
# 清字典：把不属于一级/二级的板块条目去掉
# ---------------------------------------------------------------------------

def prune_dict(db, keep: set[str], protected: set[str] | None = None) -> list[str]:
    """
    从板块字典里删掉**不在 keep 里**的条目，返回删掉的代码。

    ⚠️ 只删字典那一行（board_list），**绝不碰行情和成分股**。
       protected 里的一律保留 —— 那是「你抓过数据的板块」，就算不在层级里
       （比如手动拉过的某个三级），也不该被字典清理顺手抹掉。
    """
    protected = protected or set()
    rows = db.load_board_list()
    drop = [r["concept"] for r in rows
            if r["concept"] not in keep and r["concept"] not in protected]
    if drop:
        db.delete_board_list(drop)
    return drop


def used_boards(db) -> set[str]:
    """本地有**数据**的板块（有行情或成分股的）—— 这些不能当"多余"清掉。"""
    rows = db.query("SELECT concept FROM board_daily "
                    "UNION SELECT concept FROM concept_member")
    return {r["concept"] for r in rows}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _cols(df):
    """申万表各版本列名不一样，按关键词认「名称 / 代码 / 上级」。"""
    def find(*keys):
        for c in df.columns:
            if any(k in str(c) for k in keys):
                return c
        return None
    return find("行业名称", "名称"), find("行业代码", "代码"), find("上级", "父", "所属")
