from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from src.config import Settings
from src.feishu.client import FeishuAPIError


LOGGER = logging.getLogger(__name__)


class ActionExecutionError(RuntimeError):
    """动作执行错误（包含重试信息）。"""

    def __init__(self, action_type: str, attempts: int, detail: str) -> None:
        self.action_type = action_type
        self.attempts = attempts
        self.detail = detail
        super().__init__(f"action {action_type} failed after {attempts} attempts: {detail}")


class _SafeTemplateDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _render_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") and stripped.endswith("}") and stripped.count("{") == 1 and stripped.count("}") == 1:
            key = stripped[1:-1].strip()
            if key in context:
                return context.get(key)
        return value.format_map(_SafeTemplateDict(context))
    if isinstance(value, dict):
        return {k: _render_value(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_render_value(item, context) for item in value]
    return value


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value

    if isinstance(value, (int, float)):
        ts = int(value)
        if ts > 10_000_000_000:
            ts = int(ts / 1000)
        return datetime.fromtimestamp(ts)

    if isinstance(value, dict):
        ts = value.get("timestamp")
        if ts is not None:
            return _parse_datetime(ts)

    text = str(value or "").strip()
    if not text:
        raise ValueError("datetime value is required")

    if text.isdigit():
        return _parse_datetime(int(text))

    normalized = text.replace("T", " ")
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue

    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            return dt.astimezone().replace(tzinfo=None)
        return dt
    except ValueError as exc:
        raise ValueError(f"invalid datetime format: {value}") from exc


def _to_unix_seconds(dt: datetime) -> str:
    return str(int(dt.timestamp()))


def _normalize_compare_value(value: Any) -> Any:
    if isinstance(value, (int, float, bool, str)):
        return value

    if isinstance(value, dict):
        if "text" in value and isinstance(value.get("text"), str):
            return value.get("text")

        if "value" in value and isinstance(value.get("value"), list):
            list_value = value.get("value")
            if isinstance(list_value, list):
                text_parts: list[str] = []
                all_text = True
                for item in list_value:
                    if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                        all_text = False
                        break
                    text_parts.append(str(item.get("text")))
                if all_text and text_parts:
                    return "".join(text_parts)

        for key in ("id", "open_id", "user_id", "union_id"):
            if key in value and value.get(key):
                return {"__id__": str(value.get(key))}
        return {k: _normalize_compare_value(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}

    if isinstance(value, list):
        if all(isinstance(item, dict) for item in value):
            ids: list[str] = []
            for item in value:
                if not isinstance(item, dict):
                    continue
                normalized = _normalize_compare_value(item)
                if isinstance(normalized, dict) and "__id__" in normalized:
                    ids.append(str(normalized.get("__id__")))
            if len(ids) == len(value) and ids:
                return sorted(ids)

        text_parts: list[str] = []
        all_text_items = True
        for item in value:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                all_text_items = False
                break
            text_parts.append(str(item.get("text")))
        if all_text_items and text_parts:
            return "".join(text_parts)

        return [_normalize_compare_value(item) for item in value]
    return value


def _same_compare_value(left: Any, right: Any) -> bool:
    return _normalize_compare_value(left) == _normalize_compare_value(right)


def _normalize_bitable_field_value(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, (int, float, bool, str)):
        return value

    if isinstance(value, dict):
        if "text" in value and isinstance(value.get("text"), str):
            return value.get("text")

        if "value" in value and isinstance(value.get("value"), list):
            list_value = value.get("value")
            if isinstance(list_value, list):
                text_parts: list[str] = []
                all_text = True
                for item in list_value:
                    if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                        all_text = False
                        break
                    text_parts.append(str(item.get("text")))
                if all_text and text_parts:
                    return "".join(text_parts)

        for key in ("id", "open_id", "user_id", "union_id"):
            if key in value and value.get(key):
                return [{"id": str(value.get(key))}]
        return {k: _normalize_bitable_field_value(v) for k, v in value.items()}

    if isinstance(value, list):
        normalized_users: list[dict[str, str]] = []
        is_user_list = True
        for item in value:
            if not isinstance(item, dict):
                is_user_list = False
                break
            user_id = None
            for key in ("id", "open_id", "user_id", "union_id"):
                if item.get(key):
                    user_id = str(item.get(key))
                    break
            if not user_id:
                is_user_list = False
                break
            normalized_users.append({"id": user_id})

        if is_user_list and normalized_users:
            return normalized_users

        text_parts: list[str] = []
        all_text_items = True
        for item in value:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                all_text_items = False
                break
            text_parts.append(str(item.get("text")))
        if all_text_items and text_parts:
            return "".join(text_parts)

        return [_normalize_bitable_field_value(item) for item in value]

    return value


def _normalize_bitable_fields_payload(fields: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in fields.items():
        normalized_value = _normalize_bitable_field_value(value)
        if normalized_value is None:
            continue
        if isinstance(normalized_value, str):
            stripped = normalized_value.strip()
            if stripped.startswith("{") and stripped.endswith("}") and len(stripped) >= 3:
                continue
        normalized[key] = normalized_value
    return normalized


class ActionExecutor:
    """动作执行器：支持 log.write、bitable.update、calendar.create（含重试）。"""

    def __init__(self, settings: Settings, client: Any) -> None:
        self._settings = settings
        self._client = client
        self._max_retries = max(0, int(settings.automation.action_max_retries or 0))
        self._retry_delay_seconds = max(0.0, float(settings.automation.action_retry_delay_seconds or 0.0))
        self._status_write_enabled = bool(settings.automation.status_write_enabled)
        self._status_field = str(settings.automation.status_field or "").strip()
        self._error_field = str(settings.automation.error_field or "").strip()

    async def _run_with_retry(self, action_type: str, runner: Any) -> dict[str, Any]:
        attempts = self._max_retries + 1
        for attempt in range(attempts):
            try:
                result = await runner()
                if not isinstance(result, dict):
                    raise ValueError(f"action {action_type} returned non-dict result")
                result["retry_count"] = attempt
                return result
            except Exception as exc:
                if attempt >= attempts - 1:
                    raise ActionExecutionError(action_type, attempts, str(exc)) from exc
                if self._retry_delay_seconds > 0:
                    await asyncio.sleep(self._retry_delay_seconds * (2**attempt))
        raise ActionExecutionError(action_type, attempts, "unreachable")

    @staticmethod
    def _compose_context(context: dict[str, Any]) -> dict[str, Any]:
        fields = context.get("fields")
        merged: dict[str, Any] = dict(context)
        if isinstance(fields, dict):
            merged.update(fields)
        return merged

    def _filter_status_fields(self, fields: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        if self._status_write_enabled:
            return fields, []

        status_names = {name for name in (self._status_field, self._error_field) if name}
        if not status_names:
            return fields, []

        filtered: dict[str, Any] = {}
        skipped: list[str] = []
        for key, value in fields.items():
            if key in status_names:
                skipped.append(key)
                continue
            filtered[key] = value
        return filtered, skipped

    async def _action_log_write(self, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        level = str(action.get("level") or "info").lower()
        message_template = str(action.get("message") or "")
        rendered_message = _render_value(message_template, self._compose_context(context))

        log_fn = getattr(LOGGER, level, LOGGER.info)
        log_fn("automation.log.write %s", rendered_message)
        return {
            "type": "log.write",
            "level": level,
            "message": rendered_message,
        }

    async def _action_bitable_update(
        self,
        action: dict[str, Any],
        context: dict[str, Any],
        app_token: str,
        table_id: str,
        record_id: str,
    ) -> dict[str, Any]:
        fields_template = action.get("fields")
        if not isinstance(fields_template, dict) or not fields_template:
            raise ValueError("bitable.update requires non-empty fields")

        rendered_fields = _render_value(fields_template, self._compose_context(context))
        if not isinstance(rendered_fields, dict) or not rendered_fields:
            raise ValueError("bitable.update rendered fields is empty")

        filtered_fields, skipped_fields = self._filter_status_fields(rendered_fields)
        filtered_fields = _normalize_bitable_fields_payload(filtered_fields)
        if not filtered_fields:
            return {
                "type": "bitable.update",
                "fields": {},
                "skipped": True,
                "skip_reason": "status_write_disabled",
                "skipped_fields": skipped_fields,
            }

        await self._client.request(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            json_body={"fields": filtered_fields},
        )

        fields = context.get("fields")
        if isinstance(fields, dict):
            fields.update(filtered_fields)

        return {
            "type": "bitable.update",
            "fields": filtered_fields,
            "skipped_fields": skipped_fields,
        }

    async def _action_calendar_create(self, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        rendered_action = _render_value(action, self._compose_context(context))
        if not isinstance(rendered_action, dict):
            raise ValueError("calendar.create action payload is invalid")

        calendar_id = str(
            rendered_action.get("calendar_id")
            or self._settings.calendar.default_calendar_id
            or ""
        ).strip()
        if not calendar_id:
            raise ValueError("calendar.create requires calendar_id")

        summary = str(
            rendered_action.get("summary")
            or rendered_action.get("summary_template")
            or ""
        ).strip()
        if not summary:
            raise ValueError("calendar.create requires summary/summary_template")

        description = str(
            rendered_action.get("description")
            or rendered_action.get("description_template")
            or ""
        )

        timezone = str(
            rendered_action.get("timezone") or self._settings.calendar.timezone or "Asia/Shanghai"
        ).strip()
        need_notification = bool(rendered_action.get("need_notification", True))
        rrule = str(rendered_action.get("rrule") or "").strip()

        start_value: Any = rendered_action.get("start_at")
        start_field = str(rendered_action.get("start_field") or "").strip()
        if start_value is None and start_field:
            fields = context.get("fields")
            if isinstance(fields, dict):
                start_value = fields.get(start_field)
        if start_value is None:
            raise ValueError("calendar.create requires start_at or start_field")
        start_dt = _parse_datetime(start_value)

        end_value: Any = rendered_action.get("end_at")
        end_field = str(rendered_action.get("end_field") or "").strip()
        if end_value is None and end_field:
            fields = context.get("fields")
            if isinstance(fields, dict):
                end_value = fields.get(end_field)

        if end_value is not None:
            end_dt = _parse_datetime(end_value)
        else:
            duration = int(
                rendered_action.get("duration_minutes")
                or self._settings.calendar.default_duration_minutes
                or 30
            )
            if duration <= 0:
                duration = 30
            end_dt = start_dt + timedelta(minutes=duration)

        if end_dt <= start_dt:
            raise ValueError("calendar.create end_at must be later than start_at")

        body: dict[str, Any] = {
            "summary": summary,
            "description": description,
            "need_notification": need_notification,
            "start_time": {
                "timestamp": _to_unix_seconds(start_dt),
                "timezone": timezone,
            },
            "end_time": {
                "timestamp": _to_unix_seconds(end_dt),
                "timezone": timezone,
            },
        }
        if rrule:
            body["rrule"] = rrule

        response = await self._client.request(
            "POST",
            f"/calendar/v4/calendars/{calendar_id}/events",
            json_body=body,
        )
        raw_data = response.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        raw_event = data.get("event")
        event = raw_event if isinstance(raw_event, dict) else {}
        event_id = event.get("event_id") or data.get("event_id") or ""
        event_url = event.get("url") or event.get("html_link") or data.get("url") or ""

        return {
            "type": "calendar.create",
            "calendar_id": calendar_id,
            "event_id": str(event_id),
            "event_url": str(event_url),
            "summary": summary,
            "start_at": start_dt.strftime("%Y-%m-%d %H:%M"),
            "end_at": end_dt.strftime("%Y-%m-%d %H:%M"),
            "timezone": timezone,
        }

    async def _action_bitable_upsert(
        self,
        action: dict[str, Any],
        context: dict[str, Any],
        app_token: str,
    ) -> dict[str, Any]:
        rendered_action = _render_value(action, self._compose_context(context))
        if not isinstance(rendered_action, dict):
            raise ValueError("bitable.upsert action payload is invalid")

        target_table_id = str(rendered_action.get("target_table_id") or "").strip()
        if not target_table_id:
            raise ValueError("bitable.upsert requires target_table_id")

        target_app_token = str(rendered_action.get("target_app_token") or app_token or "").strip()
        if not target_app_token:
            raise ValueError("bitable.upsert requires target_app_token or current app_token")

        match_fields = rendered_action.get("match_fields")
        if not isinstance(match_fields, dict) or not match_fields:
            raise ValueError("bitable.upsert requires non-empty match_fields")

        update_fields = rendered_action.get("update_fields")
        if not isinstance(update_fields, dict):
            update_fields = {}

        create_fields = rendered_action.get("create_fields")
        if not isinstance(create_fields, dict):
            create_fields = {}

        page_size = max(1, min(int(self._settings.automation.scan_page_size or 100), 500))
        max_pages = max(1, int(self._settings.automation.max_scan_pages or 50))
        match_field_names = sorted([str(key) for key in match_fields.keys() if str(key).strip()])
        update_all_matches = bool(rendered_action.get("update_all_matches", False))

        matched_record_ids: list[str] = []
        matched_set: set[str] = set()
        page_token = ""
        pages = 0
        while pages < max_pages:
            payload: dict[str, Any] = {
                "page_size": page_size,
            }
            if page_token:
                payload["page_token"] = page_token
            if match_field_names:
                payload["field_names"] = match_field_names

            response = await self._client.request(
                "POST",
                f"/bitable/v1/apps/{target_app_token}/tables/{target_table_id}/records/search",
                json_body=payload,
            )
            data = response.get("data") or {}
            items = data.get("items") or []
            if not isinstance(items, list):
                items = []

            for item in items:
                if not isinstance(item, dict):
                    continue
                record_fields = item.get("fields")
                if not isinstance(record_fields, dict):
                    record_fields = {}

                matched = True
                for field_name, expected_value in match_fields.items():
                    if not _same_compare_value(record_fields.get(field_name), expected_value):
                        matched = False
                        break
                if not matched:
                    continue

                matched_record_id = str(item.get("record_id") or item.get("recordId") or "").strip()
                if matched_record_id and matched_record_id not in matched_set:
                    matched_record_ids.append(matched_record_id)
                    matched_set.add(matched_record_id)

            pages += 1
            if matched_record_ids and not update_all_matches:
                break

            has_more = bool(data.get("has_more"))
            if not has_more:
                break
            page_token = str(data.get("page_token") or "")
            if not page_token:
                break

        write_match_fields_on_update = bool(rendered_action.get("write_match_fields_on_update", False))

        async def update_with_fallback(record_id_to_update: str, fields_to_update: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
            if not fields_to_update:
                return {}, {}

            try:
                await self._client.request(
                    "PUT",
                    (
                        f"/bitable/v1/apps/{target_app_token}/tables/{target_table_id}"
                        f"/records/{record_id_to_update}"
                    ),
                    json_body={"fields": fields_to_update},
                )
                return dict(fields_to_update), {}
            except FeishuAPIError:
                success_fields: dict[str, Any] = {}
                failed_fields: dict[str, str] = {}
                for field_name, field_value in fields_to_update.items():
                    try:
                        await self._client.request(
                            "PUT",
                            (
                                f"/bitable/v1/apps/{target_app_token}/tables/{target_table_id}"
                                f"/records/{record_id_to_update}"
                            ),
                            json_body={"fields": {field_name: field_value}},
                        )
                        success_fields[field_name] = field_value
                    except FeishuAPIError as exc:
                        failed_fields[field_name] = str(exc)
                return success_fields, failed_fields

        if matched_record_ids:
            merged_update_fields: dict[str, Any] = {}
            if write_match_fields_on_update:
                merged_update_fields.update(match_fields)
            merged_update_fields.update(update_fields)
            merged_update_fields = _normalize_bitable_fields_payload(merged_update_fields)

            if merged_update_fields:
                ids_to_update = matched_record_ids if update_all_matches else [matched_record_ids[0]]
                merged_success_fields: dict[str, Any] = {}
                merged_failed_fields: dict[str, str] = {}
                for matched_record_id in ids_to_update:
                    success_fields, failed_fields = await update_with_fallback(matched_record_id, merged_update_fields)
                    if success_fields:
                        merged_success_fields.update(success_fields)
                    if failed_fields:
                        merged_failed_fields.update(failed_fields)

                if merged_success_fields and merged_failed_fields:
                    operation = "updated_partial_many" if len(ids_to_update) > 1 else "updated_partial"
                elif merged_success_fields:
                    operation = "updated_many" if len(ids_to_update) > 1 else "updated"
                else:
                    raise ValueError(f"bitable.upsert update failed for all fields: {merged_failed_fields}")
            else:
                operation = "matched_no_update"
                merged_success_fields = {}
                merged_failed_fields = {}

            return {
                "type": "bitable.upsert",
                "operation": operation,
                "target_app_token": target_app_token,
                "target_table_id": target_table_id,
                "target_record_id": matched_record_ids[0],
                "target_record_ids": matched_record_ids,
                "match_fields": match_fields,
                "update_fields": merged_success_fields,
                "failed_fields": merged_failed_fields,
            }

        merged_create_fields: dict[str, Any] = {}
        merged_create_fields.update(match_fields)
        merged_create_fields.update(update_fields)
        merged_create_fields.update(create_fields)
        merged_create_fields = _normalize_bitable_fields_payload(merged_create_fields)
        if not merged_create_fields:
            raise ValueError("bitable.upsert create payload is empty")

        failed_fields: dict[str, str] = {}
        try:
            create_response = await self._client.request(
                "POST",
                f"/bitable/v1/apps/{target_app_token}/tables/{target_table_id}/records",
                json_body={"fields": merged_create_fields},
            )
            create_operation = "created"
            created_update_fields = dict(merged_create_fields)
        except FeishuAPIError:
            minimal_create_fields = _normalize_bitable_fields_payload(match_fields)
            if not minimal_create_fields:
                raise

            create_response = await self._client.request(
                "POST",
                f"/bitable/v1/apps/{target_app_token}/tables/{target_table_id}/records",
                json_body={"fields": minimal_create_fields},
            )
            create_operation = "created_partial"
            created_update_fields = dict(minimal_create_fields)

        create_data = create_response.get("data") or {}
        create_record = create_data.get("record")
        created_record_id = ""
        if isinstance(create_record, dict):
            created_record_id = str(create_record.get("record_id") or create_record.get("recordId") or "").strip()
        if not created_record_id:
            created_record_id = str(create_data.get("record_id") or create_data.get("recordId") or "").strip()

        if create_operation == "created_partial" and created_record_id:
            minimal_keys = set(_normalize_bitable_fields_payload(match_fields).keys())
            extra_fields = {k: v for k, v in merged_create_fields.items() if k not in minimal_keys}
            success_fields, extra_failed = await update_with_fallback(created_record_id, extra_fields)
            created_update_fields.update(success_fields)
            failed_fields.update(extra_failed)

        return {
            "type": "bitable.upsert",
            "operation": create_operation,
            "target_app_token": target_app_token,
            "target_table_id": target_table_id,
            "target_record_id": created_record_id,
            "match_fields": match_fields,
            "update_fields": created_update_fields,
            "create_fields": create_fields,
            "failed_fields": failed_fields,
        }

    async def run_actions(
        self,
        actions: list[dict[str, Any]],
        context: dict[str, Any],
        app_token: str,
        table_id: str,
        record_id: str,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for action in actions:
            action_type = str(action.get("type") or "").strip()
            if action_type == "log.write":
                result = await self._run_with_retry(
                    action_type,
                    lambda: self._action_log_write(action, context),
                )
                results.append(result)
                context["last_action_type"] = action_type
                context["last_action_result"] = result
                continue
            if action_type == "bitable.update":
                result = await self._run_with_retry(
                    action_type,
                    lambda: self._action_bitable_update(action, context, app_token, table_id, record_id),
                )
                results.append(result)
                context["last_action_type"] = action_type
                context["last_action_result"] = result
                continue
            if action_type == "calendar.create":
                result = await self._run_with_retry(
                    action_type,
                    lambda: self._action_calendar_create(action, context),
                )
                results.append(result)
                context["last_action_type"] = action_type
                context["last_action_result"] = result
                continue
            if action_type == "bitable.upsert":
                result = await self._run_with_retry(
                    action_type,
                    lambda: self._action_bitable_upsert(action, context, app_token),
                )
                results.append(result)
                context["last_action_type"] = action_type
                context["last_action_result"] = result
                context["upsert_record_id"] = str(result.get("target_record_id") or "")
                context["upsert_operation"] = str(result.get("operation") or "")
                continue
            raise ValueError(f"unsupported action type: {action_type}")
        return results
