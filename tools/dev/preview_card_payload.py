from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = REPO_ROOT / "apps" / "agent-host"
if str(AGENT_HOST_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_HOST_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(AGENT_HOST_ROOT / ".env")
except Exception:
    pass

from src.adapters.channels.feishu.ui_cards.card_template_config import resolve_template_version  # noqa: E402
from src.adapters.channels.feishu.protocol.formatter import FeishuFormatter  # noqa: E402
from src.config import get_settings  # noqa: E402
from src.core.expression.response.models import Block, CardTemplateSpec, RenderedResponse  # noqa: E402
from src.utils.platform.feishu.feishu_api import send_message  # noqa: E402


_OPEN_ID_PATTERN = re.compile(r"^ou_[A-Za-z0-9_-]+$")


def _build_case_record(index: int) -> dict[str, Any]:
    return {
        "record_id": f"rec_case_{index}",
        "record_url": f"https://example.com/rec_case_{index}",
        "fields_text": {
            "项目 ID": f"CASE-2026-{index:03d}",
            "项目类型": "争议解决",
            "案件分类": "劳动争议",
            "案号": f"(2026)粤010{index}民初{100+index}号",
            "委托人": f"委托人{index}",
            "对方当事人": f"对方当事人{index}",
            "联系人": "陈桂媚",
            "联系方式": "15019446008",
            "案由": "合同纠纷",
            "审理法院": "广州中院",
            "承办法庭": "第78法庭",
            "程序阶段": "一审",
            "案件状态": "进行中",
            "主办律师": "张三",
            "协办律师": "李四",
            "承办法官": "二审：俞颖（020-83210730）\n助理：谢琛玲（020-83210727）\n书记员：冯芷晴（020-83210767）",
            "开庭日": "2026-03-15",
            "管辖权异议截止日": "2026-03-01",
            "举证截止日": "2026-03-01",
            "重要紧急程度": "一般",
            "进展": "2026-02-20 已提交证据",
            "待做事项": "补充证据目录",
            "关联合同": "20250131",
        },
    }


def _build_contract_record(index: int) -> dict[str, Any]:
    contract_no = f"202501{30 + index}"
    return {
        "record_id": f"rec_contract_{index}",
        "record_url": f"https://example.com/rec_contract_{index}",
        "fields_text": {
            "合同编号": contract_no,
            "合同名称": "委托代理合同" if index == 1 else f"服务合同-{index}",
            "客户名称": "香港华艺设计顾问（深圳）有限公司" if index == 1 else f"客户{index}",
            "甲方": "甲方公司",
            "乙方": "乙方公司",
            "合同金额": "100000" if index == 1 else "180000",
            "合同状态": "履约中" if index == 1 else "审批中",
            "开票付款状态": "未开票未付款" if index == 1 else "部分开票",
            "签约日期": "2026-02-04",
            "合同开始日期": "2026-02-04",
            "合同结束日期": "2026-01-28" if index == 1 else "2026-12-30",
            "盖章日期": "2026-02-04",
            "盖章状态": "待盖章" if index == 1 else "已盖章",
            "关联项目": "JFTD-20260001" if index == 1 else f"JFTD-202600{20 + index}",
            "主办律师": "管理员" if index == 1 else "赵六",
            "linked_case_url": "https://example.com/case",
            "edit_contract_url": "https://example.com/edit",
        },
    }


def _build_bidding_record(index: int) -> dict[str, Any]:
    return {
        "record_id": f"rec_bid_{index}",
        "record_url": f"https://example.com/rec_bid_{index}",
        "fields_text": {
            "项目号": f"BID-{index:04d}",
            "投标项目名称": f"城市更新项目-{index}",
            "招标方名称": "城建集团",
            "阶段": "投标准备",
            "标书购买截止时间": "2026-03-01",
            "开标时间": "2026-03-20",
            "投标截止日": "2026-03-18",
            "保证金截止日期": "2026-03-10",
            "标书领取状态": "已领取",
            "保证金缴纳状态": "待缴纳",
            "文件编制进度": "编制中",
            "标书类型": "电子标",
            "承办律师": "赵六",
            "是否中标": "待定" if index == 1 else "中标",
            "中标金额": "300000" if index != 1 else "",
            "备注": "重点关注资格审查",
        },
    }


def _build_team_record(index: int) -> dict[str, Any]:
    return {
        "record_id": f"rec_team_{index}",
        "record_url": f"https://example.com/rec_team_{index}",
        "fields_text": {
            "成员": f"成员{index}",
            "在办事项": str(3 + index),
            "状态": "处理中",
            "截止日": "2026-03-10",
            "风险事项": "待补正材料",
            "今日节点": "整理庭审提纲",
        },
    }


def _query_defaults(domain: str, style: str, total: int) -> tuple[str, list[dict[str, Any]], str, str]:
    count = max(total, 1)
    if domain == "contracts":
        records = [_build_contract_record(i) for i in range(1, count + 1)]
        return "合同管理表查询结果", records, "合同管理表", "tbl_contract_demo"
    if domain == "bidding":
        records = [_build_bidding_record(i) for i in range(1, count + 1)]
        return "招投标台账查询结果", records, "招投标台账", "tbl_bid_demo"
    if domain == "team_overview":
        records = [_build_team_record(i) for i in range(1, count + 1)]
        return "团队成员工作总览（只读）", records, "团队成员工作总览（只读）", "tbl_team_demo"
    records = [_build_case_record(i) for i in range(1, count + 1)]
    _ = style
    return "案件项目总库查询结果", records, "案件项目总库", "tbl_case_demo"


def _query_actions() -> dict[str, Any]:
    return {
        "next_page": {
            "callback_action": "query_list_next_page",
            "extra_data": {
                "kind": "pagination",
                "query": "下一页",
                "pagination": {"current_page": 1, "page_token": "demo_token"},
            },
        },
        "today_hearing": {"callback_action": "query_list_today_hearing"},
        "week_hearing": {"callback_action": "query_list_week_hearing"},
    }


def _build_default_params(template_id: str, version: str, args: argparse.Namespace) -> dict[str, Any]:
    if template_id == "query.list" and version == "v2":
        title, records, table_name, table_id = _query_defaults(args.domain, args.style, args.total)
        return {
            "title": title,
            "total": len(records),
            "records": records,
            "actions": _query_actions(),
            "style": args.style,
            "style_variant": args.style,
            "domain": args.domain,
            "table_name": table_name,
            "table_id": table_id,
        }

    if template_id == "action.confirm":
        return {
            "title": "新增案件 - 请确认",
            "message": "请确认新增信息。",
            "action": "create_record",
            "table_type": "case",
            "record_id": "",
            "payload": {
                "table_type": "case",
                "fields": {
                    "项目ID": "JFTD-20260001",
                    "案号": "(2026)粤0101民初100号",
                    "委托人": "香港华艺设计顾问",
                    "对方当事人": "广州荔富汇景",
                },
                "required_fields": ["案号", "委托人"],
            },
            "actions": {
                "confirm": {"callback_action": "create_record_confirm"},
                "cancel": {"callback_action": "create_record_cancel"},
            },
        }

    if template_id == "create.success":
        return {
            "title": "新增成功",
            "table_name": "案件项目总库",
            "record": {
                "record_id": "rec_new_001",
                "record_url": "https://example.com/rec_new_001",
                "fields_text": _build_case_record(1)["fields_text"],
            },
        }

    if template_id == "update.success":
        return {
            "title": "操作成功",
            "changes": [{"field": "案件状态", "old": "进行中", "new": "已结案"}],
            "record_id": "rec_case_1",
            "record_url": "https://example.com/rec_case_1",
            "progress_append": "2026-02-24 案件已结案归档",
        }

    if template_id == "delete.confirm":
        return {
            "title": "危险操作确认",
            "subtitle": "该操作不可撤销，请再次确认。",
            "table_type": "case",
            "record_id": "rec_case_1",
            "summary": {"案号": "(2026)粤0101民初100号", "记录 ID": "rec_case_1"},
            "actions": {
                "confirm": {"callback_action": "delete_record_confirm"},
                "cancel": {"callback_action": "delete_record_cancel"},
            },
        }

    if template_id == "error.notice":
        return {
            "title": "操作失败",
            "message": "权限不足，无法执行删除操作。",
            "skill_name": "DeleteSkill",
            "error_class": "permission_denied",
        }

    return {"message": "card preview"}


def _preset_spec(preset: str) -> tuple[str, str, dict[str, Any]]:
    normalized = preset.strip().lower()
    if normalized == "t1":
        return "query.list", "v2", _build_default_params(
            "query.list",
            "v2",
            argparse.Namespace(domain="case", style="T1", total=1),
        )
    if normalized == "t2":
        return "query.list", "v2", _build_default_params(
            "query.list",
            "v2",
            argparse.Namespace(domain="case", style="T2", total=4),
        )
    if normalized == "t3":
        params = _build_default_params(
            "query.list",
            "v2",
            argparse.Namespace(domain="case", style="T3", total=4),
        )
        records_raw = params.get("records")
        records = records_raw if isinstance(records_raw, list) else []
        today = date.today()
        if records and isinstance(records[0], dict):
            first_record = records[0]
            fields_raw = first_record.get("fields_text")
            fields = dict(fields_raw) if isinstance(fields_raw, dict) else {}
            fields["管辖权异议截止日"] = (today - timedelta(days=1)).isoformat()
            fields["举证截止日"] = (today - timedelta(days=1)).isoformat()
            first_record["fields_text"] = fields
        if len(records) > 1 and isinstance(records[1], dict):
            second_record = records[1]
            fields_raw = second_record.get("fields_text")
            fields = dict(fields_raw) if isinstance(fields_raw, dict) else {}
            fields["举证截止日"] = (today + timedelta(days=2)).isoformat()
            second_record["fields_text"] = fields
        params["style_variant"] = "T3B"
        return "query.list", "v2", params
    if normalized == "t5":
        return "query.list", "v2", _build_default_params(
            "query.list",
            "v2",
            argparse.Namespace(domain="case", style="T5", total=4),
        )
    if normalized == "ht-t1":
        return "query.list", "v2", _build_default_params(
            "query.list",
            "v2",
            argparse.Namespace(domain="contracts", style="HT-T1", total=1),
        )
    if normalized == "ht-t2":
        return "query.list", "v2", _build_default_params(
            "query.list",
            "v2",
            argparse.Namespace(domain="contracts", style="HT-T2", total=4),
        )
    if normalized == "zb-t1":
        return "query.list", "v2", _build_default_params(
            "query.list",
            "v2",
            argparse.Namespace(domain="bidding", style="ZB-T1", total=1),
        )
    if normalized == "zb-t2":
        return "query.list", "v2", _build_default_params(
            "query.list",
            "v2",
            argparse.Namespace(domain="bidding", style="ZB-T2", total=4),
        )
    if normalized == "rw-t1":
        return "query.list", "v2", _build_default_params(
            "query.list",
            "v2",
            argparse.Namespace(domain="team_overview", style="RW-T1", total=1),
        )
    if normalized in {"c1", "c1-confirm"}:
        return "action.confirm", "v1", _build_default_params("action.confirm", "v1", argparse.Namespace())
    if normalized == "c1-success":
        return "create.success", "v1", _build_default_params("create.success", "v1", argparse.Namespace())
    if normalized in {"c2", "c2-confirm"}:
        return "action.confirm", "v1", {
            "title": "修改确认",
            "message": "请确认是否执行本次修改。",
            "action": "update_record",
            "table_type": "case",
            "record_id": "rec_case_1",
            "payload": {
                "table_type": "case",
                "record_id": "rec_case_1",
                "diff": [
                    {"field": "案件状态", "old": "进行中", "new": "已结案"},
                ],
            },
            "actions": {
                "confirm": {"callback_action": "update_record_confirm"},
                "cancel": {"callback_action": "update_record_cancel"},
            },
        }
    if normalized in {"c3", "c3-confirm"}:
        return "delete.confirm", "v1", _build_default_params("delete.confirm", "v1", argparse.Namespace())
    if normalized == "error":
        return "error.notice", "v1", _build_default_params("error.notice", "v1", argparse.Namespace())
    if normalized == "feedback":
        return "error.notice", "v1", _build_default_params("error.notice", "v1", argparse.Namespace())
    raise ValueError(f"Unsupported preset: {preset}")


def _load_json(path: str) -> dict[str, Any]:
    file_path = Path(path).expanduser().resolve()
    data = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("mock JSON must be an object")
    return data


def _render_payload(template_id: str, version: str, params: dict[str, Any]) -> dict[str, Any]:
    rendered = RenderedResponse(
        text_fallback=str(params.get("title") or params.get("message") or "card preview"),
        blocks=[Block(type="paragraph", content={"text": "card preview"})],
        card_template=CardTemplateSpec(template_id=template_id, version=version, params=params),
    )
    formatter = FeishuFormatter(card_enabled=True)
    return formatter.format(rendered, prefer_card=True)


def _extract_http_error_text(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return ""
    body = getattr(response, "text", "")
    return str(body or "")


async def _send_preview(receive_id: str, receive_id_type: str, payload: dict[str, Any]) -> None:
    settings = get_settings()
    msg_type = str(payload.get("msg_type") or "text")
    if msg_type == "interactive":
        content_raw = payload.get("card")
        content = content_raw if isinstance(content_raw, dict) else {}
    else:
        msg_type = "text"
        content_raw = payload.get("content")
        content = content_raw if isinstance(content_raw, dict) else {"text": "card preview"}

    result = await send_message(
        settings,
        receive_id,
        msg_type,
        content,
        receive_id_type=receive_id_type,
    )
    print(f"sent message_id: {result.get('message_id', '')}")


def _extract_open_id_from_session_key(session_key: str) -> str:
    key = str(session_key or "").strip()
    if not key:
        return ""
    if _OPEN_ID_PATTERN.fullmatch(key):
        return key

    marker = ":user:"
    if marker in key:
        candidate = key.rsplit(marker, 1)[-1].strip()
        if _OPEN_ID_PATTERN.fullmatch(candidate):
            return candidate

    for part in key.split(":"):
        token = part.strip()
        if _OPEN_ID_PATTERN.fullmatch(token):
            return token
    return ""


def _candidate_midterm_db_paths(settings: Any) -> list[Path]:
    sqlite_path = str(
        getattr(getattr(getattr(settings, "agent", None), "midterm_memory", None), "sqlite_path", "")
        or "workspace/memory/midterm_memory.sqlite3"
    ).strip()
    raw_path = Path(sqlite_path).expanduser()

    candidates: list[Path] = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        candidates.extend(
            [
                Path.cwd() / raw_path,
                AGENT_HOST_ROOT / raw_path,
                REPO_ROOT / raw_path,
            ]
        )

    normalized: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item.resolve(strict=False))
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
    return normalized


def _find_recent_open_id_from_midterm_db(db_path: Path, scan_limit: int = 200) -> str:
    safe_limit = max(20, min(int(scan_limit), 2000))
    if not db_path.exists():
        return ""

    try:
        with sqlite3.connect(str(db_path)) as conn:
            cursor = conn.execute(
                """
                SELECT user_id
                FROM midterm_memory
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_limit,),
            )
            rows = cursor.fetchall()
    except sqlite3.Error:
        return ""

    for row in rows:
        user_id = str(row[0]) if row and row[0] is not None else ""
        open_id = _extract_open_id_from_session_key(user_id)
        if open_id:
            return open_id
    return ""


def _resolve_last_open_id(settings: Any) -> str:
    for db_path in _candidate_midterm_db_paths(settings):
        open_id = _find_recent_open_id_from_midterm_db(db_path)
        if open_id:
            print(f"[info] resolved latest open_id: {open_id} (from {db_path})")
            return open_id
    raise ValueError("cannot find recent open_id in midterm memory, please chat with bot once first")


def _resolve_preview_receiver(args: argparse.Namespace) -> tuple[str, str] | None:
    send_chat_id = str(getattr(args, "send_chat_id", "") or "").strip()
    if send_chat_id:
        return "chat_id", send_chat_id

    send_open_id = str(getattr(args, "send_open_id", "") or "").strip()
    if send_open_id:
        return "open_id", send_open_id

    send_user_id = str(getattr(args, "send_user_id", "") or "").strip()
    if send_user_id:
        return "user_id", send_user_id

    send_last_user = bool(getattr(args, "send_last_user", False))
    if send_last_user:
        open_id = _resolve_last_open_id(get_settings())
        return "open_id", open_id

    send_to_me = bool(getattr(args, "send_to_me", False))
    if send_to_me:
        env_open_id = str(os.getenv("FEISHU_PREVIEW_OPEN_ID", "") or "").strip()
        if env_open_id:
            return "open_id", env_open_id
        env_user_id = str(os.getenv("FEISHU_PREVIEW_USER_ID", "") or "").strip()
        if env_user_id:
            return "user_id", env_user_id
        raise ValueError("send-to-me requires FEISHU_PREVIEW_OPEN_ID or FEISHU_PREVIEW_USER_ID")

    env_receive_id = str(os.getenv("FEISHU_PREVIEW_RECEIVE_ID", "") or "").strip()
    if env_receive_id:
        env_receive_id_type = str(os.getenv("FEISHU_PREVIEW_RECEIVE_ID_TYPE", "chat_id") or "chat_id").strip().lower()
        if env_receive_id_type not in {"chat_id", "open_id", "user_id"}:
            env_receive_id_type = "chat_id"
        return env_receive_id_type, env_receive_id

    env_open_id = str(os.getenv("FEISHU_PREVIEW_OPEN_ID", "") or "").strip()
    if env_open_id:
        return "open_id", env_open_id
    env_user_id = str(os.getenv("FEISHU_PREVIEW_USER_ID", "") or "").strip()
    if env_user_id:
        return "user_id", env_user_id
    return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview actual Feishu card payload")
    parser.add_argument(
        "--preset",
        default="",
        help="quick preset: t1/t2/t3/t5/ht-t1/ht-t2/zb-t1/zb-t2/c1-confirm/c1-success/c2-confirm/c3-confirm/feedback/all",
    )
    parser.add_argument("--template-id", default="query.list", help="template id, e.g. query.list")
    parser.add_argument("--template-version", default="", help="template version, e.g. v2")
    parser.add_argument("--domain", default="case", help="query domain: case/contracts/bidding/team_overview")
    parser.add_argument("--style", default="T1", help="query style, e.g. T1/HT-T1/ZB-T1")
    parser.add_argument("--total", type=int, default=1, help="mock record count for query.list")
    parser.add_argument("--mock-file", default="", help="json file for params or full spec")
    parser.add_argument("--send-chat-id", default="", help="optional chat_id to send preview")
    parser.add_argument("--send-open-id", default="", help="optional open_id to send preview")
    parser.add_argument("--send-user-id", default="", help="optional user_id to send preview")
    parser.add_argument("--send-to-me", action="store_true", help="send to FEISHU_PREVIEW_OPEN_ID/USER_ID")
    parser.add_argument("--send-last-user", action="store_true", help="auto use latest user open_id from memory")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    if str(args.preset or "").strip().lower() == "all":
        preset_names = [
            "t1",
            "t2",
            "t3",
            "t5",
            "ht-t1",
            "ht-t2",
            "zb-t1",
            "zb-t2",
            "c1-confirm",
            "c1-success",
            "c2-confirm",
            "c3-confirm",
            "feedback",
        ]
        try:
            receiver = _resolve_preview_receiver(args)
        except ValueError as exc:
            print(f"[error] {exc}")
            return 2

        for name in preset_names:
            template_id, version, params = _preset_spec(name)
            payload = _render_payload(template_id, version, params)
            print(f"\n===== preset: {name} =====")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            if receiver is not None:
                receive_id_type, receive_id = receiver
                try:
                    asyncio.run(_send_preview(receive_id, receive_id_type, payload))
                except Exception as exc:
                    detail = _extract_http_error_text(exc)
                    message = f"[error] send preset {name} failed via {receive_id_type}={receive_id}: {exc}"
                    if detail:
                        message = f"{message}\n{detail}"
                    print(message)
                    return 3
        return 0

    if args.preset:
        template_id, version, params = _preset_spec(args.preset)
    else:
        template_id = str(args.template_id or "query.list").strip()
        version = str(args.template_version or "").strip() or resolve_template_version(template_id)
        params = _build_default_params(template_id, version, args)

    if args.mock_file:
        loaded = _load_json(args.mock_file)
        if "template_id" in loaded:
            template_id = str(loaded.get("template_id") or template_id)
        if "version" in loaded:
            version = str(loaded.get("version") or version)
        params_raw = loaded.get("params")
        if isinstance(params_raw, dict):
            params = {str(key): value for key, value in params_raw.items()}
        else:
            params = {str(key): value for key, value in loaded.items()}

    payload = _render_payload(template_id, version, params)
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    try:
        receiver = _resolve_preview_receiver(args)
    except ValueError as exc:
        print(f"[error] {exc}")
        return 2
    if receiver is not None:
        receive_id_type, receive_id = receiver
        try:
            asyncio.run(_send_preview(receive_id, receive_id_type, payload))
        except Exception as exc:
            detail = _extract_http_error_text(exc)
            message = f"[error] send preview failed via {receive_id_type}={receive_id}: {exc}"
            if detail:
                message = f"{message}\n{detail}"
            print(message)
            return 3
    else:
        print(
            "[info] payload rendered only. set --send-last-user/--send-to-me/--send-chat-id/--send-open-id to push via bot"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
