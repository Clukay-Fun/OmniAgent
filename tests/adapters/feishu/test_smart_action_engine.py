from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.feishu.action_engine import ActionEngine
from src.adapters.channels.feishu.smart_engine import SmartEngine


def test_smart_engine_extracts_date_and_suggestions() -> None:
    engine = SmartEngine()
    suggestions = engine.analyze_progress_for_suggestions("开庭时间变更为2026-03-15，建议同步")

    assert len(suggestions) >= 1
    assert suggestions[0]["field"] == "hearing_date"
    assert suggestions[0]["suggested_value"] == "2026-03-15"


def test_action_engine_builds_update_lines_with_suggestion() -> None:
    engine = ActionEngine(SmartEngine())
    title, lines = engine.build_confirm_lines(
        action="update_record",
        message="请确认修改",
        table_name="案件项目总库",
        payload={
            "diff": [
                {
                    "field": "进展",
                    "old": "已立案",
                    "new": "开庭改为2026-03-16",
                }
            ]
        },
    )

    text = "\n".join(lines)
    assert title == "C2 修改确认"
    assert "变更明细" in text
    assert "建议同步确认字段：开庭日" in text


def test_action_engine_append_mode_shows_boundary() -> None:
    engine = ActionEngine(SmartEngine())
    _, lines = engine.build_confirm_lines(
        action="update_record",
        message="请确认修改",
        table_name="案件项目总库",
        payload={
            "diff": [
                {
                    "field": "进展",
                    "old": "[2026-01-01] 已立案",
                    "new": "[2026-01-01] 已立案\n[2026-02-23] 已联系法官",
                    "mode": "append",
                    "delta": "[2026-02-23] 已联系法官",
                }
            ]
        },
    )
    text = "\n".join(lines)
    assert "追加模式" in text
    assert "新增:" in text
    assert "追加后:" in text


def test_action_engine_builds_auto_reminders_for_case_dates() -> None:
    engine = ActionEngine(SmartEngine())
    reminders = engine.build_auto_reminders(
        "案件项目总库",
        {
            "开庭日": "2099-01-10",
            "举证截止日": "2099-01-20",
        },
    )

    assert len(reminders) >= 2
    assert any("开庭日" in item for item in reminders)


def test_action_engine_create_confirm_reuses_detail_fields_dsl() -> None:
    engine = ActionEngine(SmartEngine())
    title, lines = engine.build_confirm_lines(
        action="create_record",
        message="请确认新增",
        table_name="案件项目总库",
        payload={
            "table_name": "案件项目总库",
            "fields": {
                "案号": "(2026)粤0101民初100号",
                "委托人": "甲公司",
                "对方当事人": "乙公司",
                "案由": "合同纠纷",
                "审理法院": "广州中院",
                "程序阶段": "一审",
            },
        },
    )

    text = "\n".join(lines)
    assert title == "C1 新增确认"
    assert "案号" in text
    assert "委托人" in text
    assert "审理法院" in text
