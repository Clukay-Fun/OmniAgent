#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# 迁移期过渡阈值，后续提升
python3 -m pytest "${REPO_ROOT}/tests" \
  --cov="src.core.orchestrator" \
  --cov="src.api.webhook" \
  --cov="src.core.response" \
  --cov="src.adapters.channels.feishu.formatter" \
  --cov="core.response" \
  --cov-report=term-missing \
  --cov-fail-under=35
