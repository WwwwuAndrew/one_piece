#!/usr/bin/env bash
# hunter-app.sh —— 双击就能起 app（不用敲命令）。
#
# 它只做一件事：用项目自带的虚拟环境跑 `hunter.py`（不带参数 = 起 app）。
# 虚拟环境不在 .venv 的话改下面这行；想换端口：./hunter-app.sh --port 8899

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "❌ 没找到 $PY —— 先按 README 建虚拟环境：python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  read -r -p "按回车关闭…" _
  exit 1
fi

exec "$PY" hunter.py app "$@"
