# AgentOS 组件契约规范

2026 年 11 月 17 日 · 设计基线 · 所有接口、能力和验收均待实现

本规范定义各类 Agent 共用组件的行为边界，不表示已提供对应 SDK、服务或隔离保证。任务状态、身份、幂等、租约、根预算、事件和未知外部副作用复用[运行契约](runtime-contracts.md)，本文不另建 Task 状态机。需求与测试分别见[需求基线](../requirements/agentos-platform.md)和[验收规范](acceptance.md)。

MySQL 保存组件目录、授权、运行和审计的权威元数据；媒体、工作区载荷和检索索引通过受控引用关联。MySQL 恢复及事件投递归入 REQ-DATA-001 / AC-017 / TASK-013；异步调度与资源配额归入 REQ-OPS-001 / AC-028，由运行契约统一规定。

## 需求、验收与任务映射

| 契约范围 | 需求 | 验收 | 实施任务 |
| --- | --- | --- | --- |
| Agent 能力 Profile | REQ-AGT-001 | AC-018 | TASK-019，复用 TASK-008 |
| 多模态与实时事件 | REQ-IO-001 | AC-019 | TASK-019 |
| 沙箱与工作空间 | REQ-EXE-001 | AC-020 | TASK-014 |
| 围栏与执行策略 | REQ-GOV-002 | AC-021 | TASK-015 |
| MCP 双向接口 | REQ-TOOL-001 | AC-022 | TASK-017 |
| Tool、Skill 与 Prompt | REQ-TOOL-002 | AC-023 | TASK-017 |
| RAG 服务与内部流水线 | REQ-RAG-002 | AC-024 | TASK-016，复用 TASK-004 |
| 人工澄清、审批与接管 | REQ-HITL-001 | AC-025 | TASK-015 |
| 编码工作流 | REQ-CODE-001 | AC-026 | TASK-018 |
| 外部 Agent 与框架接入 | REQ-INT-001 | AC-027 | TASK-019 |

## 公共组件调用契约

| 项目 | 必需语义 |
| --- | --- |
| 输入封装 | 服务端身份引用、根任务与步骤、operation_id、组件版本、规范化 payload_digest、deadline_at、预算账户、取消代次；有状态写入另带预期版本和幂等键 |
| 输出封装 | 操作引用、实际组件版本、受控结果/产物引用、来源及内容摘要、用量、执行证据、明确错误；长操作返回已受理和查询引用，不能据此宣布已完成 |
| 能力宣告 | 输入输出模态、Schema、streaming、取消强度、幂等、可执行费用上限、隔离后端、平台限制、状态兼容性和可观测/可控制范围 |
| 能力授予 | 清单是申请值，执行范围取企业策略、身份授权、Profile、组件和执行环境支持范围的交集；不支持的组合在发布或调用前拒绝 |
| 错误与重试 | 使用运行契约错误封装，补充本文件组件错误；retryable 不授权重复副作用，未知结果进入原 operation_id 的核对流程 |
| 副作用 | 声明 read、workspace_write、external_write 或 mixed，以及目标范围、去重能力和补偿能力；插件声明须由登记和运行证据验证 |
| 取消与预算 | 执行前复核取消、租约、权限和预算；取消阻止新动作，已发生费用照常结算；远端不支持终止时记录不可终止及核对责任 |

## AgentProfile 与能力组合

Profile 是 AgentVersion 的可验证能力需求，不改变用户、Agent 或工具的身份。必填 profile_id、version、输入/输出模态、模型要求、组件依赖、工具集、上下文/记忆策略、交互方式、执行环境、授权上限、预算规则和代表验收集。

每个版本和环境另有 `availability=planned / experimental / certified / unavailable`、认证清单及报告引用。只有已完成该组合全部必需案例的 certified 版本可供生产选择；隔离实验不能把自身结果当作生产认证。平台类型矩阵覆盖与单个 Profile 能力通过率分别计算，未实现类型保持可见。

运行时适配器可以复用框架的循环、图执行、暂停和内部检查点。平台 Task、授权、取消、预算和外部操作账本仍由 MySQL 权威管理；一个逻辑动作只有一个重试责任方。适配器声明引擎运行ID/检查点ID与平台ID映射、持久化后端、恢复顺序和核对方式。框架恢复后每次真实外发继续走平台准入，checkpoint不能回退账本或墓碑，不能因节点重放重复业务写入。

