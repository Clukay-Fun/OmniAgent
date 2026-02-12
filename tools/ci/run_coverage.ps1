$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path

pytest (Join-Path $RepoRoot "agent/feishu-agent/tests") `
  --cov=(Join-Path $RepoRoot "agent/feishu-agent/src/core") `
  --cov-report=term-missing `
  --cov-fail-under=80
