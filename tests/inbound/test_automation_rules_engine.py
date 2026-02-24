from pathlib import Path
import json
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.api.automation_rules import (
    AutomationActionExecutor,
    AutomationRule,
    AutomationRuleLoader,
    AutomationRuleMatcher,
)


def test_rule_loader_returns_empty_when_yaml_missing(tmp_path: Path) -> None:
    loader = AutomationRuleLoader()

    ruleset = loader.load(tmp_path / "missing.yaml")

    assert ruleset.rules == []
    assert ruleset.watched_fields_by_table == {}


def test_rule_loader_skips_invalid_rule_and_keeps_valid(tmp_path: Path) -> None:
    path = tmp_path / "automation_rules.yaml"
    path.write_text(
        """
watched_fields:
  tbl_valid:
    - 状态
rules:
  - rule_id: bad_missing_table
    trigger:
      all:
        - field: 状态
          equals: 完成
    actions:
      - type: log.write
  - rule_id: r_valid
    table_id: tbl_valid
    trigger:
      all:
        - field: 状态
          equals: 完成
    actions:
      - type: log.write
""".strip(),
        encoding="utf-8",
    )
    loader = AutomationRuleLoader()

    ruleset = loader.load(path)

    assert [rule.rule_id for rule in ruleset.rules] == ["r_valid"]
    assert ruleset.rules[0].watched_fields == {"状态"}


def test_rule_matcher_filters_by_table_watched_fields_and_conditions() -> None:
    rule = AutomationRule(
        rule_id="r1",
        source_table="tbl_1",
        watched_fields={"状态"},
        condition_mode="all",
        conditions=[
            {"field": "状态", "operator": "equals", "value": "完成"},
            {"field": "标题", "operator": "contains", "value": "周报"},
            {"field": "状态", "operator": "changed", "value": True},
        ],
        actions=[{"type": "log.write"}],
    )
    matcher = AutomationRuleMatcher()

    result = matcher.match(
        rule,
        {
            "table_id": "tbl_1",
            "changed_fields": ["状态"],
            "raw_fragment": {
                "changed_fields": {
                    "状态": {"old": "待处理", "new": "完成"},
                    "标题": {"old": "旧", "new": "周报-2026"},
                }
            },
        },
    )

    assert result.matched is True


def test_action_executor_dry_run_supported_actions() -> None:
    executor = AutomationActionExecutor(dead_letter_path=Path("/tmp/not-used.jsonl"), sleeper=lambda _: None)

    result = executor.execute_rule(
        AutomationRule(
            rule_id="r1",
            source_table="tbl_1",
            watched_fields=set(),
            condition_mode="all",
            conditions=[],
            actions=[{"type": "log.write"}, {"type": "send_message"}, {"type": "bitable.update"}],
        ),
        {"record_id": "rec_1"},
    )

    assert result["dry_run"] is True
    assert [item["status"] for item in result["actions"]] == ["success", "success", "skipped_status_write_disabled"]


def test_action_executor_retries_then_dead_letters(tmp_path: Path) -> None:
    dead_letter = tmp_path / "dead_letters.jsonl"
    executor = AutomationActionExecutor(dead_letter_path=dead_letter, sleeper=lambda _: None)

    def _always_fail(action: dict[str, object], payload: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("boom")

    executor._execute_action_once = _always_fail  # type: ignore[assignment]

    result = executor.execute_rule(
        AutomationRule(
            rule_id="r_retry",
            source_table="tbl_1",
            watched_fields=set(),
            condition_mode="all",
            conditions=[],
            actions=[{"type": "send_message"}],
        ),
        {"event_id": "evt_1", "record_id": "rec_1"},
    )

    assert result["actions"][0]["status"] == "failed"
    assert result["actions"][0]["attempts"] == 3
    lines = dead_letter.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["rule_id"] == "r_retry"
    assert entry["action_type"] == "send_message"
