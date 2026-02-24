from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


logger = logging.getLogger(__name__)


BUILTIN_TEMPLATE_DEFAULT_VERSIONS: dict[str, str] = {
    "query.list": "v2",
    "query.detail": "v1",
    "action.confirm": "v1",
    "error.notice": "v1",
    "todo.reminder": "v1",
    "upload.result": "v1",
    "create.success": "v1",
    "update.success": "v1",
    "update.guide": "v1",
    "delete.confirm": "v1",
    "delete.success": "v1",
    "delete.cancelled": "v1",
}


BUILTIN_TEMPLATE_ENABLED: dict[str, bool] = {
    "query.list.v1": True,
    "query.list.v2": True,
    "query.detail.v1": True,
    "action.confirm.v1": True,
    "error.notice.v1": True,
    "todo.reminder.v1": True,
    "upload.result.v1": True,
    "create.success.v1": True,
    "update.success.v1": True,
    "update.guide.v1": True,
    "delete.confirm.v1": True,
    "delete.success.v1": True,
    "delete.cancelled.v1": True,
}


BUILTIN_RENDER_TEMPLATES: dict[str, Any] = {
    "query_list_v2": {
        "texts": {
            "default_title": "查询结果",
            "not_found": "未找到相关记录。",
            "not_found_suggestion": "建议补充案号、负责人、时间范围等条件后重试。",
            "narrowing_hint": "结果较多，建议补充关键词或时间范围缩小范围。",
            "view_detail": "查看详情",
            "table_badge": "数据表: {table_name}{table_suffix} | 模板: {style}",
        },
        "list_limits": {
            "small": 10,
            "large": 5,
        },
        "actions": {
            "next_page": "下一页",
            "next_page_with_remaining": "下一页（剩余 {remaining} 条）",
            "today_hearing": "今天开庭",
            "week_hearing": "本周开庭",
        },
        "field_keys": {
            "case": {
                "case_no": ["案号", "项目ID"],
                "title_left": ["委托人及联系方式", "委托人"],
                "title_right": ["对方当事人"],
                "cause": ["案由"],
                "court": ["审理法院"],
                "stage": ["程序阶段"],
                "status": ["案件状态", "进展", "程序阶段"],
                "date": ["开庭日", "截止日", "上诉截止日", "举证截止日"],
                "owner": ["主办律师", "负责人"],
                "urgency": ["紧急程度", "优先级", "风险等级"],
                "co_owner": ["协办律师"],
                "courtroom": ["承办法庭"],
            },
            "contracts": {
                "id": ["合同编号", "编号", "项目ID"],
                "name": ["合同名称", "标题"],
                "party_a": ["甲方"],
                "party_b": ["乙方"],
                "amount": ["合同金额", "金额"],
                "status": ["合同状态", "状态"],
                "date": ["签约日期", "到期日期", "付款截止", "截止日"],
            },
            "bidding": {
                "name": ["项目名称", "标段名称"],
                "owner_org": ["招标方", "业主单位"],
                "phase": ["阶段", "进度", "状态"],
                "due": ["投标截止日", "截止日", "开标时间"],
                "owner": ["负责人", "主办律师"],
            },
            "team_overview": {
                "member": ["成员", "姓名", "负责人"],
                "workload": ["在办事项", "任务数"],
                "today": ["今日节点", "今日安排"],
                "risk": ["风险事项", "风险"],
                "status": ["状态", "进展"],
                "due": ["截止日", "下个节点"],
            },
        },
    },
    "action_cards": {
        "confirm": {
            "title": "C1 操作确认",
            "message": "请确认是否继续执行该操作。",
            "extra_note": "说明: 确认后将立即执行",
            "confirm_text": "确认执行",
            "cancel_text": "取消",
            "template_file": "action/C1_confirm.md",
            "layout_file": "action/C1_confirm_layout.json",
            "wrapper_file": "wrapper/card_action_C1_confirm.json",
        },
        "create_confirm": {
            "title": "C1 新增确认",
            "template_file": "action/C1_confirm.md",
            "layout_file": "action/C1_confirm_layout.json",
            "wrapper_file": "wrapper/card_action_C1_confirm.json",
        },
        "update_confirm": {
            "title": "C2 修改确认",
            "template_file": "action/C2_confirm.md",
            "layout_file": "action/C2_confirm_layout.json",
            "wrapper_file": "wrapper/card_action_C2_confirm.json",
        },
        "create_success": {
            "title": "C1 创建成功",
            "template_file": "action/C1_success.md",
            "layout_file": "action/C1_success_layout.json",
            "wrapper_file": "wrapper/card_action_C1_success.json",
        },
        "update_success": {
            "title": "C2 更新成功",
            "progress_prefix": "进展追加",
            "template_file": "action/feedback.md",
            "layout_file": "action/feedback_layout.json",
            "wrapper_file": "wrapper/card_action_feedback_success.json",
        },
        "delete_confirm": {
            "title": "C3 关闭/删除确认",
            "subtitle": "该操作不可撤销，请再次确认。",
            "confirm_text": "确认删除",
            "cancel_text": "取消",
            "empty_summary": "- 即将删除目标记录",
            "template_file": "action/C3_confirm.md",
            "layout_file": "action/C3_confirm_layout.json",
            "wrapper_file": "wrapper/card_action_C3_confirm.json",
        },
        "feedback": {
            "template_file": "action/feedback.md",
            "layout_file": "action/feedback_layout.json",
            "success_wrapper_file": "wrapper/card_action_feedback_success.json",
            "error_wrapper_file": "wrapper/card_action_feedback_error.json",
        },
    },
    "upload_result": {
        "titles": {
            "processing": "文件处理中",
            "success": "文件解析成功",
            "failed": "文件解析失败",
            "disabled": "文件解析未开启",
            "unconfigured": "文件解析未配置",
            "rejected": "文件不符合要求",
        },
        "status_labels": {
            "processing": "处理中",
            "success": "已完成",
            "failed": "失败",
            "disabled": "未开启",
            "unconfigured": "未配置",
            "rejected": "已拒绝",
        },
        "status_icons": {
            "processing": "OK",
            "success": "OK",
            "failed": "OK",
            "disabled": "OK",
            "unconfigured": "OK",
            "rejected": "OK",
        },
        "provider_labels": {
            "none": "未使用外部解析",
            "mineru": "MinerU",
            "llm": "LLM Extractor",
            "ocr": "OCR Provider",
            "asr": "ASR Provider",
        },
        "reason_texts": {
            "file_too_large": "文件体积超过当前限制",
            "unsupported_file_type": "文件类型暂不支持",
            "extractor_disabled": "解析能力已关闭",
            "extractor_unconfigured": "解析服务尚未配置",
            "ocr_unconfigured": "OCR 服务尚未配置",
            "ocr_disabled": "OCR 服务已关闭",
            "asr_unconfigured": "ASR 服务尚未配置",
            "asr_disabled": "ASR 服务已关闭",
            "extractor_timeout": "解析服务响应超时",
            "extractor_rate_limited": "解析服务限流",
            "extractor_auth_failed": "解析服务鉴权失败",
            "extractor_malformed_response": "解析服务响应格式异常",
            "extractor_empty_content": "未识别到有效内容",
        },
        "next_steps": {
            "success": "可继续提问，如“帮我提取关键条款并给出风险提示”。",
            "failed": "请稍后重试，或直接发送文字描述你的问题。",
            "disabled": "可联系管理员开启文件解析能力，或直接发送文字。",
            "unconfigured": "请联系管理员补齐解析服务配置后重试。",
            "rejected": "请调整文件大小/格式后重试，或补充文字说明。",
        },
        "preview": {
            "max_chars": 240,
        },
    },
}


