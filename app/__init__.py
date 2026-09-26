#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/ —— 本地 app（浏览器界面）。

    python3 hunter.py app           # 起服务并打开浏览器
    python3 hunter.py               # 不带参数 = 同上

    server.py        HTTP 服务 + 接口 + 后台任务（只用标准库 http.server）
    static/          前端外壳：首页 / 看板 / 个股 三个界面

界面里能做的：拉行情、算板块、更新板块定义（后台跑 + 实时日志 + 成功失败弹框），
看看板（二级板块因子表）、点板块进它的成分股。
表格的渲染与交互和命令行**同源**（ui/factor_table.py 里的那一份 CSS/JS），不另写一套。
"""
