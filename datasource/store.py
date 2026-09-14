#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
store.py —— SQLite 存取层。

按「**能不能重建**」分成两个库（这是分库的判据，不是体积）：

    data/raw/raw.sqlite          Layer A · 原始数据 —— 全都能重新下载，删了不可惜
        stock_daily      全市场个股日线（一天约 5550 行）
        stock_list       个股字典（代码 → 名字）★ 名字属于字典，不属于行情
        board_daily      东财板块日线
        board_list       板块字典（代码 → 名称 + 成员同步时间，**纯字典**）
        concept_member   板块 → 成员（快照式，没有日期列）

    data/local.sqlite            本地状态 —— **既下不到、也算不出，删了就真没了**
        watchlist        自选（板块 + 个股共用一张表）

为什么自选要单独一个文件：
    raw 里的东西全都能重新下载，所以你想「把全市场重新拉一遍」时可以放心重建 raw；
    但自选丢了就没了。分开之后两者互不影响，而且自选只有几行，
    单独一个几 KB 的文件还能单独备份 / 进 git。

    跨库**不需要 JOIN** —— 自选读出来就是一个 Python 列表，
    直接 `WHERE code IN (...)` 用即可，所以没有 ATTACH 那种复杂度。

用法：
    from datasource.store import store, local
    store.save_stock_daily(rows)              # 全市场某天
    store.replace_board_members("BK1201", codes)
    local.watch("board", "BK1201", name="电子")
    local.load_watchlist()

⚠️ 单位约定：库里一律存**原始单位**（amount = 元，volume = 股）。
   Layer A 只采不算；展示时再换算成 亿/万（base.fmt_amount 已做好）。

自检：
    python3 -m datasource.store        # 用临时库跑一遍建表/写入/查询，不碰 data/
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .base import BEIJING, to_date

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_RAW_DB = DATA_DIR / "raw" / "raw.sqlite"
DEFAULT_LOCAL_DB = DATA_DIR / "local.sqlite"

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
    """今天的 YYYYMMDD（北京时区）。

    只用于「什么时候加入自选」这种时间戳，不参与任何交易日的判断 ——
    数据入库永远以行情自带的交易日为准。
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
        """校对已存在的表结构和代码定义，返回被重建的表名。

        ⚠️ `CREATE TABLE IF NOT EXISTS` **不会**修改已存在的表结构 ——
        所以改了 schema 之后，旧库会**静默地少一列**，属于最难查的那类错误。

        处理规则：
          · 缺列 + 表是空的   -> 重建（没数据可丢）
          · 缺列 + 表有数据   -> **直接报错**，绝不自动删你的数据
          · 多列 + 整列全是 NULL -> 删掉这一列（它没携带任何信息，删了绝对安全）
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
        """批量插入，**一个事务**（否则一行一个事务会慢上百倍）。

        用 INSERT OR IGNORE：同一条已经存过就不覆盖，
        语义就是「同一交易日不覆盖」：先到的算数，后来的不覆盖。
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
        """每个交易日已经有多少行（YYYYMMDD -> 行数）。

        一次查询同时回答两件事：
          · 这天有没有（在不在字典里）= 「要不要再去拉」；
          · 这天是不是只存了一半（行数明显少于全市场）= 「要不要重拉」。
        """
        rows = self.query("SELECT trade_date, COUNT(*) AS n FROM stock_daily "
                          "GROUP BY trade_date")
        return {r["trade_date"]: r["n"] for r in rows}

    def last_trade_day(self) -> int | None:
        """全市场日线覆盖到的最后一天（没有就是 None）。

        ★ 这就是整个系统的**交易日钟**：每天 fetch market 一次，其余一切以它为准。
        有了它，「本地是不是最新的」不用去猜「今天是哪天、今天是不是交易日」——
        那种判断在节假日一定会算错，而这种永远不会。
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
        """删掉这些交易日的个股日线，返回删掉的行数。

        只在「这天没取全、要重拉」时用：因为写入一律 INSERT OR IGNORE，
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
