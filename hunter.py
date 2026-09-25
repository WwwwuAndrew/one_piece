#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hunter.py —— 整个项目唯一的执行入口。

这个文件只做一件事：把命令行交给 cli/ 里的应用层。

    cli/app.py       命令行长什么样，怎么变成命令对象
    cli/base.py      依赖装配（Context）+ 标的/新鲜度判据 + 命令基类
    cli/console.py   所有给人看的输出
    cli/fetch.py     拉行情（fetch / backfill）
    cli/watch.py     自选
    cli/catalog.py   板块层级表、成分股、删板块
    cli/show.py      展示（板块 / 个股的因子数字表）
    factor/          因子层（Participation / Cost / RS / screen）
"""

from __future__ import annotations

import sys

from cli.app import main

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断")
        sys.exit(130)
