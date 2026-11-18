# AgentOS 语言选型、需求复核与 CubeSandbox 调研

2026 年 11 月 17 日。依据团队已熟悉 C++、Python 的反馈，复核现有需求与实施边界。本轮只修改文档；没有安装沙箱、生成应用代码或运行性能实验。官方滚动文档按本日核查，不等于已经固定生产依赖版本。

## 建议与依据

建议采用 **Python 主平台 + 按需接入的 C++ 模块**。先让 Python 完成可测的业务闭环；已有成熟原生库可以直接复用。只有剖析确认计算热点或已有本地资产需要时，才开发新的 C++ 组件。平台的主要复杂度在上下文策略、状态、外部工具和治理语义，换成更快的语言不会自动解决这些问题。

MySQL 是已确认约束，保留。上下文核心、多 Agent、全部 Agent 类型和基础组件、可插拔与受控 RSI 均保留。修订重点是区分产品目标、实现方式、交付批次和证据要求。需求已更新到[第五版](../requirements/agentos-platform.md)，实现分工见[技术设计](../design/technical-design.md)，案例与计划分别见[验收](../specs/acceptance.md)和[实施计划](../plan/implementation-plan.md)。

## 从任务本身推导语言分工

一次任务的时间可拆为排队、授权与存储、上下文准备、模型等待、工具执行及交付。选语言应改善实际瓶颈，同时控制开发和运维成本。举例：若固定工作负载有80%的关键路径时间花在外部服务，单独把其余20%的本地计算无限加速，理想总加速上限也只有1.25倍。这是串行比例的算术示例，不是本项目测量。

| 方案 | 与本项目的匹配 | 当前选择 |
| --- | --- | --- |
| Python 主平台，复用已有原生库 | 上下文、记忆、评测及协议适配可共享现有Python实现，团队已有经验 | 起始方案 |
| Python主平台 + 少量C++模块 | 保留策略迭代速度，可复用本地资产或优化已确认热点；承担边界与发布成本 | 达到采用条件后扩展 |
| C++主平台 + Python策略服务 | 能实现服务与数据库接入，但会把高频变化的策略与平台之间变成更多跨语言接口 | 只有已有大型C++平台资产或实测约束支持时再考虑 |
| 再引入Go/Rust/Java等主后端 | 本轮没有团队或负载证据支持增加第三套后端维护责任 | 不列为当前建设任务 |

