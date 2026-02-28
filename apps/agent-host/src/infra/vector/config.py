"""
描述: 向量数据库配置加载器
主要功能:
    - 加载 engine/vector.yaml 配置文件
    - 支持环境变量展开 (${VAR} 或 ${VAR:-default})
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml


_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


# region 环境变量展开与加载
def load_vector_config(config_path: str = "config/engine/vector.yaml") -> dict[str, Any]:
    """
    加载并解析向量配置
    
    参数:
        config_path: 配置文件路径
        
    返回:
        解析后的配置字典 (已处理环境变量)
    """
    path = Path(config_path)
    if not path.exists() and config_path == "config/engine/vector.yaml":
        path = Path("config/vector.yaml")
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _expand_env(data)


def _expand_env(value: Any) -> Any:
    """递归展开配置中的环境变量"""
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            expr = match.group(1)
            if ":-" in expr:
                key, default = expr.split(":-", 1)
                return os.getenv(key, default)
            return os.getenv(expr, "")

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(val) for key, val in value.items()}
    return value
# endregion
