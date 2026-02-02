$ErrorActionPreference = "Stop"

docker compose -f docker-compose.yml -f monitoring/docker-compose.monitoring.yml up -d prometheus grafana
