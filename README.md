# one_piece

猎人系统 —— 通过**板块势能**找寻资金留下的脚印，顺着脚印判断资金的方向。

> 模型本身写在 [`doc/model.md`](doc/model.md)：四因子（Flow / Breadth / Efficiency /
> Crowding）、五个阶段、三层判断。这份 README 只讲**怎么用**。

---

## 快速开始

```bash
# 1. 装依赖（建议用虚拟环境）
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2. 填 tushare token（去 https://tushare.pro 注册后复制）
#    config/system.yaml 里的 tushare_token

# 3. 第一次用：补一段历史 + 拉申万板块定义（板块定义走乐咕，全市场行情走 tushare）
python3 hunter.py backfill market --days 30   # 最近 30 个交易日全市场日线（≈31 个请求）
python3 hunter.py update board                # 申万一/二级板块 + 成分股（乐咕，低频）

# 4. 之后每天收盘后跑这两条（第二条是纯本地计算，0 个请求）
python3 hunter.py fetch market
python3 hunter.py fetch board
```

---

## 数据来源（就两条腿）

| 数据 | 谁提供 | 说明 |
| --- | --- | --- |
| 全市场个股日线 | **tushare**（120 积分） | 按交易日一次拉全市场，存 `stock_daily` |
| 板块定义（层级 + 成分股） | **乐咕乐股**（legulegu.com） | 申万一/二级分类 + 成分股，存 `board_tree`/`board_list`/`concept_member` |
| 板块指标（涨跌幅/成交额/涨跌家数） | **本地计算** | 从 `stock_daily` + `concept_member` 聚合，存 `board_daily` |

> **行情只拉一次，板块全部本地算**：板块的成交额 = Σ成分股成交额、涨跌家数 = 数成分股涨跌、
> 涨跌幅 = 成分股市值加权平均。所以「板块行情」不依赖任何第三方，零反爬风险。
>
> 板块代码用**申万行业代码 + .SI 后缀**，形如 `801080.SI`（电子）、`801081.SI`（半导体二级）。

---

## 用法（`hunter.py` 是唯一的执行入口）

```
python3 hunter.py <动作> <内容> [选项]
```

| 动作 | 命令 | 做什么 | 联网 |
| --- | --- | --- | :-: |
| **backfill** | `backfill market --days 30` | 补最近 N 个交易日全市场日线（首次用） | ✅ 每天 1 个请求 |
| | `backfill market --days 30 --force` | 已有的也重拉（修「那天没取全」） | ✅ |
| **update** | `update board` | 拉最新申万一/二级板块 + 成分股，更新数据库 | ✅ 乐咕 |
| | `update board --force` | 今天同步过的板块也重新拉 | ✅ |
| **fetch** | `fetch market` | 拉全市场**一天**的日线（日常，1 个请求） | ✅ tushare |
| | `fetch market --date 20260911` | 指定交易日 | ✅ |
| | `fetch board` | **本地计算**板块指标入库（增量，缺的交易日才算） | ❌ 0 请求 |
| | `fetch board --force` | 全量重算板块指标（先清空 board_daily） | ❌ |
| **drop** | `drop board 801080.SI` | 删掉某板块本地数据（行情+成分股+字典） | ❌ 只改本地 |
| **watch** | `watch 801080.SI 300308` | 加入自选（板块/个股都行） | ❌ |
| | `watch` | 看当前自选 | ❌ |
| **unwatch** | `unwatch 801080.SI` | 移出自选 | ❌ |
| **show** | `show board` | 一级一个标签，面板里一级在上、它的二级依次在下 | ❌ 只读本地 |
| | `show watch` | 只看自选 | ❌ |
| | `show 801080.SI` | 某个板块 / 个股（`--days N` 最近 N 天） | ❌ |

`show` 只读本地数据库，**不联网**；页面落在 `data/view.html`，可以反复看。

---

## 一级 / 二级板块

```bash
python3 hunter.py update board      # 拉申万一级 31 + 二级 131 + 成分股（乐咕）
python3 hunter.py fetch board       # 本地算这 162 个板块的指标
python3 hunter.py show board        # 一级一个标签，面板里一级在上、它的二级依次在下
```

