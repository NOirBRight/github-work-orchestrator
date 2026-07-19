# 敏捷多 Agent 编排设计（独立成稿）

> **Non-normative historical candidate.** V6 的当前锁定决策与待决问题见
> [`orchestrator-v6-living-design.md`](../orchestrator-v6-living-design.md)。本文件
> 保留用于比较，不得直接作为实现规格。

> 本文是从零开始的完整设计，供实现 Agent 参考，实施后以本文为准。
> 场景：一个仓库有一批开放 Issue，需要多个 Agent 并行、快速地把它们
> 变成合并进集成分支的 PR。旧 GWO 体系（`shared/`、`skills/github-*`）整体废弃。

## 1. 问题定义：那一小时花在了哪

旧 harness 在两个仓库（codexhub、ayaspace2）建制一小时、零行代码交付。
把这笔固定开销拆成四类，才能看清楚该砍什么：

1. **阅读成本**：Agent 开工前被要求消化的协议与契约——约 1100 行 `shared/`
   规范、13KB 的 orchestrator SKILL、references 与 8400 行脚本。
2. **建制成本**：Campaign 工作区、控制 worktree、契约校验、双 Reviewer 组建。
3. **握手成本**：AGENT_READY → START 确认、WAKE/ACK 投递事务、全程 readback。
4. **监督成本**：心跳、身份回执、候选锁、双轴审计、结果聚合。

四类开销全部发生在"写第一行代码之前"。逐项压缩没有意义，要换一条公理：

> **编排系统只有两个 KPI：time-to-first-commit（TTFC）和每小时合并的 PR 数。
> 任何机制如果不能直接贡献于这两个指标，就必须能指出自己防的是哪一种
> 真实发生过的故障——指不出来的，一律不存在。**

## 2. 设计目标

- **TTFC**：Coordinator 决定派发 → Worker 首次 commit，普通 Issue ≤ 10 分钟。
- **编排开销占比** ≤ 任务总耗时的 5%。
- **并行度**：默认 3 个 Worker 同时在飞；互不冲突的工作永不串行等待。
- **可恢复**：杀掉任意 Worker、重启 Coordinator，仅凭 GitHub 状态无损恢复。

## 3. 总览：一个大脑，多双手

```text
            ┌────────────────────────────────────────────────┐
  用户 ⇄    │ Coordinator（每仓库 1 个，长驻）                │
 （批量问答）│ 分诊 → 聚类 → 定级 → 排波 → 派发 → 监督 → 验收 → 集成 │
            └────────┬──────────┬──────────┬─────────────────┘
                  Worker A    Worker B    Worker C
                  （一次性：一个 Issue、一个 worktree、一个分支、一个 PR）
```

三条公理：

1. **GitHub 是唯一持久状态。** Issue、label、评论、分支、PR、CI 就是全部
   编排状态。Agent 和 worktree 是易失资源：丢了重建，不为它们设计任何
   保护、回执或事务协议。
2. **思考集中在 Coordinator，动手交给 Worker。** 所有需要全局视野的判断
   （分诊、排程、验收、集成）由唯一的 Coordinator 做；Worker 拿到的是
   一段自包含的指令，不需要理解编排系统。
3. **乐观执行，事后对账。** 派发即授权，不做事前握手。兜底是 Coordinator
   的幂等 reconcile（对比 GitHub 实际状态）和 Git 的可逆性（分支可弃、
   PR 可关）。事前校验只保留在不可逆操作（merge、删除）之前。

## 4. 角色：只有两种

### 4.1 Coordinator（项目经理，不是排班器）

职责按时间顺序：

1. **分诊**：把新进来的粗糙 Issue 补齐四要素（任务描述、hotset、
   done_when、优先级），补不齐就打回报告人，不达标不派发。
2. **规划**：对整个 Issue 前沿全量重算 Wave Plan（§6）。
3. **派发**：选取与在飞工作无冲突的最高优先级 cluster，创建 Worker。
4. **监督**：只看客观信号（新 commit、PR 更新、进程存活）；把需要人
   拍板的问题攒成批量清单一次上报。
5. **验收**：CI + 亲自读 diff；高风险任务临时起一个一次性 Reviewer。
6. **集成**：唯一有权 merge、关闭 Issue、删除资源的角色，串行执行。
7. **（可选）速修**：S 级小活自己动手，不值得派一个 Worker（§7.3）。

