from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
DEFAULT_SCENARIOS = ROOT / "docs" / "scenarios" / "scenarios.yaml"

sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.config import get_settings  # noqa: E402
from src.core.skills.query import QuerySkill  # noqa: E402
from src.core.types import SkillContext  # noqa: E402
from src.llm.provider import create_llm_client  # noqa: E402
from src.mcp.client import MCPClient  # noqa: E402
from src.utils.time_parser import parse_time_range  # noqa: E402

try:  # noqa: E402
    from src.core.orchestrator import AgentOrchestrator  # type: ignore
    from src.core.session import SessionManager  # type: ignore

    ORCHESTRATOR_AVAILABLE = True
    ORCHESTRATOR_IMPORT_ERROR = ""
except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
    class AgentOrchestrator:  # type: ignore[no-redef]
        pass

    class SessionManager:  # type: ignore[no-redef]
        pass

    ORCHESTRATOR_AVAILABLE = False
    ORCHESTRATOR_IMPORT_ERROR = str(exc)


PLACEHOLDER_RE = re.compile(r"\$\{([^{}]+)\}")


@dataclass
class CaseResult:
    case_id: str
    query: str
    passed: bool
    reason: str
    reply_text: str
    skill_name: str
    tool_name: str
    field_name: str


class RecordingMCPClient:
    def __init__(self, inner: MCPClient) -> None:
        self._inner = inner
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = params if isinstance(params, dict) else {}
        self.calls.append((str(tool_name), dict(payload)))
        return await self._inner.call_tool(tool_name, params)

    def clear_calls(self) -> None:
        self.calls.clear()

    def pick_last_query_tool(self) -> tuple[str, str]:
        allowed = {
            "feishu.v1.bitable.search",
            "feishu.v1.bitable.search_exact",
            "feishu.v1.bitable.search_keyword",
            "feishu.v1.bitable.search_person",
            "feishu.v1.bitable.search_date_range",
            "feishu.v1.bitable.search_advanced",
        }
        for tool_name, params in reversed(self.calls):
            if tool_name in allowed:
                return tool_name, str(params.get("field") or "")
        return "", ""

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("scenarios 文件顶层必须是对象")
    return data


def _build_runtime_variables(raw: dict[str, Any]) -> dict[str, str]:
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    next_friday = week_start + timedelta(days=11)

    vars_map: dict[str, str] = {
        "today": today.isoformat(),
        "tomorrow": (today + timedelta(days=1)).isoformat(),
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "next_friday": next_friday.isoformat(),
        "today + 7days": (today + timedelta(days=7)).isoformat(),
        "last_month_start": (today.replace(day=1) - timedelta(days=1)).replace(day=1).isoformat(),
        "last_month_end": (today.replace(day=1) - timedelta(days=1)).isoformat(),
    }

    for key, value in (raw or {}).items():
        if isinstance(value, str):
            vars_map[key] = value
    return vars_map


def _resolve_text(text: str, vars_map: dict[str, str]) -> str:
    def _replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        return str(vars_map.get(key, match.group(0)))

    return PLACEHOLDER_RE.sub(_replace, str(text))


def _resolve_any(value: Any, vars_map: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _resolve_text(value, vars_map)
    if isinstance(value, list):
        return [_resolve_any(v, vars_map) for v in value]
    if isinstance(value, dict):
        return {k: _resolve_any(v, vars_map) for k, v in value.items()}
    return value


def _extract_live_cases(
    data: dict[str, Any],
    vars_map: dict[str, str],
    scenario_ids: set[str],
    categories: set[str],
    max_cases: int,
) -> list[tuple[str, str, dict[str, Any]]]:
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list):
        return []

    cases: list[tuple[str, str, dict[str, Any]]] = []
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            continue
        sid = str(scenario.get("scenario_id") or "").strip()
        if not sid:
            continue
        if scenario_ids and sid not in scenario_ids:
            continue

        category = str(scenario.get("category") or "").strip()
        if categories and category not in categories:
            continue

        live_test = scenario.get("live_test")
        if not isinstance(live_test, dict) or not bool(live_test.get("enabled")):
            continue

        dialogue = scenario.get("dialogue")
        dialogue_variants = scenario.get("dialogue_variants")
        live_assert = _resolve_any(live_test.get("assert") or {}, vars_map)

        if isinstance(dialogue, list):
            user_turns: list[str] = []
            for turn in dialogue:
                if not isinstance(turn, dict):
                    continue
                if str(turn.get("role") or "") != "user":
                    continue
                text = turn.get("text")
                if isinstance(text, str) and text.strip():
                    user_turns.append(_resolve_text(text, vars_map))
            if user_turns:
                cases.append((sid, "dialogue", {"user_turns": user_turns, "assert": live_assert}))

        if isinstance(dialogue_variants, list):
            for idx, item in enumerate(dialogue_variants, start=1):
                text = item.get("text") if isinstance(item, dict) else None
                if isinstance(text, str) and text.strip():
                    cases.append(
                        (
                            sid,
                            f"variant_{idx}",
                            {
                                "user_turns": [_resolve_text(text, vars_map)],
                                "assert": live_assert,
                            },
                        )
                    )

        if max_cases > 0 and len(cases) >= max_cases:
            return cases[:max_cases]

    return cases


