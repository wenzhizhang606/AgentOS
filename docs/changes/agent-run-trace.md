# Agent 运行实例轨迹

关联 REQ-OBS-001、TASK-009、AC-013。范围为当前单 Agent 只读 Profile 的实例检查。代码与测试同步交付，运行验收待统一执行。

## 查看某次运行

1. 基础 Compose 打开 `http://127.0.0.1:8000/monitor`，公网网关部署打开 `https://<公网 IPv4>:8000/monitor`，输入项目 ID 和访问令牌，点击“载入项目”。[端口与 SSH 隧道](docker-deployment.md#公网-ip-监控入口)
2. 进入“Agent 运行轨迹”，选择 Agent 发布版本。同一个 Profile 可有多个发布版本，按创建任务时绑定的 `agent_version_id` 定位。目录依据窗口内的实际领取记录，默认最近 24 小时，最长 7 天。刚提交且尚未被领取的任务可在项目任务列表查看。
3. 在该版本的运行列表中选择任务 ID 和尝试号。已知 `run_id` 时也可直接定位，例如 `<task_id>:2`。在 Compose 部署中，运行 `docker compose logs worker`，从 `Claimed task` 行复制 `run_id=` 后面的值。按 ID 读取已保留的轨迹不受列表时间窗口限制。
4. 查看该次领取后的事件、上下文准备、Skill 加载、Tool 执行及模型准入与回执。首次展示最近一页，每页最多 500 条，页内按时间从早到晚排列。点“更早事件”可回看同一次运行中较早的步骤。结合开始、结束时间和错误码定位中断位置。已准入但没有回执的模型调用仍待核对。检查回答或快照正文需走原有的资源读取接口。

## 重试与历史记录

worker 在领取事务中递增尝试号。若第一次领取后失联，第二个 worker 重新领取同一任务，两次运行分别对应 `<task_id>:1` 和 `<task_id>:2`。若模型准入和回执均属于第二次，其用量和费用只出现在第二次轨迹中。第一次显示 `superseded`（已被接替）；缺少明确结束记录时，结束时间留空。只有开始记录的能力调用也保留为未结束。

目录、运行列表及轨迹只显示当前租户、项目和数据库成员权限允许查看的范围。普通凭证限本人任务。观察整个项目需要在凭证内同时配置 `project_ids` 和对应的 `monitor_project_ids`。项目观察者可看其他主体的运行元数据，读取其输入、回答和快照正文仍按原接口的所有者规则处理。[监控权限与配置](capability-monitoring.md) · [三个 API 的路径与参数](../specs/runtime-contracts.md)

## 部署与验证

已有 001、002 的手工部署先用迁移账户在 MySQL 客户端执行 `SOURCE D:/agentOS/migrations/mysql/003_run_trace.sql`，再部署新版 API 和 worker。新库从 001 开始依序执行。Compose 部署由 `migrate` 按编号应用缺少的文件。003 为任务领取事件保存 worker 标识并增加查询索引。升级以前的记录可继续按尝试浏览；历史证据无法确认的 worker 标识留空。若需回退应用，保留新增字段和历史记录，按发布版本使用对应 worker。[数据库迁移](../../migrations/mysql/003_run_trace.sql) · [容器部署](docker-deployment.md)

统一验收使用已完成 001 至 003 迁移的专用 MySQL 测试库。测试覆盖同一任务两次领取的独立轨迹、普通主体与项目观察者的可见范围、历史记录缺少 worker 或回执时的显示，以及长轨迹在相同时间戳处的分页。测试模块位于 `tests/contracts/`、`tests/integration/` 和 `tests/acceptance/`。本轮未执行测试、数据库迁移或浏览器验收。[AC-013 验收定义](../specs/acceptance.md)
