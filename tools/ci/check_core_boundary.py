from __future__ import annotations

import argparse
import ast
from pathlib import Path
from typing import Iterable

DEFAULT_FORBIDDEN_KEYWORDS = ("lark_oapi", "feishu", "webhook")


def _match_keyword(value: str, keywords: Iterable[str]) -> str | None:
    lowered = value.lower()
    for keyword in keywords:
        if keyword.lower() in lowered:
            return keyword
    return None


def scan_forbidden_imports(
    root: Path | str,
    forbidden_keywords: tuple[str, ...] = DEFAULT_FORBIDDEN_KEYWORDS,
) -> list[dict[str, str | int]]:
    root_path = Path(root)
    violations: list[dict[str, str | int]] = []

    for py_file in sorted(root_path.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError as exc:
            violations.append(
                {
                    "file": str(py_file),
                    "line": exc.lineno or 1,
                    "statement": f"syntax error: {exc.msg}",
                    "keyword": "syntax-error",
                }
            )
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    keyword = _match_keyword(alias.name, forbidden_keywords)
                    if keyword:
                        violations.append(
                            {
                                "file": str(py_file),
                                "line": node.lineno,
                                "statement": f"import {alias.name}",
                                "keyword": keyword,
                            }
                        )
            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                module_with_level = f"{'.' * node.level}{module_name}"
                for alias in node.names:
                    statement = f"from {module_with_level or '.'} import {alias.name}"
                    keyword = _match_keyword(module_name, forbidden_keywords) or _match_keyword(alias.name, forbidden_keywords)
                    if keyword:
                        violations.append(
                            {
                                "file": str(py_file),
                                "line": node.lineno,
                                "statement": statement,
                                "keyword": keyword,
                            }
                        )

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Check forbidden imports under core boundary")
    parser.add_argument("--root", default="apps/agent-host/src/core", help="Directory to scan")
    args = parser.parse_args()

    violations = scan_forbidden_imports(root=args.root)
    if violations:
        print("Core boundary check failed:")
        for item in violations:
            print(f"- {item['file']}:{item['line']} -> {item['statement']} (keyword: {item['keyword']})")
        return 1

    print("Core boundary check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
