"""
L1 Planner 引擎。

职责：
- 单次 LLM 规划 intent/tool/params
- 输出 schema 校验
- LLM 失败时规则降级
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import ValidationError

from src.core.planner.prompt_builder import build_planner_system_prompt, load_scenario_rules
from src.core.planner.schema import PlannerOutput

logger = logging.getLogger(__name__)


class PlannerEngine:
    """L1 Planner。"""

    def __init__(
        self,
        llm_client: Any,
        scenarios_dir: str,
        enabled: bool = True,
    ) -> None:
        self._llm = llm_client
        self._enabled = enabled
        self._rules = load_scenario_rules(scenarios_dir)
        self._system_prompt = build_planner_system_prompt(self._rules)

    async def plan(self, query: str) -> PlannerOutput | None:
        if not self._enabled:
            return self._fallback_plan(query)

        # 无可用 LLM 配置则直接规则降级
        if not getattr(getattr(self._llm, "_settings", None), "api_key", ""):
            return self._fallback_plan(query)

        user_prompt = f"用户输入：{query}\n请输出 JSON。"
        try:
            raw = await self._llm.chat_json(
                user_prompt,
                system=self._system_prompt,
                timeout=10,
            )
            if not isinstance(raw, dict) or not raw:
                return self._fallback_plan(query)
            try:
                self._warn_close_semantic_drift(raw)
                output = PlannerOutput.model_validate(raw)
            except ValidationError as exc:
                logger.warning("Planner schema validation failed: %s", exc)
                return self._fallback_plan(query)
            return output
        except Exception as exc:
            logger.warning("Planner failed, fallback to rules: %s", exc)
            return self._fallback_plan(query)

    def _fallback_plan(self, query: str) -> PlannerOutput | None:
        text = (query or "").strip()
        normalized = text.replace(" ", "")

        # 越权/注入类输入：统一降级为 out_of_scope
        if any(token in normalized.lower() for token in ["drop table", "ignore previous", "system prompt"]):
            return PlannerOutput(
                intent="out_of_scope",
                tool="none",
                params={},
                confidence=0.95,
            )
        if any(token in normalized for token in ["忽略之前", "系统提示", "越狱", "写一首诗"]):
            return PlannerOutput(
                intent="out_of_scope",
                tool="none",
                params={},
                confidence=0.92,
            )

        case_tokens = ["案件", "案子", "项目"]
        has_case_token = any(token in normalized for token in case_tokens)

        # 表/台账/库类泛查询（表名识别前置）
        if any(token in normalized for token in ["什么表", "哪个表", "那个表"]):
            return PlannerOutput(
                intent="clarify_needed",
                tool="none",
                params={},
                confidence=0.6,
                clarify_question="请说明您要查询哪类数据，例如：案件、收费、招投标。",
            )

        if normalized in {"查数据", "看看数据", "查一下数据", "数据"}:
            return PlannerOutput(
                intent="clarify_needed",
                tool="none",
                params={},
                confidence=0.55,
                clarify_question="请补充您要查的数据类型，例如：查所有案件、查收费记录。",
            )

        if any(token in normalized for token in ["表", "台账", "登记", "库"]) and any(
            token in normalized for token in ["查", "查询", "看", "搜索", "找", "有什么", "哪些"]
        ):
            return PlannerOutput(
                intent="query_all",
                tool="search",
                params={"ignore_default_view": True},
                confidence=0.72,
            )

        if any(token in normalized for token in ["收费", "费用", "缴费"]) and any(
            token in normalized for token in ["查", "查询", "看", "搜索", "找", "情况"]
        ):
            return PlannerOutput(
                intent="query_all",
                tool="search",
                params={"ignore_default_view": True},
                confidence=0.75,
            )

        # 视图查询（优先）
        if has_case_token and any(token in normalized for token in ["按视图", "当前视图", "仅视图", "视图内", "只看视图", "视图"]):
            return PlannerOutput(
                intent="query_view",
                tool="search",
                params={},
                confidence=0.9,
            )

        # 提醒相关
        if any(token in normalized for token in ["查看提醒", "提醒列表", "我的提醒", "有哪些提醒", "查看待办", "待办列表"]):
            return PlannerOutput(
                intent="list_reminders",
                tool="reminder.list",
                params={},
                confidence=0.95,
            )

        if any(token in normalized for token in ["取消提醒", "撤销提醒"]) or (
            any(token in normalized for token in ["取消", "撤销", "不要"]) and any(token in normalized for token in ["提醒", "开庭前", "提前"]) 
        ):
            return PlannerOutput(
                intent="cancel_reminder",
                tool="reminder.cancel",
                params={},
                confidence=0.92,
            )

        if any(token in normalized for token in ["提醒我", "帮我提醒", "帮我设置提醒", "设置提醒", "设提醒", "记得", "别忘了", "提醒一下", "开庭前", "提前提醒", "提前"]):
            return PlannerOutput(
                intent="create_reminder",
                tool="reminder.create",
                params={},
                confidence=0.9,
            )

        # CRUD 相关
        if any(token in normalized for token in ["新增", "创建", "添加", "新建"]) and has_case_token:
            return PlannerOutput(
                intent="create_record",
                tool="record.create",
                params={},
                confidence=0.88,
            )

        if any(token in normalized for token in ["更新", "修改", "改成", "改为", "变更"]):
            return PlannerOutput(
                intent="update_record",
                tool="record.update",
                params={},
                confidence=0.86,
            )

        if any(token in normalized for token in ["结案", "判决生效", "撤诉", "调解结案"]):
            return PlannerOutput(
                intent="close_record",
                tool="record.close",
                params={"close_semantic": "default"},
                confidence=0.9,
            )

        if any(token in normalized for token in ["执行终本", "终本", "终结本次执行", "执行不了了"]):
            return PlannerOutput(
                intent="close_record",
                tool="record.close",
                params={"close_semantic": "enforcement_end"},
                confidence=0.9,
            )

        if any(token in normalized for token in ["删除", "移除"]) and any(token in normalized for token in ["案件", "案号", "项目", "记录"]):
            return PlannerOutput(
                intent="delete_record",
                tool="record.delete",
                params={},
                confidence=0.9,
            )

        # 组合查询：人员 + 法院 + 时间
        has_person_pattern = bool(re.search(r"([^的\s]{2,6})的案件", text))
        has_court = any(token in normalized for token in ["法院", "中院", "高院", "基层院"])
        has_time = any(token in normalized for token in ["今天", "明天", "后天", "本周", "下周", "本月", "本年"])
        status_candidates = ["进行中", "审理中", "已结案", "已完结", "待开庭", "已开庭"]
        has_status = any(token in normalized for token in status_candidates)
        if has_person_pattern and has_court and has_time:
            return PlannerOutput(
                intent="query_advanced",
                tool="search_advanced",
                params={},
                confidence=0.9,
            )

        if has_status and has_time and any(token in normalized for token in ["开庭", "庭审"]):
            return PlannerOutput(
                intent="query_advanced",
                tool="search_advanced",
                params={},
                confidence=0.86,
            )

        # 我的案件（优先于“xx的案件”文本模式）
        if any(token in normalized for token in ["我的案件", "我负责", "我的案子", "我经手", "我跟进"]):
            return PlannerOutput(
                intent="query_my_cases",
                tool="search_person",
                params={"field": "主办律师"},
                confidence=0.93,
            )

        # 日期范围查询（优先于“xx的案件”文本模式）
        has_date_keyword = any(token in normalized for token in [
            "今天", "明天", "后天", "本周", "下周", "本月", "上周", "上个月", "下个月", "这周", "这月", "期间", "到", "至", "最近", "近期",
            "明早", "今早", "上午", "下午", "中午", "晚上", "今晚", "明晚", "凌晨", "傍晚",
        ]) or bool(re.search(r"\d{1,2}月\d{1,2}", text)) or bool(re.search(r"\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}", text)) or bool(re.search(r"(?<!\d)\d{1,2}[-/\.]\d{1,2}(?!\d)", text)) or bool(re.search(r"\d{1,2}[:：]\d{1,2}|\d{1,2}点(?:\d{1,2}分?|半)?", text))
        if has_date_keyword and any(token in normalized for token in ["开庭", "庭审"]):
            return PlannerOutput(
                intent="query_date_range",
                tool="search_date_range",
                params={"field": "开庭日"},
                confidence=0.85,
            )

        # 时间词 + 案件词（无明确开庭词）的弱日期查询兜底
        if has_date_keyword and has_case_token and any(token in normalized for token in ["案号", "安排", "日程"]):
            return PlannerOutput(
                intent="query_date_range",
                tool="search_date_range",
                params={"field": "开庭日"},
                confidence=0.72,
            )

        # 状态精确筛选（优先于“xx的案件”文本模式）
        status_value = next((s for s in status_candidates if s in normalized), "")
        if status_value and has_case_token:
            return PlannerOutput(
                intent="query_exact",
                tool="search_exact",
                params={"field": "案件状态", "value": status_value},
                confidence=0.84,
            )

        # 无前缀的项目编号（如 PRJ-2024-088）
        bare_project_id = re.search(r"\b[A-Z]{2,}-\d{4}-\d{2,}\b", text)
        if bare_project_id:
            return PlannerOutput(
                intent="query_exact",
                tool="search_exact",
                params={"field": "项目ID", "value": bare_project_id.group(0)},
                confidence=0.8,
            )

        # 指定人员案件
        person_match = re.search(r"([^的\s]{2,8})的案件", text)
        if person_match:
            person_name = person_match.group(1).strip()
            person_name = re.sub(r"^(查询|查找|查|搜索|找|请查|请帮我查)", "", person_name).strip()
            stopwords = {
                "所有", "全部", "我的", "自己",
                "今天", "明天", "后天", "本周", "下周", "本月",
                "进行中", "审理中", "已结案", "已完结", "待开庭", "已开庭",
                "开庭", "庭审", "中院", "高院", "法院", "视图", "按视图", "当前视图",
            }
            if person_name and person_name not in stopwords and "案号" not in person_name:
                return PlannerOutput(
                    intent="query_person",
                    tool="search_keyword",
                    params={"keyword": person_name},
                    confidence=0.88,
                )

        if any(token in normalized for token in ["所有案件", "全部案件", "案件列表", "查全部", "所有项目", "全部项目"]):
            if any(token in normalized for token in ["按视图", "当前视图", "仅视图", "视图内", "只看视图"]):
                return PlannerOutput(
                    intent="query_view",
                    tool="search",
                    params={},
                    confidence=0.95,
                )
            return PlannerOutput(
                intent="query_all",
                tool="search",
                params={"ignore_default_view": True},
                confidence=0.95,
            )

        if has_case_token and any(token in normalized for token in ["有什么", "有哪些", "列表", "清单"]):
            return PlannerOutput(
                intent="query_all",
                tool="search",
                params={"ignore_default_view": True},
                confidence=0.8,
            )

        # 合同/侵权等模糊组合查询兜底
        if has_case_token and any(token in normalized for token in ["合同", "侵权", "纠纷", "之前", "那个"]):
            return PlannerOutput(
                intent="query_advanced",
                tool="search_advanced",
                params={},
                confidence=0.68,
            )

        exact_case = re.search(r"(?:案号|案件号)[是为:：\s]*([A-Za-z0-9\-_/（）()_\u4e00-\u9fa5]+)", text)
        if exact_case:
            return PlannerOutput(
                intent="query_exact",
                tool="search_exact",
                params={"field": "案号", "value": exact_case.group(1).strip()},
                confidence=0.95,
            )

        exact_project = re.search(r"(?:项目ID|项目编号|项目号)[是为:：\s]*([A-Za-z0-9\-_/（）()_\u4e00-\u9fa5]+)", text)
        if exact_project:
            return PlannerOutput(
                intent="query_exact",
                tool="search_exact",
                params={"field": "项目ID", "value": exact_project.group(1).strip()},
                confidence=0.94,
            )

        if any(token in normalized for token in ["查", "查询", "找", "搜索"]) and any(
            token in normalized for token in ["案件", "案子", "项目"]
        ):
            return PlannerOutput(
                intent="query_all",
                tool="search",
                params={"ignore_default_view": True},
                confidence=0.7,
            )

        if len(normalized) <= 1:
            return PlannerOutput(
                intent="clarify_needed",
                tool="none",
                params={},
                confidence=0.2,
                clarify_question="请再描述一下您的需求，例如：查所有案件、我的案件、查案号 XXX。",
            )

        return None

    def _warn_close_semantic_drift(self, raw: dict[str, Any]) -> None:
        intent = str(raw.get("intent") or "").strip()
        tool = str(raw.get("tool") or "").strip()
        params_raw = raw.get("params")
        params = params_raw if isinstance(params_raw, dict) else {}
        close_related = intent == "close_record" or tool == "record.close"
        if not close_related:
            return

        if "close_semantic" not in params and any(alias in params for alias in ("close_type", "close_profile", "profile")):
            logger.warning(
                "Planner close semantic alias is not allowed, fallback to default",
                extra={
                    "event_code": "planner.schema.close_semantic.alias_rejected",
                    "intent": intent,
                    "tool": tool,
                },
            )

        semantic = str(params.get("close_semantic") or "").strip()
        if semantic and semantic not in {"default", "enforcement_end"}:
            logger.warning(
                "Planner close semantic invalid, fallback to default",
                extra={
                    "event_code": "planner.schema.close_semantic.invalid",
                    "intent": intent,
                    "tool": tool,
                    "close_semantic": semantic,
                },
            )