C++并非不能写服务或连接MySQL，官方有[Connector/C++](https://dev.mysql.com/doc/connector-cpp/26.10/en/)。选择Python是本项目的工程取舍：研究与评测可以复用[LangGraph](https://github.com/langchain-ai/langgraph)和[Ragas](https://github.com/vibrantlabsai/ragas)等现有实现，平台契约仍由自身定义。核查时[MCP官方SDK清单](https://modelcontextprotocol.io/docs/2026-07-28/sdk)包含Python而未列C++；这不代表不存在社区C++ SDK。协议网关放Python可避免重复维护两套MCP适配，SDK声明仍须逐版本兼容测试。[Python SDK v2说明](https://py.sdk.modelcontextprotocol.io/whats-new/)

| 模块 | 默认归属 | 不应移交的责任 |
| --- | --- | --- |
| 身份/策略、发布、任务、根预算、MySQL事务 | Python | 平台权威状态不能由C++组件另建一套 |
| 上下文编译、RAG配方、记忆修订、协作、评测和RSI | Python | 来源、权限、候选隔离与验收口径保持统一 |
| 模型、MCP、连接器与I/O调度 | Python异步worker | 有界并发、调用超时和真实取消需明确 |
| 批量计算、代码/媒体处理热点、已有C++库 | C++候选 | 不直接扩大权限、写账本或重试外部副作用 |
| 设备/OS接口、复杂原生依赖、需独立终止的工作 | 独立C++进程或已有服务 | 通过统一执行入口取得权限，按实际执行环境认证 |

Python API与长任务worker分进程，主控服务不运行不可信代码。FastAPI只是API候选，其后台任务机制不被当作持久调度引擎。[FastAPI后台任务边界](https://fastapi.tiangolo.com/tutorial/background-tasks/)

## Python / C++ 接口与采用门槛

| 边界 | 适用条件 | 必须验证 |
| --- | --- | --- |
| pybind11进程内绑定；nanobind备选 | 可信、稳定、有界的批量计算，调用频繁且复制成本可控 | GIL、所有权、线程安全、ABI、缓冲生命周期及worker崩溃范围 |
| 独立进程 + RPC/受控IPC | 需独立重启、强制截止、资源限制，或处理不可信代码和复杂解析 | 序列化、精度、取消、背压、迟到结果、进程回收与重复副作用 |
| 已有服务/原生库的Python接口 | 现有实现已满足用途，如检索计算或本地推理 | 实际许可证、版本、数据范围及接口能力，不重复封装无价值层 |

pybind11默认不释放GIL；显式释放也不会使同步调用自动让出所在asyncio事件循环，需使用有界executor或独立进程。这是两种执行模型组合后的工程判断。Python有可选free-threaded构建，但扩展可能重新启用GIL，初期不把该模式当作性能或正确性前提。[pybind11 GIL](https://pybind11.readthedocs.io/en/stable/advanced/misc.html#global-interpreter-lock-gil) · [asyncio阻塞代码](https://docs.python.org/3.14/library/asyncio-dev.html#running-blocking-code) · [CPython free-threading](https://docs.python.org/3.14/howto/free-threading-python.html)

线程池仅用于已证明长计算区间释放GIL且不访问Python对象的可信扩展；否则独立进程。需要单任务强取消时使用独立可终止单元，不能承诺杀掉共享worker中的一个线程即可安全恢复。

绑定不是隔离机制：nanobind文档给出所有权错误导致解释器崩溃的示例。普通STL自动转换也可能复制数据，不应把绑定宣传成天然零复制。采用批量调用和明确生命周期，避免逐Token跨语言调用。[nanobind所有权](https://nanobind.readthedocs.io/en/latest/ownership.html) · [pybind11 STL转换](https://pybind11.readthedocs.io/en/stable/advanced/cast/stl.html)

正在运行的线程任务不能依赖`Future.cancel()`强制停止，RPC取消也需要服务端协作。硬截止由独立控制器及执行环境保证。跨语言Schema固定整数金额、时间、版本、deadline、操作ID和取消代次；ProtoJSON的int64采用十进制字符串，不能经浮点转换丢精度。[Python Future取消](https://docs.python.org/3.14/library/concurrent.futures.html#concurrent.futures.Future.cancel) · [gRPC取消](https://grpc.io/docs/guides/cancellation/) · [ProtoJSON](https://protobuf.dev/programming-guides/json/)

建议新C++组件以C++20为项目基线，固定编译器/CMake/绑定版本，先验证目标Linux环境；这是团队维护选择，不是性能结论。Python固定受支持且依赖验证通过的常规CPython版本。静态类型或Python类型标注都不能代替外部请求的Schema、范围和权限验证。[Python typing边界](https://docs.python.org/3.14/library/typing.html)

引入一个新C++模块应提交：剖析中的热点占比、现有库为何不满足、固定输入下的正确性或误差范围、含复制与排队的端到端收益、CPU/内存和吞吐、构建发布及故障处理成本。需求SLO已达成且没有显著成本收益时，保留较简单的实现。没有证据时不设置武断的语言占比。

## 原需求中需要修正的边界

下表区分已存在的合理目标与过强或含混的实现承诺。修改属于本项目的设计推导，来源提供能力或方法依据，并不直接证明本方案性能最优。

| 原问题 | 第一性原理判断 | 已落地修订 |
| --- | --- | --- |
| P0同时指基础必备与所有类型首期完成 | 产品范围与排期是两个维度；当前没有人力/周期证据支持同时成熟 | 保留28项REQ和10类矩阵；P0跨M1–M3交付，Profile分planned/experimental/certified/unavailable |
| 统一上下文入口容易变成中心同步服务 | 必须统一信息与治理语义，计算位置由数据和延迟决定 | Context Service可嵌入worker；管理面故障与权威准入故障分开处理 |
| 每步重跑RAG或逐模块远程调用 | 必要约束、直接引用、业务查询与文件读取不都需要向量召回 | 流水线按需；M1一个真实证据路径即可，完整RAG与独立接口继续交付 |
| 首期各两实现及任意组合的隐含成本 | 替换证据有价值，重复实现数量不是业务成果 | 两实现目标保留到基础平台完成；组件契约与实际发布组合分别验收 |
| 每次完整传输字节永久可回放 | 可追溯、可审计、可重建是不同能力；存在敏感数据和保留期限 | 强制清单/语义哈希/发送绑定，full/redacted/metadata_only分开，字节诊断可选 |
| 记忆召回被误当当前事实 | 相似度决定相关性，权威来源决定业务事实 | 分信息类别、权威来源和有效时间；接口不可用时历史记忆不提升为当前事实 |
| 自建全部持久runtime成为默认前提 | 平台独有价值是上下文与治理，图执行和暂停可先核验复用 | TASK-001先比较复用，平台与引擎明确状态所有权、检查点和唯一重试责任 |
| 账本原子预留被误认为实际费用有上界 | 账本正确与供应商计费可限制是两个命题 | hard_budget须附用量/价格/租期/释放假设和故障证据，实际账单单独核对 |
| 安全停止与业务恢复共用成功率 | 正确拒绝未知外部动作可能使业务未完成，但符合安全契约 | 预定义故障安全处理逐例通过；可恢复故障业务恢复初始目标仍≥95% |
| 200任务×3运行看起来足够证明2个百分点非劣 | 重复运行相关；样本量取决于差异方差、功效与分层 | 独立pilot、任务簇统计、主指标与停止规则预登记，证据不足为inconclusive |

集中治理与就近决策参考[OPA部署说明](https://www.openpolicyagent.org/docs/deploy)，但其[策略包更新](https://www.openpolicyagent.org/docs/management-bundles)不能替代本项目严格撤权。最终准入仍检查MySQL的当前权威状态，不能用减少网络调用为理由改成旧缓存放行。

工作流与Agent的适用边界参考[Anthropic工程经验](https://www.anthropic.com/research/building-effective-agents)，该旧文已提示具体工具变化，因此这里只采用概念区分。上下文按需读取参考其[上下文工程文章](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents)。固定流程可包含模型步骤，模型输出有不确定性不意味着所有控制流程都必须动态生成。

运行时复用参考[LangGraph持久化](https://docs.langchain.com/oss/python/langgraph/persistence)与[故障语义](https://docs.langchain.com/oss/python/langgraph/fault-tolerance)。存在检查点不代表外部副作用恰好一次，也不能从概览推断已有受维护的MySQL适配器。若引入Temporal，必须核验其[持久化支持矩阵](https://docs.temporal.io/temporal-service/persistence)与项目实际MySQL版本；本轮不新增数据库、不预先锁定该引擎。

组合认证参考[NIST组合测试方法](https://csrc.nist.gov/Projects/automated-combinatorial-testing-for-software/software-testing-methodology/dos-and-don-ts-of-testing)：两两覆盖不能作为所有交互均正确的证明。输入内容留存参考[OpenTelemetry GenAI规范](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/client-inference.md)，内容捕获与普通运行元数据分开；AgentOS仍强制记录受控来源与外发绑定。

## 量化指标的使用方式

原有召回、证据覆盖、业务成功率、Token/成本降幅和P95初始目标保留为待验证建议。评估分三层：确定性不变量逐例通过，业务质量/费用/延迟按Profile检验，诊断指标解释原因。首期不把未测得的目标写成已有能力。

200个独立二元结果、成功率85%时，普通正态近似95%区间半宽约4.95个百分点；这只是样本量级示例，不能用于证明配对差异。配对单次结果若不一致比例约10%，在真实差异接近0的近似下，仅使差异区间半宽约2个百分点就约需961对，尚未计功效与聚类影响。最终以独立pilot的方差估计为准。[NIST比例区间](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm) · [样本量与功效](https://www.itl.nist.gov/div898/handbook/prc/section2/prc242.htm)

最终集解封前固定样本、主要指标、−2个百分点非劣界、多重比较和停止规则。三次重复不变成三倍独立用户任务。零越权仍是固定专项集的观察值；攻击夹具不是随机生产样本，不能套二项区间声称生产风险为零。

语言与部署决策另采集平台额外P50/P95、关键路径CPU占比、序列化/复制耗时、每任务跨服务次数与字节数、内存峰值和故障恢复结果。这些作为选型诊断，不在没有基线时硬造统一门槛。

## CubeSandbox 固定版本核查

用户提出将CubeSandbox作为沙箱参考。准确项目为腾讯云 **TencentCloud/CubeSandbox**，本轮固定 **v0.8.0，2026-11-12，commit `41748bb78d6384962b4e417ff9215720ea7472fd`**。核查官方文档与部分Python SDK源码，未部署或执行攻击/恢复实验。[发布版本](https://github.com/TencentCloud/CubeSandbox/releases/tag/v0.8.0) · [固定提交](https://github.com/TencentCloud/CubeSandbox/commit/41748bb78d6384962b4e417ff9215720ea7472fd)

建议将其列为 **Linux microVM执行池的优先验证候选**，通过SandboxProvider接入。复用其执行、文件、快照、暂停恢复和网络设施；平台仍持有业务身份、绝对截止、预算、审批、幂等、恢复范围和清理核对。它的内部实现语言不决定AgentOS主平台语言。

| 能力 | 固定版已核查事实 | AgentOS采用边界 |
| --- | --- | --- |
| 隔离与部署 | RustVMM/KVM、独立guest kernel；Linux计算节点，原生KVM支持x86_64/aarch64 | 独立Linux池；Windows工作站作为远程客户端，不承诺本机原生执行 |
| 普通云VM的PVM路径 | 需要专用宿主内核，当前仅x86_64 | 与原生KVM部署分别认证，不把它视作普通容器安装 |
| SDK与执行 | 官方Python SDK，代码/命令/PTY/文件和流式输出；v0.8加入fork等API | Python适配器直接对接；E2B兼容需按实际方法与版本验证 |
| 生命周期 | running/pausing/paused/resuming/terminated/unknown | 保留原状态，unknown既不等于已销毁也不等于成功，须核对 |
| timeout | 闲置TTL，活动刷新；无硬wall-clock ceiling，默认值可为−1 | 外置绝对deadline与执行侧终止机制；心跳和auto-resume不得续命 |
| full / fs-only快照 | full保存内存与根文件系统；fs-only不保存内存且不支持原地rollback | 明确模式与范围；整机回滚不能直接承诺保留合法后续文件写入 |
| 外部Volume | 记录引用和挂载，不复制卷数据，clone可共享可写卷 | 克隆隔离验收必须包含外卷，不能把共享卷视作独立工作区 |
| 跨节点恢复 | xfs本地后端不支持；jfs/s3需remote_status=ready并检查CPU/kernel条件 | 不是live migration；同节点恢复不代表灾备；preview能力单独标记 |
| 预览范围 | JuiceFS后端及K8s部署路径仍标preview | 另测资源压力驱逐、节点升级、CNI/PSA兼容，不扩展为所有部署路径同等成熟 |
| 网络 | 默认允许公网，L3/L4和L7分层；网络PUT为替换语义 | 显式完整默认拒绝策略，禁止局部更新意外恢复公网；模板与请求先计算授权交集 |
| L7代理 | 只覆盖匹配HTTP(S)流量；WebSocket仅检查升级握手，audit=full仍为metadata | 其他协议/端口/帧按真实能力限制，不能替代完整内容审计和全部通道治理 |
| 鉴权与管理接口 | 未设置AUTH_CALLBACK_URL时CubeAPI允许请求；部分管理接口无认证/TLS | 私有管理网络、凭据和回调失败关闭；平台另做租户/资源/审批内容授权 |
| fork/clone清理 | fork允许部分成功；clone失败清理在SDK内best-effort | 持久化每个子资源ID、独立回收与周期核对；返回失败不等于无孤儿资源 |
| 配额与成本 | 暂停释放CPU/内存，磁盘/快照等仍存在 | 暂停不是零总成本；未核实可替代平台货币预占与结算的原生契约 |
| 许可证 | 主项目Apache-2.0，LICENSE列第三方例外 | 固定发行物清单；不把全部依赖描述为同一许可证 |

上述隔离、部署与SDK事实来自[README](https://github.com/TencentCloud/CubeSandbox/blob/v0.8.0/README.md)、[快速部署](https://github.com/TencentCloud/CubeSandbox/blob/v0.8.0/docs/guide/quickstart.md)、[平台依赖](https://github.com/TencentCloud/CubeSandbox/blob/v0.8.0/deploy/one-click/README.md)和[Python SDK](https://github.com/TencentCloud/CubeSandbox/blob/v0.8.0/sdk/python/README.md)。项目自报的启动时间和内存数字没有转作AgentOS承诺，仍需指定镜像、节点、缓存及并发实测。

生命周期与状态边界见[闲置TTL](https://github.com/TencentCloud/CubeSandbox/blob/v0.8.0/docs/guide/lifecycle.md)；工作区范围见[快照/克隆](https://github.com/TencentCloud/CubeSandbox/blob/v0.8.0/docs/guide/snapshot-rollback-clone.md)、[外部卷](https://github.com/TencentCloud/CubeSandbox/blob/v0.8.0/docs/guide/volume-plugin.md)和[跨节点条件](https://github.com/TencentCloud/CubeSandbox/blob/v0.8.0/docs/guide/cross-node-snapshot/requirements.md)。

网络与鉴权边界见[网络策略](https://github.com/TencentCloud/CubeSandbox/blob/v0.8.0/docs/guide/network-policy.md)、[安全代理](https://github.com/TencentCloud/CubeSandbox/blob/v0.8.0/docs/guide/security-proxy.md)及[管理面加固](https://github.com/TencentCloud/CubeSandbox/blob/v0.8.0/docs/guide/network-hardening.md)。清理结论来自[固定SDK源码](https://github.com/TencentCloud/CubeSandbox/blob/v0.8.0/sdk/python/cubesandbox/sandbox.py)，许可证来自[固定LICENSE](https://github.com/TencentCloud/CubeSandbox/blob/v0.8.0/LICENSE)。

### 候选接入与验证顺序

1. 冻结Linux/KVM或PVM路径、镜像、CubeSandbox版本和能力，管理面只向受控平台开放；provider内部数据库按独立Schema/凭据管理，通过API对接，不直接修改其内部状态表。
2. 实现Workspace与provider ID/状态映射、执行/文件/产物、绝对截止及独立清理控制器。Cube闲置TTL作为辅助回收，无法证明硬截止的组合不认证hard_deadline或hard_budget。
3. 生成完整网络策略，验证默认拒绝、模板覆盖、DNS/IP、非HTTP和WebSocket范围，以及回调不可达和管理面隔离。
4. 验证full/fs-only、共享卷、并发后续写入、跨节点恢复；恢复后重新派生凭据和租约，旧快照内的授权不得复活。
5. 注入create响应丢失、fork部分成功、clone清理失败、节点失联、pause/delete并发及自动恢复晚到请求。核对VM、卷、快照、凭据和实际费用，保留未知状态至查明。
6. 与另一个满足同Profile隔离要求的候选在同镜像/负载下比较启动、暂停恢复、执行、回收P50/P95、失败率、资源占用与总成本，再决定默认后端。

对应REQ-EXE-001、REQ-GOV-002、REQ-OPS-001、REQ-CODE-001，验收归入AC-020、AC-021、AC-028并补AC-011/AC-026恢复案例，任务归入TASK-014/015/018。当前CubeSandbox及其他沙箱实现均未获得项目认证。

两项额外集成条件不能省略。首先，create响应丢失时平台可能还没有provider ID，必须在发送前记录operation并验证可关联发现资源的机制；未知时不能盲重建或释放预占。其次，full恢复会继续旧内存中的进程，必须在恢复前由VM外控制器完成任务/租约/策略检查并封锁直连、出站及共享写入，在隔离状态更新凭据后才放行。CubeSandbox是否提供足够的原生钩子尚未验证；适配器无法建立该边界时，不认证安全full恢复，改评估新隔离工作区的fs-only路径。

网络更新还存在跨层收敛问题：固定文档说明先更新CubeEgress，再更新CubeVS，最后写持久状态；Cubelet重启加载最后持久成功的策略。AgentOS应保存期望代次，收紧时先隔离受影响出站，部分成功或回执未知时保持隔离并核对，不能把重试收敛解释为原子撤权。[网络更新语义](https://github.com/TencentCloud/CubeSandbox/blob/v0.8.0/docs/guide/network-policy.md#updating-the-policy-of-a-running-sandbox) · [K8s预览限制](https://github.com/TencentCloud/CubeSandbox/blob/v0.8.0/docs/guide/kubernetes/index.md)

## 尚不能由文档解决的问题

团队人数、首个真实场景、身份源、Linux执行池条件、并发/数据规模和外部模型合同仍需在试点固定；它们影响交付周期和SLO。文档没有证据证明Python一定满足目标，也没有证据证明必须用C++重写。下一步应在获授权实施后用同一纵向切片测量，再按本报告门槛决策。
