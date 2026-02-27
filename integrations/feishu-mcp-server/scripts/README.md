# scripts

本目录存放 MCP Feishu Server 的运维/校验/一次性修复脚本。

---

## 脚本列表

| 脚本 | 用途 | 典型场景 |
|------|------|----------|
| `automation_gray_check.py` | 自动化灰度健康检查 | 灰度结束后汇总 run_logs/dead_letters、状态字段分布等 |
| `repair_placeholder_task_desc.py` | 历史占位符修复辅助脚本 | 修复遗留数据中的占位符描述或兼容字段 |

---

## 使用方式

建议在服务目录（`integrations/feishu-mcp-server/`）下执行：

```bash
python scripts/automation_gray_check.py --help
```

常用示例：

```bash
# 最近 24 小时概览
python scripts/automation_gray_check.py

# 严格模式：发现异常退出码非 0（适合 CI）
python scripts/automation_gray_check.py --strict

# JSON 输出
python scripts/automation_gray_check.py --json

# 零 API 模式（只读本地 run_logs/dead_letters）
python scripts/automation_gray_check.py --no-api --strict
```

---

## 注意事项

- 灰度脚本读取的运行数据默认位于 `automation_data/`（具体文件名以服务配置为准）。
- 修复脚本属于一次性操作，执行前建议先备份运行数据或在灰度环境验证。