def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[4] / "config" / "card_templates.yaml"


def _yaml_enabled() -> bool:
    raw = os.getenv("CARD_TEMPLATE_CONFIG_YAML_ENABLED", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _config_path() -> Path:
    custom_path = os.getenv("CARD_TEMPLATE_CONFIG_PATH", "").strip()
    return Path(custom_path) if custom_path else _default_config_path()


def _normalize_default_versions(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    output: dict[str, str] = {}
    for key, value in raw.items():
        template_id = str(key or "").strip()
        version = str(value or "").strip()
        if template_id and version:
            output[template_id] = version
    return output


def _normalize_enabled(raw: Any) -> dict[str, bool]:
    if not isinstance(raw, dict):
        return {}
    output: dict[str, bool] = {}
    for key, value in raw.items():
        template_key = str(key or "").strip()
        if not template_key:
            continue
        if isinstance(value, bool):
            output[template_key] = value
            continue
        text = str(value or "").strip().lower()
        output[template_key] = text in {"1", "true", "yes", "on"}
    return output


def _normalize_render_templates(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    output: dict[str, Any] = {}
    for key, value in raw.items():
        name = str(key or "").strip()
        if name and isinstance(value, dict):
            output[name] = value
    return output


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


@lru_cache(maxsize=1)
def _load_template_config() -> tuple[dict[str, str], dict[str, bool], dict[str, Any]]:
    if not _yaml_enabled():
        return BUILTIN_TEMPLATE_DEFAULT_VERSIONS, BUILTIN_TEMPLATE_ENABLED, BUILTIN_RENDER_TEMPLATES

    path = _config_path()
    if not path.exists():
        return BUILTIN_TEMPLATE_DEFAULT_VERSIONS, BUILTIN_TEMPLATE_ENABLED, BUILTIN_RENDER_TEMPLATES

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(
            "card template YAML load failed, fallback to builtins: %s",
            exc,
            extra={"event_code": "feishu.card_template.config_load_failed", "path": str(path)},
        )
        return BUILTIN_TEMPLATE_DEFAULT_VERSIONS, BUILTIN_TEMPLATE_ENABLED, BUILTIN_RENDER_TEMPLATES

    if not isinstance(data, dict):
        return BUILTIN_TEMPLATE_DEFAULT_VERSIONS, BUILTIN_TEMPLATE_ENABLED, BUILTIN_RENDER_TEMPLATES

    defaults = _normalize_default_versions(data.get("default_versions"))
    enabled = _normalize_enabled(data.get("enabled"))
    render_templates = _normalize_render_templates(data.get("render_templates"))
    defaults = _deep_merge(BUILTIN_TEMPLATE_DEFAULT_VERSIONS, defaults) if defaults else BUILTIN_TEMPLATE_DEFAULT_VERSIONS
    enabled = _deep_merge(BUILTIN_TEMPLATE_ENABLED, enabled) if enabled else BUILTIN_TEMPLATE_ENABLED
    if not render_templates:
        render_templates = BUILTIN_RENDER_TEMPLATES
    else:
        render_templates = _deep_merge(BUILTIN_RENDER_TEMPLATES, render_templates)
    return defaults, enabled, render_templates


def reset_template_config_cache() -> None:
    _load_template_config.cache_clear()


def resolve_template_version(template_id: str, version: str | None = None) -> str:
    resolved = (version or "").strip()
    if resolved:
        return resolved
    default_versions, _, _ = _load_template_config()
    return default_versions.get(template_id, "v1")


def is_template_enabled(template_id: str, version: str) -> bool:
    _, enabled, _ = _load_template_config()
    return bool(enabled.get(f"{template_id}.{version}", False))


def get_render_templates() -> dict[str, Any]:
    _, _, render_templates = _load_template_config()
    if not isinstance(render_templates, dict):
        return BUILTIN_RENDER_TEMPLATES
    return _deep_merge(BUILTIN_RENDER_TEMPLATES, render_templates)


def extract_template_spec(payload: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    raw = payload.get("card_template")
    if not isinstance(raw, dict):
        return None

    template_id = str(raw.get("template_id") or "").strip()
    if not template_id:
        return None

    version = resolve_template_version(template_id, str(raw.get("version") or "").strip())
    params = raw.get("params")
    if not isinstance(params, dict):
        params = {}
    return template_id, version, params
