"""
描述: 案件查询技能
主要功能:
    - 多维表格案件查询
    - 飞书文档内容搜索
    - 格式化查询结果并构建消息卡片
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.core.skills.base import BaseSkill
from src.core.types import SkillContext, SkillResult

logger = logging.getLogger(__name__)


# region 案件查询技能
class QuerySkill(BaseSkill):
    """
    案件查询技能

    功能:
        - 识别查询意图（表格/文档）
        - 提取关键词和时间范围
        - 调用对应 MCP 工具获取数据
    """
    
    name: str = "QuerySkill"
    description: str = "查询案件、开庭、当事人等信息"

    def __init__(
        self,
        mcp_client: Any,
        settings: Any = None,
        llm_client: Any = None,
        skills_config: dict[str, Any] | None = None,
    ) -> None:
        """
        初始化查询技能

        参数:
            mcp_client: MCP 客户端实例
            settings: 配置信息
        """
        self._mcp = mcp_client
        self._settings = settings
        self._llm = llm_client
        self._skills_config = skills_config or {}

        self._table_aliases = self._skills_config.get("table_aliases", {}) or {}
        self._alias_lookup = self._build_alias_lookup(self._table_aliases)
        self._table_recognition = self._skills_config.get("table_recognition", {}) or {}
        self._confidence_threshold = float(
            self._table_recognition.get("confidence_threshold", 0.65)
        )
        self._auto_confirm_threshold = float(
            self._table_recognition.get("auto_confirm_threshold", 0.85)
        )
        self._max_candidates = int(self._table_recognition.get("max_candidates", 3))

        # 结果格式化字段配置（支持自定义）
        query_cfg = self._skills_config.get("query", {})
        if not query_cfg:
            query_cfg = self._skills_config.get("skills", {}).get("query", {})
        self._query_cfg = query_cfg
        self._display_fields = query_cfg.get("display_fields", {
            "title_left": "委托人及联系方式",
            "title_right": "对方当事人",
            "title_suffix": "案由",
            "case_no": "案号",
            "court": "审理法院",
            "stage": "程序阶段",
        })
        self._all_cases_keywords = query_cfg.get(
            "all_cases_keywords",
            [
                "所有案件",
                "全部案件",
                "案件列表",
                "列出案件",
                "所有项目",
                "全部项目",
                "所有案子",
                "全部案子",
                "查全部",
            ],
        )
        self._keep_view_keywords = query_cfg.get(
            "keep_view_keywords",
            ["按视图", "当前视图", "仅视图", "视图内", "只看视图"],
        )
        self._all_cases_ignore_default_view = bool(
            query_cfg.get("all_cases_ignore_default_view", True)
        )

    async def execute(self, context: SkillContext) -> SkillResult:
        """
        执行查询逻辑

        参数:
            context: 技能上下文

        返回:
            查询结果（文本或卡片）
        """
        query = context.query.strip()
        extra = context.extra or {}

        if self._is_refresh_command(query):
            return await self._refresh_tables()

        target = self._select_target(query)
        if target == "doc":
            params = self._build_doc_params(query)
            try:
                result = await self._mcp.call_tool("feishu.v1.doc.search", params)
                documents = result.get("documents", [])
                if not documents:
                    return self._empty_result("未找到相关文档")
                return self._format_doc_result(documents)
            except Exception as e:
                logger.error("QuerySkill execution error: %s", e)
                return SkillResult(
                    success=False,
                    skill_name=self.name,
                    message=str(e),
                    reply_text="查询失败，请稍后重试。",
                )

        pending = self._get_pending_table(context)
        if pending:
            resolved = self._resolve_pending_response(query, pending)
            if resolved:
                query = pending.get("query") or query
                extra = dict(extra)
                extra["table_name"] = resolved["table_name"]
                extra["table_id"] = resolved.get("table_id")

        table_result = await self._resolve_table(query, extra)
        if table_result.get("status") == "need_confirm":
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={"pending_table": table_result.get("pending_table")},
                message="需要确认表名",
                reply_text=table_result.get("reply_text", ""),
            )
        if table_result.get("status") != "resolved":
            return SkillResult(
                success=False,
                skill_name=self.name,
                message=table_result.get("message", "无法识别表"),
                reply_text=table_result.get("reply_text", "无法识别要查询的表，请明确表名。"),
            )

        tool_name, params = self._build_bitable_params(query, extra, table_result)
        notice = table_result.get("notice")

        try:
            logger.info("Query tool selected: %s, params: %s", tool_name, params)
            result = await self._mcp.call_tool(tool_name, params)
            records = result.get("records", [])
            schema = result.get("schema")
            if not records:
                return self._empty_result("未找到相关案件记录")
            return self._format_case_result(records, notice=notice, schema=schema)
        except Exception as e:
            if tool_name == "feishu.v1.bitable.search_exact" and (
                "Field not found" in str(e) or "InvalidFilter" in str(e)
            ):
                try:
                    fallback_params: dict[str, Any] = {}
                    if params.get("table_id"):
                        fallback_params["table_id"] = params.get("table_id")
                    if params.get("view_id"):
                        fallback_params["view_id"] = params.get("view_id")
                    fallback_params["keyword"] = str(params.get("value") or "")
                    logger.warning(
                        "Exact field not found, fallback to keyword search: %s",
                        fallback_params,
                    )
                    result = await self._mcp.call_tool("feishu.v1.bitable.search_keyword", fallback_params)
                    records = result.get("records", [])
                    schema = result.get("schema")
                    if not records:
                        return self._empty_result("未找到相关案件记录")
                    return self._format_case_result(records, notice=notice, schema=schema)
                except Exception:
                    pass
            logger.error("QuerySkill execution error: %s", e)
            return SkillResult(
                success=False,
                skill_name=self.name,
                message=str(e),
                reply_text="查询失败，请稍后重试。",
            )

    def _select_target(self, query: str) -> str:
        """判断查询类型（表格/文档）"""
        doc_keywords = ["文档", "资料", "文件", "合同"]
        if any(kw in query for kw in doc_keywords):
            return "doc"
        return "bitable"

    def _build_doc_params(self, query: str) -> dict[str, Any]:
        params: dict[str, Any] = {}
        keyword = self._extract_keyword(query)
        if keyword:
            params["keyword"] = keyword
        return params

    def _build_alias_lookup(self, table_aliases: dict[str, Any]) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for table_name, aliases in (table_aliases or {}).items():
            alias_list = [table_name]
            if isinstance(aliases, list):
                alias_list.extend([str(item) for item in aliases if item])
            for alias in alias_list:
                alias = str(alias).strip()
                if not alias:
                    continue
                lookup[alias] = table_name
        return lookup

    def _is_refresh_command(self, query: str) -> bool:
        cmd = query.strip().lower()
        return cmd in {"/refresh", "刷新", "刷新表结构", "刷新表"}

    async def _refresh_tables(self) -> SkillResult:
        try:
            result = await self._mcp.call_tool(
                "feishu.v1.bitable.list_tables",
                {"refresh": True},
            )
            tables = result.get("tables", [])
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={"tables": tables, "total": result.get("total", len(tables))},
                message="已刷新表结构缓存",
                reply_text=f"已刷新表结构缓存（{len(tables)} 张表）。",
            )
        except Exception as exc:
            logger.error("Refresh tables error: %s", exc)
            return SkillResult(
                success=False,
                skill_name=self.name,
                message=str(exc),
                reply_text="刷新表结构失败，请稍后重试。",
            )

    def _get_pending_table(self, context: SkillContext) -> dict[str, Any] | None:
        if context.last_skill != self.name:
            return None
        last_result = context.last_result or {}
        pending = last_result.get("pending_table")
        if isinstance(pending, dict):
            return pending
        return None

    def _resolve_pending_response(
        self,
        query: str,
        pending: dict[str, Any],
    ) -> dict[str, Any] | None:
        candidates = pending.get("candidates") or []
        if not isinstance(candidates, list):
            return None
        normalized = query.strip()
        confirm_words = {"是", "确认", "对", "好的", "好", "ok", "yes"}
        if normalized in confirm_words and len(candidates) == 1:
            return candidates[0]
        for candidate in candidates:
            name = candidate.get("table_name")
            if name and name == normalized:
                return candidate
        return None

    async def _resolve_table(self, query: str, extra: dict[str, Any]) -> dict[str, Any]:
        try:
            tables_result = await self._mcp.call_tool("feishu.v1.bitable.list_tables", {})
        except Exception as exc:
            logger.error("List tables failed: %s", exc)
            return {"status": "error", "message": str(exc)}

        tables = tables_result.get("tables", [])
        if not tables:
            return {
                "status": "error",
                "message": "未配置多维表格",
                "reply_text": "当前未配置多维表格，无法查询。",
            }

        table_lookup = {item["table_name"]: item.get("table_id") for item in tables}
        table_names = list(table_lookup.keys())

        alias_match = self._match_alias(query)
        if alias_match and alias_match in table_lookup:
            logger.info("Table resolved by alias", extra={"table": alias_match, "method": "alias"})
            return {
                "status": "resolved",
                "table_name": alias_match,
                "table_id": table_lookup.get(alias_match),
                "confidence": 1.0,
                "method": "alias",
            }

        direct_match = self._match_table_name(query, table_names)
        if direct_match:
            logger.info("Table resolved by name", extra={"table": direct_match, "method": "direct"})
            return {
                "status": "resolved",
                "table_name": direct_match,
                "table_id": table_lookup.get(direct_match),
                "confidence": 1.0,
                "method": "direct",
            }

        llm_result = await self._llm_pick_table(query, table_names)
        candidates = self._normalize_candidates(llm_result.get("candidates"), table_names)
        if llm_result.get("table_name"):
            candidates = [llm_result["table_name"]] + [c for c in candidates if c != llm_result["table_name"]]
        candidates = candidates[: self._max_candidates]

        confidence = float(llm_result.get("confidence") or 0)
        selected = llm_result.get("table_name")
        if selected and selected not in table_lookup:
            selected = None

        logger.info(
            "Table resolved by llm",
            extra={
                "table": selected,
                "confidence": confidence,
                "candidates": candidates,
            },
        )

        if selected and confidence >= self._auto_confirm_threshold:
            return {
                "status": "resolved",
                "table_name": selected,
                "table_id": table_lookup.get(selected),
                "confidence": confidence,
                "method": "llm_high",
            }
        if selected and confidence >= self._confidence_threshold:
            return {
                "status": "resolved",
                "table_name": selected,
                "table_id": table_lookup.get(selected),
                "confidence": confidence,
                "method": "llm_medium",
                "notice": f"已为您定位到 {selected} 表。",
            }

        reply_text = self._build_confirmation_reply(candidates, table_names)
        pending_table = {
            "query": query,
            "candidates": [
                {"table_name": name, "table_id": table_lookup.get(name)} for name in candidates
            ],
        }
        return {
            "status": "need_confirm",
            "reply_text": reply_text,
            "pending_table": pending_table,
        }

    def _match_alias(self, query: str) -> str | None:
        logger.info(f"Matching alias for query: '{query}', alias_lookup: {self._alias_lookup}")
        query_lower = query.lower()
        matched = []
        for alias, table in self._alias_lookup.items():
            if alias in query or alias.lower() in query_lower:
                matched.append((len(alias), table))
                logger.info(f"Matched alias: '{alias}' -> '{table}'")
        if not matched:
            logger.warning("No alias matched")
            return None
        matched.sort(reverse=True)
        result = matched[0][1]
        logger.info(f"Selected table: '{result}'")
        return result

    def _match_table_name(self, query: str, table_names: list[str]) -> str | None:
        matched = [name for name in table_names if name and name in query]
        if not matched:
            return None
        matched.sort(key=len, reverse=True)
        return matched[0]

    async def _llm_pick_table(self, query: str, table_names: list[str]) -> dict[str, Any]:
        if not self._llm or not table_names:
            return {}
        system = "你是表名识别助手。"
        prompt = (
            "请根据用户问题从表名列表中选择最可能的表，并返回 JSON："
            "{\"table_name\": \"...\", \"confidence\": 0.0-1.0, \"reason\": \"...\", "
            "\"candidates\": [\"...\", \"...\"]}。只返回 JSON。\n\n"
            f"表名列表：{', '.join(table_names)}\n"
            f"用户问题：{query}"
        )
        try:
            return await self._llm.chat_json(prompt, system=system)
        except Exception as exc:
            logger.warning("LLM table match failed: %s", exc)
            return {}

    def _normalize_candidates(self, candidates: Any, table_names: list[str]) -> list[str]:
        result: list[str] = []
        if isinstance(candidates, list):
            for item in candidates:
                if isinstance(item, str) and item in table_names:
                    result.append(item)
        return result

    def _build_confirmation_reply(self, candidates: list[str], all_tables: list[str]) -> str:
        templates = (self._table_recognition.get("templates") or {})
        single_tpl = templates.get("single_candidate", "请确认表名：{table_name}")
        multi_tpl = templates.get("multi_candidate", "请确认表名：\n{candidate_list}")
        no_match_tpl = templates.get("no_match", "可用表：{all_tables}")

        if len(candidates) == 1:
            return single_tpl.format(table_name=candidates[0])
        if 1 < len(candidates) <= self._max_candidates:
            candidate_list = "\n".join([f"- {name}" for name in candidates])
            return multi_tpl.format(candidate_list=candidate_list)
        return no_match_tpl.format(all_tables="、".join(all_tables))

    def _build_bitable_params(
        self,
        query: str,
        extra: dict[str, Any],
        table_result: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        params: dict[str, Any] = {}
        table_id = table_result.get("table_id")
        if table_id:
            params["table_id"] = table_id

        if self._is_all_cases_query(query):
            if self._all_cases_ignore_default_view and not self._should_keep_view_filter(query):
                params["ignore_default_view"] = True
            logger.info("Query scenario: all_cases")
            return "feishu.v1.bitable.search", params

        # 优先级1: 检查是否为"我的案件"查询
        user_profile = extra.get("user_profile")
        if user_profile and user_profile.open_id and ("我的" in query or "自己的" in query):
            # 使用人员字段搜索工具，通过 open_id 精确匹配主办律师
            logger.info(f"Query 'my cases' for user: {user_profile.name} (open_id: {user_profile.open_id})")
            params.update({
                "field": "主办律师",
                "open_id": user_profile.open_id,
            })
            logger.info("Query scenario: my_cases")
            return "feishu.v1.bitable.search_person", params

        # 优先级2: 检查是否指定了律师（例如："查询张三的案件"、"律师李四的案件"）
        # 注意：由于只有姓名，无法获取 open_id，使用关键词搜索
        import re
        lawyer_pattern = re.compile(r"(?:查询|律师)?([^的\s]+)(?:的案件|案件)")
        match = lawyer_pattern.search(query)
        if match:
            lawyer_name = match.group(1).strip()
            # 排除一些常见的非律师关键词
            if lawyer_name not in ["所有", "全部", "今天", "明天", "本周", "本月", "我", "自己"]:
                # 使用关键词搜索
                logger.info(f"Query cases for lawyer: {lawyer_name}")
                params["keyword"] = lawyer_name
                logger.info("Query scenario: person_cases")
                return "feishu.v1.bitable.search_keyword", params

        date_from = extra.get("date_from")
        date_to = extra.get("date_to")
        if date_from or date_to:
            params.update({
                "field": self._guess_date_field(query),
                "date_from": date_from,
                "date_to": date_to,
            })
            return "feishu.v1.bitable.search_date_range", params

        exact_field = self._extract_exact_field(query)
        if exact_field:
            params.update(exact_field)
            logger.info("Query scenario: exact_match")
            return "feishu.v1.bitable.search_exact", params

        keyword = self._extract_keyword(query)
        if keyword:
            params["keyword"] = keyword
            logger.info("Query scenario: keyword")
            return "feishu.v1.bitable.search_keyword", params

        if self._all_cases_ignore_default_view and not self._should_keep_view_filter(query):
            params["ignore_default_view"] = True
        logger.info("Query scenario: full_scan")
        return "feishu.v1.bitable.search", params

    def _is_all_cases_query(self, query: str) -> bool:
        normalized = query.replace(" ", "")
        if any(token in normalized for token in self._all_cases_keywords):
            return True
        if ("所有" in normalized or "全部" in normalized) and ("案件" in normalized or "项目" in normalized):
            return True
        return False

    def _should_keep_view_filter(self, query: str) -> bool:
        normalized = query.replace(" ", "")
        return any(token in normalized for token in self._keep_view_keywords)

    def _guess_date_field(self, query: str) -> str:
        if "开庭" in query or "庭审" in query:
            return "开庭日"
        if "截止" in query:
            return "截止日"
        return "开庭日"

    def _extract_exact_field(self, query: str) -> dict[str, str] | None:
        exact_patterns: list[tuple[str, str]] = [
            (r"(?:案号|案件号)[是为:：\s]*([A-Za-z0-9\-_/（）()_\u4e00-\u9fa5]+)", "案号"),
            (r"(?:项目ID|项目Id|项目id|项目编号|项目号)[是为:：\s]*([A-Za-z0-9\-_/（）()_\u4e00-\u9fa5]+)", "项目ID"),
            (r"(?:编号)[是为:：\s]*([A-Za-z0-9\-_/（）()_\u4e00-\u9fa5]+)", "案号"),
        ]
        for pattern, field in exact_patterns:
            match = re.search(pattern, query)
            if not match:
                continue
            value = match.group(1).strip()
            if value:
                return {"field": field, "value": value}
        return None

    def _extract_keyword(self, query: str) -> str:
        """
        提取关键词

        逻辑:
            - 去除常见无效词（如动作词、通用词）
            - 如果过滤后无有效关键词，返回空（查询全部）

        参数:
            query: 原始查询文本
        返回:
            处理后的关键词
        """
        keyword = query
        
        # 查询动作词（需要去除）
        action_words = [
            "找一下", "查一下", "查询", "搜索", "帮我", "请帮我", 
            "一下", "你能", "能不能", "可以", "请",
        ]
        
        # 通用语义词（需要去除，但不是关键词）
        general_words = [
            "案子", "案件", "有什么", "有哪些", "都有哪些", "目前",
            "庭要开", "庭审", "信息", "详情", "的", "吗", "呢",
            "看看", "告诉我", "列出", "律师", "法官", "当事人",
            "委托人", "被告", "原告", "开庭", "案",
            "所有", "全部", "列表", "全部案件", "所有案件", "全部项目", "所有项目",
        ]
        
        for word in action_words + general_words:
            keyword = keyword.replace(word, "")
        
        keyword = keyword.strip()
        
        # 如果关键词太短或只是常见词，返回空（查询全部）
        if len(keyword) <= 1:
            return ""
            
        return keyword

    def _empty_result(self, message: str) -> SkillResult:
        """构造空结果响应"""
        return SkillResult(
            success=True,
            skill_name=self.name,
            data={"records": [], "total": 0},
            message=message,
            reply_text=f"{message}，请尝试调整查询条件。",
        )

    def _format_case_result(
        self,
        records: list[dict[str, Any]],
        notice: str | None = None,
        schema: list[dict[str, Any]] | None = None,
    ) -> SkillResult:
        """格式化案件查询结果"""
        count = len(records)
        title = f"📌 案件查询结果（共 {count} 条）"
        
        items = []
        df = self._display_fields  # 使用配置的字段名
        for i, record in enumerate(records, start=1):
            fields = record.get("fields_text") or record.get("fields", {})
            item = (
                f"{i}️⃣ {fields.get(df.get('title_left', ''), '')} vs {fields.get(df.get('title_right', ''), '')}｜{fields.get(df.get('title_suffix', ''), '')}\n"
                f"   • 案号：{fields.get(df.get('case_no', '案号'), '')}\n"
                f"   • 法院：{fields.get(df.get('court', '审理法院'), '')}\n"
                f"   • 程序：{fields.get(df.get('stage', '程序阶段'), '')}\n"
                f"   • 🔗 查看详情：{record.get('record_url', '')}"
            )
            items.append(item)
        
        parts = [title]
        if notice:
            parts = [notice, "", title]
        reply_text = "\n\n".join(parts + items)
        
        # 构建卡片
        card = self._build_card(title, items, notice=notice)
        
        return SkillResult(
            success=True,
            skill_name=self.name,
            data={"records": records, "total": count, "schema": schema or []},
            message=f"查询到 {count} 条记录",
            reply_type="card",
            reply_text=reply_text,
            reply_card=card,
        )

    def _format_doc_result(self, documents: list[dict[str, Any]]) -> SkillResult:
        """格式化文档查询结果"""
        count = len(documents)
        title = f"📄 文档搜索结果（共 {count} 条）"
        
        items = []
        for i, doc in enumerate(documents, start=1):
            item = (
                f"{i}. {doc.get('title', '未命名文档')}\n"
                f"   {doc.get('preview', '')}\n"
                f"   🔗 {doc.get('url', '')}"
            )
            items.append(item)
        
        reply_text = "\n\n".join([title] + items)
        
        return SkillResult(
            success=True,
            skill_name=self.name,
            data={"documents": documents, "total": count},
            message=f"搜索到 {count} 篇文档",
            reply_type="text",
            reply_text=reply_text,
        )

    def _build_card(self, title: str, items: list[str], notice: str | None = None) -> dict[str, Any]:
        """构建飞书消息卡片"""
        elements = []
        if notice:
            elements.append({"tag": "markdown", "content": notice})
        elements.extend({"tag": "markdown", "content": item} for item in items)
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": elements,
        }
# endregion
