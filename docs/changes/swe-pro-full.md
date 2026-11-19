# SWE-Bench Pro V2 Full 全量计划

关联 REQ-EVAL-001、TASK-010 和 AC-014。评测对象为使用 `gpt-6.1-sol` 的 AgentOS 单轮仓库修复流程，分母固定为上游公开 Full 划分的 642 道题。[官方 V2 套件与协议](https://github.com/scaleapi/SWE-bench_Pro-os/tree/66f92766bba642462d4bbe5479e83f91f9211862/v2) · [验收规范](../specs/acceptance.md#ac-014-业务量化评测)

## 2026-11-19 的进度

| 项目 | 实际结果 |
| --- | --- |
| 套件身份 | 固定 Git 提交 `66f92766bba642462d4bbe5479e83f91f9211862`。11 个仓库，642 个不同实例 |
| 离线校验 | 全部题面的正文、任务配置、环境镜像绑定通过校验。每题 5 个判分文件，共 3,210 个，已按 Git 对象逐一校验 |
| 全量计划 | `bench-swe-pro-v2-full-20261119`，待执行 642，完成 0，全量解决率尚无结果 |
| 服务器容量 | Docker 数据盘可用 3,331,629,056 字节，约 3.10 GiB。默认单题准入门槛为 12 GiB，快照时尚差约 8.90 GiB |

本轮只进行了发现、计划、状态查询与预检。11 月 18 日的 Open Library 试点用预先定位的单文件生成补丁，官方判定解决 1/1。该结果单独留档，全量批次从 642 道待执行任务起步。[单题协议与原始判分说明](../research/swe-model-report-pilot.md#agentos-单题实测)

## 复核计划与磁盘

在 AgentOS 仓库根目录使用 Python 3.11 或更新版本。题库须在本机检出上述提交，包含官方 `v2/tasks` 下的题面、配置、环境描述和判分文件。当前工作区已准备在 `local/benchmarks/swe-pro-v2-full/harness`。在新的 Linux 工作目录可以按下列方式取得同一版本，并将参考解答排除在工作树之外。

```bash
harness=local/benchmarks/swe-pro-v2-full/harness
git clone --filter=blob:none --no-checkout https://github.com/scaleapi/SWE-bench_Pro-os.git "$harness"
git -C "$harness" sparse-checkout set --no-cone '/LICENSE' '/v2/**' '!/v2/tasks/*/solution/' '!/v2/tasks/*/solution/**'
git -C "$harness" checkout --detach 66f92766bba642462d4bbe5479e83f91f9211862
```

以下操作只校验本地文件、建立冻结清单并读取计数。

```bash
python -m benchmarks.swe_pro_suite inspect local/benchmarks/swe-pro-v2-full/harness --verify-graders
python -m benchmarks.swe_pro_batch plan bench-swe-pro-v2-full-20261119 local/benchmarks/swe-pro-v2-full/harness
python -m benchmarks.swe_pro_batch status bench-swe-pro-v2-full-20261119
```

`inspect` 输出固定提交、`task_count` 和 `graders_verified`。旧试点目录只有 82 个实例目录得到检出，会返回 `82/642`。成功的 `plan` 将题面摘要、官方镜像标签及所需空间写入批次的 `suite.json`。同名计划复核时须与已有清单完全相符。[离线完整性检查](../../benchmarks/swe_pro_suite.py) · [批次清单与状态](../../benchmarks/swe_pro_batch.py)

在现有 Linux Docker 服务器上，对引擎实际使用的数据目录运行空间预检。642 题的官方配置均声明 `storage_mb=10240`，调度器按 10 GiB 计入本题需要，并默认保留 2 GiB 给宿主服务。12 GiB 是开始处理下一题的门槛；实际镜像大小、共享层与执行期间写入量还会影响可用空间。

```bash
docker_root=$(docker --host unix:///var/run/docker.sock info --format '{{.DockerRootDir}}')
python -m benchmarks.swe_pro_suite preflight "$docker_root" --required-bytes 10737418240 --reserve-bytes 2147483648
```

输出包含 `available_bytes`、`required_bytes`、`reserve_bytes`、`shortfall_bytes` 和 `admitted`。判定为 `false` 时，运行队列在拉取镜像、提交模型任务之前返回 `paused=insufficient_disk` 并保留待执行状态。

## 推理、判分与恢复

- **推理输入。** 按题绑定官方 linux/amd64 镜像及清单摘要。在只读、断网容器中依据题面选取仓库片段，将题面和片段送给受管 worker。每题一次模型请求，输出一个可跨文件的统一差异补丁。模型在这一轮没有交互式终端。官方解答和判分文件由独立路径处理。[仓库取证与补丁提取](../../benchmarks/swe_pro_prediction.py)
- **执行入口。** 实际执行还需要现有服务器上的私有评测 API，以 `127.0.0.1` 的端口连接，并加载该批次的访问凭证。`provision` 准备凭证清单；API 和 worker 须能使用相应身份与发布配置。`run` 要求明确的 `--port` 和 `--budget-micros`，后者以人民币微元计（1 元 = 1,000,000 微元）。缺失镜像仅在显式选择 `--fetch-missing` 且空间准入通过后拉取。[受管执行与凭证](../../benchmarks/managed.py) · [批次 CLI](../../benchmarks/swe_pro_batch.py)
- **判分。** 在全新容器应用该题候选，记录官方 `fail_to_pass` 与 `pass_to_pass` 两组结果、奖励和容器状态。不能应用的补丁计入 `unresolved` 并记录 `patch_rejected`。判分超时、OOM、无法核对输出等列为 `infrastructure_error`，待处理后重试。[隔离判分与裁定](../../benchmarks/swe_pro_verifier.py)
- **恢复与分母。** 同一批次保存 `cases/` 下的逐题状态、已生成补丁和推理记录。恢复时对已有候选核对摘要，继续判分。`status` 分开列出 `pending`、`awaiting_grade`、`infrastructure_error`、`resolved`、`unresolved`、`agent_failure`。所有题到达终态后才计算 `resolved_rate = resolved / 642`；其余情况该字段为 `null`。[批次进度汇总](../../benchmarks/swe_pro_batch.py)

进度中的 `completed` 包括模型失败及无法应用的补丁。`official_verdicts` 只计取得有效官方奖励的题目，二者可以不同。

过长题面按请求正文实际编码后的字节预算保留首尾，逐题保存原文摘要、截取范围及仓库片段摘要。首次执行还会固定推理和判分代码的摘要。模型调用得到供应商故障或结果不明的回执时，保留原任务 ID 供核对；恢复队列会沿用该记录，避免重复扣费。评测凭证默认有效 4 小时，未来长批次可通过 `provision --credential-hours` 指定有效期。更新凭证清单后需让该批次的私有 API 重新加载它。

全量流程配套了题库与磁盘准入、预测、判分及队列恢复的契约用例。本轮没有执行测试套件，也没有发起全量模型推理或官方容器判分。[题库与磁盘用例](../../tests/contracts/test_swe_pro_suite.py) · [预测用例](../../tests/contracts/test_swe_pro_prediction.py) · [判分用例](../../tests/contracts/test_swe_pro_verifier.py) · [队列恢复与计分用例](../../tests/contracts/test_swe_pro_batch.py)
