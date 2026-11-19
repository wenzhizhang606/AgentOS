# AgentOS

AgentOS 是以上下文管理为核心的企业 Agent 中台。API 受理任务，worker 组装上下文并通过模型网关调用模型，MySQL 保存任务、调用及结算记录。2026-11-18 的工程回归已有 141 项契约测试、27 项真实 MySQL 用例和 6 项部署用例通过。业务验收可沿一次真实运行核对回答、引用和费用。[启动与调用](docs/changes/m1-managed-support.md) · [M1 验收操作手册](docs/changes/m1-acceptance-walkthrough.md)

首次配置时，将根目录的 `.env.example` 复制为 `.env`，填写模型部署名、完整的 Chat Completions API 地址和密钥，并按需调整词法检索参数。API 和 worker 使用相同的模型和检索配置生成发布 ID。[配置模板](.env.example) · [参数与生效方式](docs/changes/model-rag-environment.md)

## 打开 AgentOS 控制台

| 部署方式 | 控制台地址 | 端口用途 |
| --- | --- | --- |
| 基础 Compose | `http://127.0.0.1:8000/console` | API 默认只监听宿主机回环地址 |
| 叠加公网网关 | `https://<公网 IPv4>:8000/console` | 8000 提供 HTTPS，443 用于证书验证和兼容访问，80 留给其他服务 |
| 公网部署上的 SSH 隧道 | `http://127.0.0.1:18000/console` | 本机 18000 转发到服务器 `127.0.0.1:18080` 的私有 API |

主页的三个工作区入口下方有一张 Agent 组成图。选择一个运行示例，播放或逐步查看，图上会标出当前参与的组件。示例在浏览器中使用内置资料，无需项目令牌，也不会产生模型费用。

查看实际项目时，点击「进入控制台」，输入管理员提供的项目 ID 和访问令牌。项目读取成功后，顶栏显示当前项目，可在概览、运行轨迹和资源管理之间切换。页面上的“更新于”指最近一次成功读取数据的时间。凭证验证失败时，登录表单会说明原因。令牌只保存在本页内存中。直接打开视图书签会在登录后进入目标视图。

- **项目概览**（`/console#overview`）显示所选时间内的任务状态分布、Skill 加载、Tool 执行、模型准入和已结算费用。窗口按任务创建时间筛选，调用汇总覆盖窗口内的全部可见任务。
- **Agent 运行轨迹**（`/console#runs`）可从概览最近任务的时间线点开某次领取对应的轨迹，也可按发布版本选择实例或输入 `任务 ID:尝试号`。瀑布图把上下文、Skill、Tool 和模型放在同一时间轴上。模型区间从发送准入到费用结算，事件明细列出精确时间、错误和关联标识。
- **资源管理**（`/console#resources`）列出当前发布启用的 Skill 数与 Tool 数，包括已启用但从未调用的资源。按名称和类型筛选，点击资源名可核对版本及完整 SHA-256。程序可使用同一项目观察凭证读取 `GET /v1/projects/{project_id}/resources`。[界面与组件设计](docs/design/console-ui.md)

