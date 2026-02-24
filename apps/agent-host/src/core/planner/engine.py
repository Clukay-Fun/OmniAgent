"""
L1 Planner 引擎。

职责：
- 单次 LLM 规划 intent/tool/params
- 输出 schema 校验
- LLM 失败时规则降级
"""

from __future__ import annotations

from datetime import date, timedelta
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

    async def plan(self, query: str, *, user_profile: Any = None) -> PlannerOutput | None:
        if not self._enabled:
            return self._fallback_plan(query, user_profile=user_profile)

        # 无可用 LLM 配置则直接规则降级
        if not getattr(getattr(self._llm, "_settings", None), "api_key", ""):
            return self._fallback_plan(query, user_profile=user_profile)

        user_prompt = f"用户输入：{query}\n请输出 JSON。"
        try:
            raw = await self._llm.chat_json(
                user_prompt,
                system=self._system_prompt,
                timeout=10,
            )
            if not isinstance(raw, dict) or not raw:
                return self._fallback_plan(query, user_profile=user_profile)
            try:
                self._warn_close_semantic_drift(raw)
                output = PlannerOutput.model_validate(raw)
            except ValidationError as exc:
                logger.warning("Planner schema validation failed: %s", exc)
                return self._fallback_plan(query, user_profile=user_profile)
            return output
        except Exception as exc:
            logger.warning("Planner failed, fallback to rules: %s", exc)
            return self._fallback_plan(query, user_profile=user_profile)

    def _fallback_plan(self, query: str, *, user_profile: Any = None) -> PlannerOutput | None:
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

        structured_field_plan = self._build_structured_field_plan(text)
        if structured_field_plan is not None:
            return structured_field_plan

        # 组合查询：人员 + 法院 + 时间
        has_person_pattern = bool(re.search(r"([^的\s]{2,8})的(?:案件|案子|项目)", text))
        has_court = any(token in normalized for token in ["法院", "中院", "高院", "基层院"])
        has_time = any(token in normalized for token in [
            "今天",
            "明天",
            "后天",
            "过两天",
            "两天后",
            "本周",
            "下周",
            "本月",
            "上个月",
            "下个月",
            "未来",
            "后续",
            "本年",
        ])
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

        today = date.today()
        if any(token in normalized for token in ["已经开过庭", "开过庭的", "已开庭的", "开过庭"]):
            return PlannerOutput(
                intent="query_date_range",
                tool="search_date_range",
                params={
                    "field": "开庭日",
                    "date_to": (today - timedelta(days=1)).isoformat(),
                },
                confidence=0.9,
            )

        if any(token in normalized for token in ["后续要开庭", "后续开庭", "待开庭", "未来开庭", "接下来开庭"]):
            return PlannerOutput(
                intent="query_date_range",
                tool="search_date_range",
                params={
                    "field": "开庭日",
                    "date_from": today.isoformat(),
                    "date_to": (today + timedelta(days=3650)).isoformat(),
                },
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
            "过两天", "两天后", "未来", "后续",
            "明早", "今早", "上午", "下午", "中午", "晚上", "今晚", "明晚", "凌晨", "傍晚",
        ]) or bool(re.search(r"\d{1,2}月\d{1,2}", text)) or bool(re.search(r"(?<!\d)\d{1,2}月(?!\d)", text)) or bool(re.search(r"\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}", text)) or bool(re.search(r"(?<!\d)\d{1,2}[-/\.]\d{1,2}(?!\d)", text)) or bool(re.search(r"(?:未来|接下来)\s*[一二两三四五六七八九十\d]{1,3}\s*天", normalized)) or bool(re.search(r"\d{1,2}[:：]\d{1,2}|\d{1,2}点(?:\d{1,2}分?|半)?", text))
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

        # 指定主体案件（X 的案子）消歧：当前用户 > 律师 > 当事人
        subject = self._extract_subject_entity(text)
        if subject:
            if self._is_current_user_subject(subject, user_profile):
                return PlannerOutput(
                    intent="query_my_cases",
                    tool="search_person",
                    params={"field": "主办律师"},
                    confidence=0.94,
                )

            if self._looks_like_lawyer_subject(subject, normalized):
                return PlannerOutput(
                    intent="query_person",
                    tool="search_keyword",
                    params={
                        "keyword": subject,
                        "fields": ["主办律师", "协办律师"],
                    },
                    confidence=0.9,
                )

            return PlannerOutput(
                intent="query_person",
                tool="search_keyword",
                params={
                    "keyword": subject,
                    "fields": ["委托人", "对方当事人", "联系人"],
                },
                confidence=0.86,
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

    def _build_structured_field_plan(self, text: str) -> PlannerOutput | None:
        rules: list[tuple[tuple[str, ...], list[str], float]] = [
            (("对方当事人",), ["对方当事人"], 0.9),
            (("联系人",), ["联系人"], 0.9),
            (("法官", "承办法官"), ["承办法官"], 0.9),
            (("法院", "审理法院"), ["审理法院"], 0.88),
            (("案由",), ["案由"], 0.88),
            (("当事人",), ["委托人", "对方当事人", "联系人"], 0.88),
        ]

        for labels, fields, confidence in rules:
            value = self._extract_value_after_label(text, labels)
            if not value:
                continue
            return PlannerOutput(
                intent="query_person",
                tool="search_keyword",
                params={"keyword": value, "fields": fields},
                confidence=confidence,
            )
        return None

    def _extract_value_after_label(self, text: str, labels: tuple[str, ...]) -> str:
        for label in labels:
            pattern = rf"(?:{re.escape(label)})\s*(?:是|为|=|:|：)?\s*([^，。,.！？!\s][^，。,.！？!]{{0,40}})"
            matched = re.search(pattern, text)
            if not matched:
                continue
            raw = matched.group(1).strip()
            value = re.sub(r"(?:的)?(?:案件|案子|项目)$", "", raw).strip()
            value = re.sub(r"^(?:是|为)", "", value).strip()
            if value:
                return value
        return ""

    def _extract_subject_entity(self, text: str) -> str:
        matched = re.search(r"([^的\s，。,.！？!]{1,32})的(?:案件|案子|项目)", text)
        if not matched:
            return ""
        return self._clean_subject(matched.group(1))

    def _clean_subject(self, value: str) -> str:
        subject = str(value or "").strip()
        subject = re.sub(r"^(?:查询|查找|搜索|查看|看看|帮我查|帮我|请帮我|请|麻烦)", "", subject).strip()
        subject = re.sub(r"(?:负责的?|相关的?|有关的?)$", "", subject).strip()
        return subject

    def _is_current_user_subject(self, subject: str, user_profile: Any) -> bool:
        normalized = str(subject or "").strip()
        if normalized in {"我", "自己", "本人"}:
            return True
        if user_profile is None:
            return False

        names = {
            str(getattr(user_profile, "name", "") or "").strip(),
            str(getattr(user_profile, "lawyer_name", "") or "").strip(),
        }
        names = {name for name in names if name}
        return normalized in names

    def _looks_like_org_name(self, text: str) -> bool:
        normalized = str(text or "").strip()
        if len(normalized) < 4:
            return False
        tokens = ("公司", "集团", "有限", "股份", "事务所", "中心", "医院", "学校", "委员会")
        return any(token in normalized for token in tokens)

    def _looks_like_person_name(self, text: str) -> bool:
        normalized = str(text or "").strip()
        if not (2 <= len(normalized) <= 6):
            return False
        if self._looks_like_org_name(normalized):
            return False
        if any(ch.isdigit() for ch in normalized):
            return False
        return bool(re.fullmatch(r"[A-Za-z\u4e00-\u9fa5]+", normalized))

    def _looks_like_lawyer_subject(self, subject: str, normalized_query: str) -> bool:
        if self._looks_like_org_name(subject):
            return False
        if any(token in normalized_query for token in ["当事人", "委托人", "被告", "原告", "联系人", "客户"]):
            return False
        if any(token in normalized_query for token in ["律师", "主办", "协办", "经办", "承办"]):
            return True
        return self._looks_like_person_name(subject)

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
