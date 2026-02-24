from __future__ import annotations

import argparse
import json
from collections import Counter
from collections import defaultdict
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
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    by_user: Counter[str] = Counter()
    by_user_cost: dict[str, float] = defaultdict(float)
    by_skill: Counter[str] = Counter()
    by_skill_cost: dict[str, float] = defaultdict(float)
    by_source_calls: Counter[str] = Counter()
    by_source_tokens: Counter[str] = Counter()
    by_source_cost: dict[str, float] = defaultdict(float)
    by_route: Counter[str] = Counter()
    by_complexity: Counter[str] = Counter()
    by_model_calls: Counter[str] = Counter()
    by_model_tokens: Counter[str] = Counter()
    by_model_cost: dict[str, float] = defaultdict(float)
    by_model_latency_sum: Counter[str] = Counter()
    by_model_latency_count: Counter[str] = Counter()
    by_action_classification: Counter[str] = Counter()
    by_close_semantic: Counter[str] = Counter()
    by_close_profile: Counter[str] = Counter()

    for row in records:
        token_count = _safe_int(row.get("token_count"), 0)
        cost = _safe_float(row.get("cost"), 0.0)
        user_id = str(row.get("user_id") or "unknown")
        skill = str(row.get("skill") or "unknown")
        source = str(row.get("usage_source") or "unknown")
        by_user[user_id] += token_count
        by_user_cost[user_id] += cost
        by_skill[skill] += token_count
        by_skill_cost[skill] += cost
        by_source_calls[source] += 1
        by_source_tokens[source] += token_count
        by_source_cost[source] += cost
        model = str(row.get("model") or "unknown")
        by_model_calls[model] += 1
        by_model_tokens[model] += token_count
        by_model_cost[model] += cost

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
            latency_ms = _safe_float(latency_value if latency_value is not None else 0.0, 0.0)
        except Exception:
            latency_ms = 0.0
        if latency_ms > 0:
            by_model_latency_sum[model] += int(latency_ms)
            by_model_latency_count[model] += 1

        business_raw = row.get("business_metadata")
        business = business_raw if isinstance(business_raw, dict) else {}
        action_classification = str(business.get("action_classification") or "").strip()
        close_semantic = str(business.get("close_semantic") or "").strip()
        close_profile = str(business.get("close_profile") or "").strip()
        if action_classification:
            by_action_classification[action_classification] += 1
        if close_semantic:
            by_close_semantic[close_semantic] += 1
        if close_profile:
            by_close_profile[close_profile] += 1

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
            "cost": round(float(by_model_cost.get(model, 0.0)), 6),
            "avg_latency_ms": avg_latency_ms,
        }

    source_stats: dict[str, dict[str, float | int]] = {}
    for source, calls in by_source_calls.items():
        source_stats[source] = {
            "calls": int(calls),
            "tokens": int(by_source_tokens.get(source, 0)),
            "cost": round(float(by_source_cost.get(source, 0.0)), 6),
        }

    return {
        "total_records": len(records),
        "total_tokens": sum(by_user.values()),
        "total_cost": round(float(sum(by_user_cost.values())), 6),
        "by_user": by_user,
        "by_user_cost": by_user_cost,
        "by_skill": by_skill,
        "by_skill_cost": by_skill_cost,
        "by_model": model_stats,
        "by_source": source_stats,
        "by_route": by_route,
        "by_complexity": by_complexity,
        "by_action_classification": by_action_classification,
        "by_close_semantic": by_close_semantic,
        "by_close_profile": by_close_profile,
    }


def render_report(aggregated: dict[str, Any], selected_date: str, path: Path) -> str:
    lines: list[str] = []
    lines.append(f"Usage report date: {selected_date}")
    lines.append(f"Source: {path}")
    lines.append(f"Total records: {aggregated['total_records']}")
    lines.append(f"Total tokens: {aggregated['total_tokens']}")
    lines.append(f"Total cost: {aggregated['total_cost']:.6f}")
    lines.append("")

    lines.append("Top users by tokens:")
    for user_id, tokens in aggregated["by_user"].most_common(5):
        user_cost = float(aggregated["by_user_cost"].get(user_id, 0.0))
        lines.append(f"- {user_id}: tokens={tokens}, cost={user_cost:.6f}")
    if not aggregated["by_user"]:
        lines.append("- (none)")

    lines.append("")
    lines.append("Top skills by tokens:")
    for skill, tokens in aggregated["by_skill"].most_common(5):
        skill_cost = float(aggregated["by_skill_cost"].get(skill, 0.0))
        lines.append(f"- {skill}: tokens={tokens}, cost={skill_cost:.6f}")
    if not aggregated["by_skill"]:
        lines.append("- (none)")

    lines.append("")
    lines.append("Model comparison:")
    model_items = sorted(aggregated["by_model"].items(), key=lambda item: int(item[1].get("calls") or 0), reverse=True)
    for model, stats in model_items:
        calls = int(stats.get("calls") or 0)
        tokens = int(stats.get("tokens") or 0)
        cost = float(stats.get("cost") or 0.0)
        avg_latency = stats.get("avg_latency_ms")
        if avg_latency is None:
            lines.append(f"- {model}: calls={calls}, tokens={tokens}, cost={cost:.6f}, avg_latency_ms=n/a")
        else:
            lines.append(f"- {model}: calls={calls}, tokens={tokens}, cost={cost:.6f}, avg_latency_ms={int(avg_latency)}")
    if not aggregated["by_model"]:
        lines.append("- (none)")

    lines.append("")
    lines.append("Source distribution:")
    source_items = sorted(aggregated["by_source"].items(), key=lambda item: int(item[1].get("calls") or 0), reverse=True)
    for source, stats in source_items:
        lines.append(
            f"- {source}: calls={int(stats.get('calls') or 0)}, tokens={int(stats.get('tokens') or 0)}, cost={float(stats.get('cost') or 0.0):.6f}"
        )
    if not aggregated["by_source"]:
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

    lines.append("")
    lines.append("Business semantics:")
    for action_classification, count in aggregated["by_action_classification"].most_common():
        lines.append(f"- action_classification.{action_classification}: {count}")
    for close_semantic, count in aggregated["by_close_semantic"].most_common():
        lines.append(f"- close_semantic.{close_semantic}: {count}")
    for close_profile, count in aggregated["by_close_profile"].most_common():
        lines.append(f"- close_profile.{close_profile}: {count}")
    if not aggregated["by_action_classification"] and not aggregated["by_close_semantic"] and not aggregated["by_close_profile"]:
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
