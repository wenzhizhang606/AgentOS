# 企业控制台的入口、概览与资源列表

资料核对日期为 2026-11-18。AgentOS 观察者需要进入自己的项目，判断一段时间内发生了什么，并定位某次运行或某个资源。调研选取华为云的在用控制台，以及 IBM Carbon、AWS Cloudscape 两套公开设计系统，分别考察它们如何处理这些任务。

## 官方设计如何处理这些场景

| 体系 | 官方资料中的做法 | 对 AgentOS 的启发 |
| --- | --- | --- |
| 华为云管理控制台 | 顶部导航跨页保留，可从中返回控制台首页、选择区域和项目。资源视图支持按名称、区域或 ID 查找，并通过资源名进入详情。[公共导航](https://support.huaweicloud.com/intl/zh-cn/qs-consolehome/consolehome_qs_0003.html) · [控制台总览](https://support.huaweicloud.com/intl/zh-cn/qs-consolehome/consolehome_qs_3001.html) | 登录后，观察者最需要随时确认的是正在查看哪个项目。项目 ID 适合放在稳定的顶栏，时间范围和筛选条件随相关视图呈现。 |
| IBM Carbon | 默认表单将可见标签放在输入框上方，各控件对齐。错误通知应靠近受影响的内容，用简短文字说明用户可以采取的动作。[表单](https://www.carbondesignsystem.com/building-blocks/core/components/form/guidelines) · [通知](https://www.carbondesignsystem.com/building-blocks/core/components/notification/guidelines) | 两个字段足以完成项目登录。用单列表单呈现项目 ID 和访问令牌，把首次读取进度及失败原因放在提交处。 |
| AWS Cloudscape | 服务概览支持观察和下钻调查，信息顺序明确时适合采用固定布局。资源表格适合比较同类字段，规范区分资源集合为空、筛选无匹配和读取错误。[服务概览](https://cloudscape.design/patterns/general/service-dashboard/) · [资源表格](https://cloudscape.design/patterns/resource-management/view/table-view/) | 概览先呈现实际调用量，再提供定位任务的入口。资源目录将名称、类型、版本排成可比较的列，零匹配时提供清除筛选动作。 |

## AgentOS 的设计取舍

1. **在操作位置反馈进度。** 旧版的“已连接”由成功读取已授权的项目数据触发。“未连接”也是首次打开页面的正常状态。入口可直接显示表单，数据通过项目校验后再显示项目身份。若令牌失效，将用户带回能重新输入令牌的位置。服务运行状况另有 API 探针，页面上的项目身份只表达当前可见数据的范围。[旧版连接状态实现](https://github.com/wenzhizhang606/AgentOS/blob/9f952267f1c718516cc82f84325b5f0018506b9c/src/agentos/observability/console.js#L53-L65) · [监控与授权契约](../specs/runtime-contracts.md) · [服务探针](../changes/docker-deployment.md)
2. **用排版表达信息层级。** 沿用项目指定的红色强调主要操作与选中项。入口去掉占据半屏的装饰性架构图，留出稳定的字段间距。概览中的数字按同一基线对齐，统计区标明时间范围，异常状态同时给出可读文字。[控制台界面设计](../design/console-ui.md)
3. **用现有组件支持查证。** 观察者可以从领取事件进入对应尝试，从资源名称进入版本详情。全长 SHA-256 放在可复制的详情中，窄屏优先显示名称、类型和版本。这些选择服务于 AC-013 的运行追溯与 AC-023 的能力登记检查。[验收案例](../specs/acceptance.md) · [TASK-009](../plan/implementation-plan.md#task-009-可检查的运行过程)

## 如何评估这次改版

后续可用同一项目和同一数据集，请观察者在桌面与窄屏分别完成三项任务。

- 凭证过期后重新进入项目。
- 找到指定任务第二次尝试的轨迹。
- 查出指定 Tool 的完整版本摘要。

逐项记录独立完成数、参与人数、完成耗时及误入视图次数。首次使用者与熟悉旧版的使用者分组报告。实际任务耗时和成功率有待用户走查记录。
