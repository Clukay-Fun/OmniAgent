from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml


DEFAULT_SCENARIOS = Path("docs/scenarios/scenarios.yaml")
DEFAULT_SCHEMA = Path("docs/scenarios/scenarios.schema.yaml")


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate(
    scenarios_path: Path,
    schema_path: Path,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not scenarios_path.exists():
        return [f"Scenarios file not found: {scenarios_path}"], warnings

    try:
        data = load_yaml(scenarios_path)
    except Exception as exc:  # noqa: BLE001
        return [f"YAML parse failed: {scenarios_path} ({exc})"], warnings

    if not isinstance(data, dict):
        return ["Top-level YAML must be an object"], warnings

    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list):
        errors.append("'scenarios' must be a list")
        scenarios = []

    schema_required = ["scenario_id", "title", "category"]
    schema_one_of = ["dialogue", "dialogue_variants", "trigger"]
    coverage_required = ["total_scenarios", "categories"]

    if schema_path.exists():
        try:
            schema_data = load_yaml(schema_path)
            if isinstance(schema_data, dict):
                scenario_schema = schema_data.get("scenario_schema") or {}
                coverage_schema = schema_data.get("coverage_schema") or {}
                if isinstance(scenario_schema, dict):
                    schema_required = list(
                        scenario_schema.get("required_fields") or schema_required
                    )
                    schema_one_of = list(
                        scenario_schema.get("one_of_fields") or schema_one_of
                    )
                if isinstance(coverage_schema, dict):
                    coverage_required = list(
                        coverage_schema.get("required_fields") or coverage_required
                    )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Schema parse failed: {schema_path} ({exc})")
    else:
        warnings.append(f"Schema file not found, using defaults: {schema_path}")

    seen_ids: set[str] = set()
    category_counter: Counter[str] = Counter()

    for idx, scenario in enumerate(scenarios, start=1):
        prefix = f"Scenario[{idx}]"
        if not isinstance(scenario, dict):
            errors.append(f"{prefix} must be an object")
            continue

        for field in schema_required:
            value = scenario.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                errors.append(f"{prefix} missing required field: {field}")

        if schema_one_of and not any(scenario.get(name) is not None for name in schema_one_of):
            errors.append(
                f"{prefix} must contain one of: {', '.join(schema_one_of)}"
            )

        scenario_id = scenario.get("scenario_id")
        if isinstance(scenario_id, str) and scenario_id:
            if scenario_id in seen_ids:
                errors.append(f"Duplicate scenario_id: {scenario_id}")
            seen_ids.add(scenario_id)

        category = scenario.get("category")
        if isinstance(category, str) and category:
            category_counter[category] += 1

    coverage = data.get("coverage")
    if coverage is None:
        warnings.append("'coverage' section not found")
    elif not isinstance(coverage, dict):
        errors.append("'coverage' must be an object")
    else:
        for field in coverage_required:
            if field not in coverage:
                errors.append(f"coverage missing required field: {field}")

        total = coverage.get("total_scenarios")
        if isinstance(total, int):
            if total != len(scenarios):
                errors.append(
                    f"coverage.total_scenarios={total}, actual={len(scenarios)}"
                )
        else:
            errors.append("coverage.total_scenarios must be int")

        categories = coverage.get("categories")
        if categories is not None and not isinstance(categories, dict):
            errors.append("coverage.categories must be an object")

    if scenarios:
        summary = ", ".join(
            f"{k}={v}" for k, v in sorted(category_counter.items(), key=lambda x: x[0])
        )
        warnings.append(f"category summary: {summary}")

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate scenario YAML files.")
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=DEFAULT_SCENARIOS,
        help="Path to scenarios yaml",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA,
        help="Path to scenario schema yaml",
    )
    args = parser.parse_args()

    errors, warnings = validate(args.scenarios, args.schema)

    for item in warnings:
        print(f"[WARN] {item}")
    for item in errors:
        print(f"[ERROR] {item}")

    if errors:
        print("\nScenario validation failed.")
        return 1

    print("\nScenario validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
