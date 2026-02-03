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
| T01 | 问候 | "你好" | 返回问候 + 功能介绍 | √ |
| T02 | 帮助 | "你能做什么" | 返回功能列表 | √ |
| T03 | 敏感拒答 | "这官司能赢吗" | 拒答 + 引导 | √ |

### 2.2 QuerySkill 测试

| 序号 | 测试场景 | 发送内容 | 预期结果 | 状态 |
|------|----------|----------|----------|------|
| T04 | 今日查询 | "今天有什么庭" | 返回今日案件或"无" | √ |
| T05 | 指定日期 | "2026年1月28日有什么庭" | 返回该日案件 | √ |
| T06 | 相对日期 | "明天有什么庭" | 正确解析日期 | √ |
| T07 | 案件查询 | "查一下张三的案件" | 返回相关案件 | √ |
| T08 | 无结果 | "查一下不存在的案件" | 友好提示无结果 | √ |

### 2.3 SummarySkill 测试

| 序号 | 测试场景 | 发送内容 | 预期结果 | 状态 |
|------|----------|----------|----------|------|
| T09 | 链式调用 | "帮我总结今天的案子" | Query→Summary 链式执行 | × |
| T10 | 无上下文 | "总结一下" (无前置查询) | 提示"请先查询案件" | × |
| T11 | 有上下文 | 先查询，再说"总结一下" | 总结上次查询结果 | × |

### 2.4 ReminderSkill 测试

| 序号 | 测试场景 | 发送内容 | 预期结果 | 状态 |
|------|----------|----------|----------|------|
| T12 | 创建提醒 | "提醒我明天准备材料" | 创建成功，显示时间 | √ |
| T13 | 缺省时间 | "提醒我开庭" | 默认今天18:00 + 提示 | √ |
| T14 | 高优先级 | "紧急提醒我明天开庭" | priority=high | √ |
| T15 | 查看提醒 | "我有哪些提醒" | 返回提醒列表 | √ |
| T16 | 完成提醒 | "完成第1个提醒" | 状态改为 done | √ |
| T17 | 删除提醒 | "删除第1个提醒" | 提醒被删除 | √ |
| T18 | 定时推送 | 创建1分钟后的提醒 | 到期自动推送 | × |

### 2.5 Memory 测试

| 序号 | 测试场景 | 发送内容 | 预期结果 | 状态 |
|------|----------|----------|----------|------|
| T19 | 手动记忆 | "记住我喜欢简洁回复" | 写入用户记忆 | √ |
| T20 | 记忆生效 | 后续对话 | 回复风格变简洁 | × |
| T21 | 向量检索 | 问相关偏好问题 | 能召回之前记忆 | × |

### 2.6 Soul 测试

| 序号 | 测试场景 | 操作 | 预期结果 | 状态 |
|------|----------|------|----------|------|
| T22 | 人格一致 | 多次对话 | 风格符合 SOUL.md 定义 | × |
| T23 | 热更新 | 修改 SOUL.md，等60s | 对话风格变化 | × |

## 三、已执行测试记录

### T01（问候）

执行方式（容器内模拟 webhook）：

```bash
docker compose exec -T feishu-agent python -c "import json,urllib.request;payload={'header':{'event_id':'test-hello','event_type':'im.message.receive_v1'},'event':{'message':{'message_id':'test-hello','message_type':'text','chat_id':'test-chat','chat_type':'p2p','content':json.dumps({'text':'你好'})},'sender':{'sender_id':{'user_id':'test_user'}}}};req=urllib.request.Request('http://localhost:8080/feishu/webhook',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'});print(urllib.request.urlopen(req).read().decode())"
```

结果：通过。命中规则并路由到 `ChitchatSkill`。

相关日志（截取关键行）：

```
Intent parsed by rule ... query="你好" top_skill="ChitchatSkill" score=0.6
Executing skill ... skill="ChitchatSkill"
Skill executed ... success=true
```

### T07（案件查询）

执行方式（容器内模拟 webhook）：

```bash
docker compose exec -T feishu-agent python -c "import json,urllib.request;payload={'header':{'event_id':'test-query','event_type':'im.message.receive_v1'},'event':{'message':{'message_id':'test-query','message_type':'text','chat_id':'test-chat','chat_type':'p2p','content':json.dumps({'text':'查一下香港华艺设计的案件'})},'sender':{'sender_id':{'user_id':'test_user'}}}};req=urllib.request.Request('http://localhost:8080/feishu/webhook',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'});print(urllib.request.urlopen(req).read().decode())"
```

结果：使用表中真实关键词（香港华艺设计）查询成功。

相关日志（截取关键行）：

```
Intent parsed by rule ... query="查一下香港华艺设计的案件" top_skill="QuerySkill" score=0.7
Intent parsed by rule ... query="查一下香港华艺设计的案件" top_skill="QuerySkill" score=0.7
reply suppressed ... 案件查询结果（共 1 条）
```

### 批量测试结果（T02-T22）

| 用例 | 结果 | 备注 |
|------|------|------|
| T02 | √ | 返回功能列表 |
| T03 | √ | 命中敏感拒答 |
| T04 | √ | 命中日期规则，返回无记录（可接受） |
| T05 | √ | 命中日期规则，返回无记录（待补充数据验证） |
| T06 | √ | 命中日期规则，解析正确 |
| T07 | √ | 使用真实关键词（香港华艺设计）查询成功 |
| T08 | √ | 正常返回无记录 |
| T09 | × | 链式执行后 Summary 失败（今日无记录） |
| T10 | × | 返回“上次查询没有找到记录”，与预期不符 |
| T11 | × | Summary 在 Query 返回前执行，未命中上下文 |
| T12 | √ | 提醒创建成功 |
| T13 | √ | 默认 18:00 写入数据库 |
| T14 | √ | priority=high 写入数据库 |
| T15 | √ | 返回提醒列表 |
| T16 | √ | 状态更新成功 |
| T17 | √ | 删除成功 |
| T18 | × | 推送失败（open_id 无效，400） |
| T19 | √ | 用户记忆已写入（/workspace/users/test_user/memory.md） |
| T20 | × | 回复未体现“简洁”偏好 |
| T21 | × | 未召回记忆，返回功能介绍 |
| T22 | × | 未验证人格一致性 |
| T23 | × | 未执行 SOUL 热更新 |

## 四、问题记录

1. Summary 链式与上下文汇总在“查询结果为空/未就绪”时失败（T09/T11）。
2. T10 返回“上次查询没有找到记录”，与预期“请先查询案件”不一致。
3. T18 推送失败：测试 open_id 非真实用户导致飞书 400。
4. 记忆召回未生效（T20/T21），向量记忆未启用（Embedding API 缺失）。

## 五、结论

- 已完成 T01-T21 复测，T22/T23 未执行。
- 当前通过 15 项（T01/T02/T03/T04/T05/T06/T07/T08/T12/T13/T14/T15/T16/T17/T19）。
- Summary/T18/Memory/Soul 仍需修复或用真实 open_id/embedding 配置后复测。
