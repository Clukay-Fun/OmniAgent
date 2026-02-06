from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.l0 import L0RuleEngine
from src.core.planner import PlannerEngine
from src.core.state import ConversationStateManager, MemoryStateStore
from src.core.skills.reminder import ReminderSkill
from src.core.types import SkillContext


@dataclass
class _StubLLMSettings:
    api_key: str = ""


@dataclass
class _StubLLMClient:
    _settings: _StubLLMSettings


def _load_tests(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    tests = data.get("tests")
    if not isinstance(tests, list):
        return []
    return [item for item in tests if isinstance(item, dict)]


def _subset_match(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key, value in expected.items():
        if key not in actual:
            return False
        if isinstance(value, dict):
            if not isinstance(actual[key], dict):
                return False
            if not _subset_match(actual[key], value):
                return False
        else:
            if actual[key] != value:
                return False
    return True


async def run_planner_tests(test_files: list[Path], scenarios_dir: Path) -> tuple[int, int]:
    planner = PlannerEngine(_StubLLMClient(_StubLLMSettings()), str(scenarios_dir), enabled=True)
    passed = 0
    failed = 0

    for file in test_files:
        for case in _load_tests(file):
            case_id = str(case.get("id") or file.name)
            text = str(case.get("input") or "")
            expect_raw = case.get("expect")
            expect: dict[str, Any] = expect_raw if isinstance(expect_raw, dict) else {}

            output = await planner.plan(text)
            if output is None:
                print(f"[FAIL] {case_id}: planner returned None")
                failed += 1
                continue

            ok = True
            if "intent" in expect and output.intent != expect["intent"]:
                ok = False
            if "tool" in expect and output.tool != expect["tool"]:
                ok = False
            if "params" in expect:
                if not isinstance(expect["params"], dict):
                    ok = False
                elif not _subset_match(output.params, expect["params"]):
                    ok = False

            if ok:
                print(f"[PASS] {case_id}")
                passed += 1
            else:
                print(
                    f"[FAIL] {case_id}: expect={expect}, got={{'intent': '{output.intent}', 'tool': '{output.tool}', 'params': {output.params}}}"
                )
                failed += 1

    return passed, failed


def _extract_user_input(scenario: dict[str, Any], prefer_last: bool = False) -> str | None:
    dialogue = scenario.get("dialogue")
    if isinstance(dialogue, list):
        user_texts: list[str] = []
        for item in dialogue:
            if isinstance(item, dict) and str(item.get("role") or "").lower() == "user":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    user_texts.append(text.strip())
        if user_texts:
            return user_texts[-1] if prefer_last else user_texts[0]

    variants = scenario.get("dialogue_variants")
    if isinstance(variants, list):
        for v in variants:
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, dict):
                text_val = v.get("text")
                if isinstance(text_val, str) and text_val.strip():
                    return text_val.strip()

    trigger = scenario.get("trigger")
    if isinstance(trigger, str) and trigger.strip():
        return trigger.strip()

    return None


def _map_tool_from_docs(expected_tool: str) -> str | None:
    mapping = {
        "feishu.v1.bitable.search": "search",
        "feishu.v1.bitable.search_exact": "search_exact",
        "feishu.v1.bitable.search_keyword": "search_keyword",
        "feishu.v1.bitable.search_person": "search_person",
        "feishu.v1.bitable.search_date_range": "search_date_range",
        "feishu.v1.bitable.search_advanced": "search_advanced",
        "feishu.v1.bitable.record.create": "record.create",
        "feishu.v1.bitable.record.update": "record.update",
        "feishu.v1.bitable.record.delete": "record.delete",
        "reminder.create": "reminder.create",
        "reminder.list": "reminder.list",
        "reminder.cancel": "reminder.cancel",
    }
    return mapping.get(expected_tool)


def _infer_tool_from_docs(item: dict[str, Any], expected: dict[str, Any], user_input: str) -> str | None:
    """在 expected.tool 缺失时，根据 category/expected.action 推断工具。"""
    category = str(item.get("category") or "")
    scenario_id = str(item.get("scenario_id") or "")
    action = str(expected.get("action") or "")
    intent = str(expected.get("intent") or "")

    if category == "query_all":
        return "search"
    if category == "query_view":
        return "search"
    if category == "query_exact":
        return "search_exact"
    if category == "query_date":
        return "search_date_range"
    if category == "query_advanced":
        return "search_advanced"
    if category == "query_person":
        if scenario_id.startswith("S003"):
            return "search_person"
        return "search_keyword"

    if category == "table_recognition":
        recognition_raw = expected.get("recognition")
        recognition: dict[str, Any] = recognition_raw if isinstance(recognition_raw, dict) else {}
        action = str(recognition.get("action") or "")
        if action in {"template_ask", "no_match"}:
            return None
        return "search"

    if category == "pagination":
        text = user_input.replace(" ", "")
        if any(token in text for token in ["我的", "我负责", "我经手"]):
            return "search_person"
        if "的案件" in text and not any(token in text for token in ["我的", "我负责", "我经手"]):
            return "search_keyword"
        if any(token in text for token in ["下一页", "继续", "更多"]):
            return None
        return "search"

    if category == "crud_create":
        return "record.create"
    if category == "crud_update":
        return "record.update"
    if category == "crud_delete":
        return "record.delete"

    if category == "context":
        return "record.update"

    if category == "reminder":
        if action in {"list"}:
            return "reminder.list"
        if action in {"cancel"}:
            return "reminder.cancel"
        if action in {"create", "create_from_case", "clarify_time"}:
            return "reminder.create"
        if str(expected.get("result") or "") == "invalid_time":
            return "reminder.create"
        if intent == "ReminderSkill":
            return "reminder.create"

    return None


def _is_template_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return value.startswith("${") and value.endswith("}")


def _extract_expected_params_from_docs(expected: dict[str, Any], mapped_tool: str) -> dict[str, Any]:
    """从 docs 的 expected.key_params 提取可断言参数子集。"""
    key_params = expected.get("key_params")
    if not isinstance(key_params, dict):
        return {}

    params: dict[str, Any] = {}

    # 查询全量场景：view_id=None 可映射为 ignore_default_view=true
    if mapped_tool == "search":
        view_id = key_params.get("view_id")
        if view_id is None:
            params["ignore_default_view"] = True
        return params

    if mapped_tool == "search_exact":
        field_name = key_params.get("field_name")
        value = key_params.get("value")
        if isinstance(field_name, str) and field_name.strip():
            params["field"] = field_name.strip()
        if isinstance(value, str) and value.strip() and not _is_template_value(value):
            params["value"] = value.strip()
        return params

    if mapped_tool == "search_keyword":
        keyword = key_params.get("keyword")
        if isinstance(keyword, str) and keyword.strip() and not _is_template_value(keyword):
            params["keyword"] = keyword.strip()
        return params

    if mapped_tool == "search_person":
        field_name = key_params.get("field_name")
        if isinstance(field_name, str) and field_name.strip():
            params["field"] = field_name.strip()
        return params

    if mapped_tool == "search_date_range":
        field_name = key_params.get("field_name")
        if isinstance(field_name, str) and field_name.strip():
            params["field"] = field_name.strip()
        return params

    return params


def _docs_params_match(expected_params: dict[str, Any], actual_params: dict[str, Any], tool: str) -> bool:
    """docs 参数断言（支持部分字段别名兼容）。"""
    if not expected_params:
        return True

    project_id_alias = {"项目id", "项目ID", "项目编号", "项目号"}

    for key, expected_value in expected_params.items():
        if key not in actual_params:
            return False
        actual_value = actual_params[key]

        if key == "field" and tool == "search_exact":
            if isinstance(expected_value, str) and isinstance(actual_value, str):
                if expected_value in project_id_alias and actual_value in project_id_alias:
                    continue

        if actual_value != expected_value:
            return False

    return True


async def run_docs_projection_tests(docs_file: Path, scenarios_dir: Path) -> tuple[int, int, int]:
    """从 docs/scenarios.yaml 提取可映射用例，校验 Planner tool 路由。"""
    if not docs_file.exists():
        return 0, 0, 0

    with docs_file.open("r", encoding="utf-8") as f:
        docs_data = yaml.safe_load(f) or {}

    scenarios = docs_data.get("scenarios")
    if not isinstance(scenarios, list):
        return 0, 0, 0

    planner = PlannerEngine(_StubLLMClient(_StubLLMSettings()), str(scenarios_dir), enabled=True)

    passed = 0
    failed = 0
    skipped = 0

    for item in scenarios:
        if not isinstance(item, dict):
            skipped += 1
            continue

        category = str(item.get("category") or "")
        if category in {"context", "security"}:
            skipped += 1
            continue

        scenario_id = str(item.get("scenario_id") or "DOC")
        expected = item.get("expected")
        if not isinstance(expected, dict):
            skipped += 1
            continue

        user_input = _extract_user_input(item, prefer_last=(category == "context"))
        if not user_input:
            skipped += 1
            continue

        expected_tool: str | None = None
        expected_tool_raw = expected.get("tool")
        if isinstance(expected_tool_raw, str):
            expected_tool = _map_tool_from_docs(expected_tool_raw)
        else:
            expected_tool = _infer_tool_from_docs(item, expected, user_input)

        if not expected_tool:
            skipped += 1
            continue

        output = await planner.plan(user_input)
        if output is None:
            print(f"[FAIL] {scenario_id}: planner returned None")
            failed += 1
            continue

        expected_params = _extract_expected_params_from_docs(expected, expected_tool)

        ok = output.tool == expected_tool
        if ok and expected_params:
            ok = _docs_params_match(expected_params, output.params, expected_tool)

        if ok:
            print(f"[PASS] {scenario_id} (docs)")
            passed += 1
        else:
            print(
                f"[FAIL] {scenario_id} (docs): expect tool={expected_tool}, params={expected_params}, "
                f"got tool={output.tool}, params={output.params}"
            )
            failed += 1

    return passed, failed, skipped


async def run_docs_guardrail_tests(docs_file: Path, scenarios_dir: Path) -> tuple[int, int, int]:
    """对 docs 中未映射到具体 tool 的场景做守卫性断言。"""
    if not docs_file.exists():
        return 0, 0, 0

    with docs_file.open("r", encoding="utf-8") as f:
        docs_data = yaml.safe_load(f) or {}

    scenarios = docs_data.get("scenarios")
    if not isinstance(scenarios, list):
        return 0, 0, 0

    planner = PlannerEngine(_StubLLMClient(_StubLLMSettings()), str(scenarios_dir), enabled=True)
    state = ConversationStateManager(MemoryStateStore())
    l0_engine = L0RuleEngine(
        state_manager=state,
        l0_rules={
            "cancel_phrases": ["算了", "取消", "不了", "不用了"],
            "next_page_triggers": ["下一页", "继续", "更多"],
        },
        skills_config={
            "delete": {
                "confirm_phrases": ["确认删除", "确认", "是", "删除"],
            }
        },
    )

    passed = 0
    failed = 0
    skipped = 0

    mutating_tools = {"record.create", "record.update", "record.delete"}

    for item in scenarios:
        if not isinstance(item, dict):
            skipped += 1
            continue

        category = str(item.get("category") or "")
        if category not in {"error", "security", "table_recognition", "context"}:
            skipped += 1
            continue

        expected = item.get("expected")
        if isinstance(expected, dict) and isinstance(expected.get("tool"), str):
            skipped += 1
            continue

        scenario_id = str(item.get("scenario_id") or "DOC_G")
        user_input = _extract_user_input(item, prefer_last=(category == "context"))
        if not user_input:
            skipped += 1
            continue

        # 批量删除类应被 L0 直接拦截
        if any(token in user_input for token in ["删除所有", "全部删除", "批量删除"]):
            d = l0_engine.evaluate(f"doc-{scenario_id}", user_input)
            if d.handled:
                print(f"[PASS] {scenario_id} (guardrail:l0)")
                passed += 1
            else:
                print(f"[FAIL] {scenario_id} (guardrail:l0): expected handled=True")
                failed += 1
            continue

        # 空/异常输入应被 L0 接住
        if user_input.strip() in {"", "???", "？？？"}:
            d = l0_engine.evaluate(f"doc-{scenario_id}", user_input)
            if d.handled:
                print(f"[PASS] {scenario_id} (guardrail:l0-empty)")
                passed += 1
            else:
                print(f"[FAIL] {scenario_id} (guardrail:l0-empty): expected handled=True")
                failed += 1
            continue

        output = await planner.plan(user_input)
        if output is None:
            # 无规划结果也可接受，不应抛异常
            print(f"[PASS] {scenario_id} (guardrail:none)")
            passed += 1
            continue

        if category == "security" and output.tool in mutating_tools:
            print(f"[FAIL] {scenario_id} (guardrail:security): unsafe tool={output.tool}")
            failed += 1
            continue

        print(f"[PASS] {scenario_id} (guardrail)")
        passed += 1

    return passed, failed, skipped


def run_l0_tests(file: Path) -> tuple[int, int]:
    state = ConversationStateManager(MemoryStateStore())
    engine = L0RuleEngine(
        state_manager=state,
        l0_rules={
            "cancel_phrases": ["算了", "取消", "不了", "不用了"],
            "next_page_triggers": ["下一页", "继续", "更多"],
        },
        skills_config={
            "delete": {
                "confirm_phrases": ["确认删除", "确认", "是", "删除"],
            }
        },
    )

    passed = 0
    failed = 0

    for case in _load_tests(file):
        case_id = str(case.get("id") or "L0")
        text = str(case.get("input") or "")
        setup_raw = case.get("setup")
        setup: dict[str, Any] = setup_raw if isinstance(setup_raw, dict) else {}
        expect_raw = case.get("expect")
        expect: dict[str, Any] = expect_raw if isinstance(expect_raw, dict) else {}

        user_id = f"test-{case_id}"
        if setup.get("pending_delete") and isinstance(setup.get("pending_delete"), dict):
            pd = setup["pending_delete"]
            state.set_pending_delete(
                user_id,
                str(pd.get("record_id") or "rec_test"),
                str(pd.get("record_summary") or "case_test"),
            )
        if setup.get("pagination") and isinstance(setup.get("pagination"), dict):
            pg = setup["pagination"]
            params = pg.get("params") if isinstance(pg.get("params"), dict) else {}
            state.set_pagination(
                user_id=user_id,
                tool=str(pg.get("tool") or "feishu.v1.bitable.search"),
                params=params,
                page_token=str(pg.get("page_token") or ""),
                current_page=int(pg.get("current_page") or 1),
                total=int(pg.get("total") or 0),
            )

        decision = engine.evaluate(user_id, text)
        ok = True
        if "handled" in expect and decision.handled != bool(expect["handled"]):
            ok = False
        if "force_skill" in expect and decision.force_skill != expect["force_skill"]:
            ok = False
        if "reply_contains" in expect:
            reply_text = ""
            if isinstance(decision.reply, dict):
                reply_text = str(decision.reply.get("text") or "")
            if str(expect["reply_contains"]) not in reply_text:
                ok = False

        if ok:
            print(f"[PASS] {case_id}")
            passed += 1
        else:
            print(
                f"[FAIL] {case_id}: expect={expect}, got={{'handled': {decision.handled}, 'force_skill': {decision.force_skill}, 'reply': {decision.reply}}}"
            )
            failed += 1

    return passed, failed


async def run_skill_behavior_tests() -> tuple[int, int]:
    """执行技能层行为回归（当前覆盖 Reminder 边界行为）。"""
    skill = ReminderSkill(db_client=None, skills_config={})

    cases: list[dict[str, Any]] = [
        {
            "id": "B001",
            "context": SkillContext(query="提醒我下周整理材料", user_id="u-b1", extra={}),
            "expect_success": True,
            "expect_contains": "更具体的提醒时间",
        },
        {
            "id": "B002",
            "context": SkillContext(
                query="提醒我开会",
                user_id="u-b2",
                extra={
                    "planner_plan": {
                        "intent": "create_reminder",
                        "tool": "reminder.create",
                        "params": {
                            "content": "开会",
                            "remind_time": "2000-01-01 09:00",
                        },
                    }
                },
            ),
            "expect_success": False,
            "expect_contains": "已经过去",
        },
    ]

    passed = 0
    failed = 0

    for case in cases:
        case_id = case["id"]
        ctx = case["context"]
        result = await skill.execute(ctx)
        reply_text = result.reply_text or result.message

        ok = (result.success == bool(case["expect_success"])) and (str(case["expect_contains"]) in reply_text)
        if ok:
            print(f"[PASS] {case_id}")
            passed += 1
        else:
            print(
                f"[FAIL] {case_id}: expect_success={case['expect_success']}, "
                f"expect_contains={case['expect_contains']}, got_success={result.success}, reply={reply_text}"
            )
            failed += 1

    return passed, failed


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run scenario tests for planner and L0.")
    parser.add_argument(
        "--planner-tests-dir",
        default="tests/scenarios",
        help="Planner test yaml directory",
    )
    parser.add_argument(
        "--scenarios-dir",
        default="config/scenarios",
        help="Planner scenario rules directory",
    )
    parser.add_argument(
        "--l0-test-file",
        default="tests/scenarios/l0.test.yaml",
        help="L0 test yaml file",
    )
    parser.add_argument(
        "--docs-file",
        default="../../docs/scenarios.yaml",
        help="Project docs scenario yaml for projection checks",
    )
    parser.add_argument("--min-planner-pass", type=int, default=0, help="Minimum planner pass count")
    parser.add_argument("--min-l0-pass", type=int, default=0, help="Minimum L0 pass count")
    parser.add_argument("--min-docs-pass", type=int, default=0, help="Minimum docs projection pass count")
    parser.add_argument("--min-guard-pass", type=int, default=0, help="Minimum guardrail pass count")
    parser.add_argument("--min-behavior-pass", type=int, default=0, help="Minimum behavior pass count")
    parser.add_argument("--max-docs-skip", type=int, default=-1, help="Maximum allowed docs projection skip count (-1 disables)")
    args = parser.parse_args()

    planner_tests_dir = Path(args.planner_tests_dir)
    scenarios_dir = Path(args.scenarios_dir)
    l0_test_file = Path(args.l0_test_file)
    docs_file = Path(args.docs_file)

    planner_files = [
        planner_tests_dir / "query.test.yaml",
        planner_tests_dir / "crud.test.yaml",
        planner_tests_dir / "reminder.test.yaml",
    ]
    planner_files = [f for f in planner_files if f.exists()]

    planner_passed, planner_failed = await run_planner_tests(planner_files, scenarios_dir)
    l0_passed, l0_failed = (0, 0)
    if l0_test_file.exists():
        l0_passed, l0_failed = run_l0_tests(l0_test_file)

    docs_passed, docs_failed, docs_skipped = await run_docs_projection_tests(docs_file, scenarios_dir)
    guard_passed, guard_failed, guard_skipped = await run_docs_guardrail_tests(docs_file, scenarios_dir)
    behavior_passed, behavior_failed = await run_skill_behavior_tests()

    total_passed = planner_passed + l0_passed + docs_passed + guard_passed + behavior_passed
    total_failed = planner_failed + l0_failed + docs_failed + guard_failed + behavior_failed

    print("\n=== Scenario Runner Summary ===")
    print(f"Planner: pass={planner_passed}, fail={planner_failed}")
    print(f"L0:      pass={l0_passed}, fail={l0_failed}")
    print(f"Docs:    pass={docs_passed}, fail={docs_failed}, skip={docs_skipped}")
    print(f"Guard:   pass={guard_passed}, fail={guard_failed}, skip={guard_skipped}")
    print(f"Behavior:pass={behavior_passed}, fail={behavior_failed}")
    print(f"Total:   pass={total_passed}, fail={total_failed}")

    threshold_errors: list[str] = []
    if planner_passed < args.min_planner_pass:
        threshold_errors.append(f"planner_pass {planner_passed} < {args.min_planner_pass}")
    if l0_passed < args.min_l0_pass:
        threshold_errors.append(f"l0_pass {l0_passed} < {args.min_l0_pass}")
    if docs_passed < args.min_docs_pass:
        threshold_errors.append(f"docs_pass {docs_passed} < {args.min_docs_pass}")
    if guard_passed < args.min_guard_pass:
        threshold_errors.append(f"guard_pass {guard_passed} < {args.min_guard_pass}")
    if behavior_passed < args.min_behavior_pass:
        threshold_errors.append(f"behavior_pass {behavior_passed} < {args.min_behavior_pass}")
    if args.max_docs_skip >= 0 and docs_skipped > args.max_docs_skip:
        threshold_errors.append(f"docs_skip {docs_skipped} > {args.max_docs_skip}")

    if threshold_errors:
        print("\nThreshold checks failed:")
        for err in threshold_errors:
            print(f"- {err}")
        return 1

    return 1 if total_failed > 0 else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
