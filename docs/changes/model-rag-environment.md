# 部署模型和 RAG 参数

本变更覆盖 M1 的 REQ-RAG-001、REQ-CTX-002、REQ-MOD-002，关联 TASK-004、TASK-005、TASK-008 和 AC-002、AC-010。部署方可在项目根目录 `.env` 中配置已实现的 Chat Completions 模型网关和词法检索器。[配置模板](../../.env.example)

## 参数

| 变量 | 取值和作用 |
| --- | --- |
| `AGENTOS_MODEL_NAME` | 供应商在请求体 `model` 字段接受的固定模型或部署 ID。 |
| `AGENTOS_MODEL_API_URL` | 完整的 HTTPS Chat Completions 调用地址，包含供应商要求的路径。域名须获准接收项目资料。URL 中不允许放凭证、查询参数或片段。 |
| `AGENTOS_MODEL_API_KEY` | 供应商 Bearer 密钥，Compose 仅向 worker 注入。 |
| `AGENTOS_MODEL_WINDOW_TOKENS`、`AGENTOS_MODEL_MAX_OUTPUT_TOKENS`、`AGENTOS_MODEL_SAFETY_MARGIN_TOKENS` | 总窗口、最大输出和预留安全余量，单位均为 Token。最大输出为正整数，总窗口应大于最大输出与安全余量之和。 |
| `AGENTOS_MODEL_INPUT_MICROS_PER_MILLION`、`AGENTOS_MODEL_OUTPUT_MICROS_PER_MILLION` | 每百万输入、输出 Token 的实际价格，均为非负整数。单位是人民币微元，每百万 Token 收费 2.50 元时填 `2500000`。 |
| `AGENTOS_RAG_TOP_K` | 检索结果数量上限，1～10，默认 10。启用检索 Tool 时取该值和 Tool 清单 `limit` 的较小值。例如 TOP_K 为 8，Tool 上限为 5，最多返回 5 项。上下文窗口还可进一步减少证据数。 |
| `AGENTOS_RAG_MIN_SCORE` | 词法检索最低分，非负有限数，默认 0。正分且达到阈值的结果可进入候选。在授权语料内根据词项命中计算分数，调高阈值会减少可选证据。 |
| `AGENTOS_RAG_TITLE_WEIGHT`、`AGENTOS_RAG_BODY_WEIGHT` | 标题和正文的词项权重，默认为 2 和 1。均须为非负有限数，至少一项为正。调整配比时对已召回的候选重新计算词法分数并排序；每个命中词的贡献为相应权重除以包含该词的候选文档数。 |

例如分配 8,192 Token 总窗口并检索最多 3 项时，可以在已有 `.env` 中设置以下字段。模型名、地址和单价按实际供应商配置，密钥单独填写。

```dotenv
AGENTOS_MODEL_NAME=gpt-6.1-sol
AGENTOS_MODEL_API_URL=https://llapi.org/v1/chat/completions
AGENTOS_MODEL_WINDOW_TOKENS=8192
AGENTOS_MODEL_MAX_OUTPUT_TOKENS=512
AGENTOS_MODEL_SAFETY_MARGIN_TOKENS=128
AGENTOS_RAG_TOP_K=3
AGENTOS_RAG_MIN_SCORE=0.25
AGENTOS_RAG_TITLE_WEIGHT=2
AGENTOS_RAG_BODY_WEIGHT=1
```

## 配置来源与发布

原生 `agentos` 从当前工作目录读取 `.env`，已设置的进程环境变量优先。Compose 使用项目的 `.env` 和当前 shell 环境解析服务配置，将非秘密的发布参数同时传给 API 和 worker。发布参数使用非空环境覆盖值，其余沿用基础发布 JSON 或字段默认值。既有 `local/release.json` 可继续保存 Skill、Tool 及其权限和版本声明。同一版本内保留原有的资源授权、预算和模型发送准入检查。

已有任务绑定旧版本时，先让对应 worker 处理完任务，再切换配置。修改 `.env` 后运行 `docker compose up -d api worker` 重新创建受影响的容器，通过 `docker compose exec -T api agentos release-id` 和 `docker compose exec -T worker agentos release-id` 核对两端的有效发布 ID。原生部署重启对应进程并运行 `agentos release-id`。模型、窗口、价格或检索策略的有效值变化会改变发布 ID。供应商密钥在发布清单之外轮换。[容器启动与升级](docker-deployment.md) · [原生启动](m1-managed-support.md)

## 服务器验收

2026-11-18 在 Linux x86-64、Docker Engine 29.1.3、Compose 5.0.0、Python 3.12.16 和 MySQL 8.4.13 上进行验收。应用和测试分别使用独立数据库，均完成了迁移 001 至 003。供应商调用使用测试替身。

| 验收范围 | 执行方式 | 结果 |
| --- | --- | --- |
| 模型和 RAG 配置、HTTP、运行时、MySQL、端到端和监控页面 | 测试容器启用 `AGENTOS_TEST_MYSQL=1`，使用 Playwright Chromium，执行 `python -m pytest --ignore=tests/integration/test_compose_deployment.py` | 143 项通过，含 2 个真实浏览器用例 |
| Compose 配置传递、容器健康和页面探针 | 从可访问 Docker CLI 的宿主机启用 `AGENTOS_TEST_COMPOSE=1`，执行 `python -m pytest tests/integration/test_compose_deployment.py` | 4 项通过 |

两份 JUnit 报告共 147 个不同用例，失败、错误和跳过均为 0。FastAPI 的测试客户端依赖报告了 1 条弃用提示。报告保存在部署目录 `local/test-results/2026-11-18/`。运行中的 API 与 worker 算出了相同的发布 ID，实测从环境读取了测试模型名和 `AGENTOS_RAG_TOP_K=3`。健康、就绪、监控页面和受权项目 Agent 列表均返回 HTTP 200。

### 真实模型验收

2026-11-18 通过任务 API 提交了一笔虚构退货问题。worker 向配置的 LLAPI Chat Completions 端点请求 `gpt-6.1-sol`。该次运行从 worker 领取到成功耗时 7.92 秒，返回“七天 [E1]”。引用与快照都指向 `return-policy` 第 1 版的 `section-1`。轨迹记录了上下文、文本 Skill、检索 Tool、一次模型发送准入和一次回执结算。重复提交相同请求得到原任务 ID。项目接口无令牌返回 401，使用本项目令牌读取未授权项目返回 403。

| 对账项目 | 实测值 |
| --- | --- |
| 供应商回执用量 | 输入 4,656 Token，输出 39 Token |
| 平台账本 | 发送时预留 10,240 微元，按发布配置单价结算 4,812 微元（0.004812 元），结算后预留为 0。供应商实扣金额待账单核对。 |
| 输入估算 | 本地按请求 UTF-8 字节数估算为 1,265，供应商回执多计 3,391 Token，差值超过配置的 128 Token 安全余量。本次实际输入仍在发布配置的 7,552 Token 输入预算内。生产窗口上界和费用上界需另行校准。 |

本次实测覆盖一个正向案例。发布与运行 ID、快照元数据、逐项检查及账本详见服务器私有回执 `local/test-results/2026-11-18/llapi-m1-live/receipt.json`。三项公开基准和企业业务质量仍待单独评测。[复查本次轨迹](m1-acceptance-walkthrough.md) · [质量指标与公开基准](../requirements/agentos-platform.md#m1-公开评测基准)
