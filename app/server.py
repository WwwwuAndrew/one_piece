#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server.py —— 本地 app：把 hunter 包成一个浏览器界面（只用标准库，零新依赖）。

    serve(port=8765, open_browser=True)      # python3 hunter.py app

为什么是浏览器而不是桌面窗口：tkinter 没装，而因子表本来就是 HTML —— 浏览器就是最合适的界面。
表格 / 红绿字 / 悬浮 / 点选全都复用 ui/factor_table.py 那一份渲染（app 与命令行**同一个来源**），
这里只负责：起服务、把素材做成 JSON、把命令跑成「后台任务」。

三个界面（外壳在 app/static/，负责切换）：

    首页   拉行情 / 算板块 —— 后台跑，日志实时回显，结束弹框说成功失败与原因
    看板   二级板块因子表 + good 开关；点某板块 → 底部出现「查看该板块个股」
    个股   板块内个股因子表 + good 开关 + 返回

线程与 SQLite：
    · 只读连接（self.db）主线程建、所有请求线程共用，所以 check_same_thread=False，
      并且每次读都用 _LOCK 串起来 —— 单用户本地工具，够了；
    · 后台任务在**自己的线程里**新建连接（读写都归它自己），不跟只读连接抢。
安全：只监听 127.0.0.1，没有账号密码 —— 它就是个本机工具。
"""

from __future__ import annotations

import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cli.base import Context
from cli.catalog import UpdateBoard
from cli.console import Console
from cli.fetch import FetchBoard, FetchMarket
from datasource.fetch import Fetcher
from datasource.store import LocalStore, Store, date_to_str
from factor.cost import Cost
from factor.participation import Participation
from factor.rs import RS
from ui import factor_table

STATIC_DIR = Path(__file__).resolve().parent / "static"

# 后台任务：名字 -> (标题, 怎么跑)。都复用命令行那一套命令对象，不另写一份逻辑。
_JOB_SPECS = {
    "fetch_market": ("拉取行情（fetch market）",
                     lambda ctx: FetchMarket(ctx).run()),
    "fetch_board": ("计算板块（fetch board）",
                    lambda ctx: FetchBoard(ctx).run()),
    "update_board": ("更新板块定义（update board）",
                     lambda ctx: UpdateBoard(ctx).run()),
}

# 只读连接被所有请求线程共用，用一把锁串起来（单用户，够用且不会写坏事务）
_LOCK = threading.RLock()
_READ_DB: Store | None = None
_READ_LOCAL: LocalStore | None = None


def _db() -> Store:
    global _READ_DB
    with _LOCK:
        if _READ_DB is None:
            _READ_DB = Store(check_same_thread=False)
        return _READ_DB


def _local() -> LocalStore:
    global _READ_LOCAL
    with _LOCK:
        if _READ_LOCAL is None:
            _READ_LOCAL = LocalStore(check_same_thread=False)
        return _READ_LOCAL


# ---------------------------------------------------------------------------
# 后台任务：跑一条命令、把它的输出收成日志
# ---------------------------------------------------------------------------

class _JobConsole(Console):
    """把命令的输出收进任务日志，而不是打到服务端终端。"""

    def __init__(self, db, sink):
        super().__init__(db)
        self._sink = sink

    def say(self, text: str = "") -> None:
        self._sink(str(text))

    def err(self, text: str) -> None:
        self._sink(str(text))


class Job:
    """一次后台任务的全部状态（前端每秒来问一次，所以它必须是内存里的一小块）。"""

    def __init__(self, name: str, title: str):
        self.name = name
        self.title = title
        self.lines: list[str] = []
        self.running = True
        self.ok: bool | None = None
        self.error: str = ""
        self.started = time.time()
        self.finished = 0.0

    def log(self, text: str) -> None:
        self.lines.append(text)

    def to_dict(self) -> dict:
        # 有 ⚠️ 的算「有警告」：拉行情时市值快照被限速就是这种情况 —— 主流程成功，
        # 但界面得把这条提示出来，不能只显示一个绿勾。
        warn = sum(1 for ln in self.lines if ln.strip().startswith("⚠️"))
        return {
            "name": self.name, "title": self.title, "lines": self.lines,
            "running": self.running, "ok": self.ok, "error": self.error,
            "warnings": warn,
            "seconds": round((self.finished or time.time()) - self.started, 1),
        }


class JobRunner:
    """同时只允许一个任务（本地单用户，排队反而更难解释）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.job: Job | None = None

    def start(self, name: str) -> dict:
        spec = _JOB_SPECS.get(name)
        if spec is None:
            return {"ok": False, "error": f"不认识的按钮：{name}"}
        with self._lock:
            if self.job is not None and self.job.running:
                return {"ok": False, "error": f"「{self.job.title}」还在跑，等它结束再点"}
            self.job = Job(name, spec[0])
            job, run = self.job, spec[1]
        threading.Thread(target=self._run, args=(job, run), daemon=True).start()
        return {"ok": True, "job": job.to_dict()}

    def _run(self, job: Job, run) -> None:
        print(f"[app] ▶ {job.title} 开始")
        try:
            # 任务在自己的线程里新建全套依赖：db / watch / fetcher 都归它自己，
            # 不跟只读连接共用（SQLite 连接不能跨线程用）。
            db = Store()
            ctx = Context(db=db, watch=LocalStore(), fetcher=Fetcher(db=db),
                          console=_JobConsole(db, job.log))
            code = run(ctx)
            job.ok = (code == 0)
            if not job.ok:
                job.error = job.lines[-1] if job.lines else f"命令返回 {code}"
        except Exception as exc:                     # noqa: BLE001 —— 什么错都要说给用户看
            job.ok = False
            job.error = f"{type(exc).__name__}: {exc}"
            job.log(f"❌ {job.error}")
            import traceback
            job.log(traceback.format_exc())
        finally:
            job.running = False
            job.finished = time.time()
            print(f"[app] {'✅' if job.ok else '❌'} {job.title} 结束"
                  f"（{job.finished - job.started:.1f}s）")

    def status(self) -> dict:
        with self._lock:
            if self.job is None:
                return {"running": False, "idle": True}
            return self.job.to_dict()


