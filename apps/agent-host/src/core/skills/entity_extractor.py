from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class EntityExtractor:
    """
    基于大模型的实体和意图提取器。
    用于重构基于硬编码正则表达式的字段提取逻辑，提供更加动态和高容错的解析能力。
    """

    def __init__(self, llm_client: Any) -> None:
        self._llm = llm_client

    async def extract_exact_match_field(
        self, query: str, available_fields: Optional[list[str]] = None
    ) -> Optional[dict[str, str]]:
        """
        从用户输入中提取精确匹配过滤的依据（如案号、项目ID、身份证号等）。
        如果未提供 available_fields，则默认为 ["案号", "项目ID"]。

        参数:
            query: 用户输入文本
            available_fields: 表格可用的字段列表

        返回:
            包含 "field" 和 "value" 的字典，如果未找到则返回 None。
        """
        if not self._llm:
            return None

        if not available_fields:
            available_fields = ["案号", "项目ID"]

        system_prompt = (
            "你是一个多维表格数据查询实体提取器。\n"
            "你的任务是从用户的查询语句中，提取出用于精确定位一条记录的关键标识（如案号、项目ID、特定编号）。\n"
            f"以下是当前表格中可用于精确匹配的目标字段名：{', '.join(available_fields)}。\n"
            "你需要：\n"
            "1. 判断用户想要通过哪个字段进行查询。\n"
            "2. 提取出对应的确切值（去掉末尾无意义的描述词如'的案子'、'的项目'）。\n"
            "3. 只返回 JSON 格式，不要返回其他任何解释：\n"
            '   如果找到了，返回 `{"field": "字段名", "value": "提取的值"}`\n'
            '   如果没有找到任何精确匹配标识，返回 `{}`。'
        )

        try:
            result = await self._llm.chat_json(query, system=system_prompt)
            if not result or "field" not in result or "value" not in result:
                return None
            
            field = str(result["field"]).strip()
            value = str(result["value"]).strip()
            if field and value:
                return {"field": field, "value": value}
            return None
        except Exception as exc:
            logger.warning("EntityExtractor.extract_exact_match_field failed: %s", exc)
            return None

    async def parse_create_fields(
        self,
        query: str,
        required_fields: Optional[list[str]] = None,
        available_fields: Optional[list[Any]] = None,
    ) -> dict[str, Any]:
        """
        从用户关于“新建记录”的描述中提取结构化的字段和值。

        参数:
            query: 用户输入（如“新建案件，案号123，当事人张三，主办律师李四”）
            required_fields: 必须提取的必填字段列表
            available_fields: 表格的完整 Schema 字段定义信息 (用于提供上下文)

        返回:
            提取的字段字典，形式为 {"字段A": "值A", "字段B": "值B"}。
        """
        if not self._llm:
            return {}
            
        fields_context = ""
        if available_fields:
            field_names: list[str] = []
            for item in available_fields:
                if isinstance(item, str):
                    name = item.strip()
                elif isinstance(item, dict):
                    name = str(item.get("field_name") or item.get("name") or "").strip()
                else:
                    name = ""
                if name:
                    field_names.append(name)
            fields_context = f"当前表格存在以下字段: {', '.join(field_names)}。\n"
        elif required_fields:
            fields_context = f"当前场景的常见字段包括: {', '.join(required_fields)}。\n"

        system_prompt = (
            "你是一个多维表格数据录入助手。\n"
            "用户的目的是新建一条记录，你的任务是从用户的自然语言描述中提取出结构化的字段和值。\n"
            f"{fields_context}"
            "要求：\n"
            "1. 提取出所有提供的字段信息。\n"
            "2. 映射用户的别名到最接近的标准字段名（如把'当事人'映射到'委托人'，'律师'映射到'主办律师'等）。\n"
            "3. 不要遗漏用户提供的任何有价值的信息。\n"
            "4. 只返回一个干净的 JSON 对象，键为字段名，值为提取内容。如果没有提取到内容，返回空 `{{}}`。"
        )

        try:
            result = await self._llm.chat_json(query, system=system_prompt)
            if not isinstance(result, dict):
                return {}
            return {str(k).strip(): v for k, v in result.items() if str(k).strip() and v is not None and str(v).strip()}
        except Exception as exc:
            logger.warning("EntityExtractor.parse_create_fields failed: %s", exc)
            return {}

    async def parse_update_fields(
        self, query: str, available_fields: Optional[list[str]] = None
    ) -> dict[str, Any]:
        """
        提取用户想要更新的字段及目标值。

        示例: "把张三的案件的主办律师改成李四" -> {"主办律师": "李四"}
        """
        if not self._llm:
            return {}

        fields_context = ""
        if available_fields:
            fields_context = f"参考表格候选字段：{', '.join(available_fields)}。\n"

        system_prompt = (
            "你是一个多维表格数据更新助手。\n"
            "用户的目的修改记录的某个（或某几个）字段。\n"
            f"{fields_context}"
            "要求：\n"
            "1. 只提取用户明确要求**修改**的目标字段名和**目标新值**。\n"
            "2. 不要提取用于定位该记录的条件（如'张三的案件'中的'张三'是定位条件，不是要写入的值）。\n"
            "3. 只返回一个纯 JSON 对象，键为要更新的字段名，值为新值。例如 `{\"主办律师\": \"李四\", \"案件状态\": \"已结案\"}`。\n"
            "如果没有找到明确的更新意图，返回 `{}`。"
        )

        try:
            result = await self._llm.chat_json(query, system=system_prompt)
            if not isinstance(result, dict):
                return {}
            return {str(k).strip(): v for k, v in result.items() if str(k).strip() and v is not None and str(v).strip()}
        except Exception as exc:
            logger.warning("EntityExtractor.parse_update_fields failed: %s", exc)
            return {}
