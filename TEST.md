# OmniAgent 测试用例

> 最后更新：2026-02-06  
---  
## 📋 测试检查清单  
  
- [ ] 服务成功启动（MCP Server + Feishu Agent）  
- [ ] 所有技能已注册  
- [ ] Webhook 验签正常  
- [ ] 消息回复正常  
- [ ] "我的案件"查询正确  
- [ ] 表格别名识别正确  
- [ ] 错误提示友好清晰  
  
---  
  
## 🧪 功能测试用例  
  
### 测试 1：基础查询  
  
**目的**：验证 QuerySkill 核心功能  
  
**操作**：  
1. 在飞书中发送：`查询案件`  
2. 观察是否返回案件列表  
  
**预期结果**：  
- ✅ 返回案件记录  
- ✅ 包含案号、委托人、案由等字段  
- ✅ 包含记录链接  

#### 测试结果：
正常输出

---  
  
### 测试 2："我的案件"查询  
  
**目的**：验证人员字段搜索（使用 open_id）  
  
**前提**：  
- 当前用户在"案件项目总库"的"主办律师"字段中  
  
**操作**：  
1. 发送：`我的案件`  
2. 观察结果  
  
**预期结果**：  
- ✅ 只返回当前用户负责的案件  
- ✅ 日志显示 `Query 'my cases' for user: xxx (open_id: xxx)`  

测试结果：失败
{"timestamp": "2026-02-07 20:38:34,104", "level": "ERROR", "logger": "src.core.skills.query", "message": "QuerySkill execution error: [MCP_TOOL_ERROR] MCP 工 具 'feishu.v1.bitable.search_person' 执行失败: [1254018] InvalidFilter", "request_id": "883d6c95-5fe", "user_id": "ou_da3e59d1c3b3a22ea4f6585d1dbf1d47", "taskName": "Task-9"}

---  
  
### 测试 3：表格别名识别  
  
**目的**：验证 table_aliases 配置  
  
**操作**：  
1. 发送：`查询项目`  
2. 观察是否识别为"案件项目总库"  
  
**预期结果**：  
- ✅ 别名正确匹配  
- ✅ 日志显示 `Matched alias: '项目' -> '案件项目总库'`  
测试结：正常显示所有案件
---  
  
### 测试 4：日期范围查询  
  
**目的**：验证 search_date_range 工具  
  
**操作**：  
1. 发送：`今天开庭的案件`  
2. 发送：`本周的庭`  
  
**预期结果**：  
- ✅ 正确解析日期范围  
- ✅ 返回对应日期的案件  
测试结果：查询失败
{"timestamp": "2026-02-07 20:41:13,453", "level": "ERROR", "logger": "src.core.skills.query", "message": "QuerySkill execution error: [MCP_TOOL_ERROR] MCP 工 具 'feishu.v1.bitable.search_date_range' 执行失败: [1254018] InvalidFilter", "request_id": "9247c6dc-2be", "user_id": "ou_da3e59d1c3b3a22ea4f6585d1dbf1d47", "taskName": "Task-15"}

---  
  
### 测试 5：关键词查询  
  
**目的**：验证 search_keyword 工具  
  
**操作**：  
1. 发送：`找一下张三的案件`  
2. 发送：`查询合同纠纷`  
  
**预期结果**：  
- ✅ 返回包含关键词的记录  
- ✅ 搜索多个文本字段  
测试结果：只能找到主办律师的案件。输入委托人名字、项目ID等其他名字不能正确找到案件需要按照前置词来查找不够灵活。
---  
  
### 测试 5.1：查询场景矩阵（重点）  
  
**目的**：一次性覆盖 QuerySkill 的主要场景，避免逐条修规则  
  
| 用户输入示例                    | 预期场景    | 预期工具                                           | 关键说明                       |     |
| ------------------------- | ------- | ---------------------------------------------- | -------------------------- | --- |
| `查所有案件`                   | 全量查询    | `feishu.v1.bitable.search`                     | 默认忽略 `BITABLE_VIEW_ID`，查全表 |     |
| `查所有案件 按视图`               | 视图内全量查询 | `feishu.v1.bitable.search`                     | 保留视图过滤                     |     |
| `我的案件`                    | 人员精确查询  | `feishu.v1.bitable.search_person`              | 使用 `open_id` 匹配 `主办律师`     |     |
| `查张三的案件`                  | 指定人员查询  | `feishu.v1.bitable.search_keyword`（可扩展 person） | 没有 open_id 时按关键词兜底         |     |
| `查案号 (2024)沪01民终123号`     | 精确字段查询  | `feishu.v1.bitable.search_exact`               | 字段：`案号`                    |     |
| `查项目ID JFTD-20260204-001` | 精确字段查询  | `feishu.v1.bitable.search_exact`               | 字段：`项目ID`，失败自动降级           |     |
| `今天开庭的案件`                 | 日期范围查询  | `feishu.v1.bitable.search_date_range`          | 自动解析 `date_from/date_to`   |     |
| `张三在中院的案件`                | 组合查询    | `feishu.v1.bitable.search_advanced`（后续增强）      | 多条件 AND/OR                 |     |
  