# ---------------------------------------------------------------------------
# 各接口的数据
# ---------------------------------------------------------------------------

def _state() -> dict:
    db, local = _db(), _local()
    with _LOCK:
        s = db.query("SELECT COUNT(*) n, COUNT(DISTINCT trade_date) d, "
                     "MIN(trade_date) a, MAX(trade_date) b FROM stock_daily")[0]
        b = db.query("SELECT COUNT(*) n, COUNT(DISTINCT concept) c, "
                     "MAX(trade_date) m FROM board_daily")[0]
        levels = {r["level"]: r["n"] for r in db.query(
            "SELECT level, COUNT(*) n FROM board_tree GROUP BY level")}
        watch = len(local.load_watchlist())
    return {
        "ok": True,
        "stock_rows": s["n"], "stock_days": s["d"],
        "stock_from": date_to_str(s["a"]), "stock_to": date_to_str(s["b"]),
        "board_rows": b["n"], "board_concepts": b["c"],
        "board_from": date_to_str(b["m"]),
        "boards_l1": levels.get(1, 0), "boards_l2": levels.get(2, 0),
        "watch": watch,
        "has_market": bool(s["n"]), "has_board": bool(b["n"]),
    }


def _fragments(kind: str, params: dict) -> dict:
    """看板 / 个股两种视图的素材。计算较重（几百毫秒到几秒），所以只在需要时算。"""
    db = _db()
    days = int(params.get("days", factor_table.DAYS) or factor_table.DAYS)
    good = str(params.get("good", "0")) not in ("0", "", "false", "False")
    with _LOCK:
        part = Participation(db=db)
        cost = Cost(db=db)
        if kind == "boards":
            codes = [r["concept"] for r in db.load_board_tree() if r["level"] == 2]
            parent_of = {r["concept"]: r["parent"]
                         for r in db.load_board_tree() if r["level"] == 2}
            if not codes:
                return {"ok": False, "reason": "还没有板块层级表（先点首页的「更新板块定义」）"}
            v = factor_table.boards_fragment(part, cost, codes, parent_of,
                                             good=good, days=days)
        else:
            code = str(params.get("code", "")).strip().upper()
            if not code:
                return {"ok": False, "reason": "没给板块代码"}
            if db.board_level(code) is None:
                return {"ok": False, "reason": f"板块表里没有 {code}"}
            v = factor_table.stocks_fragment(part, cost, RS(db=db), code,
                                             good=good, days=days)
    v["good"] = good
    v["days"] = days
    return v


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "one_piece"

    # 前端每秒轮询任务状态，打日志会把终端刷满；要安静
    def log_message(self, *args) -> None:      # noqa: D102
        return

    # -- 工具 -------------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: dict, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _text(self, text: str, ctype: str = "text/plain; charset=utf-8") -> None:
        self._send(200, text.encode("utf-8"), ctype)

    def _file(self, path: Path, ctype: str) -> None:
        if not path.is_file():
            self._json({"ok": False, "error": f"没有这个文件：{path.name}"}, 404)
            return
        self._send(200, path.read_bytes(), ctype)

    # -- 路由 -------------------------------------------------------------
    def do_GET(self) -> None:                  # noqa: N802 —— BaseHTTPRequestHandler 的约定
        u = urlparse(self.path)
        path, q = u.path, {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if path in ("/", "/index.html"):
                return self._file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            if path == "/static/app.css":
                return self._file(STATIC_DIR / "app.css", "text/css; charset=utf-8")
            if path == "/static/app.js":
                return self._file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
            # 因子表的 CSS/JS 只有一份，在 ui/factor_table.py 里 —— 直接发出去，避免两套渲染
            if path == "/static/table.css":
                return self._text(factor_table.TABLE_CSS, "text/css; charset=utf-8")
            if path == "/static/table.js":
                return self._text(factor_table.TABLE_JS,
                                  "application/javascript; charset=utf-8")
            if path == "/api/state":
                return self._json(_state())
            if path == "/api/job":
                return self._json(JOB.status())
            if path == "/api/boards":
                return self._json(_fragments("boards", q))
            if path == "/api/stocks":
                return self._json(_fragments("stocks", q))
            if path == "/api/watch":
                return self._json(_watch())
        except Exception as exc:               # noqa: BLE001 —— 出错也要让界面能说人话
            return self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)
        return self._json({"ok": False, "error": f"没有这个地址：{path}"}, 404)

    def do_POST(self) -> None:                 # noqa: N802
        path = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except Exception:
            body = {}
        if path == "/api/job":
            return self._json(JOB.start(str(body.get("name", ""))))
        return self._json({"ok": False, "error": f"没有这个地址：{path}"}, 404)


def _watch() -> dict:
    """自选（界面上的「自选」以后接这里）。"""
    local = _local()
    with _LOCK:
        items = local.load_watchlist()
    return {"ok": True, "items": [
        {"kind": w["kind"], "code": w["code"], "name": w["name"] or "",
         "at": date_to_str(w["watched_at"])} for w in items]}


JOB = JobRunner()


# ---------------------------------------------------------------------------
# 起服务
# ---------------------------------------------------------------------------

def serve(port: int = 8765, open_browser: bool = True, tries: int = 20) -> int:
    """起本地 app，返回进程退出码。端口被占就往后找。"""
    httpd = None
    for p in range(port, port + tries):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        print(f"❌ {port}~{port + tries} 都被占用了，换一个：python3 hunter.py app --port 9000")
        return 1

    url = f"http://127.0.0.1:{port}/"
    print("🏴 one_piece 猎人系统 · 本地 app 已启动")
    print(f"   界面：{url}")
    print("   关掉这个窗口（或 Ctrl-C）就退出。")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    finally:
        httpd.server_close()
    return 0
