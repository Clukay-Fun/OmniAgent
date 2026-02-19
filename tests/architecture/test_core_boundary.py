from pathlib import Path

from tools.ci.check_core_boundary import scan_forbidden_imports


def test_core_has_no_forbidden_imports() -> None:
    violations = scan_forbidden_imports(
        root=Path("apps/agent-host/src/core"),
        forbidden_keywords=("lark_oapi", "feishu", "webhook"),
    )

    assert violations == []


def test_detects_import_with_feishu_keyword(tmp_path: Path) -> None:
    file_path = tmp_path / "demo.py"
    file_path.write_text("import feishu_sdk\n", encoding="utf-8")

    violations = scan_forbidden_imports(root=tmp_path, forbidden_keywords=("feishu",))

    assert violations == [
        {
            "file": str(file_path),
            "line": 1,
            "statement": "import feishu_sdk",
            "keyword": "feishu",
        }
    ]


def test_detects_from_import_with_webhook_keyword(tmp_path: Path) -> None:
    file_path = tmp_path / "demo.py"
    file_path.write_text("from some.module import webhook_handler\n", encoding="utf-8")

    violations = scan_forbidden_imports(root=tmp_path, forbidden_keywords=("webhook",))

    assert violations == [
        {
            "file": str(file_path),
            "line": 1,
            "statement": "from some.module import webhook_handler",
            "keyword": "webhook",
        }
    ]


def test_reports_syntax_error_as_violation(tmp_path: Path) -> None:
    file_path = tmp_path / "broken.py"
    file_path.write_text("def broken(:\n    pass\n", encoding="utf-8")

    violations = scan_forbidden_imports(root=tmp_path)

    assert len(violations) == 1
    assert violations[0]["file"] == str(file_path)
    assert violations[0]["line"] == 1
    statement = violations[0]["statement"]
    assert isinstance(statement, str)
    assert statement.startswith("syntax error:")
    assert violations[0]["keyword"] == "syntax-error"


def test_relative_import_statement_keeps_level(tmp_path: Path) -> None:
    file_path = tmp_path / "demo.py"
    file_path.write_text("from ..webhook_module import handler\n", encoding="utf-8")

    violations = scan_forbidden_imports(root=tmp_path, forbidden_keywords=("webhook",))

    assert violations == [
        {
            "file": str(file_path),
            "line": 1,
            "statement": "from ..webhook_module import handler",
            "keyword": "webhook",
        }
    ]
