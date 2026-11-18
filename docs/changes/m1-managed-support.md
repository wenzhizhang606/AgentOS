# M1 只读售后任务

状态为已编写代码和测试，待统一验证。此切片覆盖 TASK-001～005、010、013、015 的部分行为。对应 REQ-CTX-001/002、REQ-RAG-001、REQ-GOV-001、REQ-MA-002、REQ-OBS-001、REQ-DATA-001、REQ-EVAL-001。

## 环境与首次启动

使用 Python 3.11 及以上、MySQL 8.4 主库、一个支持文本 Chat Completions 和 usage 回执的 HTTPS 模型端点。下列命令从仓库根目录执行。示例采用 PowerShell 7。

1. 安装项目及后续测试所需依赖。

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -e ".[test]"
   ```

2. 在选好的空数据库中，通过 MySQL 客户端执行 `SOURCE D:/agentOS/migrations/mysql/001_managed_task.sql`。建表使用迁移账户。API、worker 和本地运维命令使用下列连接变量，密码从实际环境注入。

   ```powershell
   $env:AGENTOS_MYSQL_HOST = "127.0.0.1"
   $env:AGENTOS_MYSQL_PORT = "3306"
   $env:AGENTOS_MYSQL_DATABASE = "agentos"
   $env:AGENTOS_MYSQL_USER = "agentos"
   # 另行设置 AGENTOS_MYSQL_PASSWORD
   New-Item -ItemType Directory -Force local | Out-Null
   Copy-Item examples/m1/release.json local/release.json
   $env:AGENTOS_RELEASE_FILE = (Resolve-Path local/release.json).Path
   ```

3. 编辑 `local/release.json`。将示例域名、模型标识、窗口、每百万 Token 的输入/输出价格替换为供应商实值。价格单位为人民币微元，100 万微元等于 1 元。模型端点应获准处理本部署所有已授权项目的资料。API 和对应 worker 使用同一份清单，启动时将组件源码摘要和发布配置登记至 MySQL。组件、模型、价格或留存模式变化会改变发布 ID，部署切换时保留旧代码、旧配置及旧 worker 至已绑定的任务结束。

4. 为本地调用者生成凭证。以下示例将原始令牌保留在当前进程环境，文件只保存摘要。两个进程均需数据库和发布配置，只有 API 读取凭证文件，只有 worker 读取 `AGENTOS_MODEL_API_KEY`。

   ```powershell
   $env:AGENTOS_DEMO_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
   $digest = python -c "import hashlib,os; print(hashlib.sha256(os.environ['AGENTOS_DEMO_TOKEN'].encode()).hexdigest())"
   $expires = [datetime]::UtcNow.AddHours(8).ToString("yyyy-MM-ddTHH:mm:ssZ")
   @{ credentials = @(@{ token_sha256=$digest; tenant_id="demo"; subject_id="reader";
       project_ids=@("support"); expires_at=$expires }) } |
       ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8NoBOM local/credentials.json
   $env:AGENTOS_CREDENTIALS_FILE = (Resolve-Path local/credentials.json).Path
   agentos grant demo reader support
   agentos put-source demo support return-policy "退货政策" section-1 examples/m1/policy.txt
   ```

5. 在两个具有相应配置的终端分别启动 `agentos api` 和 `agentos worker`。API 默认监听本机 8000 端口。凭证文件在 API 启动时加载。通过 `agentos revoke demo reader support` 撤回项目访问时，新的读取与发送准入检查主库里的最新授权。

## 提交、读取与结算

```powershell
$headers = @{ Authorization = "Bearer $env:AGENTOS_DEMO_TOKEN"; "Idempotency-Key" = [guid]::NewGuid().ToString("N") }
$body = @{ project_id="support"; goal="未拆封商品签收后多久能申请退货？";
    agent_version_id=(agentos release-id); completion_criteria_ref="criteria_support_readonly_v1";
    deadline_at=[datetime]::UtcNow.AddMinutes(5).ToString("yyyy-MM-ddTHH:mm:ssZ");
    budget=@{currency="CNY"; limit_micros=100000; max_concurrency=1} } | ConvertTo-Json
