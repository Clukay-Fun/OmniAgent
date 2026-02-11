# Monitoring 启动说明

监控服务已合并到主 Compose 文件中，使用 profile 启动。

## 启动

```bash
docker compose -f deploy/docker/compose.yml --profile monitoring up -d
```

或使用封装脚本：

```bash
./deploy/monitoring/run_monitoring.sh
# PowerShell
./deploy/monitoring/run_monitoring.ps1
```

## 配置文件

- Prometheus 抓取：`deploy/monitoring/prometheus.yml`
- Grafana 仪表盘：`deploy/monitoring/grafana/`
