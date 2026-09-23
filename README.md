# one_piece

猎人系统 —— 通过**板块势能**找寻资金留下的脚印，顺着脚印判断资金的方向。

> 模型本身（四因子、五阶段、判断三层、决策树）见 [`doc/model.md`](doc/model.md)。
> 这份 README 只讲**怎么装、怎么跑、数据存在哪**。

---

## 快速开始

```bash
# 1. 装依赖（建议用虚拟环境）
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2. 填 tushare token（去 https://tushare.pro 注册后复制）
#    config/system.yaml 里的 tushare_token

# 3. 第一次用：补一段历史 + 拉申万板块定义
python3 hunter.py backfill market --days 30   # 最近 30 个交易日全市场日线（≈31 个请求）
python3 hunter.py update board                # 申万一/二级板块 + 成分股（乐咕，约 27 分钟）

# 4. 之后每天收盘后跑这两条（第二条是纯本地计算，0 个请求）
python3 hunter.py fetch market
python3 hunter.py fetch board
```

---

## 数据来源（就两条腿）

| 数据 | 谁提供 | 说明 |
| --- | --- | --- |
| 全市场个股日线 | **tushare**（120 积分） | 按交易日一次拉全市场，存 `stock_daily` |
| 板块定义（层级 + 成分股） | **乐咕乐股**（legulegu.com） | 申万一/二级分类 + 成分股，低频同步 |
| 板块指标（涨跌幅/成交额/涨跌家数） | **本地计算** | 从个股日线 + 成分股聚合，存 `board_daily` |

板块代码用**申万行业代码 + .SI 后缀**：`801080.SI`（电子）、`801081.SI`（半导体二级）。

---

## 设计原则

**① 按「交易日」拉全市场，不要按板块拉股票**

```
❌ 板块A → 拉A的 50 只 → 算 Breadth     （几百个板块 = 几百轮请求 = 把自己做成爬虫）
✅ 每天收盘 → tushare daily(trade_date)  ← 1 次请求拿全市场约 5500 只
              → 存本地 → 按板块成员本地聚合
```

tushare 官方建议正是**循环日期取全市场、不循环 ts_code**（单次上限 6000 条、全市场约 5500 只），
所以**一天 1 个请求**。行情只拉一次，板块计算全在本地。

**② 只存「原始」，派生数据随时重建**

| 类型 | 存什么 |
| --- | --- |
| **原始** | `stock_daily` · `board_daily` · `stock_list` · `board_list` · `concept_member` |
| **派生** | 个股因子、板块 Participation / Cost / Crowding、Stage |

> 不要把「板块 Breadth 历史」当原始数据存 —— 存「个股历史 + 成员关系」，随时重建。
> 好处：想改口径（MA20 → MA30）**重算即可**，不用重新采集。

**③ 成员表用「快照」**

`concept_member(concept, code, mktcap)` 只记「**现在这个板块有哪些股票**」，没有 start/end 日期。
因为本系统的用途是**看当下的势能**，不是做跨年回测。代价是跨年回测会有**小的乐观偏差**
（等于用今天的名单回看历史）—— 这是明确知道、明确接受的取舍。

**④ 采集两条铁律**

1. **以交易日为准** —— 落盘的每一天由行情自带的交易日决定，不做「今天是哪天」的判断，
   所以周末和法定节假日都不会算错。
2. **历史靠重建，不靠数据商** —— 不要指望数据商帮你保存过「过去这个板块的涨跌家数」。

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
| **show** | `show hot board` | 热力图：全部二级板块（默认） | ❌ 只读本地 |
| | `show hot board --level 1` | 热力图：全部一级板块 | ❌ |
| | `show hot 801080.SI` | 热力图：该一级板块下的二级板块（明细带一级数据） | ❌ |
| | `show watch` | 只看自选 | ❌ |
| | `show 801080.SI` | 某个板块 / 个股（`--days N` 最近 N 天） | ❌ |
| **debug** | `debug show board` | 一级分标签看「参与度 + 推进成本」表（浏览器） | ❌ 只读本地 |
| | `debug show 801080.SI` / `300308` | 看某个板块 / 个股的参与度 + 成本（控制台） | ❌ |

`show` 只读本地数据库，**不联网**；页面落在 `data/view.html`，可以反复看。

---

## 一级 / 二级板块

```bash
python3 hunter.py update board      # 拉申万一级 31 + 二级 131 + 成分股（乐咕）
python3 hunter.py fetch board       # 本地算这 162 个板块的指标
python3 hunter.py show hot board        # 二级板块热力图（--level 1 转一级）
```

层级直接来自申万分类，**只到二级**（三级有 335 个、粒度太细）。
一级和二级怎么分工，见 model.md §8.1。

### 板块变了会怎样

`update board` 会把板块表**整块快照重建**，所以申万的增/删/改都能反映：

| 场景 | 结果 |
| --- | --- |
| 申万新增板块 | 进 `board_tree`，成分股同步进来 |
| 申万删除/合并板块 | 从树里移除，顺手清掉它的行情/成员/字典 |
| 成分股调整 | `concept_member` 整块替换（含市值权重），日志显示 `(+新增/-移除)` |

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

