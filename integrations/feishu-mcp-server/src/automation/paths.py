"""
描述: 自动化运行路径解析工具。
主要功能:
    - 以 CONFIG_PATH 所在目录作为相对路径基准
    - 提供运行时路径统一解析逻辑
"""

from __future__ import annotations

import os
from pathlib import Path


def resolve_config_base_dir() -> Path:
    config_path_text = str(os.getenv("CONFIG_PATH", "config.yaml") or "config.yaml").strip() or "config.yaml"
    config_path = Path(config_path_text)
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    return config_path.resolve().parent


def resolve_runtime_path(
    raw_path: str | Path,
    *,
    base_dir: Path | None = None,
    default_parent: Path | None = None,
) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path

    resolved_base = base_dir if base_dir is not None else resolve_config_base_dir()
    if path.parent != Path("."):
        return (resolved_base / path).resolve()

    if default_parent is not None:
        return (default_parent / path.name).resolve()

    return (resolved_base / path).resolve()
