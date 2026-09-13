#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.py —— 配置单例。

读取 config/ 目录下的所有 yaml 文件；**每一个配置文件对外提供一个取参接口**：

    from config.config import config        # 单例，全局只有一个实例

    config.system.get("fetch_type")         # -> "direct"
    config.system.get("没有这个键", "默认值")  # -> "默认值"
    config.system.all()                     # -> {"fetch_type": "direct"} 整个文件
    config.get("system", "fetch_type")      # 另一种等价写法
    config.sections()                       # -> ["system"] 当前加载了哪些配置

约定：
    · config/ 下新增一个 xxx.yaml，就自动多出一个 config.xxx 接口，不用改代码；
    · key 支持点号取嵌套值，例如 config.strategy.get("phase.window")；
    · 单例：Config() 永远返回同一个实例；改了 yaml 后调用 config.reload() 重新读取；
    · 取不到时返回 default（默认 None），不会抛错；配置文件本身不存在才会报错。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# config/ 目录（就是本文件所在目录）
CONFIG_DIR = Path(__file__).resolve().parent

_MISSING = object()


class Section:
    """一个配置文件（如 system.yaml）的取参接口。"""

    def __init__(self, name: str, path: Path, data: dict):
        self.name = name          # 配置名，即文件名去掉后缀，如 "system"
        self.path = path          # 文件路径
        self._data = data         # 解析后的字典

    def get(self, key: str, default: Any = None) -> Any:
        """取一个参数；key 支持点号路径（如 "phase.window"）；取不到返回 default。"""
        node: Any = self._data
        for part in str(key).split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def all(self) -> dict:
        """整个配置文件的字典（顶层副本）。"""
        return dict(self._data)

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __contains__(self, key: str) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    def __repr__(self) -> str:
        return f"<Section {self.name} ({self.path.name})>"


class Config:
    """配置单例：读取 config/ 下所有 yaml，每个文件一个 Section 接口。"""

    _instance: "Config | None" = None

    def __new__(cls) -> "Config":
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._load()
            cls._instance = inst
        return cls._instance

    # -- 加载 -------------------------------------------------------------
    def _load(self) -> None:
        self._dir = CONFIG_DIR
        self._sections: dict[str, Section] = {}
        for path in sorted(self._dir.glob("*.y*ml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ValueError(
                    f"{path.name} 的顶层必须是「键: 值」形式，实际解析成 {type(data).__name__}")
            self._sections[path.stem] = Section(path.stem, path, data)

    def reload(self) -> "Config":
        """重新读取 config/ 下所有文件（改完 yaml 后调用）。"""
        self._load()
        return self

    # -- 取用 -------------------------------------------------------------
    def section(self, name: str) -> Section:
        """按配置名取接口，如 config.section("system")。"""
        if name not in self._sections:
            raise KeyError(f"没有 {name!r} 这个配置文件；当前可用：{self.sections()}")
        return self._sections[name]

    def get(self, section: str, key: str | None = None, default: Any = None) -> Any:
        """config.get("system", "fetch_type")；key 省略时返回该文件的整个字典。"""
        sec = self.section(section)
        return sec.all() if key is None else sec.get(key, default)

    def sections(self) -> list[str]:
        """当前加载到的配置名（文件名去后缀）。"""
        return sorted(self._sections)

    def __getattr__(self, name: str) -> Section:
        # 只在常规属性找不到时才触发：让 config.system / config.strategy 直接可用
        sections = self.__dict__.get("_sections") or {}
        if name in sections:
            return sections[name]
        raise AttributeError(f"没有 {name!r} 这个配置文件；当前可用：{sorted(sections)}")

    def __repr__(self) -> str:
        return f"<Config {self._dir} sections={self.sections()}>"


# 全局唯一实例：from config.config import config
config = Config()
