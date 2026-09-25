#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor/ —— 板块势能因子层。

从原始数据（datasource/）算出因子（Participation / Cost / RS …）。
因子本身**不落库**：现在只用于展示，最终形态定了再决定存不存（B 层 Feature 表）。

    factor/participation.py   Participation · 资金参与度（AbsPart + RelPart）
    factor/cost.py            Cost · 推进成本（AbsCost / RelCost）
    factor/rs.py              RS · 相对板块强度（RS + RS 偏移）
    factor/screen.py          show board --good 的筛选规则（展示前把不成立的摘掉）
"""
