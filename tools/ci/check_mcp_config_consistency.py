"""
Description: MCP config consistency checker.
Main features:
    - Validates Docker runtime config source for MCP container
    - Verifies required MCP tools exist in runtime/example configs
    - Fails fast to prevent MCP 403 Tool disabled regressions
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml


def _load_enabled_tools(config_path: Path) -> set[str]:
    payload: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    tools = payload.get("tools")
    if not isinstance(tools, dict):
        return set()
    enabled = tools.get("enabled")
    if not isinstance(enabled, list):
        return set()
    return {str(name).strip() for name in enabled if str(name).strip()}


def _runtime_source_from_dockerfile(dockerfile_path: Path) -> str:
    text = dockerfile_path.read_text(encoding="utf-8")
    match = re.search(r"^COPY\s+([^\s]+)\s+\./config\.yaml$", text, flags=re.MULTILINE)
    if not match:
        raise RuntimeError("Dockerfile must COPY a runtime config to ./config.yaml")
    return str(match.group(1)).strip()


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    mcp_root = repo_root / "mcp" / "mcp-feishu-server"
    dockerfile = mcp_root / "Dockerfile"
    example_path = mcp_root / "config.yaml.example"

    sys.path.insert(0, str(mcp_root))
    from src.config import REQUIRED_AGENT_MCP_TOOLS

    required_tools = set(REQUIRED_AGENT_MCP_TOOLS)
    errors: list[str] = []

    runtime_source = _runtime_source_from_dockerfile(dockerfile)
    runtime_path = mcp_root / runtime_source

    if runtime_source == "config.yaml.example":
        errors.append("Docker runtime config source cannot be config.yaml.example")

    for name, path in (("runtime", runtime_path), ("example", example_path)):
        if not path.exists():
            errors.append(f"Missing {name} config file: {path}")
            continue
        enabled = _load_enabled_tools(path)
        missing = sorted(required_tools - enabled)
        if missing:
            errors.append(f"{name} config missing required tools: {', '.join(missing)}")

    if errors:
        print("MCP config consistency check failed:")
        for item in errors:
            print(f"- {item}")
        return 1

    print("MCP config consistency check passed")
    print(f"- runtime source: {runtime_source}")
    print(f"- required tools: {len(required_tools)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