$task = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/tasks -Headers $headers -ContentType 'application/json; charset=utf-8' -Body $body
Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/tasks/$($task.task_id)" -Headers $headers
```

重复查询至终态。成功结果包含回答、引用的来源修订和快照 ID。使用 `GET /v1/context-snapshots/{snapshot_id}` 检查选入来源、窗口估算、语义摘要和留存内容。取消任务使用 `POST /v1/tasks/{task_id}/cancel`，提供新的操作幂等键及对应当前版本的 `If-Match`。同一操作重试须沿用其原键和请求。

MySQL 在发送准入事务中预留根预算。已知回执按实际用量结算；回执不明时保留预留并记录待核对。运维人员取得可核验的供应商账单并确认租户、尝试与回执关联后，可用 `agentos reconcile TENANT DISPATCH_ID RECEIPT_ID ACTUAL_MICROS --input-tokens N --output-tokens N` 核对已结束或过期的尝试。该操作只处理账务，不会重发请求。刷新来源用 `put-source`，撤回来源用 `delete-source`。权威读检查决定旧结果能否继续交付。

## 测试与评测

以下命令留给统一测试阶段。从仓库根目录启动；启用数据库用例前，将数据库变量指向已应用迁移的**专用临时测试库**。这些用例写入测试数据。

```powershell
python -m pytest tests/contracts
$env:AGENTOS_TEST_MYSQL = "1"
python -m pytest tests/integration tests/acceptance
```

| 测试模块 | 验证范围 |
| --- | --- |
| [上下文](../../tests/contracts/test_context.py)、[模型网关](../../tests/contracts/test_model.py) | AC-001/002，证据选择、窗口、留存、精确发送内容、可引用来源以及异常回执 |
| [API](../../tests/contracts/test_api_tasks.py)、[发布](../../tests/contracts/test_release.py) | AC-009/010/012，服务端身份、作用域、幂等、取消和固定版本 |
| [MySQL 集成](../../tests/integration)、[端到端](../../tests/acceptance/test_managed_support.py) | AC-001/008/009/012/017 子集。使用真实 MySQL；端到端使用模型替身，覆盖预算、回执不明与撤回 |
| [评测](../../tests/contracts/test_support_evaluation.py) | AC-014 子集，按固定案例分母计算通过率和最终证据覆盖率 |

初始售后语料和两条标注问题见 [examples/m1](../../examples/m1)。在隔离项目中用每个案例的 question 作为 goal，保存观察记录的 JSON 数组，随后执行 `agentos score examples/m1/cases.json local/observations.json`。每条记录包含 `case_id,state,answer,citations,sent_sources` 或失败的 `case_id,state,error_code`。sent_sources 取自已确认发送的快照 source_refs，缺失的运行保留在总分母内。评分器按固定形式校验答案与证据坐标，输出通过分子、分母及每例结果。两个合成样本用于核对评分口径，业务成功率、时延和生产质量门槛须在扩大样本后实测。

## 适用边界

- 发布清单使用一个模型和一个只读工作流。单个 worker 同时处理一个任务，可以运行多个同版 worker 竞争领取。M1 模型目的地由部署方统一选定，授权粒度为租户内项目。通用插件认证、独立记忆、多 Agent 委派属于后续里程碑。
- M1 的证据路径对授权项目的固定小语料执行词法检索。整段证据、必需指令和问题在最终消息包装后一起计算窗口。字节估算和供应商实测 Token 分开；窗口、输出上限和固定单价能否构成供应商侧费用硬上界，需要用所选端点验证。
- 三种留存模式分别为完整正文、遮盖消息正文及仅元数据。首版的小载荷与准入在同一 MySQL 事务内写入。限期清理、大对象外置、完整回放和生产级灾备演练属于后续工作。
- 引用校验确认标签对应已发送且仍有权交付的来源。回答的逐断言事实支持度、权限压力测试、真实供应商计费与并发性能均待独立验收。此轮只做静态检查，没有测试运行结果。
