"""
描述: 汇总技能 (SummarySkill)
主要功能:
    - 结构化查询结果汇总
    - 案件记录/文档搜索结果聚合
    - 调用 LLM 生成自然语言摘要
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.capabilities.skills.base.base import BaseSkill
from src.core.understanding.router import SkillContext, SkillResult

logger = logging.getLogger(__name__)


# region 汇总技能实现
class SummarySkill(BaseSkill):
    """
    汇总技能核心类

    功能:
        - 基于上下文历史生成内容摘要
        - 支持普通模式与扩展字段模式
        - 智能分组与自然语言生成
    """
    
    name: str = "SummarySkill"
    description: str = "总结、汇总、概括查询结果"

    # 默认输出字段
    DEFAULT_FIELDS = ["案号", "案由", "当事人", "开庭日", "主办律师"]
    
    # 扩展输出字段
    EXTENDED_FIELDS = ["审理法院", "案件状态", "程序阶段"]
    
    # 触发扩展的关键词
    EXTEND_TRIGGERS = ["详细", "完整", "全部", "所有"]

    def __init__(
        self,
        llm_client: Any = None,
        skills_config: dict[str, Any] | None = None,
    ) -> None:
        """
        初始化技能

        参数:
            llm_client: LLM 客户端实例
            skills_config: 技能配置
        """
        self._llm = llm_client
        self._config = skills_config or {}
        
        # 从配置加载字段定义
        summary_cfg = self._config.get("summary", {})
        if not summary_cfg:
            summary_cfg = self._config.get("skills", {}).get("summary", {})
        intent_cfg = self._config.get("intent", {})
        self._default_fields = summary_cfg.get("default_fields", self.DEFAULT_FIELDS)
        self._extended_fields = summary_cfg.get("extended_fields", self.EXTENDED_FIELDS)
        self._extend_triggers = summary_cfg.get("extend_triggers", self.EXTEND_TRIGGERS)
        self._llm_timeout = float(
            summary_cfg.get("llm_timeout", intent_cfg.get("llm_timeout", 10))
        )

    async def execute(self, context: SkillContext) -> SkillResult:
        """
        执行汇总逻辑

        参数:
            context: 上下文 (必须包含 last_result)

        返回:
            SkillResult: 汇总结果消息
        """
        query = context.query
        last_result = context.last_result
        
        # 检查是否有数据可供汇总
        if not last_result:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="没有可汇总的数据",
                reply_text='请先查询案件，再进行汇总。试试问我"本周有什么庭"吧！',
            )
        
        # 获取记录列表
        records = last_result.get("records", [])
        documents = last_result.get("documents", [])
        
        if not records and not documents:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="查询结果为空，无法汇总",
                reply_text="上次查询没有找到记录，无法进行汇总。",
            )
        
        # 判断是否需要扩展字段
        use_extended = self._should_use_extended(query)
        
        # 生成汇总
        if records:
            soul_prompt = context.extra.get("soul_prompt", "")
            user_memory = context.extra.get("user_memory", "")
            shared_memory = context.extra.get("shared_memory", "")
            return await self._summarize_cases(
                records,
                query,
                use_extended,
                soul_prompt=soul_prompt,
                user_memory=user_memory,
                shared_memory=shared_memory,
            )
        else:
            return await self._summarize_docs(documents, query)

    def _should_use_extended(self, query: str) -> bool:
        """检查是否需要扩展字段"""
        return any(trigger in query for trigger in self._extend_triggers)

    async def _summarize_cases(
        self,
        records: list[dict[str, Any]],
        query: str,
        use_extended: bool,
        soul_prompt: str = "",
        user_memory: str = "",
        shared_memory: str = "",
    ) -> SkillResult:
        """
        汇总多维表格案件记录

        参数:
            records: 案件记录列表
            query: 用户提问
            use_extended: 启用扩展字段
        """
        # 选择字段
        fields_to_show = self._default_fields.copy()
        if use_extended:
            fields_to_show.extend(self._extended_fields)
        
        # 提取数据
        summary_data = []
        for record in records:
            fields = record.get("fields_text") or record.get("fields", {})
            item = {}
            for field_name in fields_to_show:
                # 字段名映射（处理不同命名）
                value = self._get_field_value(fields, field_name)
                if value:
                    item[field_name] = value
            if item:
                summary_data.append(item)
        
        # 生成汇总文本
        count = len(summary_data)
        
        if self._llm:
            # 使用 LLM 生成自然语言摘要
            summary_text = await self._llm_summarize(
                summary_data,
                query,
                soul_prompt=soul_prompt,
                user_memory=user_memory,
                shared_memory=shared_memory,
            )
        else:
            # 简单模板汇总
            summary_text = self._template_summarize(summary_data, fields_to_show)
        
        title = f"📊 案件汇总（共 {count} 条）"
        if use_extended:
            title += "【详细版】"
        
        reply_text = f"{title}\n\n{summary_text}"
        
        return SkillResult(
            success=True,
            skill_name=self.name,
            data={"summary": summary_data, "total": count},
            message=f"已汇总 {count} 条案件",
            reply_type="text",
            reply_text=reply_text,
        )

    def _get_field_value(self, fields: dict[str, Any], field_name: str) -> str | None:
        """从字段字典中提取值 (支持别名映射)"""
        # 直接匹配
        if field_name in fields:
            return str(fields[field_name])
        
        # 字段名映射
        mapping = {
            "当事人": ["委托人及联系方式", "委托人", "当事人"],
            "开庭日": ["开庭日", "开庭日期", "开庭时间"],
            "主办律师": ["主办律师", "承办律师", "律师"],
        }
        
        aliases = mapping.get(field_name, [])
        for alias in aliases:
            if alias in fields:
                return str(fields[alias])
        
        return None

    def _template_summarize(
        self,
        data: list[dict[str, Any]],
        fields: list[str],
    ) -> str:
        """基于模板的简单汇总 (兜底方案)"""
        lines = []
        for i, item in enumerate(data, start=1):
            parts = [f"{i}. "]
            for field in fields:
                if field in item:
                    parts.append(f"{field}：{item[field]}")
            lines.append(" | ".join(parts))
        return "\n".join(lines)

    async def _llm_summarize(
        self,
        data: list[dict[str, Any]],
        query: str,
        soul_prompt: str = "",
        user_memory: str = "",
        shared_memory: str = "",
    ) -> str:
        """调用 LLM 生成自然语言摘要"""
        try:
            # 构建数据描述
            data_desc = "\n".join(
                f"- {item}" for item in data[:10]  # 限制数量避免 token 过多
            )

            memory_notes = []
            if user_memory:
                memory_notes.append(f"用户偏好/记忆：\n{user_memory.strip()}")
            if shared_memory:
                memory_notes.append(f"团队共享记忆：\n{shared_memory.strip()}")
            memory_block = "\n\n".join(memory_notes)
            if memory_block:
                memory_block = f"\n\n参考记忆：\n{memory_block}"
            
            prompt = f"""请根据以下案件数据，用简洁的中文生成汇总摘要。

