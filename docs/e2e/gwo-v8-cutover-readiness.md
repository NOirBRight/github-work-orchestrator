# GWO V8 生产切换准备报告

状态：**阻塞生产切换，但实现与 Runtime 配置已准备就绪。**

检查日期：2026-07-24

本报告记录读取、配置校验、一次性 Runtime smoke，以及 PR 合并后的
frontier 清理。它没有发布生产 Activation、创建 V8 Store、发布
V6.1 stop fence 或执行 writer cutover。

## 已通过

### 实现与交付

- V8 Phase 0–4C 和 Integration Batch 已合入 `main`。
- PR `#53` 已合并：
  `https://github.com/NOirBRight/github-work-orchestrator/pull/53`。
- merge commit 为 `e247294e1a8a7f5147989dd97bcfb0691c8eb172`；
  PR exact-head CI 与合并后的 `main` push CI 均通过。
- 专用 canary 的三节点 Batch E2E 已通过，详见
  `docs/e2e/gwo-v8-canary.md`。
- `#39`–`#50` 与 `#52` 已按合并证据关闭；`#51` 保留为最终生产
  cutover 的人工 Decision Gate。
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
    "merge_issue_54_production_fence",
    "select_and_compile_one_real_low_risk_plan",
    "create_control_branch_and_run_pre_cutover_shadow",
    "publish_writer_and_activation_only_after_explicit_cutover_authorization"
  ]
}
```

这不是 Shadow 失败，而是 Shadow 正确拒绝在缺少权威输入时制造执行
状态。

## 当前阻塞项

### 1. 生产 fence 尚未发布

`#54` 已拆出为唯一 V8 可执行下一步。本变更候选实现了生产
`GitHubLegacyWriterControl`、V6.1 mutation guard，以及 GitHub/Paseo
权威 readback；在它合并前，生产线仍只有已合并的 protocol 和
InMemory fake。

即使代码合并，安装本身也不会创建 `gwo-control` 或发布 stop fence。
这两个动作属于后续 cutover 窗口。

### 2. V6.1 已清理逻辑残留，但尚未执行 durable stop

- `dispatch-issue-26-a1` 已以原身份标为 `retired`；它从未创建 Worker、
  Workspace、branch、PR 或 Candidate。
- `#26`、`#27`、`#31` 已作为被 V8 取代的 V7 工作关闭。
- V7 Orchestrator Agent
  `5007c7f3-feb4-4a54-9fbb-b4f13e77e517` 已软归档。
- 两个 V7 审计 worktree 仍保留，没有 Agent 绑定，也没有被删除。

`#54` candidate 的生产 readback 已对真实仓库执行一次只读验证：
`stopped=false`、`active_dispatches=[]`、`integration_lease=false`、
`active_workers=[]`。这说明执行残留已排空，同时也正确证明 durable
stop 尚未发生。

这些事实消除了已知 active execution，但不等于 durable stop。切换时
仍必须发布并回读 fence，并再次证明没有 non-terminal Dispatch、
Integration command 或未归档 Worker。

### 3. 还没有首个真实 canonical Plan Revision

生产切换需要一个已经通过 `/to-spec` 或等价确定性入口生成的低风险
PlanSpec。它必须在切换窗口中按顺序完成：

1. 生成 canonical PlanSpec 与 digest；
2. 发布并回读 GitHub durable record；
3. Store 以 CAS 激活同一 digest；
4. 才允许第一个 Admission。

## 最短可行切换路径

1. 完成 `#54` 的本地验收、一次 PR CI 和合并。
2. 创建专用 `gwo-control`，但此时仍不发布 Activation 或 Admission。
3. 从一个真实、低风险、文件范围明确的 Ready Ticket 生成首个
   canonical PlanSpec。
4. 再次运行只读 Shadow；预期从 `blocked` 变为 `would_admit`，且
   GitHub、Git、Paseo 和 Store 写入计数仍为零。
5. 向用户展示 exact Plan digest、预期 V6.1 fence action 和 rollback
   目标，取得单独授权。
6. 在授权窗口内发布 stop fence，回读 V6.1 零执行权，再由
   `WriterCutoverController` 发布 writer generation 与 Activation。

在上述六步完成前，不得创建生产 V8 Activation。
