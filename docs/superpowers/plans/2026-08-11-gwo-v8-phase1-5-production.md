# GWO V8 Phase 1–5 一次性推进计划

## 目标与当前状态

目标：完成 V8 从功能验收到生产默认 writer 切换，并完成 GA 验收。

基线：codex/beta3-production-bootstrap / a59a807。GitHub CI disabled；验证全部本地完成；V6.1 在 Phase 5 前始终保持唯一生产 writer。实现子代统一使用 gpt-5.6-luna、reasoning max，采用 SDD + TDD，最多 5 个子代并行，同一写集不得并行修改。

## Phase 1：公共 API 单节点功能验收

新增 scripts/run_v8_local_acceptance.py 与 tests/test_v8_local_acceptance.py。只能通过 gwo_v8.start/advance/inspect；使用临时仓库、临时 SQLite、确定性 Runtime、本地 Delivery stub；不调用 GitHub/Paseo/Hosted CI/生产目录；支持 single 与固定 run_id；输出 canonical JSON 验收记录。验证 Ticket→Campaign、Plan Revision、Worker、CandidateGate、Review/Batch/Result/Evidence、Complete，Wait/Blocked/Failure，restart/replay/idempotency。Gate：PUBLIC_API_SINGLE_NODE_GO。

## Phase 2：纯本地四节点 Root Canary

扩展上述 local runner，加入 root 场景：4 个并发 Work Runs，3 Standard-Assurance Ticket、1 Strict-Assurance Ticket；Standard Candidate 同一 Integration Batch，Strict Candidate Singleton Batch；本地 readback。验证并发 slot/exclusive resource、Review/Repair/Rejection、批次不混、git readback、重启、重复 callback、丢失 wake，CampaignWatchdog 仍通过 public advance() 推进。Gate：LOCAL_ROOT_CANARY_GO。不得使用旧 scripts/run_v8_canary.py。

## Phase 3：高保障修复与 Release Candidate

并行且写集互斥：
1. Control Attestor：scripts/beta3_control_ownership_attestor.py 及其测试；no-follow/held-handle traversal；拒绝 Windows reparse ancestor；父目录替换/junction 回归。
2. Capability Surface：scripts/beta3_bootstrap_model.py 及其测试；拒绝实例级 public callable、动态 __getattr__，只接受精确只读 surface；RED→GREEN。
3. Runner Provenance/Lease/Injection：scripts/run_beta3_live_guard.py 及其测试；runner/attestor hash、canonical path、Store/receipt/package/installed files/Guard modules/output parents 全覆盖；source read/nonce 前拒绝 DI；包含 git_runner。
然后运行五个 Beta3 focused suites、全仓库 pytest、Ruff、AST、forbidden call graph，形成新的 Phase 3 SDD ledger/review package；四轴 review（Spec、Quality/Security、TDD、Open Findings）。Gate：SPEC GO、QUALITY GO、TDD VALID、OPEN 0，并生成 RC 记录。

## Phase 4：Workspace Convergence 与 Beta3 Cutover Rehearsal

盘点 D:\Workstation\github-work-orchestrator、D:\Workstation\gwo-worktrees\beta3-production-bootstrap 下所有 worktree、旧测试/运行目录、.codex-tmp、docs/research、archive/evidence；删除前先出清单，未知归属保留。将完成 Phase 1–3 分支合并到 main；对 exact merged-main SHA/tree 重生成 provenance manifest、runner/attestor hash、release evidence；本地 full pytest 与 acceptance single/root。运行只读 run_beta3_live_guard.py rehearsal：preflight→readback→V6.1 quiescence→double attestation→frozen replay→report→evidence；失败不得改生产状态，不执行 activation，不生成额外生产 SQLite/artifact/staging。Gate：BETA3_CUTOVER_REHEARSAL_GO。

## Phase 5：生产 Activation、Root Campaign 与 GA

实际 mutation 前 owner 必须批准 exact merged-main SHA/tree、run_id、evidence root、target repository、transition v6.1 -> v8。严格执行 zero-write preflight→V6.1 quiescent→Guard receipt fresh→WriterCutoverController.cutover()→durable Activation Receipt→transition/default-writer readback。禁止手改 SQLite、双 writer、绕过 receipt、自动 rollback、重写/删除旧 receipt。激活后真实 root repository 运行四 Work Runs（3 Standard + 1 Strict Singleton），验证 Candidate/Review/Repair/Batch/Restart，并保存完整证据；证据一致后执行 v8.0.0 tag、本地 tag 验证、Release metadata、GA completion。

## 执行任务

### Task 1 — Phase 1 local public-API acceptance
创建 runner/tests，先按 TDD 写 RED 测试再实现，完成 single、状态分支、restart/replay/idempotency、canonical JSON 与 PUBLIC_API_SINGLE_NODE_GO。

### Task 2 — Phase 2 local root acceptance
在 Task 1 runner 上加入 root 场景及四节点并发、批次分流、readback/restart/callback/watchdog 断言；生成 LOCAL_ROOT_CANARY_GO。

### Task 3 — Phase 3 control attestor hardening
按 TDD 修复 no-follow/held-handle/reparse ancestor 防护与回归测试。

### Task 4 — Phase 3 capability-surface hardening
按 TDD 修复精确只读 capability surface、实例 public callable 与动态 __getattr__ 防护。

### Task 5 — Phase 3 runner provenance/lease/injection hardening
按 TDD 修复 guard runner provenance/path/lease/DI 约束与 git_runner 覆盖。

### Task 6 — Phase 3 integration, local verification, and four-axis review package
在 Task 3–5 完成后运行五个 focused suites、full pytest、Ruff、AST、forbidden call graph，生成 RC/evidence/review package 与 verdicts。

### Task 7 — Phase 4 workspace convergence and exact-main rehearsal
生成工作区清单，保留不明归属数据；合并 branch 到 main；在 exact merged-main 上重生成 provenance/evidence，运行本地 full/acceptance，完成只读 Beta3 rehearsal。

### Task 8 — Phase 5 production authorization and activation
仅在 owner 的 exact authorization 已给出时执行生产 mutation；否则只完成 zero-write preflight 并停止在 activation gate。执行 activation/readback 与真实 root campaign。

### Task 9 — GA release
汇总 exact SHA/tree、Guard/Activation/transition/root evidence；验证本地 tag SHA，生成 GA completion/release metadata。不得在证据不全时标记 GA。

## 最终成功标准

V8 public API functional
LOCAL_ROOT_CANARY_GO
SPEC GO
QUALITY GO
TDD VALID
OPEN 0
BETA3_CUTOVER_REHEARSAL_GO
Activation Receipt readback exact
V8 default writer readback exact
GA Root Campaign complete