**预期日志关键词**：  
- `Query scenario: all_cases / my_cases / exact_match / keyword / full_scan`  
- `Query tool selected: ...`  
测试结果：查案号成功、
---  
  
### 测试 6：闲聊与边界  
  
**目的**：验证 ChitchatSkill  
  
**操作**：  
1. 发送：`你好`  
2. 发送：`这个案子能赢吗？`  
3. 发送：`你能做什么？`  
  
**预期结果**：  
- ✅ 友好回应问候  
- ✅ 拒绝预测案件结果  
- ✅ 展示功能列表  
测试结果：正常回复
---  
  
### 测试 7：提醒功能  
  
**目的**：验证 ReminderSkill  
  
**操作**：  
1. 发送：`提醒我明天开庭`  
2. 发送：`我的提醒`  
3. 发送：`删除提醒 1`  
  
**预期结果**：  
- ✅ 创建提醒成功  
- ✅ 列出所有提醒  
- ✅ 删除提醒成功  
测试结果：失败
{"timestamp": "2026-02-07 20:47:10,523", "level": "ERROR", "logger": "src.core.skills.reminder", "message": "Failed to save reminder: password authentication failed for user \"omniagent\"", "request_id": "eb785b83-e11", "user_id": "ou_da3e59d1c3b3a22ea4f6585d1dbf1d47", "taskName": "Task-42"}

---  
  
### 测试 8：错误处理  
  
**目的**：验证错误提示  
  
**操作**：  
1. 发送：`查询不存在的表格`  
2. 发送一段无意义的长文本  
  
**预期结果**：  
- ✅ 显示友好的错误消息  
- ✅ 提供建议或引导  
测试结果：对我瞎打的无意义文案进行回答。不行！
以下是对话：
水电费水电费收到防守打法
您能提供更多的上下文信息吗？比如您想询问关于水电费的具体问题，或者您想了解某种防守打法。请详细说明。
想了解某种防守打法
了解某种防守打法时，可以考虑以下几个方面：

1. **基础原则**：任何防守打法都基于一些基本原则，如保持队形、限制对方进攻空间、快速回防等。

2. **具体战术**：不同的防守战术包括人盯人、区域防守、联防等。每种战术都有其特定的布置和执行方式。

3. **球员角色**：在防守中，每个球员都有其特定的角色，如中锋、前锋、后卫等，他们的职责包括盯人、抢断、拦截等。

4. **适应性**：好的防守打法需要根据对手的特点进行调整。比如，面对速度快、技术好的对手，可能需要更多的身体对抗和紧逼防守。

5. **训练与实践**：理论知识需要通过实际训练和比赛来巩固和提升。了解某种防守打法后，重要的是将其融入到球队的日常训练和比赛中。

6. **观看比赛**：通过观看高水平比赛，可以直观地学习到各种防守打法的实际应用。

希望这些信息能帮助你更好地了解和掌握某种防守打法。如果你有更具体的问题或需要进一步的指导，欢迎继续提问。
---  
  
## 🔧 API 测试命令  
  
### MCP Server 测试  
  
```bash  
# 健康检查  
curl http://localhost:8081/health  
  
# 工具列表  
curl http://localhost:8081/mcp/tools  
  
# 表格字段  
curl http://localhost:8081/bitable/fields  
  
# 列出表格  
curl -X POST http://localhost:8081/mcp/tools/feishu.v1.bitable.list_tables \  
-H "Content-Type: application/json" \  
-d "{\"params\": {}}"  
  
# 关键词搜索  
curl -X POST http://localhost:8081/mcp/tools/feishu.v1.bitable.search_keyword \  
-H "Content-Type: application/json" \  
-d "{\"params\": {\"keyword\": \"张三\"}}"  
  
# 人员字段搜索  
curl -X POST http://localhost:8081/mcp/tools/feishu.v1.bitable.search_person \  
-H "Content-Type: application/json" \  
-d "{\"params\": {\"field\": \"主办律师\", \"open_id\": \"ou_xxx\"}}"  
  
# 日期范围搜索  
curl -X POST http://localhost:8081/mcp/tools/feishu.v1.bitable.search_date_range \  
-H "Content-Type: application/json" \  
-d "{\"params\": {\"field\": \"开庭日\", \"date_from\": \"2026-02-06\", \"date_to\": \"2026-02-06\"}}"  
```  
  
