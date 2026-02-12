# 云部署检查清单（MCP + Agent，无 DB）

## 当前约束

- 域名已购买
- 备案进行中：暂不对外正式上线
- 本清单用于备案通过后快速执行

## 基础设施

- [ ] 安全组放通 `22/80/443`
- [ ] 域名 A 记录指向 ECS 公网 IP
- [ ] Nginx 已安装并可加载站点配置
- [ ] Certbot 可用（HTTPS 证书）

## 服务配置

- [ ] MCP `.env` 已填写飞书数据应用凭证
- [ ] Agent `.env` 已填写飞书机器人凭证与 LLM 凭证
- [ ] `POSTGRES_DSN` 为空（无 DB 运行）
- [ ] `MCP_SERVER_BASE=http://127.0.0.1:8081`
- [ ] 启动命令使用 `deploy/docker/compose.yml`（按需启用 `monitoring`/`db` profile）

## 进程管理

- [ ] `omni-mcp` systemd 服务已启用并自启
- [ ] `omni-agent` systemd 服务已启用并自启
- [ ] `journalctl -u omni-mcp` 无持续报错
- [ ] `journalctl -u omni-agent` 无持续报错

## 路由与回调

- [ ] `https://<domain>/feishu/events` -> MCP
- [ ] `https://<domain>/feishu/webhook` -> Agent
- [ ] 飞书后台 URL 验证通过

## 业务验证

- [ ] MCP 健康检查 OK
- [ ] Agent 健康检查 OK
- [ ] MCP `POST /automation/schema/refresh` 可用
- [ ] 风险演练开关默认关闭（需要时临时打开）

## 回滚准备

- [ ] 保留上一版 `.env` 与 `config.yaml` 备份
- [ ] 记录 `automation_rules.yaml` 上线版本号
