# M1 公开基准选型

2026-11-18 核对。首轮选用三个公开英语基准，分别检查候选检索、跨段证据引用和无依据拒答。两项问答均通过 AgentOS 的任务、上下文和模型网关调用 `gpt-6.1-sol`。检索项直接测词法检索器，无须模型调用或向量索引。本次状态为选型完成，尚无项目实测分数。[平台需求](../requirements/agentos-platform.md) · [AC-014 业务量化评测](../specs/acceptance.md#ac-014-业务量化评测)

| 基准及固定划分 | 官方指标与来源 | 拟接入 M1 的方式 |
| --- | --- | --- |
| BEIR · SciFact test | 使用 BEIR 分发的完整语料、300 条测试查询及文档相关性标注。主报 nDCG@10，辅报 Recall@10。[官方数据和运行示例](https://github.com/beir-cellar/beir#available-datasets) · [评分器](https://github.com/beir-cellar/beir/blob/main/beir/retrieval/evaluation.py) | 保留原文档 ID，用中台的词法检索与已配置的排序策略导出每个查询的有序候选。在窗口裁剪之前取数，记录实际 Top-K 与 Tool 上限；未命中查询纳入分母。 |
| HotpotQA · distractor dev | 每题给定十段材料。官方分别计算答案、支持事实及两者联合的 EM、F1。M1 首期主报 Answer F1，辅报 Answer EM。[官方数据](https://hotpotqa.github.io/) · [评分器](https://github.com/hotpotqa/hotpot/blob/master/hotpot_evaluate_v1.py) | 每题隔离十段材料，剥离答案中已校验的引用标签后评分。当前引用定位到来源，支持事实与 Joint 指标记为未评测。后续接入可逆的原句定位并取得模型实际预测的 `[标题, 原句序号]` 后，再报告句级与联合成绩。金标支持句仅由评分器读取。 |
| SQuAD 2.0 · dev | 官方按归一化答案计算 Exact Match 和词元 F1，可按 HasAns、NoAns 分组。无答案预测采用空字符串。[官方数据](https://rajpurkar.github.io/SQuAD-explorer/) · [评分器](https://github.com/rajpurkar/SQuAD-explorer/blob/master/evaluate-v2.0.py) | 每题只提供其指定段落。有效回答剥离已校验引用后保留回答原文。将平台明确的 `MISSING_EVIDENCE` 映射为空答案，并区分检索未命中和模型主动拒答。超时、预算耗尽和网关错误单独记录，避免技术失败得到拒答分。 |

## 比较口径

以当前 M1 发布配置为基线，候选版本使用相同语料、题目、模型、资源预算和确定性的答案转换规则。运行前冻结数据文件摘要、官方评分器提交、发布 ID、模型请求参数、RAG 参数和重复次数。公开开发划分只用于可复现比较，企业最终验收继续使用独立留出集。三个适配器需保留原始预测、题目 ID 与运行记录；官方评测前核对预测覆盖率，有缺失时标明“未完成”。平台另按全部计划题目计算端到端结果，技术失败记零，并报告费用与 P95 延迟。[评测规范](../specs/acceptance.md#ac-014-业务量化评测)
