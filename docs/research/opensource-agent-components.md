# 开源 Agent 组件、运行契约与存储选型调研

2026 年 11 月 17 日 · 设计研究 · 未运行或集成上游项目

AgentOS 使用 MySQL 作为核心状态数据库，面向编码、研究、业务工具、工作流、多模态及外部 Agent 提供可组合能力。沙箱、安全围栏、RAG 和 MCP 均进入正式组件范围。本文按可移植机制比较开源实现，供需求、运行契约和验收设计使用。

**证据口径。** “观察”来自下列官方源码、配置或文档。“设计推导”是 AgentOS 的建议，尚未实现。固定标签只约束对应源码引用。main、master、latest 文档均于 2026-11-17 查阅，未固定提交，不能视为已验证的发行版本行为。未执行代码、性能测试或跨项目互操作测试。

## 决策摘要

以下为设计推导，具体行为由[运行契约](../specs/runtime-contracts.md)和[验收规范](../specs/acceptance.md)约束。

| 决策 | 建议 | 需要验证的代价或边界 |
| --- | --- | --- |
| 运行内核 | 分离 Agent 行为、Session/Run、Workspace、工具与事件存储 | 适配器必须覆盖恢复、取消、预算和权限，不能只封装调用入口 |
| 沙箱与围栏 | 同时提供执行隔离和动作准入，平台策略只能被组件收紧 | 操作系统、远程工具、浏览器及连接器的实际约束范围不同 |
| 上下文 | 统一来源、预算和组装记录，保留压缩前原文的受控引用 | 摘要有损，模型私有压缩项可能无法跨提供方迁移 |
| 编码能力 | 以 Workspace、Patch、Test、Git 等组件构成编码配置 | 补丁可能部分应用，测试通过、提交存在与任务完成分别判断 |
| 核心存储 | MySQL 9.7 LTS 为拟首选，8.4 为特性兼容基线 | 两条版本线分别验证，不把兼容基线等同于升级或降级保证 |
| 检索 | OpenSearch 混合检索为默认候选，MySQL ngram 作词法基线，Qdrant 作替代 | 需同资料、同权限过滤和同预算实测，没有已证实的最优方案 |
| 数据分工 | MySQL 保存权威状态，对象存储保存大载荷，外置检索索引可重建 | 必须处理索引延迟、撤权、删除传播与对象上传失败 |

## 项目与版本证据

