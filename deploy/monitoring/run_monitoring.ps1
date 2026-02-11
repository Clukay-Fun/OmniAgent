$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path

docker compose -f (Join-Path $RepoRoot "deploy\docker\compose.yml") --profile monitoring up -d prometheus grafana
