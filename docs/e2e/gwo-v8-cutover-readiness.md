# GWO V8 生产切换准备报告

状态：**阻塞生产切换，但实现与 Runtime 配置已准备就绪。**

检查日期：2026-07-24

本报告只做读取、配置校验和一次性 Runtime smoke。它没有发布生产
Activation、创建 V8 Store、停止 V6.1、修改 Issue 状态或执行 writer
cutover。

## 已通过

### 实现与交付

- V8 Phase 0–4C 和 Integration Batch 已落在 `dev`。
- PR `#53` 已创建并解决与 `main` 的 V7 历史文档冲突：
  `https://github.com/NOirBRight/github-work-orchestrator/pull/53`。
- 合并冲突修正和模型配置验证所针对的实现 head 为
  `a6f3246700b46769f274b432542444b4b4c6ad0d`，GitHub 回读为
  `MERGEABLE/CLEAN`；本报告和 CI workflow 将作为后续文档提交加入
  同一 PR。
- 专用 canary 的三节点 Batch E2E 已通过，详见
  `docs/e2e/gwo-v8-canary.md`。
- 本地仓库验收和 Skill package quick validation 已通过。

### Runtime 配置

`~/.orch/config.json` 已升级并通过 schema 校验。原文件备份为
`~/.orch/config.pre-v8-readiness-20260724.json`。

| Profile | 模型 | Thinking | Smoke |
| --- | --- | --- | --- |
| Worker light | Kimi K2.7 | `on` | 已由完整 canary 验证 |
| Worker standard | Kimi K2.7 | `on` | 已由完整 canary 验证 |
| Worker heavy | Kimi K3 | `high` | 创建、完成、精确回读、归档通过 |
| Worker frontier | Codex Sol | `xhigh` | 配置保留 |
| Auto Coordinator | Kimi K3 | `max` | 创建、完成、精确回读、归档通过 |
| Standard Review axis | Codex Sol | `high` | 已由完整 canary 验证 |
| Strict/Recovery Review | Codex Sol | `max` | 配置保留 |

K3 `high` smoke Agent 为
`e51ff3df-8c19-4cfb-af5b-d01e0f1ce04a`，K3 `max` smoke Agent 为
`f7c19369-0fbf-4a29-9352-02dad3684bfd`。两者均已归档，不保留活跃
Runtime 资源。

仓库覆盖已从 `v7-integration` 修正为 `dev`，Intake 使用规范的
`ready-for-agent`，Active Turn 上限为 8 Worker + 1 Coordinator。

## 只读 Shadow 结论

生产 `ShadowEvaluator` 没有被强行启动，因为它的两个前置条件均不
成立：

1. `gwo-control` 分支不存在，因此没有 durable writer record、
   Activation Receipt 或 canonical Plan Revision 可供重建。
2. 真实开放 Issues 只有自然语言 Ticket 合同，没有 Compiler 所需的
   canonical PlanSpec 与精确 `outcome_contract`。V8.0 又明确不包含
   Semantic Planner，因此 Kernel 不得自行猜测实现路径或文件内容。

Fail-closed 的只读决定为：

```json
{
  "mode": "pre-cutover-shadow",
  "mutations": 0,
  "decision": "blocked",
  "proposed_actions": [
    "merge_pr_53",
    "reconcile_completed_v8_tickets",
    "select_and_compile_one_real_low_risk_plan",
    "implement_and_read_back_the_production_v61_fence",
    "publish_writer_and_activation_only_after_explicit_cutover_authorization"
  ]
}
```

这不是 Shadow 失败，而是 Shadow 正确拒绝在缺少权威输入时制造执行
状态。

## 当前阻塞项

### 1. Issue frontier 仍包含已完成的 V8 实现 Tickets

真实仓库当前有 15 个开放的 `ready-for-agent` Issues，其中
`#45`–`#52` 对应的能力已经由 PR `#53` 实现。`#39`–`#44` 也仍然
开放，只是被标记为 `ready-for-human`。

在 PR 合并并对这些 Tickets 做完成核对前，V8 Intake 会把已完成工作
重新视为候选。这些 Issues 应在合并时关闭或移除 executable triage
状态，不能靠 Kernel 猜测“代码可能已经完成”。

### 2. V6.1 writer 尚未排空

- Issue `#26` 仍带 `orch:active`。
- V7 Orchestrator Agent
  `5007c7f3-feb4-4a54-9fbb-b4f13e77e517` 仍为 idle、未归档。
- `v7-integration` Workspace/branch 仍存在。

时间经过或 Agent idle 都不能证明 writer 已停止。切换前需要真实
V6.1 stop/readback，确认没有 active Dispatch、Integration lease 或
Worker 写权限。

### 3. 生产 LegacyWriterControl 适配器尚未落地

V8 已有 `LegacyWriterControl` 协议、cutover 状态机和失败测试，但当前
只有 `InMemoryLegacyWriterControl`。没有生产适配器时，代码无法证明
V6.1 已停止；因此不能调用 `WriterCutoverController.cutover()`。

### 4. 还没有首个真实 canonical Plan Revision

生产切换需要一个已经通过 `/to-spec` 或等价确定性入口生成的低风险
PlanSpec。它必须在切换窗口中按顺序完成：

1. 生成 canonical PlanSpec 与 digest；
2. 发布并回读 GitHub durable record；
3. Store 以 CAS 激活同一 digest；
4. 才允许第一个 Admission。

## 最短可行切换路径

1. 等 PR `#53` 的最终 CI 通过并合并。
2. 依据合并结果关闭或重新分类 `#39`–`#52`，得到真实的新工作
   frontier。
3. 实现一个窄的生产 `LegacyWriterControl`，只负责 stop、restore 和
   authoritative readback，不引入新的状态机。
4. 从一个真实、低风险、文件范围明确的 Ready Ticket 生成首个
   canonical PlanSpec。
5. 再次运行只读 Shadow；预期从 `blocked` 变为 `would_admit`，且
   GitHub、Git、Paseo 和 Store 写入计数仍为零。
6. 向用户展示 exact Plan digest、V6.1 fence readback 和 rollback
   目标，取得单独授权后才执行生产 writer cutover。

在上述六步完成前，不建议创建生产 V8 Activation，也不建议关闭或
归档现有 Coordinator。
