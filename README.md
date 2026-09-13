# one_piece
猎人系统，希望能够通过板块势能找寻到资金留下的脚印，并分别资金留下的脚印从而追寻资金的方向

# 整体架构
                    ┌─────────────┐
                    │    Data     │
                    └──────┬──────┘
                           ↓
              ┌────────────────────────┐
              │       4 Dimensions     │
              │                        │
              │ Flow                   │
              │ Breadth                │
              │ Efficiency             │
              │ Crowding               │
              └────────────┬───────────┘
                           ↓
                  ┌────────────────┐
                  │  Energy Engine │
                  └───────┬────────┘
                          ↓
                 ┌──────────────────────┐
                 │  Phase Engine        │
                 │                      │
                 │ ① → ② → ③ → ④ → ⑤│
                 └────────┬─────────────┘
                          ↓
                ┌────────────────────┐
                │ Transition Engine  │
                │                    │
                │ 当前 → 下一阶段      │
                └─────────┬──────────┘
                          ↓
                 ┌────────────────┐
                 │ Decision Engine│
                 └───────┬────────┘
                         ↓
               ┌───────────────────┐
               │ Visualization     │
               │                   │
               │ 板块势能地图        │
               └───────────────────┘

# 目录结构

├── README.md
├── config/
│   ├── system.yaml
│   ├── data.yaml
│   └── strategy.yaml
│
├── data/
│   ├── raw/                    # 原始数据
│   ├── processed/              # 清洗后的数据
│   ├── features/               # 计算后的指标
│   └── snapshots/              # 每日/每次运行的结果快照
│
├── datasource/
│   ├── sector.py               # 板块数据获取
│   ├── stock.py                # 成分股数据获取
│   ├── market.py               # 大盘数据获取
│   └── provider.py             # 数据源统一接口
│
│
├── indicators/
│   ├── flow.py                 # Flow 指标
│   ├── breadth.py              # Breadth 指标
│   ├── efficiency.py           # Efficiency 指标
│   ├── crowding.py             # Crowding 指标
│   └── common.py               # 通用技术指标
│
├── strategy/
│   ├── base.py                 # 策略基类
│   ├── phase.py                # 五阶段判断
│   ├── flow_strategy.py
│   ├── breadth_strategy.py
│   ├── efficiency_strategy.py
│   ├── crowding_strategy.py
│   └── custom/                 # 个人盘感策略
│
├── scoring/
│   ├── flow_score.py
│   ├── breadth_score.py
│   ├── efficiency_score.py
│   ├── crowding_score.py
│   ├── energy_score.py
│   └── phase_score.py
│
├── decision/
│   ├── phase_classifier.py     # 当前阶段
│   ├── transition.py           # 阶段移动方向
│   └── confidence.py           # 判断置信度
│
├── visualization/              # 图形化展示
│
├── application/
│   ├── analyze_sector.py       # 分析一个板块
│   ├── analyze_batch.py        # 分析多个板块
│   └── daily_run.py            # 每日运行
│
└── tests/
    ├── datasource/
    ├── indicators/
    ├── strategy/
    ├── scoring/
    └── decision/