### Feishu Agent 测试  
  
```bash  
# 健康检查  
curl http://localhost:8080/health  
  
# 指标  
curl http://localhost:8080/metrics  
  
# 模拟 Webhook（需要正确的验签）  
# 建议使用飞书官方测试工具  
```  

测试结果
```
(.venv) E:\.Program\OmniAgent>curl http://localhost:8081/health  
{"status":"ok"}
(.venv) E:\.Program\OmniAgent>curl http://localhost:8081/mcp/tools  
{"tools":[{"name":"feishu.v1.bitable.list_tables","description":"List Feishu bitable tables under an app token."},{"name":"feishu.v1.bitable.search","description":"搜索飞书多维表格记录，支持关键词、日期范围、字段过滤"},{"name":"feishu.v1.bitable.search_exact","description":"Search a bitable record by exact field value."},{"name":"feishu.v1.bitable.search_keyword","description":"Search bitable records by keyword across fields."},{"name":"feishu.v1.bitable.search_person","description":"Search bitable records by person field using open_id."},{"name":"feishu.v1.bitable.search_date_range","description":"Search bitable records by date range."},{"name":"feishu.v1.bitable.record.get","description":"Get a single bitable record by record_id."},{"name":"feishu.v1.bitable.record.create","description":"Create a new bitable record with specified fields."},{"name":"feishu.v1.bitable.record.update","description":"Update an existing bitable record."},{"name":"feishu.v1.bitable.record.delete","description":"Delete a bitable record by record_id."},{"name":"feishu.v1.doc.search","description":"Search Feishu documents by keyword."}]}
(.venv) E:\.Program\OmniAgent>curl http://localhost:8081/bitable/fields  
{"app_token":"OOvBbsaxtaKRwzsofiPcHNArn1d","table_id":"tblDTbRZRB89q8GJ","fields":[{"name":"项目ID","type":1005,"type_name":"未知"},{"name":"项目类型","type":3,"type_name":"单选"},{"name":"案件分类","type":3,"type_name":"单选"},{"name":"主办律师","type":11,"type_name":"人员"},{"name":"协办律师","type":11,"type_name":"人员"},{"name":"案号","type":1,"type_name":"文本"},{"name":"委托人","type":1,"type_name":"文本"},{"name":"联系人","type":1,"type_name":"文本"},{"name":"联 系方式","type":13,"type_name":"电话"},{"name":"对方当事人","type":1,"type_name":"文本"},{"name":"案由","type":3,"type_name":"单选"},{"name":"审理法院","type":1,"type_name":"文本"},{"name":"审理程序","type":4,"type_name":"多选"},{"name":"承办法官","type":1,"type_name":"文本"},{"name":"开庭日","type":5,"type_name":" 日期"},{"name":"管辖权异议截止日","type":5,"type_name":"日期"},{"name":"举证截 止日","type":5,"type_name":"日期"},{"name":"查封到期日","type":5,"type_name":" 日期"},{"name":"反诉截止日","type":5,"type_name":"日期"},{"name":"上诉截止日","type":5,"type_name":"日期"},{"name":"进展","type":1,"type_name":"文本"},{"name":"待做事项","type":1,"type_name":"文本"},{"name":"重要紧急程度","type":3,"type_name":"单选"},{"name":"案件状态","type":3,"type_name":"单选"},{"name":"关联合同","type":21,"type_name":"地理位置"},{"name":"关联开票记录","type":18,"type_name":"单向关联"},{"name":"备注","type":1,"type_name":"文本"}],"total":27}
(.venv) E:\.Program\OmniAgent>curl -X POST http://localhost:8081/mcp/tools/feishu.v1.bitable.list_tables \  -H "Content-Type: application/json" \  -d "{\"params\": {}}"
{"success":true,"data":{"tables":[{"table_id":"tblDTbRZRB89q8GJ","table_name":"案件项目总库"},{"table_id":"tblgE716loeFPMzt","table_name":"招投标台账"},{"table_id":"tbllIxXhJ20pNTPl","table_name":"关键节点表"},{"table_id":"tblbHk9OGNEr3748","table_name":"合同开票统计"},{"table_id":"tbllE63yYZIaZXLR","table_name":" 发票提交明细"},{"table_id":"tbl0jyfgOIRDZgDJ","table_name":"费用发票统计"},{"table_id":"tblrKpG2oebplqtX","table_name":"合同管理表"},{"table_id":"tblnKgT7iNOQwN7J","table_name":"工作任务表"},{"table_id":"tblau1w0KVPBRjle","table_name":" 【诉讼案件】"},{"table_id":"tbli3Q0qBa09vrMH","table_name":"签约项目台账"},{"table_id":"tblfUjMJwUat41zz","table_name":"投标台账"}],"total":11},"error":null}curl: (3) URL rejected: Bad hostname
curl: (3) URL rejected: Bad hostname

(.venv) E:\.Program\OmniAgent>curl -X POST http://localhost:8081/mcp/tools/feishu.v1.bitable.search_keyword \  -H "Content-Type: application/json" \  -d "{\"params\": {\"keyword\": \"张三\"}}"
{"success":true,"data":{"records":[],"total":0,"has_more":false,"page_token":"","schema":[{"name":"上诉截止日","type":5,"type_name":"日期"},{"name":"主办律师","type":11,"type_name":"人员"},{"name":"举证截止日","type":5,"type_name":"日期"},{"name":"关联合同","type":21,"type_name":"地理位置"},{"name":"关联开票记录","type":18,"type_name":"单向关联"},{"name":"协办律师","type":11,"type_name":"人员"},{"name":"反诉截止日","type":5,"type_name":"日期"},{"name":"备注","type":1,"type_name":"文本"},{"name":"委托人","type":1,"type_name":"文本"},{"name":"审理法院","type":1,"type_name":"文本"},{"name":"审理程序","type":4,"type_name":"多选"},{"name":"对方当事人","type":1,"type_name":"文本"},{"name":"开庭日","type":5,"type_name":"日期"},{"name":"待做事项","type":1,"type_name":"文本"},{"name":"承 办法官","type":1,"type_name":"文本"},{"name":"查封到期日","type":5,"type_name":"日期"},{"name":"案件分类","type":3,"type_name":"单选"},{"name":"案件状态","type":3,"type_name":"单选"},{"name":"案号","type":1,"type_name":"文本"},{"name":" 案由","type":3,"type_name":"单选"},{"name":"管辖权异议截止日","type":5,"type_name":"日期"},{"name":"联系人","type":1,"type_name":"文本"},{"name":"联系方式","type":13,"type_name":"电话"},{"name":"进展","type":1,"type_name":"文本"},{"name":"重要紧急程度","type":3,"type_name":"单选"},{"name":"项目ID","type":1005,"type_name":"未知"},{"name":"项目类型","type":3,"type_name":"单选"}]},"error":null}curl: (3) URL rejected: Bad hostname
curl: (3) URL rejected: Bad hostname

(.venv) E:\.Program\OmniAgent>curl -X POST http://localhost:8081/mcp/tools/feishu.v1.bitable.search_person \  -H "Content-Type: application/json" \  -d "{\"params\": {\"field\": \"主办律师\", \"open_id\": \"ou_xxx\"}}"
{"success":false,"data":null,"error":{"code":"MCP_001","message":"[1254018] InvalidFilter","detail":{"code":1254018,"msg":"InvalidFilter","error":{"message":"Invalid request parameter: ''. Correct format : UserCondition field '主办律师' value '[ou_xxx]',id is invalid. Please check and modify accordingly.","log_id":"20260207205010ACF56EC4AA70B6F48FE5","troubleshooter":"排查建议查看(Troubleshooting suggestions): https://open.feishu.cn/search?from=openapi&log_id=20260207205010ACF56EC4AA70B6F48FE5&code=1254018&method_id=7301628054955556866"}}}}curl: (3) URL rejected: Bad hostname
curl: (3) URL rejected: Bad hostname

(.venv) E:\.Program\OmniAgent>curl -X POST http://localhost:8081/mcp/tools/feishu.v1.bitable.search_date_range \  -H "Content-Type: application/json" \  -d "{\"params\": {\"field\": \"开庭日\", \"date_from\": \"2026-02-07\", \"date_to\":
 \"2026-02-07\"}}"
{"success":false,"data":null,"error":{"code":"MCP_001","message":"[1254018] InvalidFilter","detail":{"code":1254018,"msg":"InvalidFilter","error":{"message":"Invalid request parameter: ''. Correct format : field '开庭日' fieldType '5' not support isGreaterEqual. Please check and modify accordingly.","log_id":"20260207205104C7B416982180568CDCAB","troubleshooter":"排查建议查看(Troubleshooting suggestions): https://open.feishu.cn/search?from=openapi&log_id=20260207205104C7B416982180568CDCAB&code=1254018&method_id=7301628054955556866"}}}}curl: (3) URL rejected: Bad hostname
curl: (3) URL rejected: Bad hostname

(.venv) E:\.Program\OmniAgent>curl http://localhost:8080/health
curl: (7) Failed to connect to localhost port 8080 after 2237 ms: Could not connect to server

(.venv) E:\.Program\OmniAgent>curl http://localhost:8080/metrics  
curl: (7) Failed to connect to localhost port 8080 after 2253 ms: Could not connect to server
```

---  
  