公网部署需在云安全组放行入站 TCP 8000 和 443。[启动网关与探针](docs/changes/docker-deployment.md#公网-ip-监控入口) · [监控凭证和调用口径](docs/changes/capability-monitoring.md) · [实例查看示例](docs/changes/agent-run-trace.md)

已有的 `/monitor` 和 `/resources` 书签仍可使用，分别进入控制台的项目概览和资源管理。专用观察令牌在 `monitor_credentials` 中配置，可读范围由观察授权和当前项目成员关系共同决定。[资源目录与授权](docs/changes/capability-monitoring.md#查看监控)

检索、编码和多 Agent 的已测结果分别列出样本数、评分方式和可比的公开基线。[基准实测与主流 Agent 对照](docs/research/agent-benchmark-comparison.md) · [组件独立发布设计](docs/design/independent-component-release.md)

仓库级编码试点采用 SWE-Bench Pro V2 Full 的 1 道 Open Library 题。`gpt-6.1-sol` 一次生成补丁，官方 5 项缺陷测试和 15 项回归测试全部通过。样本规模为 1 题，全榜规模为 642 题。[近期模型报告、运行记录及官方评分](docs/research/swe-model-report-pilot.md)

2026-11-19 的全量计划已核对 11 个仓库、642 道题和 3,210 个判分文件。现有服务器 Docker 数据盘预检可用 3.10 GiB，本批次默认单题准入门槛为 12 GiB。目前 642 题均待执行，全量解决率待判分。[全量计划、空间预检与进度查询](docs/changes/swe-pro-full.md)

## 已确认的代码结构

当前支持在同一发布中配置文本 Skill 和内置只读 Tool。同一任务被 worker 重新领取时产生新尝试，可在控制台按任务 ID 和尝试号区分。[Agent 运行轨迹与升级说明](docs/changes/agent-run-trace.md) · [Skill、Tool 与项目监控](docs/changes/capability-monitoring.md)

| 路径 | 职责 |
| --- | --- |
| `src/agentos/api/`、`worker/` | 两个进程入口。API 受理与查询任务；worker 接续持久任务，调用领域模块。表中后续 Python 包名均相对 `src/agentos/`。 |
| `contracts/`、`control/` | 跨模块交换的数据和插件契约；Agent 定义、Profile、能力注册与版本发布。具体供应商依赖留在适配层。 |
| `runtime/`、`collaboration/` | 前者负责任务、租约和根预算；后者通过运行时完成委派、并行汇总和取消传播。 |
| `context/`、`knowledge/`、`memory/` | 上下文服务选择证据、分配窗口并生成快照；知识模块负责 RAG；记忆模块负责跨任务读取、修订与遗忘。 |
| `governance/`、`gateways/{model,tool,mcp}/` | 身份、授权、围栏、人工审批和调用准入；模型、工具与 MCP 的协议边界。MCP 包承接客户端及服务端适配。 |
| `sandbox/`、`adapters/` | 工作空间与沙箱提供方契约；MySQL 权威状态、对象存储和检索索引的具体适配。CubeSandbox 是后续可验证的提供方。 |
| `observability/`、`evaluation/`、`improvement/` | 运行事件与追踪、量化评测、RSI 候选实验。候选通过评测后由控制面发布。 |
| `migrations/mysql/`、`native/` | MySQL 迁移与可选 C++ 扩展的位置。 |
| `tests/{contracts,integration,acceptance}/` | 分别验证可替换实现的契约、跨模块协作、按 AC 编号定义的端到端行为。 |

后续对外 RAG API 复用 `knowledge/`。审批与调试视图由 `api/` 提供查询和操作入口；编码执行归 `sandbox/`，多模态事件经 `api/` 接入并由 `runtime/` 调度，外部 Agent 通过 `adapters/` 接入。插件按 Release 固定，运行中的任务沿用已绑定的版本。

当前已实现部分与后续模块的交付顺序见[实施计划](docs/plan/implementation-plan.md)。

## 阅读路径

| 文档 | 用途 |
| --- | --- |
| [需求草案](docs/requirements/agentos-platform.md) | 产品范围、稳定需求编号、量化目标和待确认假设 |
| [技术设计](docs/design/technical-design.md) | 总体架构、模块、数据、运行流程、部署与故障处理 |
| [运行契约](docs/specs/runtime-contracts.md) | 数据字段、接口、状态转换、错误与一致性要求 |
| [组件契约](docs/specs/component-contracts.md) | Profile、沙箱、围栏、RAG、MCP、人工介入、编码与多模态接口 |
| [验收规范](docs/specs/acceptance.md) | 每项需求的正常、异常和并发验收案例及证据 |
| [实施计划](docs/plan/implementation-plan.md) | 论文到开发任务的映射、依赖与完成条件 |
| [SDD 工作规范](docs/specs/sdd-workflow.md) | 规范驱动开发流程、变更格式和文档同步方式 |
| [领域术语](CONTEXT.md) | 上下文、证据、记忆、任务、候选和发布版本的含义 |
| [下半年 arXiv 调研](docs/research/arxiv-2026-h2.md) | 2026年7月起首发论文的阅读记录、可采用机制和实验边界 |
| [企业平台调研](docs/research/enterprise-agent-platforms.md) | 企业中台、现有平台、RAG与多 Agent 的参考 |
| [开源实现调研](docs/research/opensource-agent-components.md) | 编码Agent源码机制、协议版本、MySQL与检索取舍 |
| [语言与需求复核](docs/research/language-and-requirements-review.md) | Python/C++分工、第一性原理修订、评测样本规划与CubeSandbox固定版本核查 |
| [设计审核记录](docs/design/review-log.md) | 每轮变更的证据、替代方案、问题修正与验证边界 |

MySQL 为已确定约束，试点指标待实测。开发状态与软件验收结果分别记录。

团队熟悉Python与C++，当前建议Python主平台、C++按实际热点或本地集成需要接入。所有Agent类型保留在建设范围内，按Profile组合分批认证；CubeSandbox列为Linux microVM沙箱的优先验证候选，尚未部署或通过项目验收。
