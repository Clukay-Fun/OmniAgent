"""
描述: Planner Prompt 构建器。
主要功能:
    - 加载目录下所有场景规则文件
    - 根据规则构建 Planner system prompt
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


# region 规则文件加载
def load_scenario_rules(scenarios_dir: str) -> list[dict[str, Any]]:
    """
    加载目录下所有场景规则文件。

    功能:
        - 检查目录是否存在且为目录
        - 遍历目录下所有 .yaml 文件
        - 加载每个文件的规则并添加到规则列表中
    """
    path = Path(scenarios_dir)
    if not path.exists() or not path.is_dir():
        return []

    rules: list[dict[str, Any]] = []
    for file in sorted(path.glob("*.yaml")):
        try:
            with file.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            continue

        file_rules = data.get("rules") if isinstance(data, dict) else None
        if isinstance(file_rules, list):
            for item in file_rules:
                if isinstance(item, dict):
                    rules.append(item)
    return rules
# endregion


# region Planner system prompt 构建
def build_planner_system_prompt(rules: list[dict[str, Any]]) -> str:
    """
    根据规则构建 Planner system prompt。

    功能:
        - 定义 prompt 的头部信息
        - 根据传入的规则生成规则行
        - 如果没有传入规则，则使用默认规则
        - 定义输出的 JSON Schema
        - 将所有部分组合成最终的 prompt
    """
    header = (
        "你是 OmniAgent 的意图规划器。"
        "你的任务是把用户输入转成结构化 JSON: intent/tool/params/confidence。"
        "只输出 JSON，不要输出解释。"
    )

    default_rules = [
        "- 查所有/全部案件 -> intent=query_all, tool=search",
        "- 按视图/当前视图/仅视图 -> intent=query_view, tool=search",
        "- 我的案件/我负责 -> intent=query_my_cases, tool=search_person",
        "- 查案号/项目ID -> intent=query_exact, tool=search_exact",
        "- 今天/本周 + 开庭 -> intent=query_date_range, tool=search_date_range",
        "- 张三在中院本周 -> intent=query_advanced, tool=search_advanced",
        "- 新建/创建 -> intent=create_record, tool=record.create",
        "- 修改/更新 -> intent=update_record, tool=record.update",
        "- 结案/终本/关闭案件 -> intent=close_record, tool=record.close, params.close_semantic in {default,enforcement_end}",
        "- 删除 -> intent=delete_record, tool=record.delete",
        "- 提醒我 -> intent=create_reminder, tool=reminder.create",
        "- 我有哪些提醒 -> intent=list_reminders, tool=reminder.list",
    ]

    rule_lines: list[str] = []
    if rules:
        for idx, rule in enumerate(rules, start=1):
            pattern = rule.get("pattern") or rule.get("trigger") or rule.get("name") or f"rule_{idx}"
            intent = rule.get("intent") or ""
            tool = rule.get("tool") or ""
            params = rule.get("params") or {}
            note = rule.get("note") or ""
            line = f"- {pattern} -> intent={intent}, tool={tool}, params={params}"
            if note:
                line += f"; note={note}"
            rule_lines.append(line)
    else:
        rule_lines = default_rules

    schema_text = (
        '{"intent":"...","tool":"...","params":{},"confidence":0.0,"clarify_question":""}；若 intent=close_record，params 必须包含 close_semantic，且仅可为 default 或 enforcement_end'
    )

    return "\n\n".join(
        [
            header,
            "规则：\n" + "\n".join(rule_lines),
            "输出 JSON Schema：\n" + schema_text,
            "要求：confidence 在 [0,1]；若无法确定 intent，输出 intent=clarify_needed 且给出 clarify_question。",
        ]
    )
# endregion
