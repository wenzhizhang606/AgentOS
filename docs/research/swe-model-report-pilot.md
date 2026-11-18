# AgentOS 仓库级编码评测与模型发布报告对照

截至 2026-11-18，给 AgentOS 的 `gpt-6.1-sol` 选仓库级基准，有两条直接可用的参照。**DeepSWE v1.1** 对应这个模型自身的发布报告，也有 DeepSeek 在同一检查点上替换执行框架的对照实验。**SWE-Bench Pro V2 Full** 提供 642 道公开题，以及同名模型在 mini-swe-agent 中的解决率。已有工作区、补丁和独立判分的早期集成，可先在固定的公开题上核验整条链路。小样本结果按题号及实际分母报告。[OpenAI 发布报告](https://openai.com/index/introducing-gpt-6-1-sol/) · [DeepSeek 技术报告表 4](https://arxiv.org/html/2609.19969v1#S5.SS3.SSS4) · [Scale 官方 Full 榜](https://labs.scale.com/leaderboard/swe_bench_pro_public_v2?tab=full)

## 近期模型发布报告采用的编码评测

| 发布日期 | 模型 | 发布方采用的指标与执行条件 |
| --- | --- | --- |
| 2026-09-29 | GPT-6.1 Sol | OpenAI 采用 **DeepSWE v1.1，high 档 75.2%**。上一代 GPT-6 Sol 的最高结果是 max 档 68.8%，相差 6.4 个百分点。发布正文未为此结果指定脚手架版本。[OpenAI 发布报告](https://openai.com/index/introducing-gpt-6-1-sol/) · [OpenAI Developers 公布的数值和档位](https://x.com/openaidevs/status/2104993067942219873) |
| 2026-10-17 | DeepSeek V4.1 Flash 1017 | 发布方报告 **DeepSWE v1.1 为 77.0%**，脚手架为 Mini-SWE-V2。该公告未给此行标注推理档位。9 月 10 日的原检查点为 74.2%，其技术报告另列出同一模型在 DSH Minimal 72.6%、Claude Code 69.8%、Codex 65.6% 和 mini-SWE 74.2% 的执行框架实验。[DeepSeek 更新记录](https://api-docs.deepseek.com/updates/) · [9 月检查点技术报告](https://arxiv.org/html/2609.19969v1#S5.SS3.SSS4) |
| 2026-11-05 | Thinking Machines Lab · Inkling 2 Small | 模型卡明确列出 **SWE-Bench Pro V2 Full 97.7%** 和 **V2 Hard 86.3%**，均使用 mini-swe-agent v2.4.6。发布方将该基准的评测归于 Scale AI，并列出 Inkling 的推理强度 0.99（xhigh）、温度 1.0、单条轨迹最多 100 万 Token。[厂商模型卡第 5 节](https://thinkingmachines.ai/model-card/inkling-2-small/) · [发布报告及评测方说明](https://thinkingmachines.ai/news/inkling-2-small/) |
| 2026-11-09 | Claude Fable 5.2 | 发布表报告 **FrontierCode 2.0 为 49.1%**，推理强度 xhigh。另列 **FrontierSWE v2 为 69.9%**。这两行属于独立评测，原表只在前一行明确标注 Fable 的 xhigh。[Anthropic 官方发布与评测表](https://www.anthropic.com/claude-fable-and-mythos-5-2) |
| 2026-11-10 | Gemini 4 Carbon | **SWE-sweep 9.6%** 为 Google 在 mini-swe-agent 中计算的子任务加权得分，方法说明默认使用最高 thinking 设置。**FrontierSWE 2 为 61.2%**，数值取自 Proximal 榜单。两项指标的分母及聚合方式不同。[Google 模型卡](https://deepmind.google/models/model-cards/gemini-4-carbon/) · [评测方法第 2 页](https://deepmind.google/models/evals-methodology/gemini-4-carbon) |
| 2026-11-12 | Gemini 4 Hydrogen | 后续模型卡也选用 **SWE-sweep，13.5%**，另列 **Terminal-Bench 4.1，经作弊校正后为 57.0%**。这给出了同一厂商在软件工程与通用终端两类任务上的两种验收口径。[Google 模型卡](https://deepmind.google/models/model-cards/gemini-4-hydrogen/) |
| 2026-11-17 | GPT-6.2 Astra / Sol | 发布报告使用 **FrontierCode 2.0** 比较代码变更能否达到维护者合并标准。两者均在 max 档位时，Sol 与 Astra 的分差在 0.5 个百分点以内，费用约为 Astra 的五分之一。Cognition 的 2.0 套件共 132 题，含维护者编写的需求及评审条件。[OpenAI 发布报告](https://openai.com/index/introducing-gpt-6-2-astra-sol/) · [Cognition 官方方法](https://cognition.com/frontiercode) |

## 可与试点配对的公开对照

**SWE-Bench Pro V2 Full** 于 2026-09-22 发布。Scale 从上一版公开拆分的 731 题中移除 89 题，其他题目亦有描述或环境修订。V2 Full 含 11 个仓库、642 题，Hard 子集为 51 题。评测时 agent 只能访问模型端点，补丁在干净环境中重评。下表在同一 Full 拆分下对照，保留官方误差值。模型、执行框架和推理档位仍是独立变量。[Scale 榜单与协议](https://labs.scale.com/leaderboard/swe_bench_pro_public_v2?tab=full) · [作者论文 v2，2026-11-11](https://arxiv.org/abs/2610.24206v2)

| 模型 | 脚手架 | 推理档位 | Full 解决率 |
| --- | --- | --- | ---: |
| Claude Opus 5 | Claude Code | xhigh | 99.40% ± 0.40 个百分点 |
| Gemini 4 Argon | mini-swe-agent | high | 99.22% ± 0.45 个百分点 |
| Inkling 2 Small | mini-swe-agent v2.4.6 | xhigh（0.99，厂商发布设置） | 97.66% ± 1.46 个百分点 |
| GPT-6 Astra | Codex | high | 96.90% ± 1.10 个百分点 |
| **GPT-6.1 Sol** | **mini-swe-agent** | **xhigh** | **96.11% ± 1.24 个百分点** |
| Claude Sonnet 5.5 | mini-swe-agent | xhigh | 95.79% ± 1.30 个百分点 |

**本试点选用 V2 Full 的依据。** Thinking Machines 的模型报告采用了同版 Full 拆分，97.7% 是 Scale 公布的 97.66% 保留一位小数，两处发布对应同一来源。Scale 的 `default/test` 对应 Full 642 题，另提供 51 题的 `hard/test` 和 731 题的 `v1/test`。这使 AgentOS 能从与厂商报告相同的公开题库取题，用表中的同模型 GPT-6.1 Sol 作最终全量参照。Reflection 的 Beam 技术报告则分别列出 V1 全集和 V2 Hard，只适合各自拆分的比较。[Inkling 2 Small 模型卡](https://thinkingmachines.ai/model-card/inkling-2-small/) · [Scale 数据卡与拆分定义](https://huggingface.co/datasets/ScaleAI/SWE-bench_Pro) · [Scale Full 榜](https://labs.scale.com/leaderboard/swe_bench_pro_public_v2?tab=full) · [Beam 技术报告](https://reflection.ai/documents/beam-model-tech-report)

**DeepSWE v1.1** 共 113 项原创长程工程任务。Datacurve 于 2026-06-14 修订评测，提交的代码在独立干净环境中评分。同一维护方榜单显示 GPT-6 Astra（xhigh）74% ± 3 个百分点、Gemini 3.8 Flash（high）74% ± 1 个百分点、Claude Opus 5（max）74% ± 4 个百分点。此处保留榜单页面的整数精度。模型发布方随后自行测出的结果可另列，并保留采样与执行环境。[Datacurve v1.1 官方协议及同榜结果](https://deepswe.datacurve.ai/blog/deepswe-v1-1)

**SWE-bench Verified** 是从原始 SWE-bench 筛选的 500 道题。官网的 Bash Only 视图统一采用 mini-SWE-agent。维护方公开 JSON 中，2026-02-17 同版本 `mini-SWE-agent 2.0.0`、同为 high 的历史参照有 Claude 4.5 Opus 76.8%、Gemini 3 Flash 75.8%、GPT 5.2 72.8%、DeepSeek V3.2 70.0%。JSON 中的 `per_instance_details` 可用于同题对照。OpenAI 针对 138 道此前不稳定解决的问题复核，在该定向样本中有 59.4% 的题存在实质问题。Verified 适合检查补丁交付与评分链路，使用其总分推断前沿能力需考虑这一限制。[官方拆分与评分定义](https://www.swebench.com/) · [维护方榜单数据](https://raw.githubusercontent.com/SWE-bench/swe-bench.github.io/master/data/leaderboards.json) · [OpenAI 的审计范围和方法](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)

## AgentOS 怎样报告结果

同题对照应保留实例 ID、代码基线、数据及判分器版本、模型端点报告的模型名、实际提交的推理档位、脚手架、可用工具、尝试次数、预算和独立评分结果。某一试点若通过上下文检索一次生成补丁，应标注检索范围和缺少交互式编辑的限制。若只复跑若干测试，则列出实际命令与覆盖范围；获得完整官方 verdict 后，再报告该题 resolved。小样本列明成功、失败与基础设施错误的分母及逐题费用。评估组件优化可在同一批题、同一模型、同一预算下替换一个组件，用逐题解决状态和成本计算差异。[SWE-bench 官方执行说明](https://www.swebench.com/SWE-bench/) · [AC-014 量化评测](../specs/acceptance.md#ac-014-业务量化评测)

## AgentOS 单题实测

选用 **SWE-Bench Pro V2 Full**。按实例 ID 字典序固定 Open Library 的第一题，评测代码能否修复图书导入时误拒绝元数据缺失、但带有强标识符的记录。官方题面点名了校验函数，试点把题面和对应的一个基线文件作为证据送入生产版受管 worker。上下文不含官方解答、测试补丁、测试名称或判分输出。模型一次返回完整文件，由评测脚本生成统一差异补丁。该流程覆盖给定文件位置时的修改与提交。[固定题面](https://github.com/scaleapi/SWE-bench_Pro-os/blob/66f92766bba642462d4bbe5479e83f91f9211862/v2/tasks/instance_internetarchive__openlibrary-00bec1e7c8f3272c469a58e1377df03f955ed478-v13642507b4fc1f8d234172bf8129942da2c2ca26/instruction.md) · [推理与计分代码](../../benchmarks/swe_pro.py)

| 一次受管运行 | 实际记录 |
| --- | --- |
| 模型与发布 | LLAPI 的 `gpt-6.1-sol`，AgentOS 发布 `006effa11d5573d7ccbf05b7c1b68be195e73b3ae837cb8162af7aa41404a0b1`，请求沿用部署默认推理档位 |
| 运行 ID | `6b710360e7504758aa1b5024bc9f5831:1` |
| 仓库基线与套件 | Open Library `02f647f7d525286b6e3a661133c3772649e585cd`，Scale 官方套件 `66f92766bba642462d4bbe5479e83f91f9211862` |
| 提交及模型账务 | 1 个候选、1 次模型准入，任务观测耗时 15.50 秒，回执 5,903 输入 / 586 输出 Token，按平台费率结算 ¥0.008247 |
| 候选补丁 SHA-256 | `5e283cd785dc66cbe0cc8fdc51ca14c36aa316c07583711c162adef7bc635541` |

请求配置的输出上限为 512 Token，本次供应商回执的输出用量为 586。现有回执没有把两者差额归因到具体供应商机制。账本使用实际回执，预算评估应单独核验代理和模型的计量语义。

### 官方测试结果

| 官方评分 | 未修改的基线 | AgentOS 单次补丁 |
| --- | ---: | ---: |
| 缺陷修复测试 FAIL_TO_PASS | 0 / 5 | **5 / 5** |
| 既有行为回归测试 PASS_TO_PASS | 15 / 15 | **15 / 15** |
| 官方 reward | 0 | **1** |

**本次解决 1 / 1 题。** 两次判分分别使用全新的官方 Linux/amd64 镜像，摘要为 `sha256:03205f63224d992c930df7a2b898353d9d3dface4ab4d921eebc73dc909c1ba6`，均关闭网络并使用同一份官方测试脚本。空补丁保留了五个预期失败。候选补丁应用后 20 项全部通过。评分脚本从官方 `output.json` 核对两组测试名称和状态，并与官方 `reward.txt` 交叉检查。[官方实例和判分脚本](https://github.com/scaleapi/SWE-bench_Pro-os/tree/66f92766bba642462d4bbe5479e83f91f9211862/v2/tasks/instance_internetarchive__openlibrary-00bec1e7c8f3272c469a58e1377df03f955ed478-v13642507b4fc1f8d234172bf8129942da2c2ca26) · [项目隔离评分入口](../../benchmarks/swe_pro_grade.sh)

在本次交付的工作区，题面、基线文件、推理记录、模型候选、轨迹和两份官方判分原件保存在 `local/benchmarks/swe-pro-v2-p01/`。执行 `python -m benchmarks.swe_pro score local/benchmarks/swe-pro-v2-p01` 可从判分原件重新生成汇总，无需调用模型。具备该实例镜像的 Linux Docker 主机也可在新目录中复跑两次官方测试。

```bash
input=local/benchmarks/swe-pro-v2-p01
instance=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["instance_id"])' "$input/protocol.json")
workdir=$(mktemp -d)
cp -R "$input/harness/v2/tasks/$instance/tests" "$input/config.json" "$input/candidate.patch" "$workdir/"
bash benchmarks/swe_pro_grade.sh "$workdir" baseline
bash benchmarks/swe_pro_grade.sh "$workdir" prediction
python -m benchmarks.swe_pro score "$workdir"
```

Scale 上同名模型的 **96.11% ± 1.24 个百分点**来自 642 题、mini-swe-agent 和 xhigh。AgentOS 此次使用预先定位的文件和一次生成，未显式请求 xhigh。适合对照的下一次实验是对同一批固定题保持模型、预算和判分器一致，只改变文件定位方式，以验证上下文检索对解决率的贡献。[Scale 官方对照](https://labs.scale.com/leaderboard/swe_bench_pro_public_v2?tab=full)
