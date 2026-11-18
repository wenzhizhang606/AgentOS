# 单机 Docker 部署与观测入口

适用范围为当前 M1 与本地 Skill/Tool 的开发、联调环境。交付覆盖 TASK-001、TASK-009、TASK-013 和 AC-013、AC-017、AC-028 的部分部署行为，状态为待统一验证。

## 观测入口

API 默认只发布在宿主机 `127.0.0.1:8000`。下面 3 个是业务监控入口。

| 方法和路径 | 可以看到什么 |
| --- | --- |
| `GET /monitor` | 给人看的页面。输入项目和凭证后，可刷新项目汇总、点击任务查看时间线。 |
| `GET /v1/projects/{project_id}/monitoring` | 项目内的任务状态、能力清单与实际调用、P95 耗时、Token、已结算费用与待核对回执。 |
| `GET /v1/tasks/{task_id}/timeline` | 一次任务中的排队和领取、上下文准备、Skill、Tool、模型准入和费用回执。 |

另有 **2 个业务诊断 API**。`GET /v1/tasks/{task_id}` 查任务和可交付结果，`GET /v1/context-snapshots/{snapshot_id}` 查来源版本、输入摘要与按策略留存的内容。普通凭证可见本人任务；显式配置项目观察权限后可以读该项目的运行元数据。观察者读其他人回答、快照正文时仍受原所有者规则约束。[监控权限与指标口径](capability-monitoring.md)

**基础设施探针**单独计数。`GET /healthz` 观察 API 是否能响应。`GET /readyz` 检查运行账户能查询所需表列和已登记的发布版本，并检查 MySQL 的全局只读开关，返回 200 或 503。二者均不请求模型。每个 worker 容器另用 `agentos worker-healthcheck` 检查本容器最近 180 秒内是否完成过一次队列轮询或任务处理。空队列的正常轮询也刷新心跳。MySQL 自身用运行账户执行 `SELECT 1`，迁移容器以退出码报告状态。

## 启动

从仓库根目录操作。以下示例使用支持 Linux 容器的 Docker Engine 或 Docker Desktop、Docker Compose V2 和 PowerShell 7。已配置本地 M1 的用户可以复用 `local/release.json` 和 `local/credentials.json`。

1. 首次部署时复制配置样例，按实际值填写。保留已有工作配置。

   ```powershell
   Copy-Item .env.example .env
   New-Item -ItemType Directory -Force local | Out-Null
   Copy-Item examples/m1/release-capabilities.json local/release.json
   ```

   在 `.env` 中分别设置 `MYSQL_ROOT_PASSWORD`、`AGENTOS_MIGRATION_PASSWORD`、`AGENTOS_MYSQL_PASSWORD` 三个独立数据库口令，以及真实 `AGENTOS_MODEL_API_KEY`。模板内的空值用于阻止误启动。若需改宿主机端口，设置 `AGENTOS_API_PORT`。在发布文件中替换模型标识、HTTPS 端点、窗口和单价。API 与 worker 需要相同的发布文件；模型密钥只交给 worker。

2. 首次创建本地调用凭证。本示例生成随机 Bearer 令牌，令牌留在当前 PowerShell 会话中，磁盘文件只保存其摘要。已有凭证文件可跳过。

   ```powershell
   $provider = [Security.Cryptography.RandomNumberGenerator]::Create()
   $bytes = [byte[]]::new(32)
   $provider.GetBytes($bytes)
   $provider.Dispose()
   $env:AGENTOS_DEMO_TOKEN = [Convert]::ToBase64String($bytes)
   $hash = [Security.Cryptography.SHA256]::Create()
   $digest = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($env:AGENTOS_DEMO_TOKEN))).Replace('-','').ToLowerInvariant()
   $hash.Dispose()
   $expires = [datetime]::UtcNow.AddHours(8).ToString("yyyy-MM-ddTHH:mm:ssZ")
   @{credentials=@(@{token_sha256=$digest; tenant_id="demo"; subject_id="reader"; project_ids=@("support"); expires_at=$expires})} |
       ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8NoBOM local/credentials.json
   ```

3. 检查配置、构建并启动。首个 build 生成 API、worker 与迁移共用的本地镜像。运行账户为容器内 UID/GID 10001，两个绑定的 JSON 文件须允许该账户读取。

   ```powershell
   docker compose config --quiet
   docker compose build migrate
   docker compose up -d
   docker compose ps -a
   docker compose logs migrate
   ```

   顺序为 MySQL 健康 → `migrate` 成功退出 → API 与 worker 启动。预期 `migrate` 显示 `Exited (0)`，其余三个服务完成探针后显示 `healthy`。失败可用 `docker compose logs mysql migrate api worker` 定位。[Compose 依赖启动语义](https://docs.docker.com/compose/how-tos/startup-order/)

4. 授予演示主体项目权限，准备资料。

   ```powershell
   docker compose exec -T api agentos grant demo reader support
   docker compose exec -T api agentos put-source demo support return-policy "退货政策" section-1 /app/examples/m1/policy.txt
   docker compose exec -T api agentos release-id
   ```

   打开本机 `/monitor`，输入项目 `support` 和第二步生成的令牌。已有任务可直接查看；首次提交可使用 [M1 请求示例](m1-managed-support.md)，将其中的 `(agentos release-id)` 改为 `(docker compose exec -T api agentos release-id)`。查看特定服务日志可运行 `docker compose logs --tail=100 worker`。

## 持久化和升级

- MySQL 使用命名卷保存数据，仅在 Compose 内部数据库网络上提供服务。普通 `docker compose down` 保留数据卷。数据库初始化时建立 `agentos_migrator` 迁移账户和 `agentos_app` 运行账户；运行账户只授予库内 SELECT、INSERT、UPDATE。
- 迁移程序按数字顺序读取 SQL，持有数据库级命名锁，文件全部执行成功后记入 `ag_schema_migration`。重启会跳过成功的文件；已有文件的摘要或顺序不匹配会停止升级。MySQL DDL 可能已部分提交，修复问题后重试当前可重入的脚本。当前 001、002 支持为此前手工初始化的 M1 数据库建立迁移台账。
- 已有卷不会重新执行 MySQL 镜像初始化脚本。若导入历史卷，先备份并确认库名为 `agentos`、两个数据库账户及权限已由管理员配置，`.env` 与库内实际口令一致。仅更改 `.env` 不会轮换库内用户口令。[MySQL 官方镜像初始化行为](https://hub.docker.com/_/mysql)
- 单机升级会重建容器。先确认旧发布下的任务已完成，仍有待处理任务时应保留绑定旧代码和旧发布配置的 worker。修改绑定的 JSON 配置后重建受影响容器，确保重新加载新文件。

## 统一验收

部署迁移与探针的契约测试位于 [迁移测试](../../tests/contracts/test_deploy_migrations.py) 和 [健康检查测试](../../tests/contracts/test_health_probes.py)。已启动的测试栈可按以下命令进行只读冒烟验收，检查服务状态、迁移退出码、HTTP 探针和面板入口。

```powershell
$env:AGENTOS_TEST_COMPOSE="1"
python -m pytest tests/integration/test_compose_deployment.py
```

本轮没有执行镜像构建、数据库迁移或测试。该 Compose 面向单机联调。生产验收仍需生产凭证管理、入口 TLS、备份恢复演练、跨节点高可用及集中告警；业务监控页与容器健康检查各自覆盖不同范围。