### 4.2 Worker（一次性执行者）

- 绑定一个 cluster（通常 = 一个 Issue）、一个 worktree、一个分支、一个 PR。
- 它的整个世界 = 派发 prompt 全文。不读任何编排文档、不进任何 room、
  不与其他 Worker 通信。仓库级约定（代码风格、测试）由仓库自己的
  AGENTS.md/CLAUDE.md 承载，那是它本来就会读的。
- 交付 = commit + push + 开 PR + 在 Issue 评论 `DONE <pr-url>`，然后停止。
  不 merge、不关 Issue、不清理、不创建其他 Agent。
- 交付后即被归档。

明确不设的角色：Campaign、Relay、常驻 Reviewer、Monitor。

## 5. 状态模型：四个 label

```text
orch:ready     分诊完成，可派发
orch:active    已派发（评论记录 worker、分支、时间戳）
orch:review    PR 已开，待验收
orch:blocked   阻塞（评论记录原因：ASK 或 BLOCKED）
Issue 关闭      已合并交付
```

规则：

- 每次 label 迁移必须伴随一条结构化评论（谁、做什么、指向哪个分支/PR）。
- label 就是领取锁：派发时 `ready→active`，改不动说明已被取走，跳过。
  没有额外的锁或回执协议。
- **就绪定义**（贴 `orch:ready` 前必须满足）：任务描述足以让一个不看
  编排材料的 Agent 直接动手；hotset（允许改动的路径，尽量窄）；
  done_when（可复现的验证命令）；优先级（P0–P3）。
- Coordinator 冷启动恢复 = 三样东西：`gh issue list`（label + 评论）、
  `gh pr list`、置顶 planning Issue 里的 Wave Plan。意图以 Plan 为准，
  事实以 label 为准，冲突时以 label 为准并重规划。

## 6. 规划：Wave Plan 的生成算法

Coordinator 每轮规划对全部就绪 Issue **全量重算**（输入很小，开放 Issue
通常不到 50 个），输出覆盖式写入一个置顶 planning Issue。四步：

### 6.1 聚类（cluster）

两个 Issue 满足任一条件即并入同一 cluster：

- **代码重叠**：hotset 相交或落在同一子系统；
- **显式依赖**：存在 blocks / blocked-by 关系；
- **语义关联**：同一特性、同一 bug 根因、互相引用。

cluster 是派发的最小单位。高度耦合的小 Issue 同 cluster = **合并派发**
（一个 Worker、一个分支、一个 PR 交付多个 Issue），比拆开互相等待快；
大 Issue 永远单独成 cluster。

### 6.2 定级

`P0` 线上故障/阻塞他人 → `P1` 当前迭代目标 → `P2` 常规 → `P3` 机会型。
依据：优先级 label、是否阻塞其他 cluster、用户显式指令。
cluster 的优先级取成员最高值。

### 6.3 排波（list-scheduling，三条规则）

按"优先级降序 → 依赖层数升序"遍历所有 cluster，逐个放入**最早的**
同时满足以下条件的 wave，没有则开新 wave：

1. **依赖序**：它的全部前置 cluster 都在更早的 wave；
2. **冲突禁则**：它与该 wave 已有成员的 hotset 均无交集；
3. **容量**：该 wave 成员数 < 并发上限。

输出形如：

```text
# Wave Plan · 2026-07-19 16:20 · 并发 3
W1: C1 [P0] #101+#104 (src/auth/**) · C2 [P1] #118 (docs/**) · C4 [P2] #110 (src/api/**)
W2: C3 [P1] #102 (src/auth/token.py · 依赖 C1)
W3: C5 [P2] #120 (src/api/v2/** · 与 C4 冲突)
```

注意 C4：它是 P2，但与 W1 成员无冲突且槽位有空，于是**提前补位**——
优先级只决定挑拣顺序，不构成对低优先级工作的串行阻塞。

### 6.4 执行是流水线，wave 不是屏障

wave 编号只表达先后约束，不是等待边界：任何一个 Worker 交付并 merge
后，它占用的 hotset 立即释放，Coordinator 立刻从后续 wave 中把"前置已
全部 merge、与当前在飞 hotset 无交集"的最高优先级 cluster 提前派发。
槽位空转是最大浪费。

