"""
描述: 自动化灰度检查脚本。
主要功能:
    - 统计运行日志并输出规则执行健康度
    - 支持严格模式与 JSON 输出
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import get_settings
from src.feishu.client import FeishuClient


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _to_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _parse_modified_time(item: dict[str, Any]) -> datetime | None:
    raw = (
        item.get("last_modified_time")
        or item.get("lastModifiedTime")
        or item.get("last_modified_timestamp")
        or item.get("lastModifiedTimestamp")
        or item.get("modified_time")
    )
    if raw is None:
        return None
    try:
        ts = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if ts > 10_000_000_000:
        ts = int(ts / 1000)
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _bucket_status(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "empty"
    if text in {"处理中", "成功", "失败"}:
        return text
    return f"other:{text}"


async def _fetch_all_records(
    client: FeishuClient,
    app_token: str,
    table_id: str,
    page_size: int,
    max_pages: int,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page_token = ""
    pages = 0

    while pages < max_pages:
        payload: dict[str, Any] = {"page_size": page_size}
        if page_token:
            payload["page_token"] = page_token

        response = await client.request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
            json_body=payload,
        )
        data = response.get("data") or {}
        items = data.get("items") or []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    records.append(item)

        pages += 1
        if not data.get("has_more"):
            break
        page_token = str(data.get("page_token") or "")
        if not page_token:
            break

    return records


def _read_dead_letters(path: Path, since: datetime) -> tuple[int, int]:
    if not path.exists():
        return 0, 0

    total = 0
    recent = 0

    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        total += 1
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        ts = _parse_iso_datetime(payload.get("timestamp"))
        if ts is not None and ts >= since:
            recent += 1

    return total, recent


def _read_run_logs(path: Path, since: datetime) -> dict[str, Any]:
    if not path.exists():
        return {
            "total": 0,
            "in_window": 0,
            "results_in_window": {},
            "failed_in_window": 0,
            "no_match_in_window": 0,
            "retry_max_in_window": 0,
        }

    total = 0
    in_window = 0
    results_in_window: dict[str, int] = {}
    failed_in_window = 0
    no_match_in_window = 0
    retry_max_in_window = 0

    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        total += 1
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue

        ts = _parse_iso_datetime(payload.get("timestamp"))
        if ts is None or ts < since:
            continue

        in_window += 1
        result = str(payload.get("result") or "unknown")
        results_in_window[result] = results_in_window.get(result, 0) + 1
        if result == "failed":
            failed_in_window += 1
        if result == "no_match":
            no_match_in_window += 1

        retry_count = int(payload.get("retry_count") or 0)
        if retry_count > retry_max_in_window:
            retry_max_in_window = retry_count

    return {
        "total": total,
        "in_window": in_window,
        "results_in_window": results_in_window,
        "failed_in_window": failed_in_window,
        "no_match_in_window": no_match_in_window,
        "retry_max_in_window": retry_max_in_window,
    }


async def _run_check(args: argparse.Namespace) -> dict[str, Any]:
    load_dotenv()
    get_settings.cache_clear()
    settings = get_settings()

    app_token = str(args.app_token or settings.bitable.default_app_token or "").strip()
    table_id = str(args.table_id or settings.bitable.default_table_id or "").strip()
    if (not bool(args.no_api)) and (not app_token or not table_id):
        raise RuntimeError("missing app_token/table_id")

    status_field = str(settings.automation.status_field or "自动化_执行状态").strip()
    error_field = str(settings.automation.error_field or "自动化_最近错误").strip()

    dead_letter_file = Path(args.dead_letter_file or settings.automation.dead_letter_file)
    if not dead_letter_file.is_absolute():
        dead_letter_file = Path.cwd() / dead_letter_file

    run_log_file = Path(args.run_log_file or settings.automation.run_log_file)
    if not run_log_file.is_absolute():
        run_log_file = Path.cwd() / run_log_file

    now = _utc_now()
    since = now - timedelta(hours=float(args.hours))

    status_all: dict[str, int] = {}
    status_recent: dict[str, int] = {}
    recent_records = 0
    recent_error_nonempty = 0

    records: list[dict[str, Any]] = []
    if not bool(args.no_api):
        client = FeishuClient(settings)
        records = await _fetch_all_records(
            client=client,
            app_token=app_token,
            table_id=table_id,
            page_size=int(args.page_size),
            max_pages=int(args.max_pages),
        )

        for item in records:
            fields = item.get("fields")
            if not isinstance(fields, dict):
                fields = {}
            status_key = _bucket_status(fields.get(status_field))
            status_all[status_key] = status_all.get(status_key, 0) + 1

            modified_at = _parse_modified_time(item)
            if modified_at is None or modified_at < since:
                continue

            recent_records += 1
            status_recent[status_key] = status_recent.get(status_key, 0) + 1

            error_value = str(fields.get(error_field) or "").strip()
            if error_value:
                recent_error_nonempty += 1

    dead_total, dead_recent = _read_dead_letters(dead_letter_file, since)
    run_log_stats = _read_run_logs(run_log_file, since)

    anomalies: list[str] = []
    if dead_recent > 0:
        anomalies.append(f"dead letters in window: {dead_recent}")
    if run_log_stats.get("failed_in_window", 0) > 0:
        anomalies.append(f"failed run logs in window: {run_log_stats.get('failed_in_window', 0)}")
    if status_recent.get("失败", 0) > 0:
        anomalies.append(f"failed status in window: {status_recent.get('失败', 0)}")
    if recent_error_nonempty > 0:
        anomalies.append(f"non-empty error field in window: {recent_error_nonempty}")

    return {
        "ok": len(anomalies) == 0,
        "anomalies": anomalies,
        "window_hours": float(args.hours),
        "checked_at": now.isoformat(),
        "since": since.isoformat(),
        "table_id": table_id,
        "app_token": app_token,
        "records_scanned": len(records),
        "records_in_window": recent_records,
        "status_all": status_all,
        "status_in_window": status_recent,
        "error_nonempty_in_window": recent_error_nonempty,
        "api_scan_enabled": not bool(args.no_api),
        "run_log_file": str(run_log_file),
        "run_logs_total": int(run_log_stats.get("total") or 0),
        "run_logs_in_window": int(run_log_stats.get("in_window") or 0),
        "run_log_results_in_window": run_log_stats.get("results_in_window") or {},
        "run_log_failed_in_window": int(run_log_stats.get("failed_in_window") or 0),
        "run_log_no_match_in_window": int(run_log_stats.get("no_match_in_window") or 0),
        "run_log_retry_max_in_window": int(run_log_stats.get("retry_max_in_window") or 0),
        "dead_letter_file": str(dead_letter_file),
        "dead_letters_total": dead_total,
        "dead_letters_in_window": dead_recent,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Automation gray-window health checker",
    )
    parser.add_argument("--hours", type=float, default=24, help="window size in hours")
    parser.add_argument("--table-id", type=str, default="", help="override table_id")
    parser.add_argument("--app-token", type=str, default="", help="override app_token")
    parser.add_argument("--dead-letter-file", type=str, default="", help="override dead letter file path")
    parser.add_argument("--run-log-file", type=str, default="", help="override run log file path")
    parser.add_argument("--page-size", type=int, default=200, help="records search page size")
    parser.add_argument("--max-pages", type=int, default=100, help="max pages to scan")
    parser.add_argument("--no-api", action="store_true", help="skip API scan and read local logs only")
    parser.add_argument("--strict", action="store_true", help="exit non-zero when anomalies found")
    parser.add_argument("--json", action="store_true", help="print JSON only")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    result = asyncio.run(_run_check(args))

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("automation gray check")
        print(f"- ok: {result['ok']}")
        print(f"- window_hours: {result['window_hours']}")
        print(f"- api_scan_enabled: {result['api_scan_enabled']}")
        print(f"- records_scanned: {result['records_scanned']}")
        print(f"- records_in_window: {result['records_in_window']}")
        print(f"- run_logs_in_window: {result['run_logs_in_window']}")
        print(f"- run_log_results_in_window: {result['run_log_results_in_window']}")
        print(f"- dead_letters_in_window: {result['dead_letters_in_window']}")
        print(f"- error_nonempty_in_window: {result['error_nonempty_in_window']}")
        print(f"- status_in_window: {result['status_in_window']}")
        if result["anomalies"]:
            print("- anomalies:")
            for item in result["anomalies"]:
                print(f"  - {item}")

    if args.strict and not result["ok"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
