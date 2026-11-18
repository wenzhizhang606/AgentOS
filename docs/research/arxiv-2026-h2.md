# AgentOS 2026 年下半年 arXiv 调研

论文纳入窗口为 **2026-07-01 至 2026-11-16**，按 arXiv 首次提交时间判断。元数据于 2026-11-17 核对，正文采用下表固定版本，均在截止日前提交。本文综合团队对全文方法、实验和局限的阅读，聚焦上下文管理、分层记忆、多 Agent、模块组合及受控递归改进。

当前交付为设计研究。以下数字均为作者报告，尚未在 AgentOS 复现，不能作为企业部署的预期收益。工程推导用于形成候选设计和验收实验，完成状态以[实施计划](../plan/implementation-plan.md)为准。本文不主张覆盖该时期所有相关论文。

## 论文与版本索引

| 编号 | 论文 | 首次提交 | 阅读版本 | 关联任务 |
| --- | --- | --- | --- | --- |
| P01 | [ACM: Agentic Context Management for Long Horizon Tasks](https://arxiv.org/abs/2607.23809) | 2026-07-26 | v1，2026-07-26 | TASK-005、TASK-006 |
| P02 | [Harness the Memory: A Holistic Evaluation of Memory Substrates in Memory Agents](https://arxiv.org/abs/2608.15008) | 2026-08-15 | v2，2026-11-04 | TASK-006、TASK-010 |
| P03 | [Just-In-Time Agent Memory with Runtime Agentic Research](https://arxiv.org/abs/2609.34385) | 2026-09-28 | v1，2026-09-28 | TASK-011 |
| P04 | [MemCodex: Self-Programming Hierarchical Memory for Language Agents](https://arxiv.org/abs/2609.39765) | 2026-09-30 | v1，2026-09-30 | TASK-006、TASK-008、TASK-012 |
| P05 | [Rethinking Multi-Agent Collaboration: When More Is Less](https://arxiv.org/abs/2609.19759) | 2026-09-17 | v2，2026-09-18 | TASK-005、TASK-007、TASK-010 |
| P06 | [PluginRSI: Recursive Improvement of Agent Harnesses with Reusable Plugins](https://arxiv.org/abs/2609.32423) | 2026-09-26 | v2，2026-10-18 | TASK-008、TASK-010、TASK-012 |
| P07 | [Harness²: Recursive Agent Harnessing for an Open World](https://arxiv.org/abs/2610.14224) | 2026-10-09 | v1，2026-10-09 | TASK-006、TASK-010、TASK-012 |
| P08 | [EvoUndo: Recoverability-Constrained Self-Evolution for LLM Agent Harnesses](https://arxiv.org/abs/2608.28363) | 2026-08-28 | v2，2026-09-16 | TASK-008、TASK-010、TASK-012 |
| P09 | [HarnessOpt-Bench: Evaluating LLMs at Harness Optimization](https://arxiv.org/abs/2608.06301) | 2026-08-06 | v1，2026-08-06 | TASK-010、TASK-012 |
| P10 | [Evo-Bench: Can Language Models Improve Agent Harness?](https://arxiv.org/abs/2608.09096) | 2026-08-10 | v2，2026-08-11 | TASK-010、TASK-012 |

## P01 ACM

**方法与证据。** `manage_context` 将历史原文卸载并留下摘要，`query_memory` 按需取回，后训练学习压缩与调用时机。作者在 BrowseComp-Plus、DeepSearchQA、SWE-bench Verified 等任务评估，主要证据来自 Qwen3.5-9B。小模型实验也出现两轮内停止、完全不调用记忆的行为，表明接口可用和模型会用需要分别评估。

**边界。** 文中的 lossless 指原文仍被保存，无法据此保证后续检索必然找回关键证据。结果依赖模型训练与任务设置，不能直接推导任意模型接入即可受益。定位为 §3、§5、§6、Limitations 与 Appendix D。[固定版本全文](https://arxiv.org/html/2607.23809v1)

**工程推导。** TASK-005 保存压缩条目到原文的引用，TASK-006 提供授权后的回取。验收分别测原文可解析率、关键证据找回率、压缩后任务成功率及回取成本，加入模型不调用记忆的失败案例。

## P02 HarnessMemory

**方法与证据。** 作者以统一 Harness 比较 11 种记忆机制、3 个模型和 26 个指标，拆开写入、读取与管理开销。问答、行动和代码任务的优选机制不同，支持按任务检验记忆配置。主要 Agent 实验涉及 134/148 任务，采用单次贪心运行。

**边界。** MemGPT、Mem0、Zep 未进入主要同变量比较，部分机制为近似实现。regime-level 路由收益为样本内结果，LoCoMo 查询路由仅在训练见过的对话所含问题上取得增益，未证明对新对话泛化。不能把榜单解释为跨企业场景的通用排序。定位为 §3–5、Appendix B/D/E/H。[固定版本全文](https://arxiv.org/html/2608.15008v2) · [作者代码](https://github.com/Jasonya/SubMem)，本项目未运行。

**工程推导。** TASK-006 保留可替换的存储和读写策略。TASK-010 分任务类型比较同模型、同资料与同预算配置，并统计离线构建、在线读取和维护的完整成本。

## P03 JAM

**方法与证据。** 系统保存完整会话、memo 和目录，由 Researcher 按请求执行 open、search、browse，组织运行时证据。训练使用 SFT 与 Hint-guided GRPO。在 LoCoMo 的层级存储消融中，F1 从 49.06 升至 52.09，在线 Token 从 8174 降至 6721，仍另有离线处理成本。

**边界。** 实验包括 LoCoMo 等记忆任务，部分训练与基线配置不完全相同。研究循环最多 20 轮，内部“信息充分”判断不能保证证据完整。上述消融也不证明每个企业任务都需要独立 Researcher。定位为 §2–4、Appendix B.3–B.5。[固定版本全文](https://arxiv.org/html/2609.34385v1)

**工程推导。** TASK-011 将证据研究器作为增强上下文策略，在普通检索暴露缺口时启用。它返回来源、证据和未解决问题，并受轮数、延迟和费用限制。对照一次检索与迭代检索的成功率及总成本。

## P04 MemCodex

**方法与证据。** 记忆分为技能、图、摘要和原始历史，并保留 source turn。系统围绕 admit、index、propose、score、route 等固定接口生成修改，每个候选改写一层的一个方法，或启用、停用一层。作者报告四个基准的 Qwen 平均分为 60.8，MemPro 为 55.2，差值为 5.6 个百分点，相对增幅约 10.1%。

**边界。** v1 标为 Work in progress。演化与测试主要按问题划分，不能替代对全新记录的隔离检验。平均收益也不能证明各层记忆或每个自编程方法分别有效。定位为 §2–4，重点为 §4.3。[固定版本全文](https://arxiv.org/html/2609.39765v1)

**工程推导。** TASK-006 建立分层表示与来源关系，TASK-008 固定方法接口和状态兼容性，TASK-012 每次产生小范围候选。评测同时按问题和原始记录隔离，并检验跨领域迁移及旧记忆读取。

## P05 SAIGE

**方法与证据。** SAIGE 按执行中发现的依赖创建子 Agent，隔离工作上下文并按需获取结果。实验采用 DeepSeek-V4-Pro，默认最多 4 个 Agent、递归深度 1。Terminal Bench 2.1 成绩为 75.50，单 Agent 为 76.71。AgentIF-OneDay 分别为 71.80 和 53.57，关闭按需获取后降至 66.22。

**边界。** 理论分析依赖正确切分假设，实际依赖标签也使用模型判断。Deep Research 的单 Agent 分数在主表为 51.49、附表为 51.94，存在未解释差异，不采用该差值立论。结果支持按依赖结构试验协作，不能推广为 Agent 越多收益越大。定位为 §3–5、Tables 1–3、Appendix B.3.3。[固定版本全文](https://arxiv.org/html/2609.19759v2)

**工程推导。** TASK-005/007 实现私有上下文、结果引用和有界动态委派。TASK-010 用相同总预算比较单 Agent、固定拓扑和动态协作，统计全部分支 Token、协调等待、死锁及重复工作。

## P06 PluginRSI

**方法与证据。** Harness 分为工作流和标准接口插件。固定其他部分变异单个插件，筛选后写入共享库，再组合并评测完整 Harness。SWE-bench Verified 使用 400 个优化任务、100 个保留任务。在 GPT-5.6 Terra 设置中，保留任务解决率为 65%，Meta-Harness 为 55%。每轮包含 560 次 solver rollout。

**边界。** 实验使用固定任务分布与有限迭代。跨领域迁移收益变化，单插件变异可能漏掉必须共同修改多个插件的机制。结果未解决长期插件库增长与维护问题。定位为 §3、§4.1 与 Table 1、Appendix D/E/J。[固定版本全文](https://arxiv.org/html/2609.32423v2)

**工程推导。** TASK-008 分别版本化插件和组合配方，TASK-012 保存候选父版本及改动证据，TASK-010 同时评测单模块替换和完整组合回归。优化预算包含变异、筛选、重组与最终检验。

## P07 Harness²

**方法与证据。** 通过 Propose–Probe–Compose 比较候选执行，再组合有证据支持的修改。任务结束后保存采用的 Harness、经验与适用条件。三个基准、四种执行配置报告三次运行均值和标准差。WorkBuddy 的 GLM-5/OpenCode 平均分由 64.2 升至 75.7，支持探索经验驱动的 Harness 改进。

**边界。** 行为对比仍受模型共同偏差和运行随机性影响，无法提供任务正确性保证。经验去重、过时处理和长期管理尚待验证。主表 `#Steps` 只表示执行器轮数，完整费用需包括改进过程。定位为 §2.1–2.3、§3.1、Tables 1/3、Appendix B、Limitations。[固定版本全文](https://arxiv.org/html/2610.14224v1)

**工程推导。** TASK-006 保存带证据与适用条件的经验，TASK-012 记录候选差异、探测和采用理由，TASK-010 以独立保留集决定正式晋升，并计入全部推理、编辑和选择成本。

## P08 EvoUndo

**方法与证据。** 为变更定义前向操作、前态见证、恢复程序和效果契约，在多个反事实状态检验恢复。600 个任务中有 197 个候选改善能力却恢复失败。另 300 个选择性撤销任务中，效果范围快照在不同表面修改下成功 300/300，EvoUndo 为 243/300。同一表面不同元素修改下为 159/300 对 131/300。

**边界。** 该比较没有 EvoUndo 独胜任务。第三方 API、非托管进程和分布式外部状态未建模，报告的回退延迟不含程序合成。结果支持可直接恢复状态下采用效果范围快照。定位为 §2–4、§8、Appendix D.2–D.4。[固定版本全文](https://arxiv.org/html/2608.28363v2)

**工程推导。** TASK-008 优先实现确定性恢复并声明影响范围。TASK-010 测试不同初始状态和后续合法修改保留。TASK-012 将恢复验证纳入候选晋升，外部动作补偿按业务规则单独设计。

## P09 HarnessOpt-Bench

**方法与证据。** 在固定目标模型、环境和判分器下比较 5 个优化器、4 类任务，共 111 次运行。开发集暴露详细反馈，验证集提供聚合结果，测试集对优化器不可见。候选冻结后，在隔离沙箱与预算条件下测试，相关比较采用三次重复。

**边界。** 优化器自身成本被计量但未设上限。GAIA 的弱种子配置限制收益解释，最高验证分数也可能偏乐观，无法单独区分选择过拟合与分布差异。正文调用预算出现 100/200 的不一致，本文不据此设置项目门槛。定位为 §3.1、§3.2、§5.3。[固定版本全文](https://arxiv.org/html/2608.06301v1)

**工程推导。** TASK-010 固定数据、预算、判分和候选摘要，最终验收集与优化器身份隔离。TASK-012 同时限制候选生成和候选执行费用。选择有效候选后再解封独立测试，报告全部试验和失败。

## P10 Evo-Bench

**方法与证据。** 固定 policy，在 Search、Office、General 上比较 9 个模型的 Harness 改进。单次演化后，Search/Office 使用单 rollout，General 使用三次。收益跨领域不均，后续迭代可能退化。作者最优演化 overall 为 46.3，仍低于人工组合的 47.5。

**边界。** 数据先筛选 Harness 敏感任务，排除敏感度不大于零的任务和多模态任务，不能代表自然业务分布。图中成本只计 evolver，不能视为整个系统的费用。定位为 §3–6、§8。[固定版本全文](https://arxiv.org/html/2608.09096v2)

**工程推导。** TASK-010 保留普通任务、难任务与敏感任务的分层结果，采用人工组合和固定 Harness 作基线。TASK-012 记录每轮退化、停止原因和总费用，最终发布依据保留集表现，避免只选择展示最高分迭代。

## 跨论文的设计与验证安排

以下均为工程推导。具体指标公式和目标以[需求基线](../requirements/agentos-platform.md)为准，案例以[验收规范](../specs/acceptance.md)为准，本文只规定研究启发的对照方向。

| 设计问题 | 首轮对照 | 必须保留的证据 | 任务 |
| --- | --- | --- | --- |
| 压缩和回取是否保留关键事实 | 固定窗口、摘要、摘要加原文回取 | 原始证据标签、实际 ContextSnapshot、遗漏及回取成本 | TASK-005、TASK-006 |
| 多层记忆是否值得构建 | 原始历史、普通 RAG、分层记忆 | 新记录隔离、来源链、读写和维护费用 | TASK-006、TASK-010 |
| 复杂问题是否需要迭代研究 | 一次检索、有限轮研究器 | 检索缺口、每轮增量证据、停止原因和完整成本 | TASK-011 |
| 多 Agent 是否产生协同收益 | 单 Agent、固定拓扑、动态协作 | 全任务树预算、私有上下文、交接结果与失败归因 | TASK-007、TASK-010 |
| 插件组合能否稳定改进 | 固定 Harness、人工组合、自动候选 | 模块版本、状态兼容、组合回归、效果范围及恢复结果 | TASK-008、TASK-012 |
| 改进是否迁移到未知任务 | 开发集筛选后冻结候选，再运行独立验收 | 数据划分、候选摘要、全部搜索费用、覆盖率和配对统计 | TASK-010、TASK-012 |

研究机制进入实际运行前，依赖 TASK-001 的规范工具链、TASK-002 的授权与来源、TASK-003 的基础运行与预算、TASK-004 的 RAG 以及 TASK-009 的观测。来源保存、共享记忆、经验库和协作结果的读取遵循同一授权语义。论文中的回取、共享或自动改进能力不能替代这些基础约束。

当前仅完成文献核对和设计映射。尚未执行论文代码、模型训练、基准复现、兼容迁移或恢复试验，也没有产生 AgentOS 软件验收结果。