层级直接来自申万分类（乐咕），**只到二级**（三级有 335 个、粒度太细）。

### 板块变了会怎样

`update board` 会把板块表**整块快照重建**，所以申万的增/删/改都能反映：

| 场景 | 结果 |
| --- | --- |
| 申万新增板块 | 进 `board_tree`，成分股同步进来 |
| 申万删除/合并板块 | 从树里移除，`update board` 顺手清掉它的行情/成员/字典 |
| 成分股调整 | `concept_member` 整块替换（含市值权重） |

成分股变动不频繁，所以 `update board` 保持**低频**（每周/成分调整时跑一次即可），
而且今天同步过的板块会跳过（`--force` 才重拉）。

---

## 自选（只看我关心的那几个）

```bash
python3 hunter.py watch 801080.SI 300308     # 加入自选（板块、个股都能加）
python3 hunter.py watch                       # 看当前自选
python3 hunter.py unwatch 801080.SI           # 移出自选（势能结束了）
```

`watch` / `unwatch` 只改本地状态，**一个请求都不发**。自选是 `show watch` 的索引，
不存在「fetch watch」—— 行情永远是 `fetch market`（全市场）和 `fetch board`（全板块）整体更新。

自选存在 `data/local.sqlite`（**不可重建**，单独一个文件，方便单独备份）。

### `drop` 和 `unwatch` 不是一回事

| | 做什么 | 数据 | 拿回来 |
| --- | --- | --- | --- |
| `unwatch` | 不再关注 | **保留**（只是不在自选列表里） | 再 `watch` 一下 |
| `drop board` | 本地数据整个删掉 | 删行情 + 成分股 + 字典 | `update board` + `fetch board` |

---

## 数据放在哪

```
data/
├── raw/raw.sqlite        ★ 原始数据（全都能重新下载/重算，删了不可惜）
│   ├── stock_daily       全市场个股日线（一天约 5500 行）  amount=元  volume=股
│   ├── stock_list        个股字典（代码 → 名字）
│   ├── board_daily       板块聚合日线（本地从个股算：涨跌幅/成交额/涨跌家数）
│   ├── board_list        板块字典（代码 → 名称 + 成员同步时间）
│   ├── board_tree        板块层级（一级/二级 + 上级）
│   └── concept_member    板块 → 成员 + 市值权重（快照式）
├── local.sqlite          ★ 本地状态（**不可重建**，删了就真没了）
│   └── watchlist         自选（板块 + 个股）
└── view.html             `show` 生成的页面（每次覆盖）
```

**单位约定**：库里一律存**原始单位**（金额 = 元、成交量 = 股），换算成「亿 / 万」只发生在展示那一步。

---

## 配置（`config/system.yaml`）

```yaml
tushare_token: "..."     # 必填，否则拉不了全市场个股行情
fetch_interval: 2.0      # 两次联网请求之间的间隔秒数（tushare 限 50 次/分钟）
```

---

## 目录结构

```
├── hunter.py                  ★ 唯一执行入口
├── cli/                       命令行层
│   ├── app.py                 命令行长什么样 + 分派
│   ├── base.py                依赖装配（Context）+ 标的 + 命令基类
│   ├── console.py             所有给人看的输出
│   ├── fetch.py               fetch market / backfill market / fetch board
│   ├── catalog.py             update board / drop board
│   ├── watch.py               自选
│   └── show.py                show
├── config/
│   ├── config.py              读 yaml 的 Config 单例
│   └── system.yaml            数据源 token / 间隔
├── datasource/
│   ├── fetch.py               数据获取主接口（全市场 + 本地读写）
│   ├── fetch_tushare.py       Tushare（全市场日线 / 个股字典 / 交易日历）
│   ├── fetch_legu.py          乐咕（申万层级 / 成分股）
│   ├── board_calc.py          板块聚合计算（本地）
│   ├── board_tree.py          板块层级模型
│   ├── store.py               数据库存取层
│   ├── base.py                数据源基类 + 工具函数
│   └── show_data.py           展示层（图形化页面 / 控制台表格）
├── doc/model.md               ★ 模型本身
└── data/                      数据（见上）
```

分层：`hunter.py` → `cli/`（命令与输出）→ `datasource/`（数据源与存储）→ `config/`。
