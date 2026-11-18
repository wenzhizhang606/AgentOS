# 验收 M1 只读售后 Agent

验收一个具体发布。先复查一次已完成的运行，再用当前发布验证售后回答、缺少依据、预算拒绝和项目权限。记录中分开填写工程测试、真实模型调用和业务质量。M1 的签字范围是单 Agent 的只读售后流程。[实施范围](m1-managed-support.md) · [AC 验收规范](../specs/acceptance.md)

2026-11-18 已完成一笔真实模型验收。复查本次结果只需下一节和部署目录中已有的 `local/test-results/2026-11-18/llapi-m1-live/receipt.json`。其中 `run_id` 可直接粘入监控台。需要重新验收另一个发布时，再执行“用当前发布验一笔新业务”。[本次回答、费用和 Token 估算差异](model-rag-environment.md#真实模型验收)

## 在 Windows 上打开服务器监控

在仓库根目录打开 PowerShell。从本机私有 SFTP 配置取得 SSH 目标，使用你已有的 SSH 登录方式。下例针对已开启公网监控网关的部署，从本机 18000 转发至服务器的私有 API 端口 18080。仅运行基础 Compose 时，将隧道目标端口改为 8000。

```powershell
$site = Get-Content -Raw -Encoding utf8 .vscode/sftp.json | ConvertFrom-Json
$target = "$($site.username)@$($site.host)"
$sshPort = if ($site.port) { [int]$site.port } else { 22 }
$sshArgs = @('-p', "$sshPort")
if ($site.privateKeyPath) {
    $sshArgs += @('-o', 'IdentitiesOnly=yes', '-i', [Environment]::ExpandEnvironmentVariables($site.privateKeyPath))
}
ssh @sshArgs $target 'cd /root/agentos && docker compose ps -a && docker compose exec -T api agentos release-id && docker compose exec -T worker agentos release-id'

$projectToken = ssh @sshArgs $target 'cat /root/agentos/local/test-observer.token'
if ($LASTEXITCODE -ne 0 -or -not $projectToken) { throw '请取得当前验收项目的有效访问令牌' }
Set-Clipboard -Value $projectToken.Trim()
Remove-Variable projectToken
ssh @sshArgs -N -o ExitOnForwardFailure=yes -L 127.0.0.1:18000:127.0.0.1:18080 $target
```

1. 确认 MySQL、API、worker 均为 `healthy`，迁移容器以 `0` 退出，两行发布 ID 相同。保持隧道窗口打开。另开终端运行 `curl.exe -fsS http://127.0.0.1:18000/readyz`，应返回 HTTP 200。
2. 打开 [本机监控](http://127.0.0.1:18000/monitor)，输入项目 `support`，把剪贴板内容粘入“访问令牌”，点击“载入项目”。上例中的文件是验收部署临时签发的项目令牌。文件缺失或登录返回 401 时，使用管理员为该身份新签发且已被 API 加载的 AgentOS 项目令牌。[凭证配置](docker-deployment.md)
3. 点击“Agent 运行轨迹”，选发布版本，再选一条已结束的任务和执行尝试。列表默认最近 24 小时，可扩至 7 天。若已知运行 ID，可直接输入 `<task_id>:<attempt>`。若没有记录，将本项记为缺证，按下一节产生样例。上述操作只读，模型调用数增量应为零。[运行轨迹说明](agent-run-trace.md)

在项目总览可看到健康之外的业务证据。成功运行的轨迹应串起上下文准备、该发布实际启用的 Skill 和 Tool、模型发送准入 `dispatch_admitted` 和结算 `receipt_settled`。核对运行 ID、worker、快照 ID、Token 和费用。观察者可以在授权范围内看他人的轨迹元数据。查回答和快照正文时使用提交任务的身份。旧版记录的 worker 可能为空。

## 用当前发布验一笔新业务

在隔离的验收项目预置 [退货政策](../../examples/m1/policy.txt)，使其在 [案例标注](../../examples/m1/cases.json) 指定的 `return-policy@1 / section-1` 可检索。准备项目权限和语料的命令见[部署步骤](docker-deployment.md)。先在发布配置中确认模型为 `gpt-6.1-sol`、价格和费用上限已核定。成功样例会向模型供应商发起调用。

在第二个 PowerShell 窗口执行下列命令。若剪贴板已被覆盖，重新复制相同项目的有效令牌。示例每任务上限为 100000 微元，即 0.10 元人民币。它必须覆盖当前发布的预留费用；超出本次验收允许费用时，记录预算阻断。截止时间须早于凭证失效时间。

```powershell
$base = 'http://127.0.0.1:18000'
$auth = @{ Authorization = 'Bearer ' + (Get-Clipboard -Raw).Trim() }
Set-Clipboard -Value ' '
$project = 'support'
$release = Read-Host '输入刚核对一致的发布 ID'
$command = @{
    project_id = $project
    agent_version_id = $release
    goal = '未拆封商品在签收之后多久可以申请退货？仅回答天数和引用。'
    completion_criteria_ref = 'criteria_support_readonly_v1'
    deadline_at = [DateTime]::UtcNow.AddMinutes(5).ToString("yyyy-MM-ddTHH:mm:ssZ")
    budget = @{ currency = 'CNY'; limit_micros = 100000; max_concurrency = 1 }
}
$write = $auth.Clone()
$write['Idempotency-Key'] = [guid]::NewGuid().ToString('N')
$body = [Text.Encoding]::UTF8.GetBytes(($command | ConvertTo-Json -Depth 4))
$submit = @{ Method = 'Post'; Uri = "$base/v1/tasks"; Headers = $write;
             ContentType = 'application/json; charset=utf-8'; Body = $body }
$task = Invoke-RestMethod @submit
$replay = Invoke-RestMethod @submit
if ($replay.task_id -ne $task.task_id) { throw '同一请求返回了不同的任务 ID' }
$taskUrl = "$base/v1/tasks/$($task.task_id)"
$stopAt = [DateTime]::UtcNow.AddMinutes(6)
do {
    $result = Invoke-RestMethod -Headers $auth -Uri $taskUrl
    if ($result.state -in @('succeeded','failed','timed_out','cancelled')) { break }
    Start-Sleep -Seconds 2
} while ([DateTime]::UtcNow -lt $stopAt)
$result | Select-Object task_id,state,attempt,error_code,snapshot_id,settled_micros,accounting_state
if ($result.state -eq 'succeeded') {
    $result.result | ConvertTo-Json -Depth 5
    $snapshot = Invoke-RestMethod -Headers $auth -Uri "$base/v1/context-snapshots/$($result.snapshot_id)"
    $snapshot | Select-Object snapshot_id,wire_digest,source_refs | ConvertTo-Json -Depth 5
}
```

| 验什么 | 可签字的观察结果 |
| --- | --- |
| 有依据的回答（AC-001、AC-002） | 状态为 `succeeded`，回答为“七天”或“7天”并带 `[E1]`，引用 ID、版本、原文位置与实际发送快照中的对应来源一致。保留 `wire_digest`、任务 ID 和发布 ID。 |
| 只发送一次和按实例追踪（AC-009、AC-013） | 原样重复提交得到同一任务 ID。用 `GET /v1/tasks/{task_id}/timeline` 查看全任务，按 `GET /v1/tasks/{task_id}/runs/{attempt}/trace` 分开看每次领取。该成功样例只有一次模型准入，成功回执与对应尝试匹配，账本为 `final`。平台按配置价格结算；供应商扣费需用对应供应商用量或账单核对。 |
| 无依据（AC-002） | 在只有示例政策的项目，单独以 `orbital ephemeris` 提交；应在上下文组装时 `failed / MISSING_EVIDENCE`，无模型准入，已结算费用为零。 |
| 预算不足（AC-009） | 保留有依据的问题，在预留费用大于零的发布上将 `limit_micros` 改成 `0`，应 `failed / BUDGET_EXHAUSTED`，无模型准入，已结算费用为零。 |
| 权限（AC-012） | 对 `GET /v1/projects/support/agents` 去除认证应为 401。用仅授权 `support` 的凭证请求 `GET /v1/projects/payroll/monitoring` 应为 403。两项均无模型调用。 |

每个不同业务样例生成新的 `Idempotency-Key` 和截止时间，再序列化并提交。若截止后仍未得到终态、实例事件无法关联或结算为 `pending`，分别保留任务、轨迹和错误码，记为待核对。将缺证项留在分母内。

## 签字和量化评测

| 签字范围 | 验收材料和判定 |
| --- | --- |
| M1 只读售后 | 记录日期、代码提交或镜像标识、API/worker 发布 ID、模型、测试库环境、JUnit 通过/失败/跳过数，以及上表逐项通过/失败/待核对。附真实运行的任务、尝试、快照、费用和回执证据。2026-11-18 的历史报告为 147 项工程测试通过，使用真实 MySQL、Chromium 和模型替身。每个新发布单独记录证据覆盖。[历史报告与环境](model-rag-environment.md#服务器验收) |
| 公开质量评测（AC-014） | BEIR · SciFact test 验检索，以 nDCG@10 为主，已完成全量对照。HotpotQA distractor dev 验多段证据问答，SQuAD 2.0 dev 验回答与拒答。两项生成评测固定 `gpt-6.1-sol`，适配与跑分待完成。按基准保留数据划分、评分器版本、逐题预测、全量分母和技术失败。[评测基准与适配](../requirements/agentos-platform.md#m1-公开评测基准) · [实测](../research/agent-benchmark-comparison.md) |
| 业务试点 | 业务负责人先冻结隔离留出集、质量门槛、预算和环境。按相同问题配对比较基线与候选，提交成功率、最终证据覆盖率、引用正确率、端到端 P95、每成功任务成本、分母和 95% 置信区间。需求给出的最终证据覆盖率 ≥90%、引用正确率 ≥95% 是待冻结目标。越权、重复副作用及账本错误逐例验收。跨会话记忆、多 Agent、通用插件和沙箱按各自里程碑另验。[指标与试点条件](../requirements/agentos-platform.md) · [任务映射](../plan/implementation-plan.md) |

签字记录可使用一行模板：`日期｜范围及版本｜负责人｜预期｜观察值及分母｜报告/任务/运行/快照 ID｜通过 / 失败 / 待核对｜遗留项`。
