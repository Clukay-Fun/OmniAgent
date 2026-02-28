"""
描述: 自动化服务分发侧 mixin。
主要功能:
    - Webhook 鉴权与规则触发入口
    - 飞书事件分发与路由
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any

from src.automation.models import (
    AutomationValidationError,
    EVENT_TYPE_FIELD_CHANGED,
    SUPPORTED_EVENT_TYPES,
)


LOGGER = logging.getLogger(__name__)


class AutomationDispatcherMixin:
    def _verify_shared_auth(self, headers: dict[str, str], raw_body: bytes, *, require_webhook_enabled: bool) -> None:
        if require_webhook_enabled and not bool(self._settings.automation.webhook_enabled):
            raise AutomationValidationError("automation webhook is disabled")

        configured_key = str(self._settings.automation.webhook_api_key or "").strip()
        signature_secret = str(self._settings.automation.webhook_signature_secret or "").strip()
        if not configured_key and not signature_secret:
            raise AutomationValidationError(
                "automation webhook auth is not configured, set AUTOMATION_WEBHOOK_API_KEY "
                "or AUTOMATION_WEBHOOK_SIGNATURE_SECRET"
            )

        header_map = {str(key).lower(): str(value) for key, value in headers.items()}
        key_ok = False
        signature_ok = False

        if configured_key:
            provided_key = str(header_map.get("x-automation-key") or "").strip()
            if provided_key and hmac.compare_digest(provided_key, configured_key):
                key_ok = True

        if signature_secret:
            timestamp_text = str(header_map.get("x-automation-timestamp") or "").strip()
            signature_text = str(header_map.get("x-automation-signature") or "").strip()
            if timestamp_text and signature_text:
                try:
                    timestamp = int(timestamp_text)
                except ValueError as exc:
                    raise AutomationValidationError("invalid x-automation-timestamp") from exc

                now = int(time.time())
                tolerance = max(1, int(self._settings.automation.webhook_timestamp_tolerance_seconds or 300))
                if abs(now - timestamp) > tolerance:
                    raise AutomationValidationError("webhook signature timestamp expired")

                normalized_signature = signature_text
                if normalized_signature.lower().startswith("sha256="):
                    normalized_signature = normalized_signature.split("=", 1)[1].strip()

                signed_payload = f"{timestamp}".encode("utf-8") + b"." + raw_body
                expected_signature = hmac.new(
                    signature_secret.encode("utf-8"),
                    signed_payload,
                    hashlib.sha256,
                ).hexdigest()

                if hmac.compare_digest(normalized_signature, expected_signature):
                    signature_ok = True

        if configured_key and signature_secret:
            if not key_ok and not signature_ok:
                raise AutomationValidationError("invalid webhook api key or signature")
            return

        if configured_key and not key_ok:
            raise AutomationValidationError("invalid webhook api key")
        if signature_secret and not signature_ok:
            raise AutomationValidationError(
                "missing or invalid webhook signature headers: x-automation-timestamp / x-automation-signature"
            )

    def _verify_webhook_auth(self, headers: dict[str, str], raw_body: bytes) -> None:
        self._verify_shared_auth(headers, raw_body, require_webhook_enabled=True)

    def verify_management_auth(self, headers: dict[str, str], raw_body: bytes) -> None:
        self._verify_shared_auth(headers, raw_body, require_webhook_enabled=False)

    @staticmethod
    def _extract_webhook_fields(payload: dict[str, Any]) -> dict[str, Any]:
        fields = payload.get("fields")
        if isinstance(fields, dict):
            return fields

        reserved = {
            "event_id",
            "record_id",
            "table_id",
            "app_token",
            "event_kind",
            "old_fields",
            "diff",
            "force",
            "triggered_by",
            "notify_target",
            "notify_chat_id",
            "notify_user_id",
            "chat_id",
            "user_id",
        }
        result: dict[str, Any] = {}
        for key, value in payload.items():
            if key in reserved:
                continue
            result[str(key)] = value
        return result

    async def trigger_rule_webhook(
        self,
        *,
        rule_id: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        raw_body: bytes,
        force: bool = False,
    ) -> dict[str, Any]:
        self._ensure_enabled()
        self._verify_webhook_auth(headers, raw_body)

        normalized_rule_id = str(rule_id or "").strip()
        if not normalized_rule_id:
            raise AutomationValidationError("rule_id is required")

        rule = self._find_enabled_rule(normalized_rule_id)
        if rule is None:
            raise AutomationValidationError(f"rule not found or disabled: {normalized_rule_id}")

        if not isinstance(payload, dict):
            raise AutomationValidationError("webhook payload must be object")

        rule_table = rule.get("table") or {}
        if not isinstance(rule_table, dict):
            rule_table = {}

        app_token = str(
            payload.get("app_token")
            or rule_table.get("app_token")
            or self._settings.bitable.default_app_token
            or ""
        ).strip()
        table_id = str(
            payload.get("table_id")
            or rule_table.get("table_id")
            or self._settings.bitable.default_table_id
            or ""
        ).strip()

        event_id = str(payload.get("event_id") or "").strip()
        if not event_id:
            event_id = f"manual_webhook:{normalized_rule_id}:{int(time.time() * 1000)}"

        record_id = str(payload.get("record_id") or "").strip()
        if not record_id:
            record_id = f"manual:{int(time.time() * 1000)}"

        old_fields_raw = payload.get("old_fields")
        old_fields = old_fields_raw if isinstance(old_fields_raw, dict) else {}
        current_fields = self._extract_webhook_fields(payload)

        diff_raw = payload.get("diff")
        if isinstance(diff_raw, dict):
            diff = diff_raw
        else:
            diff = self._snapshot.diff(old_fields, current_fields)

        event_kind = str(payload.get("event_kind") or "").strip().lower()
        if not event_kind:
            event_kind = "updated" if old_fields else "created"

        notify_target = self._resolve_notify_target_from_context(payload)
        extra_context = {"notify_target": notify_target} if notify_target else None

        rule_execution = await self._engine.execute(
            app_token=app_token,
            table_id=table_id,
            record_id=record_id,
            event_id=event_id,
            old_fields=old_fields,
            current_fields=current_fields,
            diff=diff,
            event_kind=event_kind,
            rule_id_filter=normalized_rule_id,
            force_match=bool(force),
            extra_context=extra_context,
        )
        await self._notify_rule_execution_results(
            app_token=app_token,
            table_id=table_id,
            record_id=record_id,
            event_kind=event_kind,
            rule_execution=rule_execution,
            inherited_notify_target=notify_target,
        )

        return {
            "status": "ok",
            "kind": "webhook_rule_triggered",
            "rule_id": normalized_rule_id,
            "event_id": event_id,
            "event_kind": event_kind,
            "app_token": app_token,
            "table_id": table_id,
            "record_id": record_id,
            "force": bool(force),
            "rules": rule_execution,
        }

    def _decrypt_envelope_if_needed(self, payload: dict[str, Any]) -> dict[str, Any]:
        encrypted = payload.get("encrypt")
        if not encrypted:
            return payload

        encrypt_key = str(self._settings.automation.encrypt_key or "").strip()
        if not encrypt_key:
            raise AutomationValidationError("encrypted payload received but encrypt_key is empty")

        try:
            from Crypto.Cipher import AES  # type: ignore
            from Crypto.Util.Padding import unpad  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise AutomationValidationError(
                "encrypted payload requires dependency 'pycryptodome'"
            ) from exc

        try:
            encrypted_bytes = base64.b64decode(str(encrypted))
            iv = encrypted_bytes[:16]
            cipher_text = encrypted_bytes[16:]
            key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
            cipher = AES.new(key, AES.MODE_CBC, iv)
            plain = unpad(cipher.decrypt(cipher_text), AES.block_size)
            decoded = json.loads(plain.decode("utf-8"))
        except Exception as exc:
            raise AutomationValidationError(f"failed to decrypt payload: {exc}") from exc

        if not isinstance(decoded, dict):
            raise AutomationValidationError("decrypted payload is not an object")
        return decoded

    async def handle_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_enabled()

        decoded = self._decrypt_envelope_if_needed(payload)

        request_type = decoded.get("type")
        if request_type == "url_verification":
            self._verify_token(decoded.get("token"))
            challenge = str(decoded.get("challenge") or "")
            if not challenge:
                raise AutomationValidationError("url_verification missing challenge")
            return {"kind": "challenge", "challenge": challenge}

        header = decoded.get("header") or {}
        event = decoded.get("event") or {}
        if not isinstance(header, dict) or not isinstance(event, dict):
            raise AutomationValidationError("invalid event envelope")

        event_id = str(header.get("event_id") or "").strip()
        event_type = str(header.get("event_type") or "").strip()
        self._verify_token(header.get("token"))

        LOGGER.info(
            "automation event received: event_id=%s event_type=%s",
            event_id or "-",
            event_type or "-",
        )

        if not event_id:
            raise AutomationValidationError("missing header.event_id")
        if event_type not in SUPPORTED_EVENT_TYPES:
            result = {
                "kind": "ignored",
                "reason": "unsupported_event_type",
                "event_id": event_id,
                "event_type": event_type,
            }
            LOGGER.info(
                "automation event ignored: event_id=%s reason=%s",
                event_id,
                result.get("reason"),
            )
            return result
        if self._idempotency.is_event_duplicate(event_id):
            result = {
                "kind": "duplicate_event",
                "event_id": event_id,
            }
            LOGGER.info("automation event duplicate: event_id=%s", event_id)
            return result

        app_token = str(event.get("app_token") or event.get("appToken") or "").strip()
        table_id = str(event.get("table_id") or event.get("tableId") or "").strip()

        if event_type == EVENT_TYPE_FIELD_CHANGED:
            self._idempotency.mark_event(event_id)
            if not app_token or not table_id:
                result = {
                    "kind": "ignored",
                    "reason": "field_changed_missing_app_or_table",
                    "event_id": event_id,
                }
                LOGGER.info(
                    "automation event ignored: event_id=%s reason=%s",
                    event_id,
                    result.get("reason"),
                )
                return result

            if bool(self._settings.automation.schema_sync_event_driven):
                schema_result = await self.refresh_schema_table(
                    table_id=table_id,
                    app_token=app_token,
                    triggered_by="event",
                )
                result = {
                    "kind": "schema_refreshed",
                    "event_id": event_id,
                    "app_token": app_token,
                    "table_id": table_id,
                    "schema": schema_result,
                }
                LOGGER.info(
                    "automation schema event processed: event_id=%s table_id=%s changed=%s",
                    event_id,
                    table_id,
                    schema_result.get("changed"),
                )
                return result

            result = {
                "kind": "ignored",
                "reason": "schema_sync_event_driven_disabled",
                "event_id": event_id,
                "app_token": app_token,
                "table_id": table_id,
            }
            LOGGER.info(
                "automation event ignored: event_id=%s reason=%s",
                event_id,
                result.get("reason"),
            )
            return result

        record_id = str(event.get("record_id") or event.get("recordId") or "").strip()

        if not app_token or not table_id or not record_id:
            raise AutomationValidationError("record_changed event missing app_token/table_id/record_id")

        notify_target = self._extract_notify_target_from_event(event)

        result = await self.handle_record_changed(
            event_id,
            app_token,
            table_id,
            record_id,
            trigger_on_new_record=bool(self._settings.automation.trigger_on_new_record_event),
            notify_target=notify_target,
        )
        self._idempotency.mark_event(event_id)
        LOGGER.info(
            "automation event processed: event_id=%s kind=%s table_id=%s record_id=%s",
            event_id,
            result.get("kind"),
            table_id,
            record_id,
        )
        return result
