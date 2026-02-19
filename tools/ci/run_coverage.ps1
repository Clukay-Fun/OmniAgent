$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path

# 迁移期过渡阈值，后续提升
python -m pytest (Join-Path $RepoRoot "tests") `
  --cov="src.core.orchestrator" `
  --cov="src.api.webhook" `
  --cov="src.core.response" `
  --cov="src.adapters.channels.feishu.formatter" `
  --cov="core.response" `
  --cov-report=term-missing `
  --cov-fail-under=35
