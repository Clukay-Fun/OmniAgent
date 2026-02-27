"""
描述: 案件查询技能
主要功能:
    - 多维表格案件查询
    - 飞书文档内容搜索
    - 格式化查询结果并构建消息卡片
"""

from __future__ import annotations

import logging
import random
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from src.core.skills.base import BaseSkill
from src.core.skills.action_execution_service import ActionExecutionService
from src.core.skills.data_writer import DataWriter
from src.core.skills.multi_table_linker import MultiTableLinker
from src.core.skills.table_adapter import TableAdapter
from src.core.skills.entity_extractor import EntityExtractor
from src.core.skills.field_formatter import format_field_value
from src.core.skills.semantic_slots import SemanticSlotExtraction, SemanticSlotKey
from src.core.skills.schema_cache import SchemaCache, get_global_schema_cache
from src.core.types import SkillContext, SkillResult
from src.utils.metrics import (
    observe_bitable_query_latency,
    observe_query_semantic_confidence,
    record_field_format,
    record_query_resolution,
    record_query_semantic_fallback,
)
from src.utils.time_parser import parse_time_range

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
        schema_cache: SchemaCache | None = None,
        data_writer: DataWriter | None = None,
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
        self._schema_cache = schema_cache or get_global_schema_cache()
        self._data_writer = data_writer
        self._table_adapter = TableAdapter(mcp_client, skills_config=skills_config)
        self._linker = MultiTableLinker(
            mcp_client,
            skills_config=skills_config,
            data_writer=self._data_writer,
        )
        self._action_service = ActionExecutionService(data_writer=self._data_writer, linker=self._linker)
        self._extractor = EntityExtractor(llm_client)

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

        # ============================================
        # region 加载回复模板随机池
        # ============================================
        self._response_pool = self._load_response_pool()
        # endregion
        # ============================================

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
        self._keyword_fields = query_cfg.get(
            "keyword_fields",
            [
                "委托人",
                "对方当事人",
                "案件分类",
                "案件状态",
                "案由",
                "案号",
                "项目ID",
                "项目类型",
                "审理法院",
                "主办律师",
                "协办律师",
                "进展",
                "备注",
            ],
        )
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
        classification_fields_raw = query_cfg.get("classification_fields", ["项目类型", "案件分类"])
        self._classification_fields = [
            str(item).strip() for item in classification_fields_raw if str(item).strip()
        ] if isinstance(classification_fields_raw, list) else ["项目类型", "案件分类"]
        raw_aliases = query_cfg.get("classification_aliases", {
            "非诉": ["非诉", "非诉类型", "非诉案件", "非诉类"],
            "诉讼": ["诉讼", "诉讼类型", "诉讼案件"],
            "劳动仲裁": ["劳动仲裁", "劳动争议仲裁", "仲裁案件"],
            "执行": ["执行案件", "执行", "终本执行"],
        })
        self._classification_aliases = self._normalize_classification_aliases(raw_aliases)
        self._classification_alias_pairs = self._build_classification_alias_pairs(self._classification_aliases)
        reply_settings = getattr(settings, "reply", None) if settings is not None else None
        self._query_card_v2_enabled = bool(getattr(reply_settings, "query_card_v2_enabled", False))
        semantic_cfg = query_cfg.get("semantic_resolution", {})
        if not isinstance(semantic_cfg, dict):
            semantic_cfg = {}
        self._semantic_resolution_enabled = bool(
            semantic_cfg.get(
                "enabled",
                query_cfg.get("semantic_resolution_enabled", True),
            )
        )
        self._semantic_min_confidence = float(
            semantic_cfg.get(
                "min_confidence",
                query_cfg.get("semantic_min_confidence", 0.55),
            )
        )
        self._semantic_trace_slots = bool(semantic_cfg.get("trace_slots", True))
        self._last_resolution_trace: list[dict[str, Any]] = []

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
                return self._error_result(str(e))

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

        tool_name, params = await self._build_bitable_params(query, extra, table_result)
        override = self._linker.resolve_query_override(
            query=query,
            current_tool=tool_name,
            params=params,
            target_table_id=table_result.get("table_id"),
            target_table_name=table_result.get("table_name"),
            active_table_id=extra.get("active_table_id"),
            active_table_name=extra.get("active_table_name"),
            active_record=extra.get("active_record") if isinstance(extra.get("active_record"), dict) else None,
        )
        notice = table_result.get("notice")
        if override:
            tool_name, params = override
            if not notice:
                notice = "已按当前案件上下文联动查询关联表。"

        try:
            logger.info("Query tool selected: %s, params: %s", tool_name, params)
            query_started_at = time.perf_counter()
            result = await self._mcp.call_tool(tool_name, params)
            observe_bitable_query_latency(time.perf_counter() - query_started_at)
            records = result.get("records", [])
            schema = result.get("schema")
            has_more = bool(result.get("has_more", False))
            page_token = result.get("page_token") or ""
            total = result.get("total")

            entity_keyword = self._extract_entity_keyword(query)
            if entity_keyword and isinstance(records, list) and records:
                filtered = self._filter_records_for_org_entity(records, entity_keyword)
                if not filtered:
                    return self._empty_result(
                        f"未找到委托人/对方当事人包含“{entity_keyword}”的记录。"
                        "\n建议：提供案号/项目ID，或确认表中当事人字段是否已填写。",
                        prefer_message=True,
                    )
                records = filtered
                total = len(records)

            relevance_keyword = entity_keyword or str(params.get("keyword") or "").strip()
            if relevance_keyword and isinstance(records, list) and len(records) > 1:
                records = self._apply_keyword_relevance(records, relevance_keyword)
                total = len(records)

            table_id = str(params.get("table_id") or "").strip()
            self._sync_schema_cache(table_id, schema)

            pagination_extra = extra.get("pagination") if isinstance(extra.get("pagination"), dict) else None
            current_page = int(pagination_extra.get("current_page") or 0) + 1 if pagination_extra else 1
            query_meta = {
                "tool": tool_name,
                "params": {k: v for k, v in params.items() if k != "page_token"},
                "table_name": table_result.get("table_name") or "",
                "table_id": table_result.get("table_id") or params.get("table_id") or "",
                "resolution_trace": list(self._last_resolution_trace),
            }

            if not records:
                if pagination_extra:
                    return SkillResult(
                        success=True,
                        skill_name=self.name,
                        data={
                            "records": [],
                            "total": total or 0,
                            "schema": schema or [],
                            "pagination": {
                                "has_more": False,
                                "page_token": "",
                                "current_page": current_page,
                                "total": total or 0,
                            },
                            "query_meta": query_meta,
                        },
                        message="没有更多记录",
                        reply_text="已经没有更多记录了。",
                    )
                if tool_name == "data.bitable.search_date_range":
                    field = str(params.get("field") or "").strip()
                    if field == "开庭日":
                        return self._empty_result("该时间范围内没有开庭安排", prefer_message=True)
                    return self._empty_result("该时间范围内没有匹配记录", prefer_message=True)
                return self._empty_result("未找到相关案件记录")
            records = self._apply_schema_formatting(records, table_id)
            return self._format_case_result(
                records,
                notice=notice,
                schema=schema,
                table_name=str(table_result.get("table_name") or ""),
                pagination={
                    "has_more": has_more,
                    "page_token": page_token,
                    "current_page": current_page,
                    "total": total,
                },
                query_meta=query_meta,
            )
        except Exception as e:
            if tool_name == "data.bitable.search_exact" and (
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
                    fallback_started_at = time.perf_counter()
                    result = await self._mcp.call_tool("data.bitable.search_keyword", fallback_params)
                    observe_bitable_query_latency(time.perf_counter() - fallback_started_at)
                    records = result.get("records", [])
                    schema = result.get("schema")
                    table_id = str(fallback_params.get("table_id") or params.get("table_id") or "").strip()
                    self._sync_schema_cache(table_id, schema)
                    if not records:
                        return self._empty_result("未找到相关案件记录")
                    records = self._apply_schema_formatting(records, table_id)
                    return self._format_case_result(records, notice=notice, schema=schema)
                except Exception:
                    pass
            logger.error("QuerySkill execution error: %s", e)
            return self._error_result(str(e))

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
                "data.bitable.list_tables",
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
            pool = self._response_pool.get("error")
            base_reply = random.choice(pool) if pool else "刷新表结构失败，请稍后重试。"
            return SkillResult(
                success=False,
                skill_name=self.name,
                message=str(exc),
                reply_text=base_reply,
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
        explicit_table_id = str(extra.get("table_id") or "").strip()
        explicit_table_name = str(extra.get("table_name") or "").strip()

        try:
            tables_result = await self._mcp.call_tool("data.bitable.list_tables", {})
        except Exception as exc:
            logger.error("List tables failed: %s", exc)
            alias_match = self._match_alias(query)
            if alias_match:
                return {
                    "status": "resolved",
                    "table_name": alias_match,
                    "table_id": None,
                    "confidence": 0.6,
                    "method": "alias_without_tables",
                    "notice": "表结构服务暂时不可用，已按默认表执行查询。",
                }
            return {
                "status": "resolved",
                "table_name": None,
                "table_id": None,
                "confidence": 0.3,
                "method": "default_without_tables",
                "notice": "表结构服务暂时不可用，已按默认表执行查询。",
            }

        tables = tables_result.get("tables", [])
        if not tables:
            return {
                "status": "error",
                "message": "未配置多维表格",
                "reply_text": "当前未配置多维表格，无法查询。",
            }

        table_lookup = {item["table_name"]: item.get("table_id") for item in tables}
        table_names = list(table_lookup.keys())

        if explicit_table_id:
            matched_name = next(
                (name for name, tid in table_lookup.items() if tid == explicit_table_id),
                explicit_table_name or None,
            )
            return {
                "status": "resolved",
                "table_name": matched_name,
                "table_id": explicit_table_id,
                "confidence": 1.0,
                "method": "explicit_table_id",
            }

        if explicit_table_name and explicit_table_name in table_lookup:
            return {
                "status": "resolved",
                "table_name": explicit_table_name,
                "table_id": table_lookup.get(explicit_table_name),
                "confidence": 1.0,
                "method": "explicit_table_name",
            }

        if self._is_case_domain_query(query):
            case_table = self._pick_case_default_table(table_names)
            if case_table:
                return {
                    "status": "resolved",
                    "table_name": case_table,
                    "table_id": table_lookup.get(case_table),
                    "confidence": 0.98,
                    "method": "case_default",
                }

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
            logger.info("No alias matched")
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

    def _is_case_domain_query(self, query: str) -> bool:
        normalized = query.replace(" ", "")
        keywords = ["开庭", "庭审", "案件", "案子", "案号", "审理", "法院", "委托人", "对方当事人"]
        if any(token in normalized for token in keywords) or self._is_hearing_query(normalized):
            return True
        return self._extract_unlabeled_case_identifier(query) != ""

    def _pick_case_default_table(self, table_names: list[str]) -> str | None:
        preferred = ["案件项目总库", "案件 项目总库", "【诉讼案件】", "诉讼案件"]
        for name in preferred:
            if name in table_names:
                return name
        for name in table_names:
            if "案件" in name and ("库" in name or "台账" in name):
                return name
        return None

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

    async def _build_bitable_params(
        self,
        query: str,
        extra: dict[str, Any],
        table_result: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        params: dict[str, Any] = {}
        selected_tool: str | None = None
        semantic_extraction = self._extract_semantic_slots(query)
        self._last_resolution_trace = []
        if semantic_extraction.confidence is not None:
            observe_query_semantic_confidence(semantic_extraction.confidence)

        def add_trace(source: str, status: str = "selected", reason: str | None = None) -> None:
            entry: dict[str, Any] = {
                "source": source,
                "status": status,
                "confidence": semantic_extraction.confidence,
            }
            if self._semantic_trace_slots:
                entry["slots"] = self._summarize_semantic_slots(semantic_extraction)
            if reason:
                entry["reason"] = reason
            self._last_resolution_trace.append(entry)
            record_query_resolution(source=source, status=status)

        pagination = extra.get("pagination") if isinstance(extra.get("pagination"), dict) else None
        if isinstance(pagination, dict) and pagination.get("tool"):
            base_params_raw = pagination.get("params")
            if isinstance(base_params_raw, dict):
                base_params: dict[str, Any] = {str(k): v for k, v in base_params_raw.items()}
                params.update(base_params)
            if pagination.get("page_token"):
                params["page_token"] = pagination.get("page_token")
            logger.info("Query scenario: pagination_next")
            add_trace("pagination.next")
            return str(pagination.get("tool")), params

        planner_plan = extra.get("planner_plan") if isinstance(extra.get("planner_plan"), dict) else None
        if isinstance(planner_plan, dict):
            mapped_tool = self._map_planner_tool(str(planner_plan.get("tool") or ""))
            if mapped_tool:
                plan_params_raw = planner_plan.get("params")
                plan_params: dict[str, Any] = {}
                if isinstance(plan_params_raw, dict):
                    plan_params = {str(k): v for k, v in plan_params_raw.items()}
                params.update(plan_params)

                intent = str(planner_plan.get("intent") or "")
                if mapped_tool == "data.bitable.search" and intent == "query_all":
                    params.setdefault("ignore_default_view", True)

                if mapped_tool == "data.bitable.search_date_range":
                    guessed_field = self._guess_date_field(query)
                    current_field = str(params.get("field") or "").strip()
                    if guessed_field and (not current_field or current_field in {"截止日", "日期", "时间"}):
                        params["field"] = guessed_field

                    if extra.get("date_from"):
                        params.setdefault("date_from", extra.get("date_from"))
                    if extra.get("date_to"):
                        params.setdefault("date_to", extra.get("date_to"))
                    if extra.get("time_from"):
                        params.setdefault("time_from", extra.get("time_from"))
                    if extra.get("time_to"):
                        params.setdefault("time_to", extra.get("time_to"))
                    if not params.get("date_from") or not params.get("date_to"):
                        parsed = parse_time_range(query)
                        if parsed:
                            params.setdefault("date_from", parsed.date_from)
                            params.setdefault("date_to", parsed.date_to)
                            if parsed.time_from:
                                params.setdefault("time_from", parsed.time_from)
                            if parsed.time_to:
                                params.setdefault("time_to", parsed.time_to)
                        elif any(token in query for token in ["最近", "近期", "最新"]):
                            today = date.today()
                            params.setdefault("date_from", (today - timedelta(days=30)).isoformat())
                            params.setdefault("date_to", (today + timedelta(days=30)).isoformat())

                if mapped_tool == "data.bitable.search":
                    parsed = parse_time_range(query)
                    if parsed and self._is_hearing_query(query):
                        mapped_tool = "data.bitable.search_date_range"
                        params["field"] = params.get("field") or "开庭日"
                        params.setdefault("date_from", parsed.date_from)
                        params.setdefault("date_to", parsed.date_to)
                        if parsed.time_from:
                            params.setdefault("time_from", parsed.time_from)
                        if parsed.time_to:
                            params.setdefault("time_to", parsed.time_to)

                if mapped_tool == "data.bitable.search_person" and not params.get("open_id"):
                    user_profile = extra.get("user_profile")
                    open_id = getattr(user_profile, "open_id", "") if user_profile else ""
                    if open_id:
                        params["open_id"] = open_id
                if mapped_tool == "data.bitable.search_person" and not params.get("user_name"):
                    user_profile = extra.get("user_profile")
                    user_name = getattr(user_profile, "lawyer_name", "") if user_profile else ""
                    if not user_name:
                        user_name = getattr(user_profile, "name", "") if user_profile else ""
                    if user_name:
                        params["user_name"] = user_name
                if mapped_tool == "data.bitable.search_person" and not params.get("user_name"):
                    entity_keyword = self._extract_entity_keyword(query)
                    if entity_keyword:
                        params["user_name"] = entity_keyword
                if mapped_tool == "data.bitable.search_person" and not params.get("field"):
                    params["field"] = "主办律师"

                if mapped_tool == "data.bitable.search_exact" and not params.get("value"):
                    exact = await self._extract_exact_field(query)
                    if exact:
                        params.setdefault("field", exact.get("field"))
                        params["value"] = exact.get("value")

                if mapped_tool == "data.bitable.search" and not any(
                    params.get(k) for k in ("keyword", "date_from", "date_to", "filters")
                ):
                    exact = await self._extract_exact_field(query)
                    if exact:
                        mapped_tool = "data.bitable.search_exact"
                        params.update(exact)
                    else:
                        entity_keyword = self._extract_entity_keyword(query)
                        keyword = entity_keyword or self._extract_keyword(query)
                        if keyword:
                            mapped_tool = "data.bitable.search_keyword"
                            params["keyword"] = keyword
                            params.setdefault("fields", self._build_keyword_fields())

                if mapped_tool == "data.bitable.search_keyword" and not params.get("fields"):
                    params["fields"] = self._build_keyword_fields()

                if mapped_tool == "data.bitable.search_keyword" and params.get("keyword"):
                    params["keyword"] = self._sanitize_search_keyword(str(params.get("keyword") or ""))

                if mapped_tool == "data.bitable.search_exact" and params.get("value"):
                    params["value"] = self._sanitize_search_keyword(str(params.get("value") or ""))

                if mapped_tool == "data.bitable.search_exact":
                    exact_field = str(params.get("field") or "").strip()
                    exact_value = str(params.get("value") or "").strip()
                    entity_keyword = self._extract_entity_keyword(query)
                    if exact_field in {"主办律师", "协办律师"} and (
                        self._looks_like_org_name(exact_value) or self._looks_like_org_name(entity_keyword)
                    ):
                        mapped_tool = "data.bitable.search_keyword"
                        params.pop("value", None)
                        params["keyword"] = self._sanitize_search_keyword(entity_keyword or exact_value)
                        params.setdefault("fields", self._build_keyword_fields())

                if mapped_tool in {
                    "data.bitable.search_keyword",
                    "data.bitable.search_exact",
                    "data.bitable.search_person",
                    "data.bitable.search_date_range",
                }:
                    self._maybe_ignore_default_view(params, query)

                selected_tool = mapped_tool

                logger.info("Query scenario: planner")

        table_id = table_result.get("table_id")
        if table_id:
            params["table_id"] = table_id

        classification_target = self._extract_case_classification_target(query, table_result)
        if classification_target:
            compatible_selected = selected_tool in {
                None,
                "data.bitable.search",
                "data.bitable.search_keyword",
                "data.bitable.search_advanced",
            }
            if compatible_selected:
                params.pop("conditions", None)
                params.pop("conjunction", None)
                params.pop("keyword", None)
                params.pop("fields", None)
                params.pop("filters", None)
                params["keyword"] = classification_target
                params["fields"] = list(self._classification_fields)
                self._maybe_ignore_default_view(params, query)
                logger.info("Query scenario: case_classification")
                add_trace("rule.case_classification")
                return "data.bitable.search_keyword", params

        if not isinstance(planner_plan, dict):
            if not self._semantic_resolution_enabled:
                add_trace("semantic.skipped", status="skipped", reason="disabled")
                record_query_semantic_fallback("disabled")
            elif not semantic_extraction.slots:
                add_trace("semantic.skipped", status="skipped", reason="no_slots")
                record_query_semantic_fallback("no_slots")
            elif (
                semantic_extraction.confidence is not None
                and semantic_extraction.confidence < self._semantic_min_confidence
            ):
                add_trace("semantic.skipped", status="skipped", reason="low_confidence")
                record_query_semantic_fallback("low_confidence")
            else:
                semantic_compiled = self._compile_semantic_slots(semantic_extraction)
                if semantic_compiled:
                    semantic_tool, semantic_params, source = semantic_compiled
                    params.update(semantic_params)
                    self._maybe_ignore_default_view(params, query)
                    logger.info("Query scenario: semantic_slots (%s)", source)
                    add_trace(source)
                    return semantic_tool, params
                add_trace("semantic.fallback", status="fallback", reason="compile_failed")
                record_query_semantic_fallback("compile_failed")

        structured_query = self._extract_structured_query_params(query)
        if structured_query:
            structured_tool = str(structured_query.get("tool") or "").strip()
            structured_params_raw = structured_query.get("params")
            structured_params = structured_params_raw if isinstance(structured_params_raw, dict) else {}
            params.update(structured_params)
            self._maybe_ignore_default_view(params, query)
            logger.info("Query scenario: structured_query")
            if structured_tool:
                add_trace("rule.structured_query")
                return structured_tool, params

        if isinstance(planner_plan, dict):
            mapped_tool = selected_tool or self._map_planner_tool(str(planner_plan.get("tool") or ""))
            if mapped_tool:
                if mapped_tool == "data.bitable.search_person" and not (
                    params.get("open_id") or params.get("user_name")
                ):
                    mapped_tool = None
                if mapped_tool == "data.bitable.search_exact" and (not params.get("field") or not params.get("value")):
                    mapped_tool = None
                if mapped_tool == "data.bitable.search_keyword" and not params.get("keyword"):
                    mapped_tool = None
                if mapped_tool == "data.bitable.search_date_range" and (not params.get("date_from") or not params.get("date_to")):
                    mapped_tool = None

            if mapped_tool:
                add_trace("planner.mapped")
                return mapped_tool, params

        if self._is_all_cases_query(query):
            if self._all_cases_ignore_default_view and not self._should_keep_view_filter(query):
                params["ignore_default_view"] = True
            logger.info("Query scenario: all_cases")
            add_trace("rule.all_cases")
            return "data.bitable.search", params

        # 优先级1: 检查是否为"我的xxx"查询（根据目标表动态选择人员字段）
        user_profile = extra.get("user_profile")
        if user_profile and user_profile.open_id and ("我的" in query or "自己的" in query):
            logger.info("Query 'my records' for user: %s (open_id: %s)", user_profile.name, user_profile.open_id)

            # 从 extra 读取已按当前表解析好的身份字段列表
            table_identity_fields: list[str] = []
            tif_raw = extra.get("table_identity_fields")
            if isinstance(tif_raw, list):
                table_identity_fields = [str(f) for f in tif_raw if f]

            # 兜底：使用默认的主办律师
            primary_field = table_identity_fields[0] if table_identity_fields else "主办律师"

            params.update({
                "field": primary_field,
                "open_id": user_profile.open_id,
            })
            # 多字段支持：把剩余字段也带上，由 MCP Client 按序搜索直到命中
            if len(table_identity_fields) > 1:
                params["multi_fields"] = table_identity_fields[1:]

            if getattr(user_profile, "lawyer_name", None):
                params["user_name"] = user_profile.lawyer_name
            elif getattr(user_profile, "name", None):
                params["user_name"] = user_profile.name
            self._maybe_ignore_default_view(params, query)
            logger.info(
                "Query scenario: my_records (table_identity_fields=%s)",
                table_identity_fields or ["主办律师"],
            )
            add_trace("rule.my_records")
            return "data.bitable.search_person", params

        # 优先级2: 提取“X的案件/项目”主体关键词
        entity_keyword = self._extract_entity_keyword(query)
        if entity_keyword:
            logger.info("Query scenario: entity_cases (%s)", entity_keyword)
            params["keyword"] = entity_keyword
            params["fields"] = self._build_keyword_fields()
            self._maybe_ignore_default_view(params, query)
            add_trace("rule.entity_keyword")
            return "data.bitable.search_keyword", params

        date_from = extra.get("date_from")
        date_to = extra.get("date_to")
        time_from = extra.get("time_from")
        time_to = extra.get("time_to")
        if not date_from or not date_to:
            parsed = parse_time_range(query)
            if parsed:
                date_from = date_from or parsed.date_from
                date_to = date_to or parsed.date_to
                time_from = time_from or parsed.time_from
                time_to = time_to or parsed.time_to
        if date_from or date_to:
            params.update({
                "field": self._guess_date_field(query),
                "date_from": date_from,
                "date_to": date_to,
            })
            if time_from:
                params["time_from"] = time_from
            if time_to:
                params["time_to"] = time_to
            self._maybe_ignore_default_view(params, query)
            add_trace("rule.date_range")
            return "data.bitable.search_date_range", params

        unlabeled_case_id = self._extract_unlabeled_case_identifier(query)
        if unlabeled_case_id:
            params["keyword"] = unlabeled_case_id
            params["fields"] = self._build_identifier_keyword_fields()
            self._maybe_ignore_default_view(params, query)
            logger.info("Query scenario: id_keyword")
            add_trace("rule.id_keyword")
            return "data.bitable.search_keyword", params

        exact_field = await self._extract_exact_field(query)
        if exact_field:
            params.update(exact_field)
            self._maybe_ignore_default_view(params, query)
            logger.info("Query scenario: exact_match")
            add_trace("rule.exact_match")
            return "data.bitable.search_exact", params

        keyword = self._extract_keyword(query)
        if keyword:
            params["keyword"] = keyword
            params["fields"] = self._build_keyword_fields()
            self._maybe_ignore_default_view(params, query)
            logger.info("Query scenario: keyword")
            add_trace("rule.keyword")
            return "data.bitable.search_keyword", params

        if self._all_cases_ignore_default_view and not self._should_keep_view_filter(query):
            params["ignore_default_view"] = True
        logger.info("Query scenario: full_scan")
        add_trace("rule.full_scan")
        return "data.bitable.search", params

    def _map_planner_tool(self, tool: str) -> str | None:
        mapping = {
            "search": "data.bitable.search",
            "search_exact": "data.bitable.search_exact",
            "search_keyword": "data.bitable.search_keyword",
            "search_person": "data.bitable.search_person",
            "search_date_range": "data.bitable.search_date_range",
            "search_advanced": "data.bitable.search_advanced",
        }
        if tool in mapping:
            return mapping[tool]
        if tool.startswith("data.bitable."):
            return tool
        return None

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

    def _normalize_classification_aliases(self, raw: Any) -> dict[str, list[str]]:
        if not isinstance(raw, dict):
            return {}
        output: dict[str, list[str]] = {}
        for target_raw, aliases_raw in raw.items():
            target = str(target_raw).strip()
            if not target:
                continue
            aliases: list[str] = [target]
            if isinstance(aliases_raw, list):
                aliases.extend(str(item).strip() for item in aliases_raw if str(item).strip())
            output[target] = aliases
        return output

    def _build_classification_alias_pairs(self, aliases: dict[str, list[str]]) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for target, items in aliases.items():
            for alias in items:
                text = str(alias).strip()
                if text:
                    pairs.append((text, target))
        pairs.sort(key=lambda item: len(item[0]), reverse=True)
        return pairs

    def _extract_case_classification_target(
        self,
        query: str,
        table_result: dict[str, Any],
    ) -> str:
        if not self._classification_alias_pairs:
            return ""
        normalized = query.replace(" ", "")
        if not normalized:
            return ""
        if any(token in normalized for token in ("我的", "自己", "我负责", "我经手")):
            return ""

        table_name = str(table_result.get("table_name") or "").strip()
        if table_name and all(token not in table_name for token in ["案件", "诉讼", "项目总库"]):
            return ""
        if not table_name and not self._is_case_domain_query(query):
            return ""

        target = ""
        for alias, mapped in self._classification_alias_pairs:
            if alias and alias in normalized:
                if mapped == "诉讼" and "非诉" in normalized:
                    continue
                target = mapped
                break
        if not target:
            return ""
        return target

    def _guess_date_field(self, query: str) -> str:
        normalized = query.replace(" ", "")
        if "管辖权异议" in normalized:
            return "管辖权异议截止日"
        if "举证" in normalized:
            return "举证截止日"
        if "查封" in normalized:
            return "查封到期日"
        if "上诉" in normalized:
            return "上诉截止日"
        if self._is_hearing_query(normalized):
            return "开庭日"
        if "截止" in normalized or "到期" in normalized:
            return "截止日"
        return "开庭日"

    def _extract_structured_query_params(self, query: str) -> dict[str, Any] | None:
        normalized = query.replace(" ", "")

        labelled_rules: list[tuple[tuple[str, ...], list[str]]] = [
            (("对方当事人",), ["对方当事人"]),
            (("联系人",), ["联系人"]),
            (("法官", "承办法官"), ["承办法官"]),
            (("法院", "审理法院"), ["审理法院"]),
            (("案由",), ["案由"]),
            (("当事人",), ["委托人", "对方当事人", "联系人"]),
        ]
        for labels, fields in labelled_rules:
            value = self._extract_labeled_value(query, labels)
            if not value:
                continue
            return {
                "tool": "data.bitable.search_keyword",
                "params": {
                    "keyword": value,
                    "fields": fields,
                },
            }

        today = date.today()
        if any(token in normalized for token in ["已经开过庭", "开过庭的", "已开庭的", "开过庭"]):
            return {
                "tool": "data.bitable.search_date_range",
                "params": {
                    "field": "开庭日",
                    "date_to": (today - timedelta(days=1)).isoformat(),
                },
            }

        if any(token in normalized for token in ["后续要开庭", "后续开庭", "待开庭", "未来开庭", "接下来开庭"]):
            return {
                "tool": "data.bitable.search_date_range",
                "params": {
                    "field": "开庭日",
                    "date_from": today.isoformat(),
                    "date_to": (today + timedelta(days=3650)).isoformat(),
                },
            }
        return None

    def _extract_labeled_value(self, query: str, labels: tuple[str, ...]) -> str:
        for label in labels:
            pattern = rf"(?:{re.escape(label)})\s*(?:是|为|=|:|：)?\s*([^，。,.！？!\s][^，。,.！？!]{{0,40}})"
            match = re.search(pattern, query)
            if not match:
                continue
            value = match.group(1).strip()
            value = re.sub(r"(?:的)?(?:案件|案子|项目)$", "", value).strip()
            value = self._sanitize_search_keyword(value)
            if value:
                return value
        return ""

    def _extract_semantic_slots(self, query: str) -> SemanticSlotExtraction:
        slots: dict[SemanticSlotKey, str] = {}

        case_identifier = self._extract_labeled_case_identifier(query) or self._extract_unlabeled_case_identifier(query)
        if case_identifier:
            slots[SemanticSlotKey.CASE_IDENTIFIER] = case_identifier

        party_value = self._extract_labeled_value(query, ("委托人", "当事人", "对方当事人", "甲方", "乙方"))
        if party_value:
            slots[SemanticSlotKey.PARTY_A] = party_value

        confidence = 0.0
        if SemanticSlotKey.CASE_IDENTIFIER in slots:
            confidence += 0.72
        if SemanticSlotKey.PARTY_A in slots:
            confidence += 0.58
        if confidence <= 0:
            confidence = 0.2
        confidence = min(confidence, 0.98)

        return SemanticSlotExtraction(slots=slots, confidence=round(confidence, 2))

    def _extract_labeled_case_identifier(self, query: str) -> str:
        patterns = [
            r"(?:案号|案件号)[是为:：\s]*([A-Za-z0-9\-_/（）()_\u4e00-\u9fa5]+)",
            r"(?:项目ID|项目Id|项目id|项目编号|项目号)[是为:：\s]*([A-Za-z0-9\-_/（）()_\u4e00-\u9fa5]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, query)
            if not match:
                continue
            value = self._sanitize_search_keyword(match.group(1))
            if value:
                return value
        return ""

    def _compile_semantic_slots(
        self,
        extraction: SemanticSlotExtraction,
    ) -> tuple[str, dict[str, Any], str] | None:
        case_identifier = extraction.slots.get(SemanticSlotKey.CASE_IDENTIFIER)
        if case_identifier:
            return (
                "data.bitable.search_keyword",
                {
                    "keyword": case_identifier,
                    "fields": self._build_identifier_keyword_fields(),
                },
                "semantic.case_identifier",
            )

        party = extraction.slots.get(SemanticSlotKey.PARTY_A)
        if party:
            return (
                "data.bitable.search_keyword",
                {
                    "keyword": party,
                    "fields": ["委托人", "委托人及联系方式", "对方当事人", "联系人"],
                },
                "semantic.party",
            )

        return None

    def _summarize_semantic_slots(self, extraction: SemanticSlotExtraction) -> dict[str, str]:
        summary: dict[str, str] = {}
        case_identifier = extraction.slots.get(SemanticSlotKey.CASE_IDENTIFIER)
        if case_identifier:
            summary[SemanticSlotKey.CASE_IDENTIFIER.value] = "present"
        party = extraction.slots.get(SemanticSlotKey.PARTY_A)
        if party:
            summary[SemanticSlotKey.PARTY_A.value] = "present"
        return summary

    def _is_hearing_query(self, query: str) -> bool:
        normalized = query.replace(" ", "")
        if "开庭" in normalized or "庭审" in normalized or "庭要开" in normalized:
            return True
        return bool(re.search(r"庭.*开|开.*庭", normalized))

    async def _extract_exact_field(self, query: str) -> dict[str, str] | None:
        """
        Dynamically extract exact match criteria using LLM. 
        Will fallback to legacy basic exact mapping if needed.
        """
        result = await self._extractor.extract_exact_match_field(query)
        if result:
            return result
        
        # Fallback to legacy fast matching
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
            value = re.sub(r"(?:的)?(?:案件|案子|项目)$", "", value).strip()
            value = self._sanitize_search_keyword(value)
            if value:
                return {"field": field, "value": value}
        return None

    def _extract_unlabeled_case_identifier(self, query: str) -> str:
        """提取未显式标注“案号/项目ID”的编号型关键词。

        示例："查找JFTD-20260023" -> "JFTD-20260023"
        """
        text = str(query or "")
        # 常见案号/项目编号样式：字母前缀 + 连字符 + 数字主体
        # 例如 JFTD-20260023, ABC-12345
        match = re.search(r"([A-Za-z]{2,}[A-Za-z0-9]*-\d{4,})", text)
        if not match:
            return ""
        value = self._sanitize_search_keyword(match.group(1))
        return value

    def _extract_entity_keyword(self, query: str) -> str:
        """提取“X的案件/案子/项目”中的主体关键词。"""
        if "案号" in query or "项目ID" in query or "项目编号" in query:
            return ""

        compact = re.sub(r"[，。,.!?？!：:；;]", " ", query)
        pattern = re.compile(
            r"(?:帮我|请|麻烦)?(?:查找|查询|搜索|查一查|查一下|查|找|看看|看下|看一下|查看|帮我查)?\s*(?:一下)?\s*([^\s]{2,60}?)(?:的)?(?:案件|案子|项目)"
        )
        match = pattern.search(compact)
        if not match:
            return ""

        candidate = match.group(1).strip()
        candidate = self._sanitize_search_keyword(candidate)
        candidate = re.sub(r"^(我的|我负责的|我负责|自己的|有关|关于)", "", candidate).strip()
        candidate = re.sub(r"(负责的?|相关的?|有关的?)$", "", candidate).strip()
        if not candidate:
            return ""
        if candidate in {"我", "自己", "所有", "全部", "今天", "明天", "本周", "本月", "最近", "近期"}:
            return ""
        return candidate

    def _apply_keyword_relevance(self, records: list[dict[str, Any]], keyword: str) -> list[dict[str, Any]]:
        """对结果进行本地相关性过滤，避免全表噪声记录。"""
        target = keyword.strip().lower()
        if not target:
            return records

        high_priority_fields = {
            self._display_fields.get("title_left", "委托人"),
            self._display_fields.get("title_right", "对方当事人"),
            self._display_fields.get("case_no", "案号"),
            "委托人",
            "对方当事人",
            "案号",
            "项目ID",
        }

        scored: list[tuple[int, dict[str, Any]]] = []
        for record in records:
            fields = record.get("fields_text") or record.get("fields") or {}
            score = 0
            for field_name, value in fields.items():
                text = str(value or "").strip().lower()
                if not text or target not in text:
                    continue
                score += 3 if str(field_name) in high_priority_fields else 1
            scored.append((score, record))

        matched = [record for score, record in scored if score > 0]
        if not matched:
            return records
        scored.sort(key=lambda item: item[0], reverse=True)
        return [record for score, record in scored if score > 0]

    def _filter_records_for_org_entity(self, records: list[dict[str, Any]], keyword: str) -> list[dict[str, Any]]:
        """公司名/机构名查询的强约束过滤。

        目标：避免公司名查询时命中到“仅在备注等低优先字段出现关键词”的记录，造成用户感知的误答。
        规则：仅当关键词看起来像组织名时生效；只在高优先字段中做包含匹配。
        """
        entity = str(keyword or "").strip()
        if not entity:
            return records
        if not self._looks_like_org_name(entity):
            return records

        target = entity.replace(" ", "").lower()
        if not target:
            return records

        must_fields = {
            # 展示字段（常见）
            self._display_fields.get("title_left", "委托人"),
            self._display_fields.get("title_right", "对方当事人"),
            self._display_fields.get("case_no", "案号"),
            # 高优先业务字段（兜底）
            "委托人",
            "委托人及联系方式",
            "对方当事人",
            "案号",
            "案件号",
            "项目ID",
            "项目 ID",
            "项目编号",
        }

        matched: list[dict[str, Any]] = []
        for record in records:
            fields = record.get("fields_text") or record.get("fields") or {}
            if not isinstance(fields, dict):
                continue
            for field_name in must_fields:
                name = str(field_name or "").strip()
                if not name:
                    continue
                value = str(fields.get(name) or "").replace(" ", "").lower()
                if value and target in value:
                    matched.append(record)
                    break

        return matched

    def _build_keyword_fields(self) -> list[str]:
        fields: list[str] = []
        for key in ("title_left", "title_right", "title_suffix", "case_no", "court", "stage"):
            value = str(self._display_fields.get(key, "")).strip()
            if value and value not in fields:
                fields.append(value)
        for value in self._keyword_fields:
            field = str(value).strip()
            if field and field not in fields:
                fields.append(field)
        return fields

    def _build_identifier_keyword_fields(self) -> list[str]:
        fields: list[str] = []
        for field in ["案号", "案件号", "项目ID", "项目 ID", "项目编号"]:
            name = str(field).strip()
            if name and name not in fields:
                fields.append(name)

        display_case_no = str(self._display_fields.get("case_no", "")).strip()
        if display_case_no and display_case_no not in fields:
            fields.append(display_case_no)
        return fields

    def _maybe_ignore_default_view(self, params: dict[str, Any], query: str) -> None:
        if self._should_keep_view_filter(query):
            return
        if "view_id" in params:
            return
        params.setdefault("ignore_default_view", True)

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
            "查", "找", "搜", "查看", "看下", "看一下",
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

        keyword = self._sanitize_search_keyword(keyword)

        # 如果关键词太短或只是常见词，返回空（查询全部）
        if len(keyword) <= 1:
            return ""
            
        return keyword

    def _sanitize_search_keyword(self, keyword: str) -> str:
        cleaned = str(keyword or "").strip()
        if not cleaned:
            return ""

        cleaned = re.sub(r"^[\s'\"“”‘’]+|[\s'\"“”‘’]+$", "", cleaned)
        cleaned = re.sub(
            r"^(?:帮我|请|麻烦|查询|查找|搜索|查看|看下|看一下|看看|看|查一下|查一查|查|找|一下|帮忙|我想|我想查)+",
            "",
            cleaned,
        ).strip()
        cleaned = re.sub(r"^(?:案号(?:为|是)?|项目ID(?:为|是)?|项目编号(?:为|是)?|编号(?:为|是)?)", "", cleaned).strip()
        cleaned = re.sub(r"(?:的)?(?:案件|案子|项目|信息)$", "", cleaned).strip()
        cleaned = re.sub(r"^(?:一下)+", "", cleaned).strip()
        cleaned = re.sub(r"\s+", "", cleaned)

        if cleaned in {"最近", "近期", "最新", "一下", "查询", "查看", "查", "找"}:
            return ""
        return cleaned

    def _looks_like_org_name(self, text: str) -> bool:
        normalized = str(text or "").strip()
        if len(normalized) < 4:
            return False
        org_tokens = ("公司", "集团", "有限公司", "有限责任", "股份", "事务所", "中心")
        return any(token in normalized for token in org_tokens)

    # ============================================
    # region 回复模板加载
    # ============================================
    def _load_response_pool(self) -> dict[str, list[str]]:
        """从 config/responses.yaml 加载业务回复模板"""
        defaults = {
            "result_opener": ["查到啦~ "],
            "empty_result": ["咦，好像没能查到任何相关记录 🤔 要不您换个关键词再试试？"],
            "error": ["抱歉，处理时遇到了点问题 😅 稍后再试试？"],
            "timeout": ["处理超时了，稍后再来？"],
        }
        responses_path = Path("config/responses.yaml")
        if not responses_path.exists():
            return defaults
        try:
            data = yaml.safe_load(responses_path.read_text(encoding="utf-8")) or {}
            for key in defaults:
                val = data.get(key)
                if isinstance(val, list) and val:
                    defaults[key] = val
            return defaults
        except Exception as exc:
            logger.warning("Failed to load responses.yaml for QuerySkill: %s", exc)
            return defaults
    # endregion
    # ============================================

    def _empty_result(self, message: str, prefer_message: bool = False) -> SkillResult:
        """构造空结果响应（随机化）"""
        pool = self._response_pool.get("empty_result")
        base_reply = random.choice(pool) if pool else "咦，没查到相关记录呢 🤔"
        
        if prefer_message and message:
            reply = f"{base_reply}\n💡 提示：{message}"
        else:
            reply = base_reply
            
        return SkillResult(
            success=True,
            skill_name=self.name,
            data={"records": [], "total": 0},
            message=message,
            reply_text=reply,
        )

    def _error_result(self, message: str) -> SkillResult:
        """构造错误响应（随机化）"""
        pool = self._response_pool.get("error")
        base_reply = random.choice(pool) if pool else "抱歉，处理时遇到了点问题 😅 稍后再试试？"
        return SkillResult(
            success=False,
            skill_name=self.name,
            message=message,
            reply_text=base_reply,
        )

    def _format_case_result(
        self,
        records: list[dict[str, Any]],
        notice: str | None = None,
        schema: list[dict[str, Any]] | None = None,
        table_name: str = "",
        pagination: dict[str, Any] | None = None,
        query_meta: dict[str, Any] | None = None,
    ) -> SkillResult:
        """格式化案件查询结果"""
        count = len(records)
        total = None
        if isinstance(pagination, dict):
            total = pagination.get("total")
        title_count = total if isinstance(total, int) and total >= count else count
        # 随机开场白
        opener_pool = self._response_pool.get("result_opener")
        opener = random.choice(opener_pool) if opener_pool else ""
        title_prefix = f"{opener}" if opener else ""
        title = f"{title_prefix}案件查询结果（共 {title_count} 条）"

        items = []
        df = self._display_fields  # 使用配置的字段名
        for i, record in enumerate(records, start=1):
            fields = record.get("fields_text") or record.get("fields", {})
            left = self._clean_text_value(fields.get(df.get("title_left", ""), ""))
            right = self._clean_text_value(fields.get(df.get("title_right", ""), ""))
            suffix = self._clean_text_value(fields.get(df.get("title_suffix", ""), ""))
            case_no = self._clean_text_value(fields.get(df.get("case_no", "案号"), ""))
            court = self._clean_text_value(fields.get(df.get("court", "审理法院"), ""))
            stage = self._clean_text_value(fields.get(df.get("stage", "程序阶段"), ""))
            status_raw = self._clean_text_value(
                fields.get("案件状态") or fields.get("状态") or fields.get("进展") or ""
            )
            status_text = self._format_status_badge(status_raw)
            detail_url = self._clean_text_value(record.get("record_url", ""))

            item_lines = [
                f"- **{i}. {left or '未填写'} vs {right or '未填写'}**｜{suffix or '未填写'}",
                f"  - **状态**：{status_text}",
                f"  - **案号**：{case_no or '未填写'}",
                f"  - **法院**：{court or '未填写'}",
                f"  - **程序**：{stage or '未填写'}",
            ]
            if detail_url:
                item_lines.append(f"  - [查看详情]({detail_url})")
            item = "\n".join(item_lines)
            items.append(item)
        
        parts = [title]
        if notice:
            parts = [notice, "", title]
        if pagination and pagination.get("has_more"):
            parts.append(self._build_truncation_hint(displayed_count=count, total=total))
        reply_text = "\n\n".join(parts + items)
        
        # 构建卡片
        card = self._build_card(title, items, notice=notice)
        
        result_data: dict[str, Any] = {
            "records": records,
            "total": title_count,
            "table_name": table_name,
            "schema": schema or [],
            "pagination": pagination or {
                "has_more": False,
                "page_token": "",
                "current_page": 1,
                "total": title_count,
            },
            "query_meta": query_meta or {},
        }
        pending_action = self._build_query_card_pending_action(
            pagination=result_data.get("pagination"),
            query_meta=result_data.get("query_meta"),
        )
        if pending_action:
            result_data["pending_action"] = pending_action

        return SkillResult(
            success=True,
            skill_name=self.name,
            data=result_data,
            message=f"查询到 {count} 条记录",
            reply_type="card",
            reply_text=reply_text,
            reply_card=card,
        )

    def _format_status_badge(self, status_text: str) -> str:
        text = self._clean_text_value(status_text)
        if not text:
            return "⚪ 未标注"

        if any(token in text for token in ["进行", "处理中", "在办", "推进"]):
            return f"🟡 {text}"
        if any(token in text for token in ["结案", "已结", "完成", "办结"]):
            return f"✅ {text}"
        if any(token in text for token in ["暂停", "中止", "挂起"]):
            return f"⏸️ {text}"
        if any(token in text for token in ["失败", "异常", "驳回"]):
            return f"❌ {text}"
        return f"🔹 {text}"

    def _build_query_card_pending_action(
        self,
        pagination: Any,
        query_meta: Any,
    ) -> dict[str, Any] | None:
        if not self._query_card_v2_enabled:
            return None

        pagination_data = pagination if isinstance(pagination, dict) else {}
        query_meta_data = query_meta if isinstance(query_meta, dict) else {}
        query_params_raw = query_meta_data.get("params")
        query_params = query_params_raw if isinstance(query_params_raw, dict) else {}

        callbacks: dict[str, dict[str, Any]] = {
            "query_list_today_hearing": {
                "callback_action": "query_list_today_hearing",
                "kind": "query",
                "query": "今天开庭",
                "extra": {
                    "table_id": query_params.get("table_id"),
                },
            },
            "query_list_week_hearing": {
                "callback_action": "query_list_week_hearing",
                "kind": "query",
                "query": "本周开庭",
                "extra": {
                    "table_id": query_params.get("table_id"),
                },
            },
        }

        has_more = bool(pagination_data.get("has_more", False))
        if has_more:
            callbacks["query_list_next_page"] = {
                "callback_action": "query_list_next_page",
                "kind": "pagination",
                "query": "下一页",
                "pagination": {
                    "tool": query_meta_data.get("tool"),
                    "params": query_params,
                    "page_token": pagination_data.get("page_token"),
                    "current_page": pagination_data.get("current_page"),
                    "total": pagination_data.get("total"),
                },
            }
        else:
            callbacks["query_list_next_page"] = {
                "callback_action": "query_list_next_page",
                "kind": "no_more",
                "text": "已经是最后一页了。",
            }

        return {
            "action": "query_list_navigation",
            "ttl_seconds": 600,
            "payload": {
                "callbacks": callbacks,
            },
        }

    def _build_truncation_hint(self, displayed_count: int, total: Any) -> str:
        shown = max(0, int(displayed_count))
        if isinstance(total, int) and total > shown:
            remaining = total - shown
            return (
                f"*(注: 为避免刷屏，当前仅展示前 {shown} 条，还有 {remaining} 条未展示。"
                "可点击下方【下一页】查看)*"
            )
        return "*(注: 为避免刷屏，当前结果较多。可点击下方【下一页】继续查看)*"

    def _clean_text_value(self, value: Any) -> str:
        text = str(value or "")
        text = text.replace("\r", " ").replace("\n", " ")
        text = re.sub(r"\s*,\s*", "，", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip(" ，")

    def _apply_schema_formatting(self, records: list[dict[str, Any]], table_id: str) -> list[dict[str, Any]]:
        if not records:
            return records
        if not table_id:
            return records
        if not self._schema_cache.get_schema(table_id):
            return records

        formatted_records: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                formatted_records.append(record)
                continue
            source_fields = record.get("fields_text")
            if not isinstance(source_fields, dict):
                source_fields = record.get("fields")
            if not isinstance(source_fields, dict):
                formatted_records.append(record)
                continue

            formatted_fields: dict[str, Any] = {}
            for field_name, field_value in source_fields.items():
                field_meta = self._schema_cache.get_field_meta(table_id, str(field_name))
                result = format_field_value(field_value, field_meta)
                record_field_format(result.field_type, result.status)
                if result.status == "malformed":
                    logger.warning(
                        "Field formatting fallback due to malformed value",
                        extra={
                            "event_code": "query.field_format.malformed",
                            "table_id": table_id,
                            "field_name": str(field_name),
                            "field_type": result.field_type,
                            "value_kind": type(field_value).__name__,
                        },
                    )
                formatted_fields[str(field_name)] = result.text

            formatted_record = dict(record)
            formatted_record["fields_text"] = formatted_fields
            formatted_records.append(formatted_record)
        return formatted_records

    def _sync_schema_cache(self, table_id: str, schema: Any) -> None:
        if not table_id:
            return
        self._schema_cache.set_schema(table_id, schema)

    def _format_doc_result(self, documents: list[dict[str, Any]]) -> SkillResult:
        """格式化文档查询结果"""
        count = len(documents)
        title = f"OK 文档搜索结果（共 {count} 条）"
        
        items = []
        for i, doc in enumerate(documents, start=1):
            item = (
                f"{i}. {doc.get('title', '未命名文档')}\n"
                f"   {doc.get('preview', '')}\n"
                f"   链接: {doc.get('url', '')}"
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
