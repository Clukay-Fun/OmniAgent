#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

docker compose -f "${REPO_ROOT}/deploy/docker/compose.yml" --profile monitoring up -d prometheus grafana
