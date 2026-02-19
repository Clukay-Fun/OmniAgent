from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.response.renderer import ResponseRenderer


def build_renderer() -> ResponseRenderer:
    return ResponseRenderer(
        templates={
            "success": "成功处理 {skill_name}",
            "failure": "处理失败：{skill_name}",
        },
        assistant_name="小敬",
    )


def test_render_success_with_data_outputs_kv_list_and_non_empty_fallback():
    renderer = build_renderer()

    response = renderer.render(
        {
            "success": True,
            "skill_name": "planner",
            "message": "完成任务",
            "data": {"count": 2, "status": "ok"},
        }
    )

    assert response.text_fallback.strip() != ""
    kv_blocks = [block for block in response.blocks if block.type == "kv_list"]
    assert len(kv_blocks) == 1
    assert kv_blocks[0].content["items"] == [
        {"key": "count", "value": "2"},
        {"key": "status", "value": "ok"},
    ]


def test_render_failure_uses_failure_template_and_assistant_identity():
    renderer = build_renderer()

    response = renderer.render({"success": False, "skill_name": "executor"})

    assert response.text_fallback == "处理失败：executor"
    assert response.meta["assistant_name"] == "小敬"


def test_render_prefers_reply_text_for_text_fallback():
    renderer = build_renderer()

    response = renderer.render(
        {
            "success": True,
            "skill_name": "search",
            "message": "来自 message",
            "reply_text": "来自 reply_text",
        }
    )

    assert response.text_fallback == "来自 reply_text"


def test_render_always_contains_paragraph_block():
    renderer = build_renderer()

    response = renderer.render({"success": True, "skill_name": "router"})

    assert len(response.blocks) >= 1
    assert response.blocks[0].type == "paragraph"


def test_load_templates_falls_back_to_defaults_when_file_missing(tmp_path: Path):
    missing_path = tmp_path / "missing.yaml"
    renderer = ResponseRenderer(templates_path=missing_path)

    response = renderer.render({"success": True, "skill_name": "router"})

    assert response.text_fallback == "已完成 router"


def test_load_templates_falls_back_to_defaults_when_yaml_invalid(tmp_path: Path):
    broken_path = tmp_path / "responses.yaml"
    broken_path.write_text("success: [invalid", encoding="utf-8")
    renderer = ResponseRenderer(templates_path=broken_path)

    response = renderer.render({"success": False, "skill_name": "executor"})

    assert response.text_fallback == "处理失败：executor"