| 能力组合 | 必须声明的能力与可观察结果 |
| --- | --- |
| 对话、知识与客服 | 会话、RAG、精确业务查询、证据输出和转人工；回答记录实际输入及引用 |
| 研究、规划与长任务 | 搜索/浏览、计划、有界证据补取、持久运行和报告；缺口、剩余预算及恢复位置可查询 |
| 编码、数据分析与计算 | 仓库/数据范围、文件/SQL/命令工具、隔离、测试与产物；输出补丁、查询或计算证据 |
| 浏览器、桌面与设备 | 执行器类型、观察/动作接口、会话隔离、屏幕或设备范围、接管与副作用核对；声明远程可控边界 |
| 企业流程与多 Agent | 业务工具、人工门禁、委派、共享证据、幂等及补偿；每个分支沿用根权限与根预算 |
| 长期事件与实时多模态 | 触发器、状态保留、音视频流、背压、打断和预算周期；事件游标及处理缺口可见 |
| 外部专业 Agent | 协议、能力和状态映射、外部身份、治理覆盖等级；不能把登记成功视为具备全部控制能力 |

validate_profile 接受 Profile 与候选环境，返回能力交集、缺项及兼容报告；resolve_profile 为任务冻结组合版本；inspect_capabilities 返回调用方可见清单。新增类型复用这些接口并补充代表案例；发布验证失败返回 CAPABILITY_UNSUPPORTED，运行中能力消失停止受影响调用并重新路由，禁止静默移除必需隔离或模态。

## 多模态输入、产物与双向流

| 对象/操作 | 输入 | 输出、状态及约束 |
| --- | --- | --- |
| ContentPart | text/structured/file/image/audio/video、MIME、编码、大小、内容摘要、来源、权限及载荷引用；音视频另带时基/时长和采样信息 | 文本可内联，媒体按额度存储并引用；模型适配前校验真实类型、尺寸与模型能力 |
| Artifact | 内容引用、生成操作、类型、摘要、保留期限、来源/派生关系 | staging → available；删除为 retracted，再按保留规则 purge；未完成或撤回载荷不可作为正式结果 |
| open_stream | 身份、任务、输入/输出模态、codec、时基、缓冲与速率上限、截止时间 | stream_id、stream_epoch、协商参数、可恢复游标和取消方式；不支持编码返回 MEDIA_UNSUPPORTED |
| append_input | stream_id/epoch、分片序号、时间戳、摘要、内容或引用、幂等键 | 接收确认和剩余流量额度；序号重复去重，摘要冲突拒绝，超额返回 BACKPRESSURE |
| read_events | stream_id、游标、可接受模态与额度 | 增量内容、工具提议、产物、usage、缺口或结束事件；不同事件类型独立标记，不把文本增量当执行指令 |
| close_input / cancel_stream | 流身份、epoch、预期版本和取消原因 | 关闭输入可继续输出；取消使后续动作失去准入资格，返回已收敛或仍待外部核对的范围 |

流事件沿用 event_id 和任务 sequence，另记录 stream_id、stream_epoch、chunk_index、media_timestamp、operation_id。接收确认只表示已受理分片。断线重连仅从可回放游标恢复已持久化事件；无法恢复时返回 STREAM_GAP，不能伪造连续音视频。实时丢帧仅在 Profile 明确允许的模态与策略内发生，并产生缺口事件；控制与审批事件不得静默丢弃。

背压同时约束消息数、字节、媒体时长、待处理帧和费用；超限暂停接收或有界终止。音频打断递增流取消代次并关联当前操作，旧输出不得启动新工具动作。增量输出也经过围栏；必须完整检查的内容先缓冲再发送，无法在延迟预算内完成时返回部分不可用状态。已发出的内容不能通过取消收回。

受撤权和墓碑约束的媒体、快照及产物通过鉴权代理读取，代理从MySQL检查当前状态后流式返回，不能向调用者发放可复用且绕过后续授权的对象存储直读URL。内部暂存上传可使用受限签名，发布仍经权威校验。已准入的下载与已发出字节按在途边界处理，后续读取重新鉴权。模型外发、转写、视觉识别和存储均计入预算与处理范围；媒体URL经出站策略获取。

