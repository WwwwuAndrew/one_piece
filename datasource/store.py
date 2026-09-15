#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
store.py —— SQLite 存取层。按「**能不能重建**」分两个库（判据不是体积）：

    data/raw/raw.sqlite      原始数据，全都能重新下载，删了不可惜
        stock_daily      全市场个股日线（一天约 5550 行）  amount=元 volume=股
        stock_list       个股字典（代码 → 名字）
        board_daily      板块日线（一级 + 二级）
        board_list       板块字典（代码 → 名称 + 成员同步时间）
        board_tree       板块层级（一级/二级 + 上级），来自申万分类
        concept_member   板块 → 成员（**快照**，没有日期列）

    data/local.sqlite        **既下不到、也算不出**，删了就真没了，所以单独一个文件
        watchlist        自选（板块 + 个股共用一张表）

跨库**不需要 JOIN** —— 自选读出来就是个 Python 列表，直接 `WHERE code IN (...)` 用。

⚠️ 单位：库里一律存**原始单位**（amount = 元，volume = 股）。
   Layer A 只采不算，换算成 亿/万 是展示层的事。

⚠️ 库路径可用环境变量覆盖：`ONEPIECE_RAW_DB` / `ONEPIECE_LOCAL_DB`。

自检：python3 -m datasource.store      # 用临时库跑一遍，不碰 data/
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from .base import BEIJING, to_date

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ★ 库的路径可以被环境变量改掉。默认当然是项目里的 data/，
#   但这给了一个**结构性的保险**：测试（或想拿副本试玩的场景）只要在 import 之前设好
#       ONEPIECE_RAW_DB=/tmp/x/raw.sqlite   ONEPIECE_LOCAL_DB=/tmp/x/local.sqlite
#   那么**任何**代码路径（包括忘了注入 db 的那些）都不可能写到真实库上去 ——
#   靠「记得给每个入口注入临时库」是防不住的，我在这上面栽过。
DEFAULT_RAW_DB = Path(os.environ.get("ONEPIECE_RAW_DB")
                      or (DATA_DIR / "raw" / "raw.sqlite"))
DEFAULT_LOCAL_DB = Path(os.environ.get("ONEPIECE_LOCAL_DB")
                        or (DATA_DIR / "local.sqlite"))

# ---------------------------------------------------------------------------
# 建表语句
# ---------------------------------------------------------------------------
# 日期一律存 INTEGER YYYYMMDD：比文本更小更快，排序天然就是时间顺序。
# 主键用 (xxx, trade_date) + WITHOUT ROWID：数据按第一列聚簇，单标的取历史很快；
# 再补一个 trade_date 索引，横截面查询也快。

