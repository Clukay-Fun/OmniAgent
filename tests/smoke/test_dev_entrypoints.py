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


def _run_and_capture_command(module: ModuleType, monkeypatch) -> list[str]:
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], cwd: str, check: bool):
        captured["command"] = command
        assert isinstance(cwd, str)
        assert check is False

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.sys, "argv", ["run_dev.py", "up", "--build"])
    monkeypatch.setattr(module.sys, "executable", "/usr/bin/python3")

    rc = module.main()

    assert rc == 0
    return captured["command"]


def test_root_run_dev_targets_agent_host_shim(monkeypatch) -> None:
    module = _load_module(REPO_ROOT / "run_dev.py", "root_run_dev")

    command = _run_and_capture_command(module, monkeypatch)

    expected_target = REPO_ROOT / "apps" / "agent-host" / "run_dev.py"
    assert command[1] == str(expected_target)


def test_agent_host_run_dev_shim_targets_feishu_agent(monkeypatch) -> None:
    shim_path = REPO_ROOT / "apps" / "agent-host" / "run_dev.py"
    assert shim_path.exists()

    module = _load_module(shim_path, "agent_host_run_dev")
    command = _run_and_capture_command(module, monkeypatch)

    expected_target = REPO_ROOT / "agent" / "feishu-agent" / "run_dev.py"
    assert command[1] == str(expected_target)


def test_agent_host_run_server_shim_targets_feishu_agent(monkeypatch) -> None:
    shim_path = REPO_ROOT / "apps" / "agent-host" / "run_server.py"
    assert shim_path.exists()

    module = _load_module(shim_path, "agent_host_run_server")
    command = _run_and_capture_command(module, monkeypatch)

    expected_target = REPO_ROOT / "agent" / "feishu-agent" / "run_server.py"
    assert command[1] == str(expected_target)
