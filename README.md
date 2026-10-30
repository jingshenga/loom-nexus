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

## 建表机制

`scripts/schema.sql` 以只读方式挂载到 nexus-db 的 `/docker-entrypoint-initdb.d/`，
由 PostgreSQL 官方镜像**仅在数据目录为空的首次启动时**自动执行（ON_ERROR_STOP=1，
单文件原子生效）。`scripts/init_db.py` 不执行任何写路径 SQL，只做只读校验：
核对 9 张核心表、3 个关键索引与默认规则种子是否就绪，缺失时打印重放指引并以
退出码 1 结束。对已存在的旧集群手工重放（语句均带守卫，可重复执行）：

```bash
docker compose exec -T nexus-db psql -U nexus -d nexus < scripts/schema.sql
```

## 命名说明

全项目自有命名（文件 / 服务 / 容器 / 网络 / 表 / 变量）均不含规范禁止的两字母子串。
仓库中仅有的例外是**上游契约**：官方 `postgres` 镜像引用与其 `POSTGRES_*` 初始化
环境变量由镜像固定，无法重命名。

## 里程碑

- **M0（已完成）**：三容器骨架、健康检查、首启自动建表
- **M1（已完成）**：协议网关长连接、来源监听、相册 3 秒去抖入库
- **M2（已完成）**：规则抽取管道 + RapidOCR 子进程（并发 1）+ 生命周期自动下架
- **M3（已完成）**：Vue 3 前台广场 + 三栏审核工作台 + 单管理员认证 + 网关大盘
- **M4（已完成）**：Outbox 防串线代理私聊 + WebSocket 实时收发 + CONFLICT_LOCKED 隔离
- **M5（已完成）**：规则工坊（CRUD / 沙盒回测 / 来源绑定 / 可插拔 AI 生成）
- **M6（已完成）**：类型 B 聚合链接消息（引用拉取合并）、联系人公开在离线、
  广场在线状态展示与签到筛选、详情页站内咨询直连聊天（含 CONFLICT_LOCKED 警示卡）
- **M7（已完成）**：类型 C 联系人简介穿透链路 —— bio 候选房间入队、
  管理端「穿透」页人工确认门禁、确认后网关进入房间将置顶帖与房主近况合并入库
- **M8（已完成）**：会话生命周期与体验补全 —— 手动「结束会话」释放网关账号、
  72 小时空闲自动回收（账号持续循环防锁死）、会话未读徽标、规则工坊历史抽样

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
