# 场景与回归（Feishu Agent）

本目录用于**人类阅读与评审**：场景定义用于校验“对话能力/守卫/规则”的可回归性。

单一事实来源：
- 可执行场景数据：`docs/scenarios/scenarios.yaml`

---

## 覆盖矩阵（概览）

| 模块 | 正常 | 边界 | 异常 |
| --- | --- | --- | --- |
| 查询（只读） | ✅ | ✅ | ✅ |
| 人员匹配 | ✅ | ✅ | ✅ |
| 日期解析 | ✅ | ✅ | ✅ |
| 组合条件 | ✅ | ✅ | ✅ |
| 创建 | ✅ | ✅ | ✅ |
| 更新 | ✅ | ✅ | ✅ |
| 删除 | ✅ | ✅ | ✅ |
| 提醒 | ✅ | ✅ | ✅ |
| 表名识别 | ✅ | ✅ | ✅ |
| 分页 | ✅ | ✅ | - |
| 上下文 | ✅ | ✅ | - |
| 多表联动 | ✅ | ✅ | ✅ |
| 安全 | - | - | ✅ |

说明：场景条目数量随版本演进，实际以 `docs/scenarios/scenarios.yaml` 为准。

---

## 本地校验命令

仓库根目录执行：

```bash
python tools/ci/validate_scenarios.py
```

建议在做对话/路由/守卫类改动前后，用 `scenario_id` 做针对性回归。

---

## 场景新增/修改流程

1) 先更新 `docs/scenarios/scenarios.yaml`
2) 运行 `python tools/ci/validate_scenarios.py` 校验格式、重复 id、字段约束
3) 若涉及真实飞书对话回归（仅 `live_test.enabled=true` 的场景）：

```bash
python tools/dev/run_scenario_dialogue_tests.py --show-pass
```

---

## 命名约定

- 多表联动场景：示例 `M001` ~ `M005`（具体范围以 YAML 为准）
- 统一使用稳定的 `scenario_id`，避免用标题做程序依赖
