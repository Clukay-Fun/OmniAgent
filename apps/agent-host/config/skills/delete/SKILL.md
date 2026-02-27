# Skill: delete

## 描述
删除多维表格中的指定记录。

## 触发条件
用户想要删除、移除多维表格中的某条或某些记录。

## 参数
- table_name: 目标表格名称（必需）
- record_id: 要删除的记录 ID（必需）
- filter: 批量删除时的筛选条件（可选）

## 约束
- 写链路需通过 locator triplet 校验（app_token + table_id + record_id 全部必填）
- 删除操作不可逆，必须通过 PendingAction 确认流程
- 删除功能可通过配置禁用（delete_disabled 错误码）
- 批量删除遵循 OperationEntry 逐条执行策略

## 示例对话
用户：帮我删掉项目表里编号 P-0042 那条记录
