#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# 迁移期过渡阈值，后续提升
python3 -m pytest "${REPO_ROOT}/tests" \
  --cov="src.core.brain.orchestration.orchestrator" \
  --cov="src.api.channels.feishu.webhook_router" \
  --cov="src.core.expression.response" \
  --cov="src.adapters.channels.feishu.protocol.formatter" \
  --cov="core.expression.response" \
  --cov-report=term-missing \
  --cov-fail-under=35
