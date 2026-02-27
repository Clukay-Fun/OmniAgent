# Monitoring

本目录提供 Prometheus + Grafana 的本地/生产监控配置。
监控服务已集成到主 Compose（`deploy/docker/compose.yml`）中，通过 profile 启动。

---

## 快速开始

### 方式 A：Docker Compose（推荐）

在仓库根目录执行：

```bash
docker compose -f deploy/docker/compose.yml --profile monitoring up -d
```

停止：

```bash
docker compose -f deploy/docker/compose.yml --profile monitoring down
```

### 方式 B：脚本封装

```bash
./deploy/monitoring/run_monitoring.sh

# Windows PowerShell
./deploy/monitoring/run_monitoring.ps1
```

---

## 访问地址

- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000`
  - 默认账号：`admin`
  - 默认密码：`admin`

---

## 指标与仪表盘

Prometheus 抓取配置：
- `deploy/monitoring/prometheus.yml`

Grafana 预置内容：
- Datasource/Provisioning: `deploy/monitoring/grafana/provisioning/`
- Dashboards: `deploy/monitoring/grafana/dashboards/`

服务侧指标端点（服务启动后）：
- Agent: `http://localhost:8080/metrics`
- MCP: （如有暴露 metrics，按对应服务 README 为准）

---

## 常见问题

- Grafana 无法看到仪表盘：确认 `deploy/docker/compose.yml` 的 grafana volumes 挂载路径存在且为只读挂载。
- Prometheus 抓不到数据：先用 `curl http://localhost:8080/health` 确认服务已启动，再检查 `prometheus.yml` 中 target/端口。
