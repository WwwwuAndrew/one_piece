#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli/__init__.py —— 命令行层。

分两层：命令「做什么」（各命令类），和「怎么说给人听」（console.Console）。
两者之间的依赖由 base.Context 装配。
"""
