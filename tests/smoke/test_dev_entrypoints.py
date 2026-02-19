from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_root_run_dev_uses_repo_compose_files() -> None:
    module = _load_module(REPO_ROOT / "run_dev.py", "root_run_dev")

    compose_args = module._compose_base_args(REPO_ROOT)
    assert str(REPO_ROOT / "deploy" / "docker" / "compose.yml") in compose_args
    assert str(REPO_ROOT / "deploy" / "docker" / "compose.dev.yml") in compose_args


def test_root_run_dev_agent_ws_starts_ws_client(monkeypatch) -> None:
    module = _load_module(REPO_ROOT / "run_dev.py", "root_run_dev_ws")

    captured: dict[str, object] = {}

    def fake_run(command: list[str], cwd: str, check: bool):
        captured["command"] = command
        captured["cwd"] = cwd
        assert check is False

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.sys, "argv", ["run_dev.py", "agent-ws"])
    monkeypatch.setattr(module.sys, "executable", "/usr/bin/python3")

    rc = module.main()
    assert rc == 0

    assert captured["command"] == ["/usr/bin/python3", "-m", "src.api.ws_client"]
    assert captured["cwd"] == str(REPO_ROOT / "apps" / "agent-host")


def test_root_run_dev_agent_ws_watch_delegates_to_watch_runner(monkeypatch) -> None:
    module = _load_module(REPO_ROOT / "run_dev.py", "root_run_dev_ws_watch")

    captured: dict[str, object] = {}

    def fake_watch_runner(repo_root: Path) -> int:
        captured["repo_root"] = repo_root
        return 0

    monkeypatch.setattr(module, "_run_agent_ws_watch", fake_watch_runner)
    monkeypatch.setattr(module.sys, "argv", ["run_dev.py", "agent-ws-watch"])

    rc = module.main()
    assert rc == 0
    assert captured["repo_root"] == REPO_ROOT


def test_agent_host_run_dev_targets_root_entry(monkeypatch) -> None:
    module = _load_module(REPO_ROOT / "apps" / "agent-host" / "run_dev.py", "agent_host_run_dev")

    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], cwd: str, check: bool):
        captured["command"] = command
        assert isinstance(cwd, str)
        assert check is False

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.sys, "argv", ["run_dev.py", "up"])
    monkeypatch.setattr(module.sys, "executable", "/usr/bin/python3")

    rc = module.main()
    assert rc == 0
    expected_target = REPO_ROOT / "run_dev.py"
    assert captured["command"][1] == str(expected_target)


def test_mcp_run_dev_targets_root_entry(monkeypatch) -> None:
    module = _load_module(REPO_ROOT / "integrations" / "feishu-mcp-server" / "run_dev.py", "mcp_run_dev")

    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], cwd: str, check: bool):
        captured["command"] = command
        assert isinstance(cwd, str)
        assert check is False

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.sys, "argv", ["run_dev.py", "logs"])
    monkeypatch.setattr(module.sys, "executable", "/usr/bin/python3")

    rc = module.main()
    assert rc == 0
    expected_target = REPO_ROOT / "run_dev.py"
    assert captured["command"][1] == str(expected_target)


def test_agent_host_run_server_no_legacy_path_reference() -> None:
    content = (REPO_ROOT / "apps" / "agent-host" / "run_server.py").read_text(encoding="utf-8")
    assert "agent/feishu-agent" not in content