| 项目 | 核查基准 | 主要材料 |
| --- | --- | --- |
| OpenHands Software Agent SDK | v1.62.0，短提交 504a88c；架构文档为 main | [发行页](https://github.com/OpenHands/software-agent-sdk/releases) · [架构](https://github.com/OpenHands/docs/blob/main/sdk/arch/overview.mdx) |
| OpenCode | v2.3.0，短提交 a6b15d3；配置核查为官方 V2 滚动文档 | [发行页](https://github.com/anomalyco/opencode/releases) · [权限](https://opencode.ai/docs/permissions/) |
| Aider | v0.86.0，短提交 a4be6cc；源码引用固定标签 | [发行页](https://github.com/Aider-AI/aider/releases) · [RepoMap](https://github.com/Aider-AI/aider/blob/v0.86.0/aider/repomap.py) |
| SWE-agent | v1.1.0，短提交 0f3acaf；官方声明 maintenance-only | [发行页](https://github.com/SWE-agent/SWE-agent/releases) · [当前架构与维护状态](https://swe-agent.com/latest/background/architecture/) |
| Codex | rust-v0.169.0，2026-11-13，短提交 d32063f | [固定发行页](https://github.com/openai/codex/releases/tag/rust-v0.169.0) · [沙箱执行源码](https://github.com/openai/codex/blob/rust-v0.169.0/codex-rs/core/src/tools/sandboxing.rs) |
| Gemini CLI | v0.68.0，2026-11-10，短提交 5a5c718 | [固定发行页](https://github.com/google-gemini/gemini-cli/releases/tag/v0.68.0) · [策略配置](https://github.com/google-gemini/gemini-cli/blob/v0.68.0/docs/reference/policy-engine.md) |
| DeepSeek Harness | master，未固定提交 | [架构](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md) · [能力接缝](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/capability-seams.md) |
| DeepAgents、LangGraph | main，未固定提交 | [分层架构](https://github.com/langchain-ai/deepagents/blob/main/libs/ARCHITECTURE.md) · [中断恢复](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/interrupts.mdx) |
| Google ADK、A2A | main，未固定提交 | [Runner](https://github.com/google/adk-python/blob/main/docs/guides/runners/runner/index.md) · [A2A 规范](https://github.com/a2aproject/A2A/blob/main/docs/specification.md) |

## 运行、会话和组件组合

**观察。** OpenHands 分为 SDK、内置工具、Workspace 实现和 Agent Server 四个包。Conversation 管理生命周期与状态，本地和远程模式共享接口。工具与 Agent 在选定环境中共同运行，并不统一经 Workspace API 转发。[OpenHands 架构](https://github.com/OpenHands/docs/blob/main/sdk/arch/overview.mdx)

Conversation 分别维护状态字段和追加事件，提供持久化与恢复服务。这可以借鉴为会话和事件契约，但恢复会话记录不等于恢复全部文件、进程或外部动作。[Conversation 架构](https://github.com/OpenHands/docs/blob/main/sdk/arch/conversation.mdx)

DeepSeek Harness 通过有序 profiles 和 bundles 装配能力，基础组合覆盖模型、工具、持久化、沙箱及审批。子 Agent 可接不同 provider，支持将能力契约与具体实现分开。[DSH 架构](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md) · [能力接缝](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/capability-seams.md)

DeepAgents 区分 Harness、LangChain 执行循环和 LangGraph 持久运行时。工具可见性与调用权限独立，子 Agent 有自己的中间件，文件系统持久化和检查点持久化也独立。[DeepAgents 架构](https://github.com/langchain-ai/deepagents/blob/main/libs/ARCHITECTURE.md)

**设计推导。** 组件类型至少覆盖模型、规划与路由、上下文、记忆、检索、工具与技能、沙箱、围栏与人工接续、协作、事件、产物、评测及外部 Agent 适配。类型目录可扩展，共享发布和生命周期语义。

| 契约 | 应固定的内容 |
| --- | --- |
| ComponentManifest | 类型、接口版本、实现摘要、依赖、配置与状态 Schema、所需能力、取消及恢复支持 |
| AgentVersion | 固定组件组合、装配顺序和配置摘要，运行中变更形成新版本或明确迁移 |
| RuntimeAdapter | 创建、推进、暂停、恢复、终止及事件读取，声明实际可执行的边界 |
| Session / Run | 会话历史与一次运行尝试分开，记录工作空间、模型调用、工具调用及未完成动作 |
| ToolInvocation | 原生、MCP、检索和外部 Agent 入口统一经过授权、预算、审计及结果校验 |

“支持组件”需要说明支持登记、调用还是完整生命周期管理。缺少恢复或取消能力的适配器应显式降级能力声明，不能默认为具备。

## 沙箱、安全围栏和人工接续

**观察。** DSH 以 ctx.sandbox 消费实际执行的 argv，由 provider 报告 enforcement。官方沙箱文档列出 Linux、macOS、Windows 与 SSH 后端，后端不可用返回 SandboxUnavailableError，不能静默转为无隔离执行。[能力接缝](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/capability-seams.md) · [沙箱后端](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/sandbox.md)

Codex 的固定版本工具执行层分别处理审批与沙箱，并限制存在 denied-read 时直接无沙箱重试。审批缓存有独立键语义。官方说明命令网络代理的范围不覆盖 MCP、浏览器、连接器和模型流量，须分别核对通道。[固定源码](https://github.com/openai/codex/blob/rust-v0.169.0/codex-rs/core/src/tools/sandboxing.rs) · [通道范围](https://learn.chatgpt.com/docs/agent-approvals-security#traffic-outside-the-command-network-proxy)

Gemini CLI 策略支持 allow、deny、ask_user，非交互模式下 ask 被拒绝，管理员策略具有更高优先级。该版本文档标记工作区策略尚未实现，不能按已具备能力纳管。[固定策略文档](https://github.com/google-gemini/gemini-cli/blob/v0.68.0/docs/reference/policy-engine.md)

Gemini 的文件夹信任默认关闭，Seatbelt 默认配置为 permissive-open，允许较宽的读取和网络访问，另有容器沙箱选项。平台基线需独立定义，不能照抄这些默认值。[文件夹信任](https://github.com/google-gemini/gemini-cli/blob/v0.68.0/docs/cli/trusted-folders.md) · [沙箱](https://github.com/google-gemini/gemini-cli/blob/v0.68.0/docs/cli/sandbox.md)

OpenCode V2 使用 action/resource/effect，复合操作任一资源被 deny 则拒绝。默认基础策略广泛允许，外部目录默认允许，Shell 继承宿主权限。自定义子 Agent 使用自己的权限，不自动取父权限子集。policy 可进一步收紧决定。[OpenCode 权限](https://opencode.ai/docs/permissions/)

OpenHands 将风险分析与确认策略分离，ConfirmRisky 默认阈值为 HIGH，UNKNOWN 默认确认。LLM 风险分析读取动作生成时提供的风险等级，不能独立证明动作得到企业授权。[确认策略源码](https://github.com/OpenHands/software-agent-sdk/blob/v1.62.0/openhands-sdk/openhands/sdk/security/confirmation_policy.py) · [安全架构](https://github.com/OpenHands/docs/blob/main/sdk/arch/security.mdx)

**设计推导。** 安全围栏包含输入检查、动作准入、输出检查和人工接续。沙箱负责实际文件、进程和网络约束。两者各自声明 enforcement，并记录所约束的调用通道。

- 子任务权限取当前身份、父委派、子组件能力和资源策略的交集，项目配置不得扩大平台硬拒绝。
- 审批绑定动作摘要、规范化参数、资源、策略版本和有效期。参数或授权变化后重新判定。
- 沙箱不可用、策略无法求值、执行参数与已批准摘要不同均明确失败，禁止隐式扩大权限。
- 每种运行环境报告文件挂载、网络出口、进程及资源限制、凭证注入和清理能力。
- 非交互工作流定义等待、超时与拒绝语义，禁止将无人响应解释为同意。

LangGraph 的 interrupt 恢复会重新执行所在节点。中断前已有副作用可能再次发生，需要幂等处理或把副作用拆到独立节点。[官方中断文档](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/interrupts.mdx)

**设计推导。** 人工审批状态持久化到 MySQL，恢复操作与业务副作用分别保存幂等键。审批通过只解除对应等待，不改变其他权限和预算条件。

## 上下文、记忆与产物

**源码观察。** Aider RepoMap 提取代码定义与引用，构图并通过 PageRank 排序，再依据 Token 预算选择符号上下文。排序考虑已入聊天的文件及提及标识符，可参考为代码检索适配器。[v0.86.0 RepoMap](https://github.com/Aider-AI/aider/blob/v0.86.0/aider/repomap.py)

OpenCode 压缩保存摘要和近期原文，官方明确摘要有损。后台压缩仍为实验能力，待应用摘要保存在内存。提供方原生压缩项可能绑定模型与提供方，迁移时需使用原始会话。[压缩文档](https://opencode.ai/docs/compaction/)

ADK Runner 绑定 session、memory、artifact、credential 服务，run_async 与 run_live 产生结构化事件，并提供关闭及 rewind 行为。这支持将运行状态、可复用记忆和产物分别建模。[ADK Runner](https://github.com/google/adk-python/blob/main/docs/guides/runners/runner/index.md)

**设计推导。** ContextSnapshot 保存一次模型调用的实际序列化输入及来源。摘要、记忆、检索片段和多模态产物采用统一引用，但分别保留类型、生命周期、授权范围及更新语义。

| 内容 | 建议表示 | 重点验收 |
| --- | --- | --- |
| 会话历史 | 追加事件与可重建视图 | 重试去重、事件顺序、恢复位置 |
| 压缩摘要 | 原文范围、策略版本、生成模型与信息缺口 | 关键约束保留、原文回取、跨模型恢复 |
| 记忆 | 来源、有效时间、修订及替代关系 | 冲突、更正、撤回和派生失效 |
| 检索证据 | 文档版本、片段位置、候选得分与授权结果 | 证据一致性、时效及权限过滤 |
| 多模态产物 | 类型、对象引用、内容摘要、衍生关系 | 转写或摘要回链、大小限制及访问控制 |

## 编码 Workspace、补丁、测试与检查点

**源码观察。** Aider EditBlockCoder 逐块应用 SEARCH/REPLACE，匹配失败时可能尝试其他已入上下文文件。最终报错时，已成功的块仍然保留，因此调用失败与完全未修改不能画等号。[固定编辑源码](https://github.com/Aider-AI/aider/blob/v0.86.0/aider/coders/editblock_coder.py)

Aider 默认自动提交，并可在编辑 dirty 文件前先提交已有修改。默认提交跳过 pre-commit hooks。自动 lint 和可配置测试依据退出码及输出继续修复。[Git 集成](https://aider.chat/docs/git.html) · [测试流程](https://aider.chat/docs/usage/lint-test.html)

SWE-agent 的 SWEEnv 适配 SWE-ReX Deployment，由本地或远程容器运行 Shell 会话，HistoryProcessor 处理模型历史。该项目已进入维护模式，当前新运行时选型需另行评估。[官方架构](https://swe-agent.com/latest/background/architecture/)

固定版本 ToolConfig 将安装、重置、解析器、单次超时与总执行超时分别配置。其总执行超时在下一步阻止 Agent，不中断已经运行的命令。ToolFilterConfig 主要排除不适合该交互环境的命令。[v1.1.0 工具源码](https://github.com/SWE-agent/SWE-agent/blob/v1.1.0/sweagent/tools/tools.py)

SWE-agent 自 v1.1.0 用 query 保存当前步骤实际送入模型的输入，可以借鉴为精确输入追踪。轨迹保存不等于同模型输出可以完全复现。[官方轨迹说明](https://swe-agent.com/latest/usage/trajectories/)

OpenCode 快照尽力捕获活动目录内符合条件的文件，不包含数据库、进程、Git 元数据或网络动作。恢复可能覆盖当前修改，回退也不构成安全删除。[快照范围](https://opencode.ai/docs/snapshots/)

**设计推导。** 编码 Agent 作为通用组件体系的一种组合，以受控 Workspace 承载修改，显式区分生成补丁、应用、测试、提交和发布。

| 契约 | 必需信息与失败语义 |
| --- | --- |
| WorkspaceLease | 仓库与基准 revision、独立目录、租约 epoch、镜像摘要、状态和清理策略 |
| PatchResult | 基线内容摘要、目标路径、逐块结果、实际变化与部分应用状态 |
| TestResult | 命令、环境、退出码、日志、覆盖范围、测试是否被候选修改 |
| Checkpoint | 事件位置、文件捕获范围、排除项、可恢复能力和并发冲突条件 |
| GitAction | 提交、分支、推送分别授权，用户已有改动有明确归属和保留规则 |
| Deadline | Agent 软停止、工具中断与沙箱硬截止分别记录，终止后核对残留进程和费用 |

## MCP、技能与外部 Agent 边界

**观察。** OpenHands 原生工具采用 Action/Observation/Executor，MCP 工具初始化时发现并加入工具集合，路径绕过原生 ToolRegistry。AgentOS 应在适配后统一登记和执行策略。[工具架构](https://github.com/OpenHands/docs/blob/main/sdk/arch/tool-system.mdx)

OpenCode V2 的 MCP 配置位于 mcp.servers，支持本地 stdio、远程 Streamable HTTP 和 OAuth。V1 配置名称不同，不能直接混用。[官方 MCP 文档](https://opencode.ai/docs/mcp-servers/)

Gemini 的 MCP trust=true 会跳过该 server 的确认，提供给模型的 Schema 可能删除部分字段。平台应保留原始 Schema，在执行入口重新校验，不依赖模型可见 Schema 完成约束。[固定 MCP 文档](https://github.com/google-gemini/gemini-cli/blob/v0.68.0/docs/tools/mcp-server.md)

MCP 2026-07-28 删除 initialize/initialized 握手及协议级会话，Streamable HTTP 不再使用 Mcp-Session-Id。版本和能力进入每请求的 _meta，并通过 server/discover 发现服务信息。[版本变更](https://modelcontextprotocol.io/specification/2026-07-28/changelog)

该版本的 HTTP 交互采用 POST 请求及其 JSON 或 SSE 响应，不应继续按旧版 GET 消息流及 Last-Event-ID 设计恢复。stdio 也需要按该版本的请求约定实现。[版本变更](https://modelcontextprotocol.io/specification/2026-07-28/changelog) · [stdio 传输](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio)

授权需校验受保护服务的 audience 与 scope，不能透传不适用于目标服务的 token。工具 annotations 是不可信提示，Roots 也不承担强制访问控制。[授权规范](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization) · [工具规范](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) · [版本变更](https://modelcontextprotocol.io/specification/2026-07-28/changelog)

**设计推导。** 2026-07-28 与 2025-11-25 采用独立版本适配器。平台 Session、身份与审计生命周期独立于 MCP 协议会话。上述 CLI 的具体版本与新版协议互操作尚未验证。

| 接入对象 | 建议契约 |
| --- | --- |
| MCP server | 协议版本、transport、能力、身份、凭证引用、超时、原始 Schema 及发现快照 |
| Tool | 命名空间、输入输出、资源范围、幂等及副作用说明、策略版本 |
| Skill | 内容摘要、来源与依赖，声明可能调用的能力，执行时仍逐调用鉴权 |
| RAG tool | 返回证据引用、资料版本、权限校验结果及缺口，不能只返回无法追溯的文本 |
| External Agent | 输入任务与消息、能力声明、进度事件、终态、产物和取消映射 |

A2A AgentCard 描述能力，任务与消息存在不同响应路径。不支持 streaming 的服务应返回 UnsupportedOperationError，不能假定每个外部 Agent 都能提供流式状态。[A2A 官方规范](https://github.com/a2aproject/A2A/blob/main/docs/specification.md)

**设计推导。** 外部 Agent 纳管分别记录能力声明和实际验证。取消超时、未知终态、重复产物及消息直返均有显式适配结果，不能把 HTTP 请求完成当作业务任务完成。

## MySQL 版本、事务与字节证据

**观察。** 官方手册将 9.7 标为 LTS，同时单列 8.4 LTS 文档，并提醒跨版本存在功能差异。[9.7 手册](https://dev.mysql.com/doc/refman/9.7/en/preface.html) · [发行模式](https://dev.mysql.com/doc/refman/9.7/en/mysql-releases.html)

**设计推导。** 核心数据库采用 MySQL，拟首选 9.7 LTS，SQL 与数据模型尽量保持 8.4 特性兼容。上线前固定实际发行版、补丁号、字符集、时区、隔离级别、驱动和备份方式，并分别执行兼容验收。

MySQL 9.7 的 DISTANCE() 只提供给 MySQL HeatWave on OCI 和 MySQL AI，官方明确不包含在 Commercial 或 Community 发行版中。不能由 VECTOR 类型或函数文档推导社区版已具备所需向量检索能力。[向量函数范围](https://dev.mysql.com/doc/refman/9.7/en/vector-functions.html)

| 官方事实 | 对 AgentOS 的设计推导 |
| --- | --- |
| SKIP LOCKED 返回跳过已锁定行的视图，适合队列式访问。 [锁定读取](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html) | 可用于领取队列任务，不用于要求完整一致判断的预算、权限或余额校验 |
| Atomic DDL 不等于可放入业务事务的事务性 DDL，DDL 可能隐式提交。 [Atomic DDL](https://dev.mysql.com/doc/refman/8.4/en/atomic-ddl.html) | Schema 迁移独立编排，逐步验收和恢复，不能承诺一条事务整体回退 |
| JSON 入库会规范化，重复键等表示细节可能变化。 [JSON 类型](https://dev.mysql.com/doc/refman/8.4/en/json.html) | 精确输入摘要基于已保存的规范化字节或原始载荷，禁止从 JSON 列重新序列化后假定字节一致 |
| ngram 全文解析器支持中文等连续文本的分词。 [ngram](https://dev.mysql.com/doc/refman/8.4/en/fulltext-search-ngram.html) | 作为中文词法检索基线，验证切分粒度、专名、代码与混合语言效果 |

## 检索方案与数据职责

OpenSearch 混合检索通过 search pipeline 合并关键词和语义结果，可采用分数归一化或排名融合。过滤后的 k-NN 可能按引擎及条件选择近似或精确搜索，权限过滤规模也会影响延迟。[混合检索](https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/index/) · [k-NN 过滤](https://docs.opensearch.org/latest/vector-search/filter-search-knn/efficient-knn-filtering/)

Qdrant Query API 支持预取和稀疏、稠密等结果融合，可作为同一检索接口的替代实现。[官方 Hybrid Queries](https://qdrant.tech/documentation/search/hybrid-queries/)

| 候选 | 建议用途 | 选择前必须测量 |
| --- | --- | --- |
| MySQL ngram | 首个词法基线及低复杂度部署候选 | 中文词法召回、语义改写缺口、查询成本和更新延迟 |
| MySQL + OpenSearch | 默认混合检索候选，统一比较关键词与向量效果 | 过滤后 Recall@k、p95、精确检索回退、运维及同步成本 |
| MySQL + Qdrant | 可替换的向量及混合检索候选 | 稀疏与稠密配置、过滤召回、写入及资源成本 |
| MySQL + 对象存储 | 所有方案共同保留的权威状态与大载荷分工 | 元数据和对象不一致、孤儿对象、删除及备份恢复 |

**设计推导。** OpenSearch 是试点评测默认候选，MySQL ngram 是对照基线，Qdrant 是替代候选。没有实测证据支持其中任一方案在本项目上最优。

| 数据 | 权威位置 | 一致性与恢复约定 |
| --- | --- | --- |
| 身份、权限、组件版本、任务、预算、审批 | MySQL InnoDB | 原子提交、并发约束、审计和恢复 |
| 会话事件、调用与检查点元数据 | MySQL InnoDB | 序号、幂等、租约和状态转换约束 |
| 原文、精确模型输入、大日志、多模态产物 | 对象存储，MySQL 保存引用与摘要 | 上传状态、完整性校验、访问控制和保留策略 |
| 全文、向量与派生摘要索引 | 可重建外置检索 | 来源版本、索引版本、删除标记和重建进度 |
| 待传播变更 | MySQL Outbox | 与业务状态同事务提交，消费者幂等处理 |

**设计推导。** 检索先携带租户和资源过滤，候选返回后按 MySQL 当前权威状态再次校验授权、来源版本及撤回标记，再读取正文和组装上下文。索引中的旧 ACL 不得成为最终授权依据。

索引停机时可以按策略退回词法基线或返回检索不可用，必须报告召回能力变化。权限校验不可用时阻止内容投递。对象上传失败、消费乱序和重复事件均保留可恢复状态。

## 验证实验与任务映射

以下为待实施实验，无已通过结论。保持同资料、同模型池和同预算，报告失败、覆盖率、成本及任务级配对统计，最终验收集与优化器隔离。

| 实验 | 对照或故障注入 | 观察结果 | 任务 |
| --- | --- | --- | --- |
| 数据库并发与迁移 | 两个 worker 竞争租约、预算并发预留、DDL 中断 | 无重复执行和超支，迁移恢复证据，版本兼容结果 | TASK-013 |
| 沙箱一致性 | 后端缺失、越界路径、网络拒绝、进程未退出 | 不静默放宽，enforcement 与实际结果一致，资源清理 | TASK-014 |
| 围栏与人工接续 | 子权限扩大、参数变更、审批超时、中断后重跑 | 拒绝生效，审批不能复用到其他动作，副作用不重复 | TASK-015 |
| RAG 质量与权限 | 三种检索候选、中文改写、严格过滤、撤权和旧索引 | Recall@k、证据覆盖、p95、费用、越权投递数 | TASK-016 |
| MCP 与技能 | 两版协议、Schema 漂移、伪造 annotations、token audience 错误 | 适配结果明确，执行入口验证，能力未扩大 | TASK-017 |
| 编码修改与恢复 | 基线漂移、补丁部分成功、测试改写、检查点缺失 | 可识别实际改动，失败不误报完成，用户修改保留 | TASK-018 |
| 多模态与外部 Agent | 非流式返回、未知终态、重复产物、取消超时 | 状态映射、产物回链及权限一致，事件可追踪 | TASK-019 |
| 上下文及协作 | 原文、摘要、代码图及按需检索，同预算单/多 Agent | 任务成功率、关键事实遗漏、全部分支 Token 与时延 | TASK-005、TASK-007、TASK-010 |

源码机制进入设计时，还需同步组件清单、错误语义、状态转换与验收案例。较旧项目的维护状态、滚动文档与标签差异、MCP 互操作，以及各沙箱的实际约束范围列为选型验证项。

当前已完成资料核对与机制比较。数据库部署、插件开发、沙箱执行、协议互操作、检索评测与软件验收均未开展。

