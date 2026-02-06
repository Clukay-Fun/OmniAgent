# OmniAgent 日志与监控指南

## 1. 日志查看

### 1.1 实时查看日志

```bash
# 查看 Feishu Agent 日志
docker logs -f omniagent-feishu-agent

# 查看 MCP Server 日志
docker logs -f omniagent-mcp-feishu

# 查看最近 100 行
docker logs --tail 100 omniagent-feishu-agent
```

### 1.2 日志格式

日志使用 **JSON 结构化格式**，便于采集和分析：

```json
{
  "timestamp": "2026-01-29 18:00:00,000",
  "level": "INFO",
  "logger": "src.core.orchestrator",
  "message": "Intent parsed",
  "request_id": "abc123xyz",
  "user_id": "user_xxx",
  "query": "查一下本周的庭",
  "intent": {"skills": [...], "is_chain": false}
}
```

### 1.3 日志轮转

日志自动轮转配置：
- `feishu-agent`: 最大 100MB × 5 个文件
- `mcp-feishu-server`: 最大 50MB × 3 个文件

日志存储位置：
```bash
docker inspect omniagent-feishu-agent --format='{{.LogPath}}'
```

---

## 2. 健康检查

### 2.1 检查服务状态

```bash
# 查看容器健康状态
docker ps

# 手动检查健康端点
curl http://localhost:8080/health
curl http://localhost:8081/health
```

### 2.2 健康检查配置

| 服务 | 端点 | 间隔 | 超时 |
|------|------|------|------|
| feishu-agent | `/health` | 30s | 10s |
| mcp-feishu-server | `/health` | 30s | 10s |

---

## 3. Prometheus 指标

### 3.1 可用指标

访问 `http://localhost:8080/metrics` 查看所有指标：

| 指标名 | 类型 | 说明 |
|--------|------|------|
| `feishu_agent_requests_total` | Counter | 请求总数 |
| `feishu_agent_skill_executions_total` | Counter | 技能执行次数 |
| `feishu_agent_skill_execution_duration_seconds` | Histogram | 技能执行延迟 |
| `feishu_agent_intent_parse_duration_seconds` | Histogram | 意图解析延迟 |
| `feishu_agent_llm_calls_total` | Counter | LLM 调用次数 |
| `feishu_agent_active_sessions` | Gauge | 活跃会话数 |

### 3.2 启用 Prometheus

1. 取消 `docker-compose.yml` 中 prometheus 服务的注释
2. 重新启动：
   ```bash
   docker-compose up -d prometheus
   ```
3. 访问 `http://localhost:9090` 查看 Prometheus UI

### 3.3 常用 PromQL 查询

```promql
# 每分钟请求数
rate(feishu_agent_requests_total[1m])

# 技能执行 P95 延迟
histogram_quantile(0.95, rate(feishu_agent_skill_execution_duration_seconds_bucket[5m]))

# 错误率
rate(feishu_agent_skill_executions_total{status="error"}[5m])
/ rate(feishu_agent_skill_executions_total[5m])

# 各技能执行次数
sum by (skill_name) (feishu_agent_skill_executions_total)
```

---

## 4. 常见问题排查

### 4.1 日志关键词搜索

```bash
# 搜索错误日志
docker logs omniagent-feishu-agent 2>&1 | grep -i error

# 搜索特定用户
docker logs omniagent-feishu-agent 2>&1 | grep "user_xxx"

# 搜索特定请求
docker logs omniagent-feishu-agent 2>&1 | grep "request_id"
```

### 4.2 性能问题

1. 检查技能延迟：
   ```bash
   curl -s http://localhost:8080/metrics | grep skill_execution_duration
   ```

2. 检查活跃会话：
   ```bash
   curl -s http://localhost:8080/metrics | grep active_sessions
   ```

### 4.3 服务不健康

1. 检查容器日志
2. 检查依赖服务（MCP Server）
3. 检查资源使用：
   ```bash
   docker stats omniagent-feishu-agent
   ```

---

## 5. 告警配置（可选）

如需配置告警，创建 `monitoring/alert_rules.yml`：

```yaml
groups:
  - name: omniagent
    rules:
      - alert: HighErrorRate
        expr: rate(feishu_agent_skill_executions_total{status="error"}[5m]) > 0.1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "高错误率告警"

      - alert: SlowSkillExecution
        expr: histogram_quantile(0.95, rate(feishu_agent_skill_execution_duration_seconds_bucket[5m])) > 5
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "技能执行延迟过高"
```

---

## 6. 日志采集到外部系统

### 6.1 采集到 ELK

修改 docker-compose.yml 的 logging driver：

```yaml
logging:
  driver: syslog
  options:
    syslog-address: "tcp://logstash:5000"
```

### 6.2 采集到 Loki

使用 Loki Docker 插件：

```bash
docker plugin install grafana/loki-docker-driver:latest --alias loki --grant-all-permissions
```

然后修改 logging driver：

```yaml
logging:
  driver: loki
  options:
    loki-url: "http://loki:3100/loki/api/v1/push"
```