用户问题：{query}

案件数据：
{data_desc}
{memory_block}

要求：
1. 用简洁的自然语言描述
2. 突出关键信息（案号、当事人、开庭时间）
3. 如有多条，可按时间或类型分组
4. 总字数控制在 200 字以内"""

            system_prompt = "你是一个信息提取与总结助手。"
            if soul_prompt:
                system_prompt = f"{soul_prompt.strip()}\n\n{system_prompt}"

            response = await self._llm.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ], timeout=self._llm_timeout)
            return response or self._template_summarize(data, self._default_fields)
        except Exception as e:
            logger.warning(f"LLM summarize failed: {e}")
            return self._template_summarize(data, self._default_fields)

    async def _summarize_docs(
        self,
        documents: list[dict[str, Any]],
        query: str,
    ) -> SkillResult:
        """汇总云文档搜索结果"""
        count = len(documents)
        
        lines = [f"📄 文档汇总（共 {count} 篇）", ""]
        for i, doc in enumerate(documents, start=1):
            title = doc.get("title", "未命名")
            preview = doc.get("preview", "")[:50]
            lines.append(f"{i}. {title}")
            if preview:
                lines.append(f"   摘要：{preview}...")
        
        reply_text = "\n".join(lines)
        
        return SkillResult(
            success=True,
            skill_name=self.name,
            data={"documents": documents, "total": count},
            message=f"已汇总 {count} 篇文档",
            reply_type="text",
            reply_text=reply_text,
        )
# endregion
