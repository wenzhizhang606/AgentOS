# Agent 基准对照与优化实测

2026-11-18 核对。仓库修复用 SWE-Bench Pro V2 和 Terminal-Bench 衡量，团队增益参考 TeamBench-90。AgentOS 本轮在 BEIR SciFact 上完成同语料配对实验，并用六条 HumanEval 官方题验证经任务 API 生成代码、保留轨迹及独立判分的链路。[量化验收 AC-014](../specs/acceptance.md#ac-014-业务量化评测) · [实施计划](../plan/implementation-plan.md)

## 主流编码 Agent 公布成绩

SWE-Bench Pro V2 的 Full 视图以仓库任务解决率衡量模型与执行框架的组合。保留官方表中的推理档位和误差值。高分区间已较集中，后续实际优化也需要观察失败类型、每个成功任务的价格和运行时间。[Scale 官方 Full 榜](https://labs.scale.com/leaderboard/swe_bench_pro_public_v2?tab=full)

| 模型 | 执行框架 | 推理档位 | Full 解决率 |
| --- | --- | --- | --- |
| Opus 5 | Claude Code | xhigh | 99.40% ± 0.40 个百分点 |
| Gemini 4 Argon | mini-swe-agent | high | 99.22% ± 0.45 个百分点 |
| GPT-6 Astra | Codex | high | 96.90% ± 1.10 个百分点 |
| GPT-6.1 Sol | mini-swe-agent | xhigh | 96.11% ± 1.24 个百分点 |

Terminal-Bench 官方当前视图将下列结果标为 **4.1**。它以实际命令行任务的解决率衡量代码、工具及环境操作。差值随任务版本与运行限制变化，复现时应冻结选中的数据版本、执行框架版号、任务镜像和资源上限。[Terminal-Bench 官方榜](https://www.tbench.ai/) · [基准版本与复现方法](https://arxiv.org/abs/2611.08417)

| 模型 | 执行框架 | 推理档位 | 4.1 解决率 |
| --- | --- | --- | --- |
| Copilot Fusion | GitHub Copilot | max | 71.2% ± 2.9 个百分点 |
| Opus 5.5 | Claude Code | max | 65.8% ± 3.1 个百分点 |
| Gemini 4 Carbon | Antigravity CLI | high | 64.5% ± 3.2 个百分点 |
| GPT-6.2 Astra | Codex | max | 62.1% ± 2.3 个百分点 |
| GPT-6.1 Sol | Codex | max | 60.9% ± 3.0 个百分点 |

DeepSeek 官方另用同一个 V4.1-Flash 检查点控制变量，仅替换 Harness。在 DeepSWE v1.1 上，DSH Minimal 为 72.6%，Standard 为 70.5%，Claude Code 为 69.8%，Codex 为 65.6%，mini-SWE 为 74.2%。该表与上面的 Terminal-Bench 公开成绩各有独立模型和评分条件。AgentOS 将来在同一题目、模型及预算下替换单个组件，可沿用这种配对方法。[DeepSeek 官方技术报告表 4](https://arxiv.org/html/2609.19969v1#S5.SS3.SSS4)

AgentOS 本轮对同一 `gpt-6.1-sol` 发布运行固定六题 `HumanEval/0`、`/17`、`/31`、`/64`、`/113`、`/142`，每题一次推理，六条均通过单元测试。任务 API 提供函数签名和题目，测试和标准答案留在独立判分侧。每份程序在独立、无网络的 Linux 容器内评判，推理轨迹关联任务、快照和回执。这个 **6/6** 衡量六条公开 Python 函数题，适合检查生成与判分链路。仓库补丁、工作空间和终端操作的成绩待编码 Profile 及隔离执行实现后补测。[HumanEval 官方数据与评分定义](https://github.com/openai/human-eval) · [实际适配及冻结的数据版本](../../benchmarks/coding.py) · [编码 Profile 验收 AC-026](../specs/acceptance.md#ac-026-编码流程)

## 多 Agent 公开基准

TeamBench-90 的官方榜在 90 道题上比较同一模型的 Solo、Restricted、去掉 Planner、去掉 Verifier 和 Full Team。Planner、Executor、Verifier 分别拥有读规格、改工作区、验证的权限，权限由容器挂载落实。公开记录展示了分工的正收益和负收益，报告团队增益时应保留同题单 Agent 基线。[TeamBench 官方方法与榜单](https://teambench.github.io/#leaderboard)

| 模型 | Solo 通过率 | Full Team 通过率 | 增量 |
| --- | ---: | ---: | ---: |
| Claude Opus 4.7 | 35.6% | 37.8% | +2.2 个百分点 |
| Gemini 3.1 Pro | 27.8% | 28.9% | +1.1 个百分点 |
| GPT-5.4 | 12.2% | 27.8% | +15.6 个百分点 |
| GPT-5.4 Mini | 33.3% | 28.9% | −4.4 个百分点 |

现有 M1 可用两条独立的证据提取任务和一个汇总任务做编排实验。将它们与同一发布、同一题目的单任务流程配对，记录各阶段来源、正确率、实际费用、延迟和技术失败。应用层负责汇总与交接。原生委派、根预算传递、并行调度、取消传播及容器级权限对照按 TASK-007 单独验收。正式比较使用相同的根预算和资源上限，并按题报告增益及其区间。[协作实验入口](../../benchmarks/collaboration.py) · [多 Agent 实施与验收](../plan/implementation-plan.md#task-007-多-agent)

| AgentOS 实测流程 | 正确且引用完整 | 模型发送 | 四题累计平台费用 | 四题累计等待 |
| --- | ---: | ---: | ---: | ---: |
| 单 Agent 直接回答 | 4/4 | 4 | ¥0.019891 | 21.0 秒 |
| 两个独立分支提取后再汇总 | 4/4 | 12 | ¥0.057923 | 126.7 秒 |

使用同一 `gpt-6.1-sol` 发布，独立分支分别读取授权的原始材料，汇总保留对两条原始证据的引用。首轮判分将正确的 `VALUE=WAIT.` 误判。固定同一批回执后放宽答案末尾标点并新增回归用例，复算不增加模型调用。该样本中协作流程没有提高正确率，平台费用约为单任务的 2.91 倍。累计时间包含单 worker 的逐次排队，不能推导并行后的耗时。

六题编码与四对协作共发出 22 次请求，平台账本合计 ¥0.110008，无待结算。供应商回报输入 Token 比本地按字节估算多 2,400～3,342。在这 22 条记录中，128 Token 安全余量全部低于差值。窗口压力和费用的生产验收要保留此差异，用独立的长请求及高负载样本校准。私有回执位于部署目录 `local/benchmarks/bench-agentos-20261118-p01/`，入口使用独立测试凭证，并可从同一批回执复算。[真实评测入口](../../benchmarks/managed.py)

在 Linux 部署目录可以复跑两组真实试点。运行目录名应与既有回执区分，首次运行会产生模型用量。入口在每次提交前持久化请求与固定幂等键，同一目录和同一发布重入复用已有回执。

```bash
run=bench-agentos-pilot
python3 -m benchmarks.managed "$run" provision
curl -fL https://raw.githubusercontent.com/openai/human-eval/6d43fb980f9fee3c892a914eda09951f772ad10d/data/HumanEval.jsonl.gz -o local/benchmarks/HumanEval.jsonl.gz
docker compose run -d --rm --no-deps --name "$run" --publish 127.0.0.1:18001:8000 \
  --volume "$PWD/local/benchmarks/$run/credentials.json:/run/agentos/credentials.json:ro" api
curl -f http://127.0.0.1:18001/readyz
python3 -m benchmarks.managed "$run" live --humaneval local/benchmarks/HumanEval.jsonl.gz
docker stop "$run"
```

## SciFact 检索优化实验

现有词法排序按唯一命中词和逆文档频率打分，较长正文可能积累更多低价值命中，重复出现的关键术语也没有区分。新增 `bm25_search/1`，纳入词频饱和、长度归一化和标题增益。候选采用 `k1=0.9`、`b=0.4` 和标题词频权重 2。运行前固定参数，先跑从官方 train 按查询 ID 哈希排序取前 40 条的开发样本，再验证全部 300 条官方 test 查询。两个版本读取相同的 5,183 篇标题和摘要，调用同一证据 Tool，设置 Top-K=10、最小分数为 0，模型调用数为零。[实现](../../src/agentos/knowledge/bm25.py) · [BEIR 官方数据划分](https://github.com/beir-cellar/beir/wiki/Datasets-available) · [nDCG 原评分实现](https://github.com/usnistgov/trec_eval/blob/main/m_ndcg_cut.c)

| 划分及样本数 | 排序 | nDCG@10 | Recall@10 | 空排序 |
| --- | --- | ---: | ---: | ---: |
| train 抽样 40 | `lexical_search/1` | 0.6440 | 0.7750 | 0 |
| train 抽样 40 | `bm25_search/1` | 0.7189 | 0.8250 | 0 |
| test 全部 300 | `lexical_search/1` | 0.5656 | 0.7367 | 0 |
| test 全部 300 | `bm25_search/1` | 0.6636 | 0.7901 | 0 |

测试集 nDCG@10 提升 **0.0979**，配对 bootstrap 95% 区间 **[0.0692, 0.1289]**。Recall@10 提升 **0.0534**，区间 **[0.0193, 0.0918]**。Windows、Python 3.13.5、八个评测进程下，证据 Tool 加过滤的 P95 为 1,672 ms 和 1,652 ms，仅供本机同批诊断。BEIR 论文的同划分 Elasticsearch BM25 为 0.665，Anserini 的 Lucene BM25 复现为 0.6789。两项外部结果对应各自的分词与索引配置。[BEIR 论文表 2](https://arxiv.org/html/2104.08663#S5) · [Anserini 复现](https://github.com/castorini/anserini/blob/master/docs/reproduce/from-document-collection/beir-v1.0.0-scifact.flat.md)

默认适配器为 `lexical_search/1`，其实现摘要经核对与基线提交一致。在隔离发布中选用 `bm25_search/1` 时，应记录新的 Tool 实现摘要和发布 ID，并独立校准最小分数。复现本实验的排序时保持 RAG 标题和正文权重为 2 和 1。修改这两个权重会启用现有词法二次排序。SciFact 提供英文科学摘要，中文知识库和实际发送上下文的覆盖率需另用留出数据评测。[发布选择与摘要](../../src/agentos/gateways/tool/registry.py) · [现有 RAG 二次排序](../../src/agentos/knowledge/rag.py)

在仓库根目录运行以下 PowerShell 命令。下载的官方压缩包为 2,816,079 字节，脚本校验 SHA-256 `536e14446a0ba56ed1398ab1055f39fe852686ecad24a6306c80c490fa8e0165`。报告包含每题排序、样本分母、适配器摘要和配对差异。[BEIR 下载与校验](https://github.com/beir-cellar/beir/wiki/Datasets-available) · [实验脚本](../../benchmarks/scifact.py)

```powershell
New-Item -ItemType Directory -Force local/benchmarks | Out-Null
curl.exe -fL https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip -o local/benchmarks/scifact.zip
if ($LASTEXITCODE -ne 0) { throw 'SciFact download failed' }
$env:PYTHONPATH = 'src'
python benchmarks/scifact.py local/benchmarks/scifact.zip --split train --output local/benchmarks/scifact-train.json
python benchmarks/scifact.py local/benchmarks/scifact.zip --split test --workers 8 --output local/benchmarks/scifact-test.json
```
