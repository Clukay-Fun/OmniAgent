# MCP Feishu Server

飞书 MCP 工具层，封装多维表格与文档检索能力，供多个 Agent 复用。

## 功能
- 飞书 Tenant Token 自动获取与刷新
- MCP 工具：
  - `feishu.v1.bitable.search`
  - `feishu.v1.bitable.record.get`
  - `feishu.v1.doc.search`

## 快速开始

1) 安装依赖

```bash
pip install -r requirements.txt
```

2) 准备配置

```bash
cp config.yaml.example config.yaml
cp .env.example .env
```

3) 启动服务

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8081
```

## 接口

- `GET /health` 健康检查
- `GET /mcp/tools` 工具列表
- `POST /mcp/tools/{tool_name}` 工具调用

## 配置

配置文件：`config.yaml`
- 支持环境变量覆盖
- 多维表格字段映射见 `bitable.field_mapping`