## Workspace 与 Sandbox

Workspace 保存 workspace_id、tenant/project、来源仓库/数据引用及基线摘要、所有者、lease_epoch、挂载清单、sandbox_profile_id、checkpoint_refs 和清理进度。状态为 provisioning、ready、quarantined、releasing、released；失败可进入 quarantined，未完成清理不得复用给其他身份。

| 接口 | 输入 | 输出、失败及副作用 |
| --- | --- | --- |
| create_workspace | 授权来源、基线 revision、隔离要求、磁盘/计算额度、保留规则 | 独立工作空间及环境证明；准备副作用仅限授予范围；无法满足隔离返回 SANDBOX_UNAVAILABLE |
| inspect_workspace | 身份、workspace_id | 有效挂载、读写/网络范围、后端及镜像摘要、租约和清理状态；不暴露凭据 |
| execute | operation、argv 或明确 shell、cwd、输入引用、环境变量白名单、执行限额、输出额度 | 进程引用、退出码/信号、stdout/stderr 产物、资源用量和实际策略；非零退出码与启动失败分开记录 |
| checkpoint / restore | 身份、workspace、预期版本、明确路径/文件集合和基线摘要 | 检查点与恢复报告；恢复先比较现有修改，冲突返回 WORKSPACE_CONFLICT，保留合法后续写入 |
| terminate / release | operation/workspace、租约、原因、保留引用 | 停止进程树、撤销凭据/挂载、回收资源并记录残留；超时隔离资源，不能假报已清理 |

SandboxProfile 明确文件读取与写入范围、不可变保护路径、用户/进程权限、可执行能力、网络目的地/协议/端口、可用凭据、CPU/内存/GPU/磁盘/进程数、执行时长和输出上限。cwd、Git worktree、容器名称和 MCP roots 不能充当隔离证明。路径检查处理规范化、符号链接、大小写和竞态，执行器以系统约束阻止实际越界。

受限执行的后端、策略加载或关键控制点不可用时拒绝执行；不得自动切换为宿主进程或无隔离模式。单次扩权绑定操作与目标，仍受企业硬限制；扩权失效后不能被后续调用沿用。审批通过不能撤销强制的拒绝读取规则。

沙箱执行器由模型进程之外的控制器持有lease_epoch与硬到期时间，旧租约不能执行或续期。worker失联时控制器独立到期断开新网络动作并终止进程树，不能依赖已失联worker自行退出。续期需重新检查任务状态、控制面身份和预算，沙箱内代码无续期权限。控制器失效的后端须有外层TTL回收或进入隔离核对，未确认释放前不复用且持续记录费用。清理使用运行契约的必要维护通道，不随业务任务取消。

网络控制覆盖命令及子进程、模型/embedding/rerank 请求、MCP、HTTP 工具、RAG connector、浏览器/桌面、媒体获取、遥测和外部 Agent。分别记录执行通道与策略执行点；域名解析、重定向、目标 IP/端口和本地/内网访问都要核验。仅设置命令代理不能宣称覆盖其他通道；远端内部出站无法强制控制时明确限制，所需强制能力不满足的 Profile 拒绝发布。

凭据按租户、主体、服务与任务授予，使用秘密引用和短期分发；不把宿主环境、数据库管理凭据或容器管理 socket 默认挂入工作区。环境清理、日志脱敏和任务终止后的凭据撤销必须验收。

