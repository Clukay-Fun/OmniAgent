#!/usr/bin/env bash
set -euo pipefail

docker compose -f docker-compose.yml -f monitoring/docker-compose.monitoring.yml up -d prometheus grafana