重规划触发：新 Issue 进入、优先级变化、cluster 交付或失败、用户指令。
全量重算的代价只是重写一个清单，鼓励频繁做。

## 7. 派发与执行

### 7.1 派发动作（Coordinator，共三步）

1. `ready→active` + 派发评论；
2. 创建 worktree `work/issue-<n>`（基于集成分支最新 commit）；
3. 创建 Worker，初始 prompt 用 §7.2 模板，**创建即开工**——
   没有 READY/START 握手，没有 room 预检，没有契约校验。

### 7.2 派发 prompt 模板（Worker 唯一需要读的东西，≤ 60 行）

```markdown
# 任务：<repo> Issue #<n> —— <标题>

你是一次性执行 Agent，在给定 worktree 里完成这一个 Issue，开 PR 后结束。
不要读编排系统的其他文档。

## 环境
- worktree：<绝对路径>（已创建，基于 <base-sha>）
- 分支：work/issue-<n>，PR 目标：<集成分支>
- 允许修改的范围（hotset）：<路径列表，范围外只读>

## 任务
<Issue 正文与验收标准，Coordinator 已填好，不需要再去读 Issue>

## 完成标准（done_when）
<可复现的验证命令列表，必须通过>

## 预算
最多 <60> 分钟 / <80> 回合。预计超支：把已有进展 commit+push（哪怕不完整），
在 Issue 评论 `BLOCKED: <原因>`，label 改 orch:blocked，停止。

## 交付
1. 实现最小可接受的改动，跑完 done_when 全部命令并确认通过；
2. commit、push、开 PR，描述写：改动摘要、跑过的命令及结果、范围外发现；
3. 在 Issue #<n> 评论 `DONE <pr-url>`，停止。不 merge，不清理。

## 禁区
merge / 关闭 Issue / 删除分支或 worktree / 创建其他 Agent。
需要决策（架构、兼容性、安全、超出 hotset）：评论 `ASK: <问题>`，
label 改 orch:blocked，停止。不要自行猜测。
```

### 7.3 速修通道：S 级 Coordinator 自己动手

同时满足：hotset ≤ 2 个文件、预期 diff ≤ 20 行、无架构/兼容性/安全决策、
done_when 运行 ≤ 2 分钟。

满足则 Coordinator 在自己的专用 scratch worktree 里直接修、开 PR、走
同样的验收流程。理由：S 级的派发开销（Agent 启动约 5–8 分钟）超过干活
本身，项目经理顺手修掉比派人快。

护栏：有待回答的 ASK、有待验收的 PR 或 Plan 过期时，先尽管理职责，
再动键盘。Coordinator 的主业永远是让别人快。

### 7.4 监督与升级

- 活性只看客观信号：分支有没有新 commit、PR 有没有更新、进程是否存活。
  没有心跳协议——自报的活性不算活性。
- 停滞判定：分支 30 分钟无新 commit 且 Agent 进程已死 → label 回
  `orch:ready`（评论注明沿用旧分支重派），下一空位基于旧分支的 WIP
  派新 Worker 继续。分支上已 push 的内容就是全部交接材料，什么都不丢。
- ASK/BLOCKED：授权范围内的 Coordinator 直接回复并恢复；范围外的
  **攒成一份批量清单**一次性报给用户，不逐个打断。

## 8. 验收与集成

验收（`orch:review`）：

1. **机器关（必过）**：CI 绿、diff 未越出 hotset、done_when 命令可复现。
2. **人审分级**：默认 Coordinator 亲自读 diff 对照验收标准；Issue 带
   `risk:high`（安全/迁移/公共 API 变更）时临时创建一个一次性 Reviewer，
   读完给出通过/打回意见即归档。不搞双轴、候选锁、结果聚合。
3. **打回**：PR 上留具体意见，prompt 原 Worker 修改（已死则基于分支
   重建），label 回 `orch:active`。

集成：

- merge 到集成分支**只由 Coordinator 串行执行**，天然互斥，不需要租约。
- merge 前唯一的事前校验：PR 基于集成分支最新 commit，落后则先
  update-branch 并等 CI 重跑。