工作区检查点只恢复声明的文件与执行环境状态；MySQL 恢复处理数据库事务、事件和权威记录；外部业务写入采用领域补偿与核对。三者有独立范围、权限和结果，禁止以 git reset 或数据库回档宣称撤销全部副作用。隔离与审批分层参考[Codex 固定版实现](https://github.com/openai/codex/blob/rust-v0.169.0/codex-rs/core/src/tools/sandboxing.rs)及[Gemini CLI 沙箱](https://github.com/google-gemini/gemini-cli/blob/v0.68.0/docs/cli/sandbox.md)；本项目不继承其默认隔离强度。

### CubeSandbox候选适配

固定候选v0.8.0，状态planned。保存provider sandbox/snapshot/volume ID、原始生命周期、镜像及部署摘要，映射到平台Workspace；provider内部存储与平台MySQL权威表分开，通过API交互。只有明确确认终止及资源释放才能标released，unknown保持quarantined或核对中。

闲置TTL不映射为硬租约。平台绝对deadline、lease_epoch和取消规则由沙箱外控制器及执行侧终止措施落实；禁止绕过平台的自动恢复和续期。控制面失联且无执行侧到期停止能力时，不认证hard_deadline/hard_budget。快照模式、外部卷共享、挂载和跨节点兼容逐项声明；恢复不得复活旧凭据、审批或租约，文件范围恢复仍需CAS或新隔离工作区验证。

网络配置必须由平台计算完整授权结果后整体下发，禁止用户传入部分配置覆盖平台限制。验证默认公网关闭、管理网络隔离、回调鉴权、L3/L4与L7实际覆盖；WebSocket帧、未覆盖协议或端口不能宣称受L7内容检查。Cube代理metadata审计不替代平台语义输入快照。创建/克隆响应丢失及部分失败进入持久资源核对，清理独立于SDK进程生存期。[固定版本研究与来源](../research/language-and-requirements-review.md)

create/fork发送前持久化operation和资源意图，并验证provider侧可关联发现资源的机制；响应丢失时sandbox ID可能尚未知，不能只靠已知ID清理，也不能盲目重新创建。无法确定结果时保留预占、标记unknown并进入独立发现/核对，关联机制未验证的组合不承诺创建幂等。

full恢复可能立即继续旧进程。恢复前必须由VM外控制器重新核对任务、deadline、lease和策略，先阻断直连CubeProxy、出站及可写共享资源，再在隔离状态完成凭据与挂载处理后放行。不得在旧内存已经恢复执行后才做准入检查。无法提供这种恢复门禁的后端不认证安全full恢复，可另评估fs-only在新隔离工作区启动的路径。

网络策略更新不是跨CubeEgress、CubeVS与持久状态的原子事务。平台保存期望策略代次与完整配置摘要，策略收紧先隔离受影响出站，确认各层及持久状态一致后再开放。中断、回执未知或Cubelet重启时重新核对，不能把最后一次HTTP受理当成全面生效，也不能让节点恢复旧策略后自动重新获得运行资格。不能建立该边界的组合不认证严格动态撤权。

## Guardrail 与执行门禁

| 检查点 | 输入 | 输出与执行约束 |
| --- | --- | --- |
| 输入与外部资料 | 内容、来源、信任类型、目标 Profile | 风险标签、过滤/隔离建议及理由；外部文本不能提升为平台指令 |
| 上下文与模型外发 | ContextSnapshot、接收服务、授权版本与处理范围 | 允许、拒绝、需审批或转换后的新快照；修改内容须重新计算摘要、预算并检查来源 |
| 模型输出与流片段 | 原始输出、目标受众、模态和预算 | 放行/缓冲/阻断、脱敏结果及证据；被拒内容不进入工具准入 |
| 工具与动作准入 | 规范化参数、真实目标、权限、操作摘要、审批与预算 | PolicyDecision 和可执行范围；在执行前同步复核，文本计划不授予权限 |
| 结果投递与产物 | 结果引用、目标用户/Agent/服务、当前授权 | 允许投递、删除敏感片段或拒绝；向另一 Agent 分享也必须经过本检查点 |

GuardrailDecision 包含 decision_id、检查点、检查器/策略版本、输入摘要、结论 allow/deny/review/transform、理由类别和证据引用。检测器可插拔，权限、取消、租约和预算硬检查不可被检测器、Skill 或模型结果覆盖。检测超时按 Profile 记录的策略处理；强制检查不可用返回 POLICY_UNAVAILABLE 并拒绝执行，不能默认为允许。

策略采用明确优先级和硬拒绝语义；企业硬拒绝不能被用户规则覆盖。发布时检查冲突，运行记录命中规则与实际判定。工具隐藏仅减少暴露面，实际调用仍鉴权。命令列表或正则匹配只作策略输入，复杂 shell 和参数展开仍须受执行隔离。机制参考[Codex 规则](https://learn.chatgpt.com/docs/agent-configuration/rules)与[Gemini 策略文档](https://github.com/google-gemini/gemini-cli/blob/v0.68.0/docs/reference/policy-engine.md)，项目的优先级不得由插件自行修改。

## HITL：澄清、审批与接管

| 操作 | 输入 | 输出与约束 |
| --- | --- | --- |
| request_input | task/step、问题、受控证据、回答 Schema、到期时间 | 持久化 interaction_id；澄清回答更新任务输入或版本，不能自动成为动作审批 |
| request_approval | operation_id、payload_digest、目标和副作用范围、policy_version、expiry、审批角色 | 可检查的待审批记录；模型只提交申请，无权替代审批人决定 |
| decide_approval | interaction_id、审批人身份、decision、expected_version | CAS 写入批准/拒绝与审计；重复相同决定幂等，竞争或内容变化返回 APPROVAL_STALE |
| consume_approval | operation、摘要、当前策略、期限、任务取消代次及预期审批版本 | 执行前重鉴权并 CAS 消耗批准，绑定一次逻辑操作；不满足条件不启动动作 |
| takeover / return_control | 授权人、任务/工作空间、范围、期限、预期控制版本 | 交接记录和控制租约；旧执行器失去动作准入，交回后重新准备上下文及权限 |

审批记录使用 pending、approved、denied、expired、cancelled、consumed；仅 pending 可经 CAS 批准/拒绝，approved 执行前再经 CAS 消耗；pending/approved 均可因到期或任务取消经 CAS 失效。批准绑定租户、发起人/执行身份、operation_id、规范化 payload_digest、目标及副作用范围、policy_version、expiry 和审批角色；任一绑定变化都需重新判定，过期批准不得恢复为有效。

首个发送尝试将approved变为consumed，并记录consumed_by_operation_id。后续允许的重试不再次消耗记录，只能引用同一业务操作、同摘要、绑定未变且仍在有效期内的consumed批准，同时重新检查当前权限、租约、取消与预算。记录处于consumed不代表永久有效；过期或绑定变化必须申请新批准，不得复用给另一个操作。

消耗批准与最终DispatchAttempt准入记录在同一事务中完成。崩溃后恢复原逻辑操作并查询尝试记录；对不支持幂等的外部写操作，结果未知时禁止凭已消耗批准盲目重发。重试保留业务操作身份但创建新的发送尝试和费用预留，并复核当前授权。最终准入到网络发送的间隙按运行契约的在途边界处理。

人工等待复用 Task.waiting 与持久唤醒条件；等待占用的资源和已发生费用按账本处理，不凭等待延长截止时间。无交互通道时按 Profile 显式排队等待或返回 APPROVAL_REQUIRED；不能自动批准。接管前停止新动作并核对在途副作用，接管结束不抹除人工操作历史。

## RAG 对外服务与内部接口

RAG 是独立可调用能力；通过 HTTP、进程内适配或 MCP 暴露时采用同一权限、配额和证据语义。它提供知识接入和证据，不绕过业务系统的实时权威查询；普通检索与迭代证据研究器分别声明成本和能力。

| 外部接口 | 输入 | 输出、状态与失败 |
| --- | --- | --- |
| POST /v1/rag/ingestions | 授权数据源/对象引用、目标集合、来源版本、解析/切分策略、预期版本、幂等键 | 202 和 ingestion/operation 引用；返回受理不代表已可检索；越界 connector 或无上界载荷拒绝 |
| GET /v1/rag/ingestions/{id} | 当前身份与 ingestion_id | accepted、processing、ready、failed、cancelled，以及阶段、索引代次、失败项与费用；ready 仅表示指定版本已发布 |
| POST /v1/rag/queries | query 或多模态引用、集合范围、过滤、top_k、证据/时间/费用上限、检索策略版本 | query_id、证据列表、分数类型、来源版本、缺口/冲突、实际策略和用量；P0查询当前已发布版本，过滤只能缩小授权 |
| POST /v1/rag/operations/{id}/cancel | 当前身份、接入或查询操作、预期版本、取消原因及幂等键 | 映射统一操作取消；停止后续阶段并报告已发布版本、在途外部处理和已发生费用，不撤销既有权威修订 |
| GET /v1/rag/evidence/{id} | 证据 ID、请求内容版本、读取目的 | 当前授权的原文片段、文档位置及派生链；显式历史读取可返回仍获保留的superseded版本并标历史，retracted或墓碑一律不可读 |
| POST /v1/rag/sources/{id}/retract | 来源版本、撤回范围、预期版本和幂等键 | 原子撤回与清理操作引用；读取立即检查权威状态，索引/缓存异步清理进度可查询 |

ingestion 的状态只表示组件作业，不替代承载它的 Task 状态；取消使用运行契约的操作取消机制。失败重试沿用同一逻辑接入操作，新来源版本产生新代次。检索结果状态为 complete、partial 或 no_evidence，partial 必须列出超时或缺失阶段；无证据是正常结果，不能编造引用。

P0不定义含糊的as_of查询参数。语义检索使用当前发布清单，历史证据按明确ID和版本读取并检查当前ACL、墓碑与保留策略，缓存键包含版本和读取目的。历史版本不能冒充当前事实。按历史业务有效时间检索或按当时已知状态回放若需开放，必须另定义时间维度、历史索引与能力声明；记忆接口的业务有效时间语义不自动扩展到RAG。

| 内部可替换接口 | 输入 | 输出与不变量 |
| --- | --- | --- |
| connect / fetch_source | 受控 connector、资源、身份和版本条件 | 带摘要的来源载荷与权限元数据；获取网络、解压、文件大小和重定向均受限 |
| parse | 来源载荷、格式及解析器版本 | 文本/表格/媒体段、结构与原文定位；失败保留诊断，不执行文档内脚本 |
| chunk | 结构化段、切分策略及预算 | 有稳定来源坐标、内容版本和 ACL 引用的片段；重叠与父子关系可追踪 |
| embed / index | 授权片段、模型/维度/距离规则、索引代次 | embedding 及索引发布引用；不同模型/维度不混用，发布前复核来源当前版本 |
| retrieve / fuse | 查询、服务端权限范围、索引版本、过滤和预算 | 各路候选、原始分数及融合策略；按授权过滤并在返回前复核，禁止先泄露再删结果 |
| rerank | 候选引用、查询、重排器及目的地 | 有界排序与用量；外部重排先验证数据可外发范围，失败按显式降级策略记录 |
| resolve_evidence | 候选、原文版本、当前身份 | 可进入 ContextSnapshot 的证据载荷、来源、引用及失效状态；只有链接不等于已取得证据 |

EvidenceBundle 至少包含 document_id/version、chunk_id、来源定位、payload_digest、有效时间、授权修订、检索/重排版本和分数含义。检索缓存按身份范围、策略版本、查询及数据代次分区。外置索引先写不可变暂存代次，再按运行契约在MySQL条件发布清单，迟到写入不可复活旧数据。各阶段费用及失败尝试都归根预算。RAG内部可选向量后端不改变MySQL的权威状态地位。

## MCP 客户端接入与中台对外 Server

MCPAdapter 声明 inbound/outbound 方向、协议版本、transport、能力、端点或进程摘要、认证方式、凭据引用、工具目录摘要、超时/输出上限和副作用策略。MCP 提供协议接入；OAuth、工具注解或 roots 均不代替平台授权与沙箱。

| 协议维度 | 2025-11-25 兼容适配 | 2026-07-28 适配 |
| --- | --- | --- |
| 初始化与能力 | initialize / notifications/initialized 协商版本与能力 | 每请求 _meta 携带版本和客户端能力；server/discover 返回支持版本、能力和身份 |
| HTTP 与状态 | Streamable HTTP 可有协议 session、GET SSE 及重连语义 | 单 POST 端点；无协议 session/GET 消息流；JSON 或请求关联 SSE，订阅使用 subscriptions/listen |
| 服务端请求 | 按协商能力处理独立 sampling/elicitation 等请求 | input_required / inputRequests，经原操作的重试提交 inputResponses；检查 resultType |
| 取消与断线 | HTTP 断线不自动表示取消，显式取消；按实际支持能力恢复 | HTTP 关闭 SSE 表示取消；无 Last-Event-ID 恢复；stdio 用 cancelled 通知，重试新 request ID |
| 任务与可选功能 | tasks 为该版可选实验能力，单独验收 | tasks 为可选扩展；roots/sampling 已弃用，新实现默认不启用 |

版本选择与 fallback 按各版规范执行，适配器保留原请求/响应版本和映射结果。2026 的 header/body 版本、Mcp-Method、Mcp-Name 一致性必须校验；工具目录缓存遵守 ttlMs/cacheScope 且按身份隔离。未知版本、能力缺项或无法无损映射返回 MCP_VERSION_UNSUPPORTED / CAPABILITY_UNSUPPORTED，不把现代鉴权失败误当旧协议进行降级。当前 SDK/CLI 支持矩阵待逐项实测，不能声称全部兼容。

| 方向/操作 | 输入与输出 | 授权、预算和副作用约束 |
| --- | --- | --- |
| 客户端 connect / discover | 已登记 server、版本/transport、凭据引用 → 可见目录与能力快照 | stdio 在声明的沙箱启动；HTTP 经过独立出站控制；发现权限和执行权限分别校验 |
| 客户端 invoke | 内部 server ID、tool、原始 schema 摘要、operation 和参数 → 经校验结果/产物 | 准入时重查目录、参数、身份及策略；目录变化阻止沿用旧批准，超时结果未知时按副作用核对 |
| 对外 tools/list / tools/call | 认证调用方、查询或工具参数 → 授权工具目录/结果 | 对外工具映射同一内部接口；不公开数据库管理工具或自动提升执行权限；每次调用计入该主体的配额和预算 |
| 对外 resources / prompts（可选） | list/read/templates 或 list/get → 授权资源与模板版本 | 未实现不宣告；资源读取、模板解析和嵌入引用分别鉴权；Prompt 内容不获得策略修改权限 |
| sampling / elicitation（可选） | 可信 server 的请求、能力、原操作及数据范围 → 拒绝或独立授权的交互结果 | 默认关闭；sampling 仅兼容已批准旧接入，模型调用独立计费；elicitation 独立检查来源、问题、期限与发送内容 |

受保护 HTTP 端点按 OAuth 规范发现授权服务器、使用 PKCE、最小 scope、resource/audience 校验和 issuer 绑定；401/403 不触发匿名重试。MCP 上游业务调用使用独立凭据，不透传入站 token。stdio 仅注入明确授予的环境变量。服务自报的只读/破坏性注解是不可信提示，工具身份使用内部登记 ID，不以 serverInfo.name 唯一区分。

启用 elicitation 时，表单不得索要密码/token；敏感认证通过已批准域名的站外交互完成。其用户回答不是本平台业务动作批准，需按 HITL 契约另行判定。嵌套请求限制深度、次数、等待时间和根预算，禁止引入未授权上下文或模型。远端显式 handle 每次重新授权并检查有效期；协议取消不撤销已提交的外部副作用。

版本依据为[MCP 2025 生命周期](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)、[2025 transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)、[2026 变更](https://modelcontextprotocol.io/specification/2026-07-28/changelog)、[HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)、[tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)及[授权](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)。可选交互依[elicitation](https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation)和[sampling](https://modelcontextprotocol.io/specification/2026-07-28/client/sampling)分别验收。

## Tool、Skill 与 Prompt 登记

| 操作 | 输入 | 输出、状态和约束 |
| --- | --- | --- |
| register / validate | 负责人、来源/许可证、不可变版本/摘要、local/http/mcp/skill/prompt 类型、依赖和能力 | draft → validated → published；不兼容、来源缺失或越权请求拒绝；停用为 disabled，不删除历史证据 |
| discover / resolve | 身份、Profile、版本及所需能力 | 获准可见的清单、原始 schema 与适配 schema；发现不会授予调用权 |
| execute_tool | operation、版本、参数与作用域 | 结果、来源、用量和副作用记录；输入/输出按原始 schema 校验，模型适配不能放宽执行校验 |
| load_skill / render_prompt | 身份、版本、参数、当前上下文预算 | 带来源/信任标签的内容及依赖引用；脚本执行另外进入工具和沙箱准入，不因加载 Skill 自动执行 |

发布清单声明依赖工具、网络目标、文件范围、凭据、审批需求、幂等与补偿能力。Skill/Prompt 无权修改企业策略、预算或审批人；供应链更新形成新版本并验证依赖，正在运行的任务仍使用冻结版本。工具输出受大小和 token 限额控制，截断保留原始产物引用及截断标记；输出超限和 schema 漂移分别返回 OUTPUT_LIMIT_EXCEEDED / TOOL_SCHEMA_CHANGED。

## 编码 Agent 的 patch、test 与 publish

| 阶段/接口 | 输入 | 输出和完成边界 |
| --- | --- | --- |
| prepare_repository | 授权仓库/commit、子模块/依赖策略、Workspace 要求 | 基线 manifest 与隔离工作区；仓库配置、hook、构建脚本视为待执行内容，不因 clone 自动运行 |
| inspect / propose_patch | 基线、授权文件、代码上下文与修改目标 | 来源引用及 PatchArtifact，包含 base revision、文件前置摘要、diff 摘要与目标范围 |
| apply_patch | workspace、patch 摘要、预期文件版本、operation | 逐文件/块结果、新摘要及 applied/partial/conflict 状态；基线不符返回 PATCH_BASE_MISMATCH，已成功部分保留可核对，不能将部分成功隐去 |
| run_checks | 固定命令、环境/依赖摘要、patch 摘要、资源与网络限额 | CheckArtifact：退出码、测试范围、日志、用量、跳过/失败项；启动测试不代表通过 |
| review / publish | 已冻结 patch/check、目标仓库/分支/PR、预期远端基线、审批引用 | 可审查差异或独立发布操作；本地编辑、commit、push、建 PR、merge、deploy 各自授权与计费 |

测试结果绑定实际代码树、命令与环境；补丁、依赖或测试配置改变后旧结果标为不适用。发布前再检查目标基线、授权和审批摘要；冲突返回 PUBLISH_CONFLICT。取消停止尚未准入的发布，已发送写操作继续核对。恢复工作区不自动撤销远端发布。工作区读取、命令执行、版本控制和测试证据分别纳入 AC-026。

## 外部 Agent 与框架适配

| 接口 | 输入 | 输出、状态与治理范围 |
| --- | --- | --- |
| register_adapter / describe | framework/protocol/version、endpoint、能力、身份方式、状态映射和执行证明 | 受管适配清单及兼容报告；A2A/ACP 为可选适配，具体版本与功能经测试后登记 |
| start / resume | 平台 operation、任务目标、受控输入引用、委派身份、预算/截止时间 | 外部 run handle 与平台关联；外部状态保留原值并映射到现有 Task，未识别状态不映射成功 |
| observe / fetch_result | handle、游标、当前授权 | 规范事件、结果/产物、实际用量与执行证据；外部“完成”须验证本任务完成条件 |
| cancel / reconcile | handle、取消代次、原 operation | 已终止/仅请求/不支持终止及在途副作用；不可控远端不能据超时直接宣布取消完成 |

治理覆盖按 registered、observable、enforced 分项声明到身份、上下文、工具、网络、费用、取消和副作用，不能用一个总开关掩盖缺项。外部 Agent 的内部模型调用或工具执行不可观测时记录 unknown，不能冒充零费用；无可执行硬预算或隔离的适配器不进入要求该能力的 Profile。返回内容一律保留外部来源标签并重新鉴权，不能直接成为内部执行指令。

## 组件错误与完成证据

| 错误类别 | 处理要求 |
| --- | --- |
| CAPABILITY_UNSUPPORTED、MCP_VERSION_UNSUPPORTED、MEDIA_UNSUPPORTED | 拒绝不支持组合，返回可披露的缺项；选择新适配器需重做能力/权限检查 |
| SANDBOX_UNAVAILABLE、POLICY_UNAVAILABLE、APPROVAL_REQUIRED、APPROVAL_STALE | 停止受影响动作，保留申请与诊断；不静默弱化隔离或跳过审批 |
| TOOL_SCHEMA_CHANGED、PATCH_BASE_MISMATCH、WORKSPACE_CONFLICT、PUBLISH_CONFLICT | 重新解析授权状态及基线，生成新候选或操作；旧批准不能跨摘要复用 |
| BACKPRESSURE、STREAM_GAP、OUTPUT_LIMIT_EXCEEDED | 返回可用游标/部分产物和缺口，按已声明策略恢复或结束，不能伪报完整结果 |

版本冲突、授权拒绝、租约丢失、取消、截止时间、预算不足与未知外部结果使用运行契约的统一错误和核对流程。组件验收必须覆盖正常入口、权限撤销、重复请求、并发 CAS、能力不支持、策略/隔离不可用、取消后迟到结果和费用归属。完成报告记录版本、环境、实际覆盖通道及未测试范围；文档通过不代表软件实现已通过 AC-018 至 AC-027。
