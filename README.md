# nexus-edge

自托管的消息抽取 / 实体状态 / 代理通信平台，面向低功耗 x86_64 NAS（Intel N100 / 16GB 内存）。

## 架构（M0，严格 3 容器）

| 容器 | 职责 | 端口 |
|---|---|---|
| nexus-core | FastAPI REST API（`/api/health`）+ 前端静态托管占位 | 宿主 `8000` → 容器 `8000` |
| nexus-bridge | asyncio 网关连接管理进程骨架 + 数据库连接检查 | 仅内网 `nexus-net` |
| nexus-db | PostgreSQL 16 官方镜像 | 仅内网 `nexus-net` |

持久化（bind mount，默认 `./data/`，可用 `NEXUS_DATA_DIR` 调整）：
`pgdata/` 数据库集群、`media/` 媒体文件、`sessions/` 网关会话文件（M1 启用）。

## 快速开始

```bash
cp .env.example .env        # 并修改 POSTGRES_PASSWORD
docker compose build        # 在 NAS 本机执行，镜像跟随构建机架构
docker compose up -d        # 首次启动自动执行 scripts/schema.sql 建表
docker compose exec nexus-core python scripts/init_db.py   # 校验表结构（退出码 0 = 就绪）
curl http://localhost:8000/api/health

```
```bash
docker compose exec -T nexus-db psql -U nexus -d nexus < scripts/schema.sql
```

## 命名说明

全项目自有命名（文件 / 服务 / 容器 / 网络 / 表 / 变量）均不含规范禁止的两字母子串。
仓库中仅有的例外是**上游契约**：官方 `postgres` 镜像引用与其 `POSTGRES_*` 初始化
环境变量由镜像固定，无法重命名。


## 使用入口

- 前台：`http://<NAS>:8800/`（广场 / 详情 / 收藏 / 私聊）
- 管理：登录后 `/admin/review`（审核）、`/admin/rules`（规则工坊）、`/admin/gateway`（大盘）
- 管理员密码在 NAS 的 `~/nexus-edge/.env`（NEXUS_ADMIN_PASSWORD）
- AI 规则生成：`.env` 填 `NEXUS_LLM_BASE_URL/_LLM_API_KEY/_LLM_MODEL`（OpenAI 兼容端点）后自动启用
- 真实网关接入：`.env` 填 `NEXUS_BRIDGE_API_ID/_API_HASH`，执行一次
  `docker compose exec -it nexus-bridge python -m runner.login <name>`

数据库结构变更（如新增函数）后，对已存在的集群重放（语句带守卫，幂等）：

```bash
docker compose exec -T nexus-db psql -U nexus -d nexus -v ON_ERROR_STOP=1 < scripts/schema.sql
```