def _pick_query_meta(core: Any, user_id: str) -> tuple[str, str]:
    payload = core._state_manager.get_last_result_payload(user_id)  # pyright: ignore[reportPrivateUsage]
    if not isinstance(payload, dict):
        return "", ""
    query_meta = payload.get("query_meta")
    if not isinstance(query_meta, dict):
        return "", ""
    tool_name = str(query_meta.get("tool") or "")
    raw_params = query_meta.get("params")
    params = raw_params if isinstance(raw_params, dict) else {}
    field_name = str(params.get("field") or "")
    return tool_name, field_name


def _build_query_extra(query: str) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    parsed = parse_time_range(query)
    if parsed:
        extra["date_from"] = parsed.date_from
        extra["date_to"] = parsed.date_to
        if parsed.time_from:
            extra["time_from"] = parsed.time_from
        if parsed.time_to:
            extra["time_to"] = parsed.time_to
    return extra


async def _predict_query_plan(skill: QuerySkill, query: str, extra: dict[str, Any]) -> tuple[str, str]:
    table_result = await skill._resolve_table(query, extra)
    if table_result.get("status") != "resolved":
        return "", ""
    tool_name, params = skill._build_bitable_params(query, extra, table_result)
    return tool_name, str(params.get("field") or "")


def _load_skills_config() -> dict[str, Any]:
    path = AGENT_HOST_ROOT / "config" / "skills.yaml"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _validate_result(
    case_id: str,
    query: str,
    reply: dict[str, Any],
    skill_name: str,
    tool_name: str,
    field_name: str,
    asserts: dict[str, Any],
) -> CaseResult:
    reply_text = str(reply.get("text") or "")

    expected_intent = str(asserts.get("intent") or "").strip()
    if expected_intent and skill_name != expected_intent:
        return CaseResult(case_id, query, False, f"intent 不匹配: {skill_name} != {expected_intent}", reply_text, skill_name, tool_name, field_name)

    expected_tool = str(asserts.get("tool") or "").strip()
    if expected_tool and tool_name != expected_tool:
        return CaseResult(case_id, query, False, f"tool 不匹配: {tool_name} != {expected_tool}", reply_text, skill_name, tool_name, field_name)

    tool_in = asserts.get("tool_in")
    if isinstance(tool_in, list) and tool_in:
        allowed = {str(item) for item in tool_in}
        if tool_name not in allowed:
            return CaseResult(case_id, query, False, f"tool 不在允许集合: {tool_name}", reply_text, skill_name, tool_name, field_name)

    expected_field = str(asserts.get("field") or "").strip()
    if expected_field and field_name != expected_field:
        return CaseResult(case_id, query, False, f"field 不匹配: {field_name} != {expected_field}", reply_text, skill_name, tool_name, field_name)

    contains = asserts.get("reply_should_contain")
    if isinstance(contains, list):
        for item in contains:
            token = str(item)
            if token and token not in reply_text:
                return CaseResult(case_id, query, False, f"回复未包含: {token}", reply_text, skill_name, tool_name, field_name)

    not_contains = asserts.get("reply_should_not_contain")
    if isinstance(not_contains, list):
        for item in not_contains:
            token = str(item)
            if token and token in reply_text:
                return CaseResult(case_id, query, False, f"回复包含禁用词: {token}", reply_text, skill_name, tool_name, field_name)

    if bool(asserts.get("no_confirm_prompt")) and "请确认表名" in reply_text:
        return CaseResult(case_id, query, False, "误触发表名确认", reply_text, skill_name, tool_name, field_name)

    return CaseResult(case_id, query, True, "ok", reply_text, skill_name, tool_name, field_name)