RAW_SCHEMA = """
-- 全市场个股日线：一天约 5550 行
CREATE TABLE IF NOT EXISTS stock_daily (
    code       TEXT    NOT NULL,
    trade_date INTEGER NOT NULL,        -- YYYYMMDD
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    pre_close  REAL,
    change     REAL,
    pct_chg    REAL,
    volume     REAL,                    -- 股
    amount     REAL,                    -- 元
    PRIMARY KEY (code, trade_date)
) WITHOUT ROWID;

-- 横截面查询用：某一天全部股票 / 某个日期区间
CREATE INDEX IF NOT EXISTS idx_stock_daily_date ON stock_daily(trade_date);

-- 个股字典：代码 → 名字。
-- ⚠️ 名字**不放进 stock_daily**：它是「字典」（很少变），不是「行情」（每天一根）。
--    混在一起的话，每天 5500 行都要重复存一遍同样的名字，而且改个名字要改全部历史。
CREATE TABLE IF NOT EXISTS stock_list (
    code       TEXT PRIMARY KEY,
    name       TEXT,
    updated_at INTEGER            -- 上次同步时间 YYYYMMDD
) WITHOUT ROWID;

-- 东财板块日线
CREATE TABLE IF NOT EXISTS board_daily (
    concept       TEXT    NOT NULL,     -- BK1201
    trade_date    INTEGER NOT NULL,     -- YYYYMMDD
    source        TEXT,                 -- direct / akshare
    price         REAL,                 -- 板块点位
    change_pct    REAL,
    change        REAL,
    amount        REAL,                 -- 元
    volume        REAL,                 -- 股
    up            INTEGER,              -- 上涨家数
    down          INTEGER,              -- 下跌家数
    flat          INTEGER,              -- 平盘家数
    open          REAL,
    high          REAL,
    low           REAL,
    pre_close     REAL,
    amplitude_pct REAL,
    turnover_pct  REAL,
    total_mv      REAL,
    float_mv      REAL,
    pe            REAL,
    volume_ratio  REAL,
    pb            REAL,
    chg_60d       REAL,
    chg_ytd       REAL,
    quote_time    TEXT,
    PRIMARY KEY (concept, trade_date)
) WITHOUT ROWID;

-- 板块排名（全部板块 × 某一天）
CREATE INDEX IF NOT EXISTS idx_board_daily_date ON board_daily(trade_date);

-- 板块字典：纯字典（名字 + 成员同步时间）。自选不在这里，在 local.sqlite
CREATE TABLE IF NOT EXISTS board_list (
    concept           TEXT PRIMARY KEY,
    name              TEXT,
    member_updated_at INTEGER            -- 成员上次同步时间 YYYYMMDD
) WITHOUT ROWID;

-- 板块层级：东财的行业板块其实用的是**申万分类**（一级/二级/三级混在一张扁平表里），
-- 层级从申万公开的分类表拿（见 datasource/board_tree.py），这里只存结果。
CREATE TABLE IF NOT EXISTS board_tree (
    concept     TEXT PRIMARY KEY,     -- BK1201
    name        TEXT,                 -- 东财用的名字（可能带 Ⅱ/Ⅲ 后缀）
    level       INTEGER,              -- 1 / 2 / 3
    parent      TEXT,                 -- 上级板块代码；一级为 NULL
    sw_code     TEXT,                 -- 申万行业代码（801080.SI），对不上就是 NULL
    updated_at  INTEGER
) WITHOUT ROWID;

-- 按层级筛（「只看二级板块」）/ 按上级找子板块
CREATE INDEX IF NOT EXISTS idx_board_tree_parent ON board_tree(parent);

-- 板块成员：快照式（只有「现在有哪些」，没有 start/end 日期）
CREATE TABLE IF NOT EXISTS concept_member (
    concept TEXT NOT NULL,
    code    TEXT NOT NULL,
    PRIMARY KEY (concept, code)
) WITHOUT ROWID;

-- 反查「这只股票属于哪些板块」
CREATE INDEX IF NOT EXISTS idx_concept_member_code ON concept_member(code);
"""

LOCAL_SCHEMA = """
-- 自选：板块和个股共用一张表，语义一致
--   watched_at 非空 = 在自选；置 NULL = 已移除（行保留，不丢东西）
--   板块和个股都有行 = 在自选；没行 = 没关注过
CREATE TABLE IF NOT EXISTS watchlist (
    kind       TEXT    NOT NULL,        -- 'board' | 'stock'
    code       TEXT    NOT NULL,        -- BK1201 / 300308
    name       TEXT,
    watched_at INTEGER,                 -- YYYYMMDD；NULL = 已移除
    note       TEXT,
    PRIMARY KEY (kind, code)
) WITHOUT ROWID;
"""

# 每张表**应该**有的列（用于检测「旧库结构过时」，见 SqliteDB._verify_schema）
RAW_TABLE_COLS: dict[str, tuple] = {
    "stock_daily": ("code", "trade_date", "open", "high", "low", "close",
                    "pre_close", "change", "pct_chg", "volume", "amount"),
    "stock_list": ("code", "name", "updated_at"),
    "board_daily": ("concept", "trade_date", "source", "price", "change_pct",
                    "change", "amount", "volume", "up", "down", "flat",
                    "open", "high", "low", "pre_close", "amplitude_pct",
                    "turnover_pct", "total_mv", "float_mv", "pe",
                    "volume_ratio", "pb", "chg_60d", "chg_ytd", "quote_time"),
    "board_list": ("concept", "name", "member_updated_at"),
    "board_tree": ("concept", "name", "level", "parent", "sw_code", "updated_at"),
    "concept_member": ("concept", "code"),
}

