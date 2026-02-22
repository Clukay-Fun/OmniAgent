from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any


DEFAULT_USAGE_LOG_PATH = "workspace/usage/usage_log-{date}.jsonl"


def _resolve_path(path_template: str, selected_date: str) -> Path:
    return Path(path_template.replace("{date}", selected_date))


def _normalize_day(ts: Any) -> str:
    text = str(ts or "")
    if len(text) >= 10:
        token = text[:10]
        if token[4:5] == "-" and token[7:8] == "-":
            return token
    return ""


def load_usage_records(path: Path, selected_date: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if _normalize_day(payload.get("ts")) != selected_date:
                continue
            records.append(payload)
    return records


def aggregate_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_user: Counter[str] = Counter()
    by_skill: Counter[str] = Counter()
    by_route: Counter[str] = Counter()
    by_complexity: Counter[str] = Counter()
    by_model_calls: Counter[str] = Counter()
    by_model_tokens: Counter[str] = Counter()
    by_model_latency_sum: Counter[str] = Counter()
    by_model_latency_count: Counter[str] = Counter()

    for row in records:
        token_count = int(row.get("token_count") or 0)
        by_user[str(row.get("user_id") or "unknown")] += token_count
        by_skill[str(row.get("skill") or "unknown")] += token_count
        model = str(row.get("model") or "unknown")
        by_model_calls[model] += 1
        by_model_tokens[model] += token_count

        metadata_raw = row.get("metadata")
        metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
        route_label = str(row.get("route_label") or metadata.get("route_label") or "unknown")
        complexity = str(row.get("complexity") or metadata.get("complexity") or "unknown")
        by_route[route_label] += 1
        by_complexity[complexity] += 1

        latency_value = row.get("latency_ms")
        if latency_value is None:
            latency_value = metadata.get("latency_ms")
        try:
            latency_ms = float(latency_value if latency_value is not None else 0.0)
        except (TypeError, ValueError):
            latency_ms = 0.0
        if latency_ms > 0:
            by_model_latency_sum[model] += int(latency_ms)
            by_model_latency_count[model] += 1

    model_stats: dict[str, dict[str, float | int | None]] = {}
    for model, calls in by_model_calls.items():
        latency_count = int(by_model_latency_count.get(model, 0))
        latency_sum = int(by_model_latency_sum.get(model, 0))
        avg_latency_ms: int | None = None
        if latency_count > 0:
            avg_latency_ms = int(round(latency_sum / latency_count))
        model_stats[model] = {
            "calls": int(calls),
            "tokens": int(by_model_tokens.get(model, 0)),
            "avg_latency_ms": avg_latency_ms,
        }

    return {
        "total_records": len(records),
        "total_tokens": sum(by_user.values()),
        "by_user": by_user,
        "by_skill": by_skill,
        "by_model": model_stats,
        "by_route": by_route,
        "by_complexity": by_complexity,
    }


def render_report(aggregated: dict[str, Any], selected_date: str, path: Path) -> str:
    lines: list[str] = []
    lines.append(f"Usage report date: {selected_date}")
    lines.append(f"Source: {path}")
    lines.append(f"Total records: {aggregated['total_records']}")
    lines.append(f"Total tokens: {aggregated['total_tokens']}")
    lines.append("")

    lines.append("Top users by tokens:")
    for user_id, tokens in aggregated["by_user"].most_common(5):
        lines.append(f"- {user_id}: {tokens}")
    if not aggregated["by_user"]:
        lines.append("- (none)")

    lines.append("")
    lines.append("Top skills by tokens:")
    for skill, tokens in aggregated["by_skill"].most_common(5):
        lines.append(f"- {skill}: {tokens}")
    if not aggregated["by_skill"]:
        lines.append("- (none)")

    lines.append("")
    lines.append("Model comparison:")
    model_items = sorted(aggregated["by_model"].items(), key=lambda item: int(item[1].get("calls") or 0), reverse=True)
    for model, stats in model_items:
        calls = int(stats.get("calls") or 0)
        tokens = int(stats.get("tokens") or 0)
        avg_latency = stats.get("avg_latency_ms")
        if avg_latency is None:
            lines.append(f"- {model}: calls={calls}, tokens={tokens}, avg_latency_ms=n/a")
        else:
            lines.append(f"- {model}: calls={calls}, tokens={tokens}, avg_latency_ms={int(avg_latency)}")
    if not aggregated["by_model"]:
        lines.append("- (none)")

    lines.append("")
    lines.append("Route distribution:")
    for route_label, count in aggregated["by_route"].most_common():
        lines.append(f"- {route_label}: {count}")
    if not aggregated["by_route"]:
        lines.append("- (none)")

    lines.append("")
    lines.append("Complexity distribution:")
    for complexity, count in aggregated["by_complexity"].most_common():
        lines.append(f"- {complexity}: {count}")
    if not aggregated["by_complexity"]:
        lines.append("- (none)")

    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Usage JSONL report")
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="filter date (YYYY-MM-DD), default today",
    )
    parser.add_argument(
        "--path",
        default=DEFAULT_USAGE_LOG_PATH,
        help="usage log file path (supports {date})",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    selected_date = str(args.date)
    try:
        datetime.strptime(selected_date, "%Y-%m-%d")
    except ValueError:
        raise SystemExit("--date must be YYYY-MM-DD")

    path = _resolve_path(str(args.path), selected_date)
    records = load_usage_records(path, selected_date)
    aggregated = aggregate_usage(records)
    print(render_report(aggregated, selected_date, path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
