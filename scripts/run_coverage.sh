#!/usr/bin/env bash
set -euo pipefail

pytest agent/feishu-agent/tests \
  --cov=agent/feishu-agent/src/core \
  --cov-report=term-missing \
  --cov-fail-under=80