LOCAL_TABLE_COLS: dict[str, tuple] = {
    "watchlist": ("kind", "code", "name", "watched_at", "note"),
}

WATCH_KINDS = ("board", "stock")


# ---------------------------------------------------------------------------
# 日期转换（库里是 INTEGER YYYYMMDD，外部是 "YYYY-MM-DD"）
# ---------------------------------------------------------------------------

def today_int() -> int:
    """
    今天的 YYYYMMDD（北京时区）。

    只用于「什么时候加入自选」这类时间戳，不参与交易日判断 —— 数据永远以行情自带的交易日为准。
    """
    return int(datetime.now(BEIJING).strftime("%Y%m%d"))


def date_to_int(value) -> int | None:
    """把各种日期写法转成 20260911。认不出来返回 None。"""
    s = to_date(value)
    return int(s.replace("-", "")) if s else None


def date_to_str(value: int | None) -> str | None:
    """20260911 -> "2026-09-11"。"""
    if value is None:
        return None
    s = str(int(value))
    return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 else s


# ---------------------------------------------------------------------------
# 底座：SQLite 的公共部分
# ---------------------------------------------------------------------------

class SqliteDB:
    """连接、建表、结构自检、批量读写 —— 两个库共用的部分。"""

    SCHEMA: str = ""
    TABLE_COLS: dict[str, tuple] = {}

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        # WAL：读写并发好、写入快；NORMAL 在 WAL 下是安全的，比 FULL 快很多
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.rebuilt: list[str] = []
        self.cleaned: list[str] = []
        self.init_schema()

    # -- 生命周期 ---------------------------------------------------------
    def __enter__(self) -> "SqliteDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        """建表建索引。已存在就跳过（幂等）。"""
        self.cleaned = self._cleanup()
        self.rebuilt = self._verify_schema()
        self.conn.executescript(self.SCHEMA)
        self.conn.commit()

    # -- 结构自检 ---------------------------------------------------------
    def _cleanup(self) -> list[str]:
        """删掉**空的**、且不在本库定义里的残留表（历史上删过的表留下的空壳）。

        有数据的残留表不动（可能是你手工建的），只在返回值里报出来。
        """
        declared = set(self.TABLE_COLS)
        cleaned = []
        rows = self.query("SELECT name FROM sqlite_master WHERE type='table' "
                          "AND name NOT LIKE 'sqlite_%'")
        for r in rows:
            name = r["name"]
            if name in declared:
                continue
            if self.query(f"SELECT COUNT(*) AS n FROM {name}")[0]["n"] == 0:
                self.conn.execute(f"DROP TABLE {name}")
                cleaned.append(name)
        return cleaned

    def _verify_schema(self) -> list[str]:
        """
        校对已存在的表结构和代码定义，返回被重建的表名。

        ⚠️ `CREATE TABLE IF NOT EXISTS` **不会**修改已存在的表结构 —— 改了 schema 之后
        旧库会**静默地少一列**，属于最难查的那类错误。处理规则：
            缺列 + 表是空的      -> 重建（没数据可丢）
            缺列 + 表有数据      -> **直接报错**，绝不自动删你的数据
            多列 + 整列全是 NULL -> 删掉这一列（它没携带任何信息）
        """
        rebuilt = []
        for table, cols in self.TABLE_COLS.items():
            info = self.query(f"PRAGMA table_info({table})")
            if not info:
                continue                       # 表还没建，后面 executescript 会建
            have = [r["name"] for r in info]

            extra = [c for c in have if c not in cols]
            for c in extra:
                n = self.query(f"SELECT COUNT(*) AS n FROM {table} "
                               f"WHERE {c} IS NOT NULL")[0]["n"]
                if n == 0:
                    self.conn.execute(f"ALTER TABLE {table} DROP COLUMN {c}")

            if set(cols) <= set(have):
                continue                       # 结构没问题
            n = self.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
            if n:
                raise RuntimeError(
                    f"表 {table} 的结构和代码不一致（缺列 {sorted(set(cols) - set(have))}），"
                    f"而且里面有 {n} 行数据 —— 请手动处理，我不会自动删你的数据。")
            self.conn.execute(f"DROP TABLE {table}")
            rebuilt.append(table)
        return rebuilt

    # -- 低层 -------------------------------------------------------------
    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def _insert(self, table: str, cols: tuple, rows: list[dict]) -> int:
        """
        批量插入，**一个事务**（一行一个事务会慢上百倍）。用 INSERT OR IGNORE：同一条不覆盖。
        """
        if not rows:
            return 0
        sql = (f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) "
               f"VALUES ({','.join('?' * len(cols))})")
        values = [tuple(r.get(c) for c in cols) for r in rows]
        with self.conn:                      # with = 一个事务，成功才提交
            cur = self.conn.executemany(sql, values)
        return cur.rowcount

    def counts(self) -> dict[str, int]:
        """每张表有多少行（快速判断库里有什么）。"""
        return {t: self.query(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
                for t in self.TABLE_COLS}


# ---------------------------------------------------------------------------
# Layer A · 原始数据（data/raw/raw.sqlite）
# ---------------------------------------------------------------------------

class Store(SqliteDB):
    """原始行情 + 板块定义。只管「怎么存怎么取」，不含业务逻辑。"""

    SCHEMA = RAW_SCHEMA
    TABLE_COLS = RAW_TABLE_COLS

    def __init__(self, path: str | Path | None = None):
        super().__init__(path if path is not None else DEFAULT_RAW_DB)

    # -- 写：全市场个股日线 -----------------------------------------------
    def save_stock_daily(self, rows: list[dict]) -> int:
        """写入全市场某天（或某几天）的个股日线，返回新增行数。

        每行需要 code + trade_date，其余列缺了就是 NULL。
        """
        prepared = [{**r, "trade_date": date_to_int(r.get("trade_date"))} for r in rows]
        return self._insert("stock_daily", self.TABLE_COLS["stock_daily"], prepared)

    # -- 写：东财板块日线 -------------------------------------------------
    def save_board_daily(self, rows: list[dict]) -> int:
        prepared = [{**r, "trade_date": date_to_int(r.get("trade_date"))} for r in rows]
        return self._insert("board_daily", self.TABLE_COLS["board_daily"], prepared)

    # -- 写：板块字典 -----------------------------------------------------
    def upsert_stock_list(self, rows: list[dict]) -> int:
        """个股字典 upsert（代码 → 名字）。重复写只刷新名字/时间，不会报错。"""
        if not rows:
            return 0
        cols = self.TABLE_COLS["stock_list"]
        sql = (f"INSERT INTO stock_list ({','.join(cols)}) "
               f"VALUES ({','.join('?' * len(cols))}) "
               f"ON CONFLICT(code) DO UPDATE SET "
               f"name=COALESCE(excluded.name, stock_list.name), "
               f"updated_at=COALESCE(excluded.updated_at, stock_list.updated_at)")
        values = [tuple(r.get(c) for c in cols) for r in rows]
        with self.conn:
            cur = self.conn.executemany(sql, values)
        return cur.rowcount

    def upsert_board_list(self, rows: list[dict]) -> int:
        """板块字典 upsert（存在就更新名字 / 同步时间）。"""
        if not rows:
            return 0
        cols = self.TABLE_COLS["board_list"]
        sql = (f"INSERT INTO board_list ({','.join(cols)}) "
               f"VALUES ({','.join('?' * len(cols))}) "
               f"ON CONFLICT(concept) DO UPDATE SET "
               f"name=COALESCE(excluded.name, board_list.name), "
               f"member_updated_at=COALESCE(excluded.member_updated_at, "
               f"board_list.member_updated_at)")
        values = [tuple(r.get(c) for c in cols) for r in rows]
        with self.conn:
            cur = self.conn.executemany(sql, values)
        return cur.rowcount

    # -- 写：板块成员（整块替换）-----------------------------------------
    def replace_board_members(self, concept: str, codes: list[str],
                              updated_at=None) -> int:
        """同步一个板块的成员：先全删、再全插，语义 = 「删掉的去除、新增的加上」。

        比自己算差集简单，也不会算错。一个板块几百个成员是微秒级操作。
        """
        with self.conn:
            self.conn.execute("DELETE FROM concept_member WHERE concept = ?", (concept,))
            self.conn.executemany(
                "INSERT OR IGNORE INTO concept_member (concept, code) VALUES (?, ?)",
                [(concept, c) for c in codes])
            self.conn.execute(
                "INSERT INTO board_list (concept, member_updated_at) VALUES (?, ?) "
                "ON CONFLICT(concept) DO UPDATE SET "
                "member_updated_at=excluded.member_updated_at",
                (concept, date_to_int(updated_at) if updated_at else None))
        return len(codes)

    # -- 读：个股日线 -----------------------------------------------------
    def stock_day_counts(self) -> dict[int, int]:
        """
        每个交易日已经有多少行（YYYYMMDD -> 行数）。

        一次查询同时回答：这天有没有（要不要去拉）、这天是不是只存了一半（要不要重拉）。
        """
        rows = self.query("SELECT trade_date, COUNT(*) AS n FROM stock_daily "
                          "GROUP BY trade_date")
        return {r["trade_date"]: r["n"] for r in rows}

    def last_trade_day(self) -> int | None:
        """
        全市场日线覆盖到的最后一天 —— 整个系统的**交易日钟**。

        有了它，「本地是不是最新的」不用去猜「今天是哪天、今天是不是交易日」：
        那种判断在节假日一定算错，而「全市场日线到哪天了」是既成事实。
        """
        return self.query("SELECT MAX(trade_date) AS d FROM stock_daily")[0]["d"]

    def last_day(self, kind: str, code: str) -> int | None:
        """某个标的本地最后一天（没有就是 None）。走主键，很快。"""
        table, key = (("board_daily", "concept") if kind == "board"
                      else ("stock_daily", "code"))
        return self.query(f"SELECT MAX(trade_date) AS d FROM {table} WHERE {key} = ?",
                          (code,))[0]["d"]

    # -- 删：某个交易日的全市场数据（--force 重拉时才用）------------------
    def delete_stock_days(self, days) -> int:
        """
        删掉这些交易日的个股日线，返回删掉的行数。

        只在「这天没取全、要重拉」时用：写入一律 INSERT OR IGNORE，
        不先删旧数据的话，重拉也盖不掉已经写进去的错行。
        """
        ds = [d for d in (date_to_int(x) for x in days) if d is not None]
        if not ds:
            return 0
        with self.conn:
            cur = self.conn.execute(
                f"DELETE FROM stock_daily WHERE trade_date IN ({','.join('?' * len(ds))})",
                ds)
        return cur.rowcount

    def load_stock_daily(self, codes: list[str] | None = None,
                         start=None, end=None) -> list[dict]:
        """取个股日线。codes 为空 = 全部；start/end 为 YYYYMMDD 或 "YYYY-MM-DD"。"""
        where, params = [], []
        if codes:
            where.append(f"code IN ({','.join('?' * len(codes))})")
            params.extend(codes)
        if start is not None:
            where.append("trade_date >= ?"); params.append(date_to_int(start))
        if end is not None:
            where.append("trade_date <= ?"); params.append(date_to_int(end))
        sql = "SELECT * FROM stock_daily"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY code, trade_date"
        return [dict(r) for r in self.query(sql, params)]

    # -- 删：整个板块（它的行情 + 成员 + 字典那行）-------------------------
    def drop_board(self, concept: str) -> dict[str, int]:
        """
        把一个板块的本地数据**整个删掉**（board_daily + concept_member + board_list）。

        只删行情会留下「字典有、成员有、但没行情」的半截状态，
        下次 fetch board 还会把它当成「在跟的板块」又刷一遍。
        ⚠️ 删掉的是历史，要拿回来得重新 fetch + update member。
        """
        concept = str(concept).strip().upper()
        out = {}
        with self.conn:
            for table, col in (("board_daily", "concept"),
                               ("concept_member", "concept"),
                               ("board_list", "concept")):
                cur = self.conn.execute(f"DELETE FROM {table} WHERE {col} = ?", (concept,))
                out[table] = cur.rowcount
        return out

    # -- 读：板块日线 -----------------------------------------------------
    def load_board_daily(self, concept: str, days: int | None = None) -> list[dict]:
        """取一个板块的日线（按日期升序）；days 非空则只取最近 N 天。"""
        sql = ("SELECT * FROM board_daily WHERE concept = ? "
               "ORDER BY trade_date" + (" DESC LIMIT ?" if days else ""))
        params = (concept, days) if days else (concept,)
        rows = [dict(r) for r in self.query(sql, params)]
        return sorted(rows, key=lambda r: r["trade_date"])

    # -- 读：个股字典 -----------------------------------------------------
    def load_stock_list(self) -> list[dict]:
        return [dict(r) for r in self.query("SELECT * FROM stock_list ORDER BY code")]

    def stock_name(self, code: str) -> str | None:
        """一个代码的名字（不在字典里返回 None）。"""
        rows = self.query("SELECT name FROM stock_list WHERE code = ?",
                          (str(code).strip().zfill(6),))
        return rows[0]["name"] if rows else None

    def name_of(self, kind: str, code: str) -> str | None:
        """按 kind 取名字：'board' -> board_list，'stock' -> stock_list。没有就是 None。"""
        return (self.stock_name(code) if kind == "stock"
                else self._board_name(code))

    def _board_name(self, code: str) -> str | None:
        rows = self.query("SELECT name FROM board_list WHERE concept = ?",
                          (str(code).strip().upper(),))
        return rows[0]["name"] if rows else None

    def delete_board_list(self, concepts: list[str]) -> int:
        """只删板块字典里的条目（**不碰** board_daily / concept_member）。"""
        if not concepts:
            return 0
        with self.conn:
            cur = self.conn.executemany("DELETE FROM board_list WHERE concept = ?",
                                        [(c,) for c in concepts])
        return cur.rowcount

    # -- 板块层级 ---------------------------------------------------------
    def save_board_tree(self, nodes: list[dict], updated_at=None) -> int:
        """整块重建板块层级（先清空再写）：这棵树是**快照**，不是逐条累积。"""
        if not nodes:
            return 0
        at = date_to_int(updated_at) if updated_at else today_int()
        cols = self.TABLE_COLS["board_tree"]
        rows = [{**n, "updated_at": at} for n in nodes]
        with self.conn:
            self.conn.execute("DELETE FROM board_tree")
            self.conn.executemany(
                f"INSERT OR REPLACE INTO board_tree ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                [tuple(r.get(c) for c in cols) for r in rows])
        return len(rows)

    def load_board_tree(self) -> list[dict]:
        """整棵树（按层级、代码排序）。"""
        return [dict(r) for r in self.query(
            "SELECT * FROM board_tree ORDER BY level, concept")]

    def board_level(self, concept: str) -> int | None:
        got = self.query("SELECT level FROM board_tree WHERE concept = ?",
                         (str(concept).strip().upper(),))
        return got[0]["level"] if got else None

    def child_boards(self, concept: str, level: int | None = None) -> list[dict]:
        """某个板块下辖的板块（level 给了就只看那一层）。"""
        sql = "SELECT * FROM board_tree WHERE parent = ?"
        params = [str(concept).strip().upper()]
        if level is not None:
            sql += " AND level = ?"; params.append(level)
        return [dict(r) for r in self.query(sql + " ORDER BY level, concept", params)]

    # -- 读：板块字典 / 成员 ----------------------------------------------
    def load_board_list(self) -> list[dict]:
        return [dict(r) for r in self.query("SELECT * FROM board_list ORDER BY concept")]

    def load_board_members(self, concept: str) -> list[str]:
        rows = self.query("SELECT code FROM concept_member WHERE concept = ? ORDER BY code",
                          (concept,))
        return [r["code"] for r in rows]

    def boards_of(self, code: str) -> list[str]:
        """反查：这只股票属于哪些板块。"""
        rows = self.query("SELECT concept FROM concept_member WHERE code = ? ORDER BY concept",
                          (code,))
        return [r["concept"] for r in rows]


# ---------------------------------------------------------------------------
# 本地状态（data/local.sqlite）—— 不可重建，所以单独一个文件
# ---------------------------------------------------------------------------

class LocalStore(SqliteDB):
    """本地状态：自选。板块和个股共用一张表，语义一致。"""

    SCHEMA = LOCAL_SCHEMA
    TABLE_COLS = LOCAL_TABLE_COLS

    def __init__(self, path: str | Path | None = None):
        super().__init__(path if path is not None else DEFAULT_LOCAL_DB)

    def watch(self, kind: str, code: str, name: str | None = None,
              note: str | None = None, at=None) -> None:
        """加入自选。kind: 'board' | 'stock'。

        重复加入只刷新时间，不会报错；name/note 只在传入时才覆盖。
        """
        self._check_kind(kind)
        code = str(code).strip().upper()
        with self.conn:
            self.conn.execute(
                "INSERT INTO watchlist (kind, code, name, watched_at, note) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(kind, code) DO UPDATE SET "
                "watched_at=excluded.watched_at, "
                "name=COALESCE(excluded.name, watchlist.name), "
                "note=COALESCE(excluded.note, watchlist.note)",
                (kind, code, name, date_to_int(at) if at is not None else today_int(), note))

    def unwatch(self, kind: str, code: str) -> None:
        """移除自选：只把 watched_at 置 NULL，行保留（名字/备注都还在）。"""
        self._check_kind(kind)
        with self.conn:
            self.conn.execute("UPDATE watchlist SET watched_at = NULL "
                              "WHERE kind = ? AND code = ?",
                              (kind, str(code).strip().upper()))

    def load_watchlist(self, kind: str | None = None) -> list[dict]:
        """当前自选（watched_at 非空）。kind 为空则板块 + 个股一起返回。"""
        if kind is not None:
            self._check_kind(kind)
        where, params = "watched_at IS NOT NULL", []
        if kind is not None:
            where += " AND kind = ?"; params.append(kind)
        return [dict(r) for r in self.query(
            f"SELECT * FROM watchlist WHERE {where} "
            f"ORDER BY watched_at DESC, kind, code", params)]

    def is_watched(self, kind: str, code: str) -> bool:
        self._check_kind(kind)
        rows = self.query("SELECT 1 FROM watchlist WHERE kind=? AND code=? "
                          "AND watched_at IS NOT NULL",
                          (kind, str(code).strip().upper()))
        return bool(rows)

    @staticmethod
    def _check_kind(kind: str) -> None:
        if kind not in WATCH_KINDS:
            raise ValueError(f"kind 只能是 {WATCH_KINDS}，收到：{kind!r}")


# 默认实例
store = Store()          # data/raw/raw.sqlite
local = LocalStore()     # data/local.sqlite


# ---------------------------------------------------------------------------
# 自检：python3 -m datasource.store
# ---------------------------------------------------------------------------

def _selftest() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        raw = Store(Path(d) / "raw.sqlite")
        loc = LocalStore(Path(d) / "local.sqlite")
        print(f"建库: raw.sqlite + local.sqlite")

        # ---- 原始层 ----
        rows = [{"code": c, "trade_date": day, "close": 10.0, "pct_chg": 1.0,
                 "volume": 1e6, "amount": 1e8}
                for day in ("2026-09-09", "2026-09-10", "2026-09-11")
                for c in ("000001", "300308", "600519")]
        assert raw.save_stock_daily(rows) == 9
        assert raw.save_stock_daily(rows) == 0          # 重复写不新增
        print(f"stock_daily: 9 行，重复写新增 0 行 ✅")
        assert len(raw.load_stock_daily(codes=["300308"])) == 3
        assert len(raw.load_stock_daily(start="2026-09-11", end="2026-09-11")) == 3

        assert raw.stock_day_counts() == {20260909: 3, 20260910: 3, 20260911: 3}
        assert raw.delete_stock_days(["2026-09-09"]) == 3      # 按交易日整块删
        assert 20260909 not in raw.stock_day_counts()
        raw.save_stock_daily(rows)                              # 删掉的能再写回来
        assert raw.stock_day_counts() == {20260909: 3, 20260910: 3, 20260911: 3}
        print("stock_day_counts / delete_stock_days ✅（重拉靠先删后写）")

        raw.upsert_board_list([{"concept": "BK1201", "name": "电子"}])
        raw.upsert_stock_list([{"code": "300308", "name": "中际旭创",
                                "updated_at": "2026-09-11"}])
        assert raw.stock_name("300308") == "中际旭创"
        assert raw.stock_name("000001") is None
        raw.upsert_stock_list([{"code": "300308", "name": None}])   # 空名字不覆盖
        assert raw.stock_name("300308") == "中际旭创"
        print("stock_list: 个股字典 upsert / 按代码取名字 ✅（空名字不覆盖）")
        raw.replace_board_members("BK1201", ["300308", "000001"], updated_at="2026-09-11")
        assert raw.load_board_members("BK1201") == ["000001", "300308"]
        raw.replace_board_members("BK1201", ["300308", "600519"], updated_at="2026-09-12")
        assert raw.load_board_members("BK1201") == ["300308", "600519"]   # 旧的清掉了
        assert raw.boards_of("600519") == ["BK1201"]
        # 同步成员不该动字典里的名字
        assert raw.load_board_list()[0]["name"] == "电子"
        print("board_list + concept_member: 整块替换、名字不被覆盖 ✅")

        bd = raw.save_board_daily([{"concept": "BK1201", "trade_date": "2026-09-11",
                                    "price": 12730.57, "amount": 488620000000.0,
                                    "up": 97, "down": 420, "flat": 4, "source": "direct"}])
        assert bd == 1 and raw.load_board_daily("BK1201", days=5)[0]["amount"] == 488620000000.0
        print("board_daily: 写入/读取 ✅（amount 存原始「元」）")

        # ---- 自选（独立的库）----
        loc.watch("board", "bk1201", name="电子", at="2026-09-11")
        loc.watch("stock", "300308", name="中际旭创", at="2026-09-11")
        loc.watch("stock", "600519", name="贵州茅台", note="长线", at="2026-09-12")
        wl = loc.load_watchlist()
        print("\n自选: " + ", ".join(f"{x['kind']}:{x['code']}({x['name']})" for x in wl))
        assert len(wl) == 3 and loc.is_watched("board", "BK1201")

        loc.watch("stock", "300308", at="2026-09-13")      # 重复加入只刷新
        assert len(loc.load_watchlist("stock")) == 2

        loc.unwatch("board", "BK1201")
        assert not loc.is_watched("board", "BK1201")
        assert len(loc.load_watchlist()) == 2
        assert loc.query("SELECT COUNT(*) AS n FROM watchlist")[0]["n"] == 3   # 行还在
        print("移除自选: 行保留、只是不在列表里 ✅")

        try:
            loc.watch("sector", "BK1201"); raise SystemExit("应报错")
        except ValueError as e:
            print(f"kind 写错 -> {e}")

        print(f"\nraw 各表行数:   {raw.counts()}")
        print(f"local 各表行数: {loc.counts()}")

        # ---- 结构自检：新增列 / 残留表 ----
        print("\n--- 结构自检 ---")
        raw.conn.execute("ALTER TABLE board_list ADD COLUMN stale_col TEXT")
        raw.conn.execute("CREATE TABLE leftover (x INT)")
        raw.close()
        raw2 = Store(Path(d) / "raw.sqlite")
        print(f"  多出来的空列被删掉: stale_col 还在吗 -> "
              f"{'stale_col' in [r['name'] for r in raw2.query('PRAGMA table_info(board_list)')]}")
        print(f"  残留的空表被清掉: {raw2.cleaned}")
        assert "stale_col" not in [r["name"] for r in raw2.query("PRAGMA table_info(board_list)")]
        assert raw2.cleaned == ["leftover"]

        # 有数据的表缺列 -> 必须报错，不能自动删
        raw2.conn.execute("DROP INDEX idx_stock_daily_date")
        raw2.conn.execute("ALTER TABLE stock_daily DROP COLUMN pct_chg")
        raw2.close()
        try:
            Store(Path(d) / "raw.sqlite"); raise SystemExit("应报错")
        except RuntimeError as e:
            print(f"  有数据的表缺列 -> 报错 ✅（{str(e)[:46]}…）")

        print("\n✅ 自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
