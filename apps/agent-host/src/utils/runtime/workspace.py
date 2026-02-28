"""
描述: 工作区初始化助手
主要功能:
    - 创建默认工作区目录结构
    - 生成初始身份配置文件 (SOUL, IDENTITY, MEMORY)
    - 路径解析
"""

from __future__ import annotations

import os
from pathlib import Path


def get_workspace_root() -> Path:
    """
    获取工作区根目录
    
    优先级:
        1. 环境变量 OMNI_WORKSPACE_ROOT
        2. 默认路径 (项目根目录/workspace)
    """
    env_root = os.getenv("OMNI_WORKSPACE_ROOT")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[3] / "workspace"


def ensure_workspace(root: str | Path | None = None) -> Path:
    """
    确保工作区存在

    参数:
        root: 指定根目录 (可选)

    返回:
        Path: 工作区绝对路径
    """
    workspace_root = Path(root) if root else get_workspace_root()
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "users").mkdir(parents=True, exist_ok=True)

    return workspace_root
# endregion