async def _run(args: argparse.Namespace) -> int:
    os.chdir(str(AGENT_HOST_ROOT))
    load_dotenv(AGENT_HOST_ROOT / ".env")

    data = _load_yaml(Path(args.scenarios))
    raw_variables = data.get("variables")
    vars_map = _build_runtime_variables(raw_variables if isinstance(raw_variables, dict) else {})

    scenario_ids = {item.strip() for item in (args.scenario_id or []) if item.strip()}
    categories = {item.strip() for item in (args.category or []) if item.strip()}
    cases = _extract_live_cases(data, vars_map, scenario_ids, categories, max_cases=int(args.max_cases))
    if not cases:
        print("[info] 没有可执行的 live_test 场景。")
        return 0

    settings = get_settings()
    mcp_client = RecordingMCPClient(MCPClient(settings))
    llm_client = create_llm_client(settings.llm)

    orchestrator_mode = ORCHESTRATOR_AVAILABLE and not bool(args.query_skill_only)
    core: Any = None
    query_skill: QuerySkill | None = None

    if orchestrator_mode:
        session_cls: Any = SessionManager
        orchestrator_cls: Any = AgentOrchestrator
        session_manager = session_cls(settings.session)
        core = orchestrator_cls(
            settings=settings,
            session_manager=session_manager,
            mcp_client=mcp_client,
            llm_client=llm_client,
            skills_config_path="config/skills.yaml",
        )
    else:
        if not ORCHESTRATOR_AVAILABLE and not bool(args.query_skill_only):
            print(f"[warn] Orchestrator 不可用，降级为 QuerySkill 模式: {ORCHESTRATOR_IMPORT_ERROR}")
        query_skill = QuerySkill(
            mcp_client=mcp_client,
            settings=settings,
            llm_client=llm_client,
            skills_config=_load_skills_config(),
        )

    total = 0
    failed = 0

    for sid, case_kind, payload in cases:
        total += 1
        user_id = f"scenario-live-{sid}-{case_kind}"
        raw_turns = payload.get("user_turns") if isinstance(payload, dict) else None
        user_turns = [str(item) for item in raw_turns] if isinstance(raw_turns, list) else []
        raw_assert = payload.get("assert") if isinstance(payload, dict) else None
        live_assert: dict[str, Any] = raw_assert if isinstance(raw_assert, dict) else {}

        last_reply: dict[str, Any] = {}
        last_query = ""
        tool_name = ""
        field_name = ""

        if orchestrator_mode:
            for query in user_turns:
                last_query = str(query)
                mcp_client.clear_calls()
                last_reply = await core.handle_message(user_id=user_id, text=last_query)
            tool_name, field_name = mcp_client.pick_last_query_tool()
        else:
            assert query_skill is not None
            for query in user_turns:
                last_query = str(query)
                extra = _build_query_extra(last_query)
                planned_tool, planned_field = await _predict_query_plan(query_skill, last_query, extra)
                mcp_client.clear_calls()
                result = await query_skill.execute(
                    SkillContext(
                        query=last_query,
                        user_id=user_id,
                        last_result=None,
                        last_skill=None,
                        extra=extra,
                    )
                )
                last_reply = {
                    "text": str(result.reply_text or ""),
                    "outbound": {"meta": {"skill_name": str(result.skill_name or "")}},
                }
                actual_tool, actual_field = mcp_client.pick_last_query_tool()
                tool_name = actual_tool or planned_tool
                field_name = actual_field or planned_field

        raw_outbound = last_reply.get("outbound")
        outbound: dict[str, Any] = raw_outbound if isinstance(raw_outbound, dict) else {}
        raw_meta = outbound.get("meta")
        meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
        skill_name = str(meta.get("skill_name") or "")

        result = _validate_result(
            case_id=f"{sid}:{case_kind}",
            query=last_query,
            reply=last_reply,
            skill_name=skill_name,
            tool_name=tool_name,
            field_name=field_name,
            asserts=live_assert,
        )

        if result.passed:
            if bool(args.show_pass):
                print(f"[PASS] {result.case_id} | {result.query}")
                print(f"       skill={result.skill_name} tool={result.tool_name} field={result.field_name}")
        else:
            failed += 1
            print(f"[FAIL] {result.case_id} | {result.query}")
            print(f"       reason={result.reason}")
            print(f"       skill={result.skill_name} tool={result.tool_name} field={result.field_name}")
            print(f"       reply={result.reply_text}")

    passed = total - failed
    print(f"\n[summary] total={total} passed={passed} failed={failed}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="按 scenarios.yaml 执行 live 对话场景测试")
    parser.add_argument("--scenarios", default=str(DEFAULT_SCENARIOS), help="scenarios.yaml 路径")
    parser.add_argument("--scenario-id", action="append", help="只跑指定 scenario_id，可重复")
    parser.add_argument("--category", action="append", help="只跑指定 category，可重复")
    parser.add_argument("--max-cases", type=int, default=0, help="最多执行场景数，0 表示不限制")
    parser.add_argument("--show-pass", action="store_true", help="显示通过用例")
    parser.add_argument("--query-skill-only", action="store_true", help="跳过 orchestrator，直接用 QuerySkill 执行")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
