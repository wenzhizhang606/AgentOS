# AgentOS

AgentOS 是以上下文管理为核心的企业 Agent 中台，底层权威数据库采用 MySQL。首个只读售后任务已经有 API、worker、模型网关与配套测试代码，当前状态为待统一测试。[启动、配置、调用和测试说明](docs/changes/m1-managed-support.md)

## 已确认的代码结构

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