- merge 后立即清理：关 Issue、删远程分支、删 worktree（先查脏，有未
  提交改动则保留并留言，绝不 force）、归档 Worker。
- Coordinator 永不清理自己、永不清理非自己创建的资源。

## 9. 容错：一条 reconcile 兜底全部

事件驱动（Worker 完成通知）+ 低频兜底轮询（10 分钟）。每轮 reconcile：

```text
gh 拉取全部 orch:* Issue、关联 PR 与 CI 状态
ready   → Plan 过期先重规划；有空位则按 §6 取无冲突的最高优先级项派发
active  → 停滞判定（§7.4）；活着的 Worker 绝不打扰
review  → 验收（§8）
blocked → 授权内直接答 ASK 并恢复 active；否则并入批量清单上报
验收通过的 PR → 按就绪顺序串行 merge（§8）
```

reconcile 幂等：从 GitHub 状态出发，任何一步崩溃后重跑收敛到同一结果。
这就是全部的容错机制——Worker 死了有分支，Coordinator 死了有 §5 三件套。

## 10. 配置：全部旋钮一张表

| 参数 | 默认值 | 说明 |
|---|---|---|
| 并发 Worker 数 | 3 / 仓库 | 唯一容量概念 |
| reconcile 兜底间隔 | 10 分钟 | 事件驱动之外的保险 |
| 停滞阈值 | 30 分钟无 commit 且进程死 | 触发重派 |
| Worker 预算 | 60 分钟 / 80 回合 | 写进派发 prompt |
| S 级速修线 | ≤2 文件、≤20 行 diff | §7.3 |
| 集成分支 | `dev` | merge 与 worktree 基线 |

## 11. 旧机制处置：每一项都要交代

| 旧机制 | 它声称防的故障 | 处置与替代 |
|---|---|---|
| Campaign 层 + 控制工作区 | 工作分组 | 删除。分组是 Wave Plan 里的一行清单，不是需要建制、监督、清理的运行时实体 |
| AGENT_READY/START 握手 | Worker 没准备好 | 删除。创建即开工；误派的代价是一个可弃分支 |
| WAKE/ACK 投递事务 | 消息丢失 | 删除。丢失只造成延迟，reconcile 幂等重放兜底 |
| room 协议 + paseo_room.py（2013 行） | 持久消息 | 删除。GitHub 评论就是持久消息总线 |
| 心跳协议 | 假活 | 删除。commit 与进程是更真实的活性信号 |
| 双轴 Reviewer + 候选锁 + 聚合 | 评审疏漏 | 删除。CI + 分级人审覆盖绝大部分价值（§8） |
| Integration Lease | merge 竞争 | 删除。单 Coordinator 串行 merge 天然互斥 |
| 全程 readback | 状态写坏 | 收缩为两处：merge 前基线校验、清理前查脏 |
| Operator Relay | 用户传话 | 删除。用户直接评论 Issue 或直接对话 Coordinator |
| 独立 intake 角色 | Issue 质量 | 并入 Coordinator 分诊职责，就绪定义见 §5 |

## 12. 落地清单（给实现 Agent）

1. `skills/orchestrator/SKILL.md`，≤ 300 行：本文 §4–§9 的可执行化，
   含 Wave Plan 清单格式与就绪定义检查单。
2. 派发 prompt 模板（§7.2），内嵌 SKILL 或独立模板文件。
3. 可选薄脚本 ≤ 200 行：封装 `gh` 的 label 迁移、结构化评论、前沿
   快照查询。**不做策略引擎**——判断全在 Coordinator，脚本只减少
   重复的 gh 调用。
4. 删除旧体系：`skills/github-work-orchestrator/`、
   `skills/github-issue-worker/`、`skills/github-issue-intake/`、`shared/`。
   Issue 契约中仍有用的字段（hotset、done_when、验收标准）已并入 §5
   就绪定义。
5. 本设计的验收标准：
   - 普通 Issue 从决定派发到 Worker 首次 commit ≤ 10 分钟；
   - 3 个互不冲突的 Issue 同时在飞，无任何串行等待；
   - 杀死任意 Worker，下一轮 reconcile 自动重派且已 push 进展不丢；
   - 重启 Coordinator，仅凭 GitHub 状态恢复全部在途工作。
