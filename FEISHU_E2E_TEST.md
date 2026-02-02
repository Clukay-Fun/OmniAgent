# 飞书机器人端到端测试指南与结果

本文档包含测试步骤与本次执行结果。通过的用例在“状态”列标注 `√`，未通过标注 `×`，未执行留空。

## 一、环境准备

### 1.1 启动所有服务

```bash
# 启动核心服务
docker compose up -d

# 确认服务状态
docker compose ps
```

本次检查结果（2026-02-02）：

- feishu-agent: Up (healthy)
- mcp-feishu-server: Up (healthy)
- prometheus/grafana: Up

### 1.2 检查服务健康

```bash
# Agent 健康检查（容器内执行）
docker compose exec -T feishu-agent python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8080/health').read().decode())"

# MCP 健康检查
curl http://localhost:8081/health
```

本次检查结果：

- feishu-agent `/health`：`{"status":"ok"}`
- mcp-feishu-server `/health`：`{"status":"ok"}`

### 1.3 确认配置

```bash
dir agent\feishu-agent\workspace
```

预期文件：

- SOUL.md
- IDENTITY.md
- MEMORY.md
- users/ (目录)

## 二、测试用例清单

### 2.1 基础功能测试

| 序号 | 测试场景 | 发送内容 | 预期结果 | 状态 |
|------|----------|----------|----------|------|
| T01 | 问候 | "你好" | 返回问候 + 功能介绍 | × |
| T02 | 帮助 | "你能做什么" | 返回功能列表 |  |
| T03 | 敏感拒答 | "这官司能赢吗" | 拒答 + 引导 |  |

### 2.2 QuerySkill 测试

| 序号 | 测试场景 | 发送内容 | 预期结果 | 状态 |
|------|----------|----------|----------|------|
| T04 | 今日查询 | "今天有什么庭" | 返回今日案件或"无" |  |
| T05 | 指定日期 | "2026年1月28日有什么庭" | 返回该日案件 |  |
| T06 | 相对日期 | "明天有什么庭" | 正确解析日期 |  |
| T07 | 案件查询 | "查一下张三的案件" | 返回相关案件 | × |
| T08 | 无结果 | "查一下不存在的案件" | 友好提示无结果 |  |

### 2.3 SummarySkill 测试

| 序号 | 测试场景 | 发送内容 | 预期结果 | 状态 |
|------|----------|----------|----------|------|
| T09 | 链式调用 | "帮我总结今天的案子" | Query→Summary 链式执行 |  |
| T10 | 无上下文 | "总结一下" (无前置查询) | 提示"请先查询案件" |  |
| T11 | 有上下文 | 先查询，再说"总结一下" | 总结上次查询结果 |  |

### 2.4 ReminderSkill 测试

| 序号 | 测试场景 | 发送内容 | 预期结果 | 状态 |
|------|----------|----------|----------|------|
| T12 | 创建提醒 | "提醒我明天准备材料" | 创建成功，显示时间 |  |
| T13 | 缺省时间 | "提醒我开庭" | 默认今天18:00 + 提示 |  |
| T14 | 高优先级 | "紧急提醒我明天开庭" | priority=high |  |
| T15 | 查看提醒 | "我有哪些提醒" | 返回提醒列表 |  |
| T16 | 完成提醒 | "完成第1个提醒" | 状态改为 done |  |
| T17 | 删除提醒 | "删除第1个提醒" | 提醒被删除 |  |
| T18 | 定时推送 | 创建1分钟后的提醒 | 到期自动推送 |  |

### 2.5 Memory 测试

| 序号 | 测试场景 | 发送内容 | 预期结果 | 状态 |
|------|----------|----------|----------|------|
| T19 | 手动记忆 | "记住我喜欢简洁回复" | 写入用户记忆 |  |
| T20 | 记忆生效 | 后续对话 | 回复风格变简洁 |  |
| T21 | 向量检索 | 问相关偏好问题 | 能召回之前记忆 |  |

### 2.6 Soul 测试

| 序号 | 测试场景 | 操作 | 预期结果 | 状态 |
|------|----------|------|----------|------|
| T22 | 人格一致 | 多次对话 | 风格符合 SOUL.md 定义 |  |
| T23 | 热更新 | 修改 SOUL.md，等60s | 对话风格变化 |  |

## 三、已执行测试记录

### T01（问候）

执行方式（容器内模拟 webhook）：

```bash
docker compose exec -T feishu-agent python -c "import json,urllib.request;payload={'header':{'event_id':'test-hello','event_type':'im.message.receive_v1'},'event':{'message':{'message_id':'test-hello','message_type':'text','chat_id':'test-chat','chat_type':'p2p','content':json.dumps({'text':'你好'})},'sender':{'sender_id':{'user_id':'test_user'}}}};req=urllib.request.Request('http://localhost:8080/feishu/webhook',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'});print(urllib.request.urlopen(req).read().decode())"
```

结果：失败。日志显示意图回退到 `chitchat`，但 `chitchat` 未注册为技能名，导致路由失败。

相关日志（截取关键行）：

```
Intent parsed ... query="你好" intent={"skills":[{"name":"chitchat",...}],"method":"fallback"}
Skill not found: chitchat
```

### T07（案件查询）

执行方式（容器内模拟 webhook）：

```bash
docker compose exec -T feishu-agent python -c "import json,urllib.request;payload={'header':{'event_id':'test-query','event_type':'im.message.receive_v1'},'event':{'message':{'message_id':'test-query','message_type':'text','chat_id':'test-chat','chat_type':'p2p','content':json.dumps({'text':'查一下张三的案件'})},'sender':{'sender_id':{'user_id':'test_user'}}}};req=urllib.request.Request('http://localhost:8080/feishu/webhook',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'});print(urllib.request.urlopen(req).read().decode())"
```

结果：失败。日志仍回退到 `chitchat`，路由失败。

相关日志（截取关键行）：

```
Intent parsed ... query="查一下张三的案件" intent={"skills":[{"name":"chitchat",...}],"method":"fallback"}
Skill not found: chitchat
```

## 四、问题记录

1. 意图回退技能名 `chitchat` 与已注册的 `ChitchatSkill` 不一致，导致路由失败。
2. 日志显示 `LLMClient` 缺少 `chat_json` 方法（LLM 兜底分类时报错），虽非核心阻塞，但会影响意图解析稳定性。

## 五、结论

- 当前仅完成环境健康检查。
- 核心功能用例（T01/T07）未通过，需先修复路由/意图配置问题后再继续执行全量测试。
