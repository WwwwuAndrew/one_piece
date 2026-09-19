#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_legu.py —— 乐咕乐股数据源（申万板块层级 + 成分股）。

    src = LeguSource()
    levels = src.fetch_levels()                 # {1: [...], 2: [...]} 一级/二级分类
    members = src.fetch_members("801080.SI")    # 一个板块的成分股

源站 legulegu.com（通过 akshare 的 sw_index_* 接口）。
只提供「板块定义」（层级 + 成分股），**不提供板块行情** ——
板块行情由本地从个股日线聚合（board_calc.py）。

⚠️ 乐咕是 HTML 抓取（akshare 用 BeautifulSoup 解析固定 class），
   网站改版会导致这些接口失效 —— 属于「低频同步、坏了要修」的数据源，
   所以 update board 保持低频 + 幂等，别高频去刷。
"""

from __future__ import annotations

import re
import time

from .base import DataSource, normalize_board_code, to_num


def _col(df, *keys):
    """按关键词认列名（乐咕各版本列名可能微调）。找不到返回 None。"""
    for c in df.columns:
        if any(k in str(c) for k in keys):
            return c
    return None


class LeguSource(DataSource):
    """乐咕乐股数据源（申万板块定义）。"""

    name = "legu"

    def fetch_levels(self) -> dict[int, list[dict]]:
        """拉申万一/二级分类（各 1 个请求）。

        返回 {1: [{"code", "name"}], 2: [{"code", "name", "parent_name"}]}：
            code        801080.SI（大写、带 .SI）
            parent_name 二级的上级「名称」（乐咕只给名称，不给上级代码）
        """
        import akshare as ak

        out: dict[int, list[dict]] = {}
        for level, fn in ((1, "sw_index_first_info"), (2, "sw_index_second_info")):
            f = getattr(ak, fn, None)
            if f is None:
                raise RuntimeError(f"akshare 没有 {fn}()，请升级 akshare")
            try:
                df = f()
            except Exception as exc:
                raise RuntimeError(f"乐咕 {fn}() 失败：{type(exc).__name__}: {exc}") from exc
            if df is None or len(df) == 0:
                raise LookupError(f"乐咕 {fn}() 返回空")

            c_col, n_col = _col(df, "代码"), _col(df, "名称")
            if not c_col or not n_col:
                raise RuntimeError(f"{fn}() 列名和预期不一致，实际：{list(df.columns)}")
            p_col = _col(df, "上级", "父", "所属")

            rows = []
            for _, r in df.iterrows():
                code = str(r[c_col]).strip().upper()
                name = str(r[n_col]).strip()
                if not code:
                    continue
                rec: dict = {"code": code, "name": name}
                if level == 2 and p_col:
                    rec["parent_name"] = str(r[p_col]).strip()
                rows.append(rec)
            out[level] = rows
        return out

    def fetch_members(self, code: str, retries: int = 3,
                      retry_delay: float = 3.0) -> list[dict]:
        """拉一个板块的成分股。

        code : 801080.SI（缺 .SI 后缀也会帮你补上）
        返回 [{"code": "300308", "name": "中际旭创", "mktcap": 8760000000.0}, …]
            mktcap 是总市值（元），给市值加权当权重；拿不到就是 None。

        乐咕是 HTML 抓取，偶发「No tables found」—— 页面没返回表格，多半是批量
        拉取时被瞬时限流。所以这里带重试：失败等 retry_delay 秒再试，最多 retries 次。
        """
        code = normalize_board_code(code)
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                members = self._parse_members(self._fetch_members_df(code))
                if members:
                    return members
                raise LookupError(f"乐咕 {code} 的成分股解析后为空")
            except Exception as exc:  # noqa: BLE001 —— 重试内吞掉、最后一次再抛
                last_exc = exc
                if attempt < retries:
                    time.sleep(retry_delay)
        raise RuntimeError(
            f"乐咕 sw_index_third_cons({code!r}) 失败（重试 {retries} 次）："
            f"{type(last_exc).__name__}: {last_exc}") from last_exc

    @staticmethod
    def _fetch_members_df(code: str):
        """调 akshare 拉成分股原始 DataFrame（1 个请求）。"""
        import akshare as ak

        df = ak.sw_index_third_cons(symbol=code)
        if df is None or len(df) == 0:
            raise LookupError(f"乐咕没有 {code} 的成分股")
        return df

    @staticmethod
    def _parse_members(df) -> list[dict]:
        """乐咕成分股 DataFrame -> [{code, name, mktcap}]。"""
        c_col = _col(df, "股票代码", "代码")
        n_col = _col(df, "股票简称", "简称", "名称")
        m_col = _col(df, "市值")
        if not c_col:
            raise RuntimeError(f"sw_index_third_cons 列名和预期不一致，实际：{list(df.columns)}")

        members = []
        for _, r in df.iterrows():
            raw = str(r[c_col]).strip().upper()
            m = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", raw)
            if not m:
                continue
            mktcap = to_num(r[m_col]) if m_col else None
            members.append({
                "code": m.group(1),
                "name": str(r[n_col]).strip() if n_col else "",
                # 乐咕「市值」单位是亿元 -> 换成元，和库里原始单位保持一致
                "mktcap": None if mktcap is None else mktcap * 1e8,
            })
        return members
