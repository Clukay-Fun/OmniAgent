#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

pytest "${REPO_ROOT}/agent/feishu-agent/tests" \
  --cov="${REPO_ROOT}/agent/feishu-agent/src/core" \
  --cov-report=term-missing \
  --cov-fail-under=80
