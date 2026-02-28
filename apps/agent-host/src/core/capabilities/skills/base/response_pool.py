"""
描述: 回复模板随机池
主要功能:
    - 从 config/messages/zh-CN/responses.yaml 加载所有回复模板
    - 提供 pick(key) 方法随机选取一条回复
    - 模块级单例，所有 Skill 共享同一份数据
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ============================================
# region 默认回复（YAML 缺失时兜底）
# ============================================
DEFAULT_RESPONSES: dict[str, list[str]] = {
    "greeting": ["你好！有什么可以帮你的？"],
    "greeting_morning": ["早上好！今天有什么需要处理的吗？"],
    "greeting_evening": ["晚上好！还有什么需要处理的吗？"],
    "thanks": ["不客气～有事随时找我"],
    "goodbye": ["好的，回见！有事随时找我～"],
    "out_of_scope": ["这个超出我的能力范围啦，案件相关的事可以随时问我～"],
    "help": ["我可以帮你查案件、管提醒、看日程。有什么需要帮忙的直接说～"],
    "result_opener": ["✅ 查到啦~ "],
    "empty_result": ["未找到相关记录，请尝试调整查询条件。"],
    "create_success": ["✅ 已经帮你创建好了。"],
    "update_success": ["✅ 已经帮你更新了。"],
    "delete_success": ["✅ 已经帮你删除了。"],
    "error": ["抱歉，处理时遇到了点问题 😅 稍后再试试？"],
    "timeout": ["思考超时了，换个简单点的问法试试？"],
}
# endregion
# ============================================


# ============================================
# region ResponsePool 单例
# ============================================
class ResponsePool:
    """
    回复模板随机池（单例）

    用法:
        from src.core.capabilities.skills.base.response_pool import pool
        reply = pool.pick("create_success")
    """

    _instance: ResponsePool | None = None
    _loaded: bool = False

    def __new__(cls) -> ResponsePool:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if self._loaded:
            return
        self._data: dict[str, list[str]] = self._load()
        self._loaded = True
        logger.info("ResponsePool loaded: %d categories", len(self._data))

    # ------------------------------------------------
    # 公开方法
    # ------------------------------------------------
    def pick(self, key: str, fallback: str = "") -> str:
        """随机选取一条回复；key 不存在时返回 fallback"""
        pool = self._data.get(key)
        if pool:
            return random.choice(pool)
        return fallback

    def get_list(self, key: str) -> list[str]:
        """获取某个 key 的完整回复列表"""
        return self._data.get(key, [])

    def reload(self) -> None:
        """热重载（配合 hot_reload 使用）"""
        self._data = self._load()
        logger.info("ResponsePool reloaded: %d categories", len(self._data))

    # ------------------------------------------------
    # 私有方法
    # ------------------------------------------------
    def _load(self) -> dict[str, list[str]]:
        """从消息配置加载回复模板，缺失或出错时用默认值"""
        result = dict(DEFAULT_RESPONSES)
        path = Path("config/messages/zh-CN/responses.yaml")
        if not path.exists():
            path = Path("config/responses.yaml")
        if not path.exists():
            logger.warning("responses.yaml not found, using defaults")
            return result
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for key, values in data.items():
                if isinstance(values, list) and values:
                    result[key] = values
        except Exception as exc:
            logger.warning("Failed to load responses.yaml: %s", exc)
        return result
# endregion
# ============================================


# ============================================
# region 模块级单例
# ============================================
pool = ResponsePool()
# endregion
# ============================================
