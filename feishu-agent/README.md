# Feishu Agent

飞书私聊案件助手，消费 MCP Feishu Server 提供的工具能力。

## 功能
- Webhook 接入与消息处理
- 会话上下文管理（内存）
- 工具编排与回复生成

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
uvicorn src.main:app --host 0.0.0.0 --port 8080
```

## 接口

- `POST /feishu/webhook` 飞书事件回调
- `GET /health` 健康检查

## 说明

- 仅处理私聊文本消息
- 回答默认使用卡片格式（可配置）
