#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
webapp.py —— app 子命令：起本地浏览器界面。

    python3 hunter.py app [--port 8765] [--no-browser]

命令类只是把「起服务」这件事接进命令分发（`hunter.py` 不带参数也走这里）。
真正的实现在 app/server.py —— 那里的界面和命令行的 `show board` 共用同一份渲染。
"""

from __future__ import annotations

from .base import Command, Context


class RunApp(Command):
    """app —— 起本地 app（浏览器界面），Ctrl-C 退出。"""

    name = "app"

    def __init__(self, ctx: Context, port: int = 8765, open_browser: bool = True):
        super().__init__(ctx)
        self.port = port
        self.open_browser = open_browser

    def run(self) -> int:
        # 放在函数里 import：不起 app 的时候不加载 http 服务这一摊
        from app.server import serve

        return serve(port=self.port, open_browser=self.open_browser)