**分三层**（B / C 层会随模型实现逐步加进来）：

| 层 | 表 | 说明 |
| --- | --- | --- |
| **A · Raw** | `stock_daily` · `board_daily` · `stock_list` · `board_list` · `concept_member` | 原始数据，**只采不算** |
| **B · Feature** | `stock_features_daily` · `board_features_daily` | 算出来的因子 |
| **C · Signal** | `board_stage_daily` · `stock_score_daily` | 最终信号 |

**单位约定**：A 层一律存**原始单位**（金额 = 元、成交量 = 股），换算成「亿 / 万」只发生在展示那一步。
**名字单独放字典表**：名字很少变、行情天天变，混在一起会让每天几千行重复存同样的名字。

---

## 「已经是最新的」怎么判断（交易日钟）

`fetch market` / `fetch board` 会先看本地，已经是最新的就跳过、一个请求都不发。
判据不是「今天几号」，而是：

> **交易日钟 = `stock_daily` 里最大的交易日**（全市场日线覆盖到的最后一天）。

每天跑一次 `fetch market` 之后，这个钟就走到当天；任何标的本地最后一天 ≥ 这个钟，就说明它已经是最新的。
好处是**永远不用判断「今天是哪天、今天是不是交易日」**——那种判断在周末和节假日一定会算错。

---

## 已知边界与坑

### 复权：120 积分没有复权权限

除权除息会让**价格绝对值类**指标失真，所以按指标类型分别处理：

| 指标类型 | 有问题吗 | 怎么做 |
| --- | :-: | --- |
| **收益率类**（5D/20D/60D 收益、RS） | ✅ | 用 `∏(1 + pct_chg) − 1` 算累计收益，**不要**用 `close[t]/close[t−20] − 1` |
| 广度类（上涨占比、强势股占比、跑赢板块） | ✅ | 直接用 `pct_chg` |
| 成交额异常 | ✅ | `amount ÷ MA20(amount)` |
| **价格绝对值类**（`close > MA20`、`close ≥ 20日最高`） | ⚠️ | 第一版先这么做，等拿到复权数据再优化 |

> **不要因为复权问题卡住整个系统** —— 上面三类已经能撑起绝大部分指标。

### 乐咕限流：`update board` 要跑约 27 分钟

乐咕实测约 **6~7 个请求/分钟**，162 个板块逐个拉要 ~27 分钟（所以 `legu_interval` 设成 10 秒）。
它是**低频操作**（申万成分调整是季度级的），而且：

- 今天已同步的板块会跳过；
- 失败/中断后，**同一天重跑会接着补**；
- **明天再跑则全部重新拉最新**；
- 当天想强制全量重拉：`update board --force`。

### 申万官方接口不可用

申万宏源官网的两个接口实测都挂了（JSON API 超时、xls SSL 证书错误），所以板块定义只走乐咕。

---

## 当前进度与后续

| | 状态 |
| --- | --- |
| ✅ **已实现** | 全市场个股日线 · 板块聚合日线 · 个股字典 · 板块字典/层级/成分股 · 自选 · Participation · 推进成本（debug 观察） |
| 🔜 **下一步** | ① 个股状态向量 → 聚合板块 Breadth ② Crowding ③ Stage 判断 |

后续：板块 risk 指标 · 势能的类型 · 需要跨年回测时补 `concept_member_history`（行情不用重采）。

---

## 配置（`config/system.yaml`）

```yaml
tushare_token: "..."     # 必填，否则拉不了全市场个股行情
fetch_interval: 2.0      # tushare 请求间隔（官方限 50 次/分钟）
legu_interval: 10.0      # 乐咕请求间隔（限流严，约 6 个/分钟）
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
│   ├── show.py                show
│   └── debug.py               debug <因子>（临时看因子数字）
├── factor/                    因子层（Participation / Cost，后续 Crowding）
│   ├── participation.py       Participation · 资金参与度（AbsPart + RelPart）
│   └── cost.py                Cost · 推进成本（AbsCost / RelCost）
├── config/
│   ├── config.py              读 yaml 的 Config 单例
│   └── system.yaml            token / 请求间隔
├── datasource/
│   ├── fetch.py               数据获取主接口（全市场 + 本地读写）
│   ├── fetch_tushare.py       Tushare（全市场日线 / 个股字典 / 交易日历）
│   ├── fetch_legu.py          乐咕（申万层级 / 成分股）
│   ├── board_calc.py          板块聚合计算（本地）
│   ├── board_tree.py          板块层级模型
│   ├── store.py               数据库存取层
│   └── base.py                数据源基类 + 工具函数
├── ui/                         展示层（把数据渲染成页面 / 表格）
│   ├── show_data.py           行情展示（图形化页面 / 控制台表格）
│   ├── board_view.py          参与度 + 成本的 debug 页面（复用 show_data 的 CSS/JS）
│   └── chart_view.py          板块热力图（每板块 3 行：参与度/涨跌幅/成本）
├── doc/model.md               ★ 模型本身（交易逻辑，与实现无关）
└── data/                      数据（见上）
```

分层：`hunter.py` → `cli/`（命令与输出）→ `factor/`（因子）→ `datasource/`（数据源与存储）→ `ui/`（展示）→ `config/`。
