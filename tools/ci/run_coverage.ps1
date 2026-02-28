$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path

# 迁移期过渡阈值，后续提升
python -m pytest (Join-Path $RepoRoot "tests") `
  --cov="src.core.brain.orchestration.orchestrator" `
  --cov="src.api.channels.feishu.webhook_router" `
  --cov="src.core.expression.response" `
  --cov="src.adapters.channels.feishu.protocol.formatter" `
  --cov="core.expression.response" `
  --cov-report=term-missing `
  --cov-fail-under=35
