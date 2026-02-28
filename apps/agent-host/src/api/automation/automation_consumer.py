"""
描述: 自动化消费骨架（队列 + run log）
主要功能:
    - 接收 event_router 入队的标准 payload
    - 受 startup_mode 保护地消费并落盘 run_logs.jsonl
    - 输出结构化日志与消费指标
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
from typing import Any

from src.api.automation_rules import (
    AutomationActionExecutor,
    AutomationRule,
    AutomationRuleLoader,
    AutomationRuleMatcher,
    evaluate_rules,
    resolve_default_automation_rules_path,
    resolve_default_dead_letter_path,
)
from src.utils.metrics import record_automation_consumed
from src.utils.workspace import get_workspace_root
from src.config import get_settings
from src.mcp.client import MCPClient
from src.utils.feishu_api import send_message


def _read_bool_env(key: str, default: bool) -> bool:
    """
    从环境变量中读取布尔值

    功能:
        - 获取环境变量的值
        - 如果值为 None，返回默认值
        - 否则，检查值是否在 {"1", "true", "yes", "on"} 中，返回相应的布尔值
    """
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_run_log_path() -> Path:
    """
    解析运行日志文件路径

    功能:
        - 从环境变量中获取配置的路径
        - 如果路径为空，使用默认路径
        - 返回绝对路径或相对于当前工作目录的路径
    """
    configured = str(os.getenv("AUTOMATION_RUN_LOG_FILE", "")).strip()
    if configured:
        path = Path(configured)
        if path.is_absolute():
            return path
        return Path.cwd() / path
    return get_workspace_root() / "automation_data" / "run_logs.jsonl"


def _resolve_rules_path() -> Path:
    """
    解析自动化规则文件路径

    功能:
        - 从环境变量中获取配置的路径
        - 如果路径为空，使用默认路径
        - 返回绝对路径或相对于当前工作目录的路径
    """
    configured = str(os.getenv("AUTOMATION_RULES_FILE", "")).strip()
    if configured:
        path = Path(configured)
        if path.is_absolute():
            return path
        return Path.cwd() / path
    return resolve_default_automation_rules_path(get_workspace_root())


def _resolve_dead_letter_path() -> Path:
    """
    解析死信队列文件路径

    功能:
        - 从环境变量中获取配置的路径
        - 如果路径为空，使用默认路径
        - 返回绝对路径或相对于当前工作目录的路径
    """
    configured = str(os.getenv("AUTOMATION_DEAD_LETTER_FILE", "")).strip()
    if configured:
        path = Path(configured)
        if path.is_absolute():
            return path
        return Path.cwd() / path
    return resolve_default_dead_letter_path(get_workspace_root())


@dataclass
class AutomationStartupGate:
    """
    自动化启动门

    功能:
        - 管理自动化启动模式和基线准备状态
        - 提供方法判断是否应阻塞自动化消费
    """
    startup_mode: str = "auto"
    baseline_ready: bool = False

    def should_block(self) -> bool:
        """
        判断是否应阻塞自动化消费

        功能:
            - 根据启动模式和基线准备状态决定是否阻塞
        """
        normalized_mode = self.startup_mode.strip().lower()
        if normalized_mode != "auto":
            return False
        return not self.baseline_ready


class InMemoryAutomationQueue:
    """
    内存自动化队列

    功能:
        - 提供入队、出队、查看队列大小等操作
    """
    def __init__(self) -> None:
        self._items: deque[dict[str, Any]] = deque()

    def enqueue(self, payload: dict[str, Any]) -> None:
        """
        将 payload 入队

        功能:
            - 将 payload 添加到队列末尾
        """
        self._items.append(dict(payload))

    def pop_left(self) -> dict[str, Any] | None:
        """
        从队列头部弹出 payload

        功能:
            - 如果队列为空，返回 None
            - 否则，返回并移除队列头部的 payload
        """
        if not self._items:
            return None
        return self._items.popleft()

    def push_front(self, payload: dict[str, Any]) -> None:
        """
        将 payload 插入队列头部

        功能:
            - 将 payload 添加到队列头部
        """
        self._items.appendleft(dict(payload))

    def size(self) -> int:
        """
        获取队列大小

        功能:
            - 返回队列中 payload 的数量
        """
        return len(self._items)


class AutomationConsumer:
    """
    自动化消费者

    功能:
        - 消费队列中的 payload
        - 根据规则执行自动化操作
        - 记录消费日志和指标
    """
    def __init__(
        self,
        run_log_path: Path,
        startup_gate: AutomationStartupGate,
        rule_set: list[AutomationRule] | None = None,
        rule_matcher: AutomationRuleMatcher | None = None,
        action_executor: AutomationActionExecutor | Any | None = None,
        automation_enabled: bool | None = None,
    ) -> None:
        self._run_log_path = run_log_path
        self._startup_gate = startup_gate
        self._logger = logging.getLogger(__name__)
        if rule_set is None:
            loader = AutomationRuleLoader(logger=self._logger)
            loaded = loader.load(_resolve_rules_path())
            self._rule_set = loaded.rules
        else:
            self._rule_set = list(rule_set)
        self._rule_matcher = rule_matcher or AutomationRuleMatcher()
        settings = get_settings()
        mcp_client = MCPClient(settings)

        async def _send_message_action(**kwargs: Any) -> None:
            receive_id = str(kwargs.get("receive_id") or "").strip()
            if not receive_id:
                return
            await send_message(
                settings=settings,
                receive_id=receive_id,
                msg_type=str(kwargs.get("msg_type") or "text"),
                content=kwargs.get("content") if isinstance(kwargs.get("content"), dict) else {"text": ""},
                receive_id_type=str(kwargs.get("receive_id_type") or "chat_id"),
                credential_source="org_b",
            )

        async def _bitable_update_action(**kwargs: Any) -> None:
            await mcp_client.call_tool(
                "feishu.v1.bitable.record.update",
                {
                    "table_id": str(kwargs.get("table_id") or ""),
                    "record_id": str(kwargs.get("record_id") or ""),
                    "fields": kwargs.get("fields") if isinstance(kwargs.get("fields"), dict) else {},
                },
            )

        self._action_executor = action_executor or AutomationActionExecutor(
            dead_letter_path=_resolve_dead_letter_path(),
            dry_run=_read_bool_env("AUTOMATION_DRY_RUN", True),
            status_write_enabled=_read_bool_env("AUTOMATION_STATUS_WRITE_ENABLED", False),
            send_message_fn=_send_message_action,
            bitable_update_fn=_bitable_update_action,
        )
        self._automation_enabled = _read_bool_env("AUTOMATION_ENABLED", True) if automation_enabled is None else automation_enabled

    def consume_available(self, queue: InMemoryAutomationQueue, max_items: int = 100) -> int:
        """
        消费队列中的可用 payload

        功能:
            - 从队列中弹出最多 max_items 个 payload
            - 消费每个 payload
            - 如果遇到启动保护阻塞，将 payload 重新入队并停止消费
        """
        processed = 0
        while processed < max_items:
            payload = queue.pop_left()
            if payload is None:
                break

            status = self._consume_single(payload)
            if status == "blocked_startup_protection":
                queue.push_front(payload)
                break
            processed += 1
        return processed

    def _consume_single(self, payload: dict[str, Any]) -> str:
        """
        消费单个 payload

        功能:
            - 检查是否应阻塞消费
            - 记录消费日志
            - 执行自动化操作
        """
        event_type = str(payload.get("event_type") or "unknown")
        event_id = str(payload.get("event_id") or "")
        if self._startup_gate.should_block():
            record_automation_consumed(event_type, "blocked_startup_protection")
            self._logger.info(
                "automation consume blocked by startup protection",
                extra={
                    "event_code": "automation.consumer.blocked_startup_protection",
                    "event_id": event_id,
                    "event_type": event_type,
                    "startup_mode": self._startup_gate.startup_mode,
                    "baseline_ready": self._startup_gate.baseline_ready,
                },
            )
            return "blocked_startup_protection"

        record = {
            "event_id": event_id,
            "event_type": event_type,
            "app_token": str(payload.get("app_token") or ""),
            "table_id": str(payload.get("table_id") or ""),
            "record_id": str(payload.get("record_id") or ""),
            "changed_fields": payload.get("changed_fields") or [],
            "occurred_at": str(payload.get("occurred_at") or ""),
            "status": "consumed",
        }

        self._logger.info(
            "automation consume started",
            extra={
                "event_code": "automation.consumer.consume_started",
                "event_id": event_id,
                "event_type": event_type,
            },
        )

        try:
            self._run_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._run_log_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
            record_automation_consumed(event_type, "consumed")
            self._logger.info(
                "automation consume logged",
                extra={
                    "event_code": "automation.consumer.logged",
                    "event_id": event_id,
                    "event_type": event_type,
                    "run_log_path": str(self._run_log_path),
                },
            )
            self._run_automation(payload)
            return "consumed"
        except Exception:
            record_automation_consumed(event_type, "failed")
            self._logger.exception(
                "automation consume failed",
                extra={
                    "event_code": "automation.consumer.failed",
                    "event_id": event_id,
                    "event_type": event_type,
                    "run_log_path": str(self._run_log_path),
                },
            )
            return "failed"

    def _run_automation(self, payload: dict[str, Any]) -> None:
        """
        执行自动化操作

        功能:
            - 根据规则匹配和执行自动化操作
        """
        if not self._automation_enabled:
            self._logger.info(
                "automation rules skipped by switch",
                extra={
                    "event_code": "automation.consumer.rules_disabled",
                    "event_id": str(payload.get("event_id") or ""),
                },
            )
            return

        try:
            matched_rules = evaluate_rules(self._rule_set, payload, self._rule_matcher, logger=self._logger)
            for rule in matched_rules:
                try:
                    self._action_executor.execute_rule(rule, payload)
                except Exception:
                    self._logger.exception(
                        "automation rule execute failed",
                        extra={
                            "event_code": "automation.rule.execute_failed",
                            "rule_id": rule.rule_id,
                            "event_id": str(payload.get("event_id") or ""),
                        },
                    )
        except Exception:
            self._logger.exception(
                "automation rule evaluation failed",
                extra={
                    "event_code": "automation.rule.evaluate_failed",
                    "event_id": str(payload.get("event_id") or ""),
                },
            )


class QueueAutomationEnqueuer:
    """
    队列自动化入队器

    功能:
        - 将事件 payload 入队
        - 消费队列中的 payload
    """
    def __init__(self, queue: InMemoryAutomationQueue, consumer: AutomationConsumer) -> None:
        self._queue = queue
        self._consumer = consumer
        self._logger = logging.getLogger(__name__)

    def enqueue_record_changed(self, event_payload: dict[str, Any]) -> bool:
        """
        将记录变更事件入队

        功能:
            - 将事件 payload 入队
            - 消费队列中的 payload
        """
        self._queue.enqueue(event_payload)
        self._logger.info(
            "automation payload enqueued",
            extra={
                "event_code": "automation.enqueuer.enqueued",
                "event_id": str(event_payload.get("event_id") or ""),
                "event_type": str(event_payload.get("event_type") or ""),
                "queue_size": self._queue.size(),
            },
        )
        self._consumer.consume_available(self._queue)
        return True


def create_default_automation_enqueuer() -> QueueAutomationEnqueuer:
    """
    创建默认的自动化入队器

    功能:
        - 初始化启动门、队列和消费者
        - 返回 QueueAutomationEnqueuer 实例
    """
    startup_mode = str(os.getenv("AUTOMATION_STARTUP_MODE", "auto") or "auto")
    baseline_ready = _read_bool_env("AUTOMATION_BASELINE_READY", False)
    startup_gate = AutomationStartupGate(startup_mode=startup_mode, baseline_ready=baseline_ready)
    queue = InMemoryAutomationQueue()
    consumer = AutomationConsumer(run_log_path=_resolve_run_log_path(), startup_gate=startup_gate)
    return QueueAutomationEnqueuer(queue=queue, consumer=consumer)
