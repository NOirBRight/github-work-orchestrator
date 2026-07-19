# 轻量敏捷多 Agent 编排 —— 全新设计方案

> **Non-normative historical candidate.** V6 的当前锁定决策与待决问题见
> [`orchestrator-v6-living-design.md`](../orchestrator-v6-living-design.md)。本文件
> 保留用于比较，不得直接作为实现规格。

> 状态：草案，供实现 Agent 参考。本方案**替代**现有 GWO 协议，不向后兼容。
> 目标：任何一个普通 Issue，从派发到首次 commit 控制在 10 分钟以内；
> 编排本身的固定开销（文档阅读、握手、校验）不超过整个任务耗时的 5%。

## 0. 设计哲学

旧设计把每一步都当作不可信的分布式事务处理：readback、身份回执、
投递 ACK、候选锁、双轴审计、fail-closed。编排层自身体量：约 1100 行
协议文档（`shared/`）+ 约 8400 行脚本与参考（`skills/github-work-orchestrator/`），
Worker 开工前被要求消化的契约、房间协议、共享规范层层嵌套。实测后果：
codexhub 与 ayaspace2 两个仓库的编排 Agent 建制一小时、零行代码交付。
新设计的三条原则：

1. **GitHub 是唯一持久状态，其余一切皆可丢弃。**
   Issue、label、branch、PR、CI、评论就是全部编排状态。Agent、worktree、
   room 消息都是易失的执行资源——崩了就重建，不需要保护、回执或事务。
2. **乐观执行 + 事后对账，而非事前握手。**
   不做逐步确认。派发即授权，出错的兜底是 Coordinator 的周期性 reconcile
   （对比 GitHub 实际状态），以及 Git 本身的可逆性（分支可弃、PR 可关）。
   事前校验只保留在**不可逆操作**（merge、删除）之前。
3. **按任务规模付编排成本。**
   一行代码的修复不应该和一次大重构走同一条流程。默认走最短路径，
   只在确有依赖冲突或高风险时升级。

## 1. 角色：只有两种

```text
Coordinator（每仓库一个，长驻）
└─ Worker（每 Issue 一个，用完即弃）
```

- **Coordinator**：长驻 Agent，角色是**项目经理**而不只是排班器——
  负责分诊（把粗糙报告就地补全为可派发 Issue，见 §2 就绪定义）、
  理解 Issue 前沿、按相关度聚类、按紧急度分层、规划执行 wave（§4）、
  派发、验收、合并、清理。它是唯一有权 merge 到集成分支和删除资源的角色。
- **Worker**：一次性 Agent，绑定一个 Issue、一个 worktree、一个分支、一个 PR。
  交付后即被归档。Worker 之间互不通信。

不再有 Campaign、Relay、Spec/Quality Reviewer、Monitor 等中间角色。
需要 review 时由 Coordinator 直接做，或临时起一个一次性 Reviewer（见 §6）。

### 1.1 启动与唯一性

- Coordinator 由**用户显式创建**（或重启），每仓库一个。没有自动选举、
  提升或 Relay。
- Coordinator 必须住在**稳定的长驻 workspace**（仓库的持久 checkout），
  绝不能创建在一次性 feature worktree 里：宿主运行时可能在该 worktree
  分支的 PR 合并时自动归档整个 worktree——连同里面的 Agent（实测：Paseo
  的 auto-archive-on-merge）。同理，Coordinator 永不从自己的 workspace
  分支开 PR；它的产物（PLAN.md、label、评论）直接落到 GitHub 或集成分支。
- 启动时不做迁移或握手：直接跑一轮 reconcile，从 `orch:*` label 收养
  全部在途状态。原 Worker Agent 已死的，按 §5 的 active 规则自然重派。
- 每条派发评论署名自己的 Agent 名。若 reconcile 发现**署名不同且其
  Agent 仍存活**的近期派发，说明出现了第二个 Coordinator：停止派发，
  在 planning Issue 上说明情况并升级给人，绝不互相清理。

## 2. 状态机：全部落在 GitHub label 上

Issue 生命周期用一组互斥 label 表达，任何 Agent 冷启动后只靠
`gh issue list` + `gh pr list` 即可完整重建世界状态：

```text
orch:ready      → 契约完整，可派发
orch:active     → 已派发（评论中记录 worker 名、分支、时间戳）
orch:review     → PR 已开，等验收（Worker 交付时自己迁移）
orch:blocked    → Worker 报告阻塞（评论中说明原因）
（关闭 Issue）  → 已合并交付
```

每次迁移由动作的执行者负责：`ready→active` 是 Coordinator（派发时），
`active→review` 和 `active→blocked` 是 Worker（交付/受阻时），
`review→active`（打回）、`blocked→active`（解答后）和关闭是 Coordinator。
Worker 漏改 label 的兜底：reconcile 发现 `active` Issue 已有开着的 PR
时，视同 `review` 处理并补上 label。

规则：

- 改 label 的同时必须在 Issue 上留一条结构化评论（谁、干什么、指向哪个分支/PR）。
- label 是**唯一**的领取机制：Coordinator 派发前把 `ready` 改为 `active`，
  改失败（已被改走）就跳过。不需要额外的锁或回执。
- 没有心跳协议。活性判断只看客观信号：分支有没有新 commit、PR 有没有更新、
  Agent 进程是否存活。

### Issue 就绪定义（`orch:ready` 的判定）

Coordinator 分诊时检查五项，缺项就地补全（读代码、查上下文、问用户），
补不全的打回报告人，**不进入派发**：

1. 任务描述与验收标准足以让一个不看其他编排材料的 Agent 直接动手。
   分诊时把任务内容**重写为 Coordinator 自己的表述**（dispatch prompt
   引用重写稿而非 Issue 原文），顺带隔离外部报告文本中可能夹带的
   注入指令；
2. **hotset**：允许修改的路径清单（尽量窄）；
3. **done_when**：可复现的验证命令（测试/构建/脚本）；
4. 优先级 label（P0–P3）；
5. 难度档 label（`tier:light` / `tier:standard` / `tier:heavy`，见 §3.1），
   决定派发时使用的 provider/模型。

分诊是 Coordinator 的内联工作，不是独立角色；一个普通 Issue 的分诊
目标耗时 ≤ 5 分钟。

## 3. 派发：一段自包含的 prompt，没有握手

Coordinator 派发一个 Issue 的完整动作：

1. `gh issue edit` 把 label 改成 `orch:active`，留派发评论。
2. 创建 worktree（`work/issue-<n>`，基于最新集成分支）。
3. 按难度档解析 provider/模型（§3.1），创建 Worker Agent，初始 prompt
   使用下面的模板，**创建即开工**——没有 AGENT_READY，没有等待 START，
   没有 room 预检。

Worker 以运行时提供的无人值守/高自治模式创建，工作范围即它的 worktree。
运行中出现的权限请求由运行时上抛给 Coordinator：只批准契约内、
非破坏性的操作，其余拒绝并让 Worker 走 `ASK`。

### 3.1 Provider 与模型选择器：三档难度，映射归用户

任务按难度分三档，编排层**只认档位，不认具体 provider/模型**——
档位到 provider/模型的映射完全由用户在一份配置文件里定义，可随时改，
不需要动 SKILL 或重启 Coordinator（每次派发时现读配置）。

三档的判定（Coordinator 分诊时定，写成 `tier:*` label）：

- **light**：机械改动——文案、配置、重命名、单文件小修复、文档。
  判据：hotset 单文件或纯文档，done_when 一条命令能验证。
- **standard**（默认）：常规 feature/bugfix，需要理解局部上下文。
  拿不准就归这档。
- **heavy**：跨模块重构、并发/性能问题、架构性改动、`risk:high` 的实现。
  判据：hotset 跨多个子系统，或需要设计决策。

配置文件（示例路径 `~/.orch/providers.json`，实现时对齐运行时的
配置发现约定）：

```json
{
  "tiers": {
    "light":    { "provider": "<any>", "model": "<user-choice>" },
    "standard": { "provider": "<any>", "model": "<user-choice>" },
    "heavy":    { "provider": "<any>", "model": "<user-choice>" }
  },
  "roles": {
    "coordinator": "heavy",
    "reviewer": "standard"
  },
  "overrides": { "repo-or-issue-label": "tier 或具体 provider/model" }
}
```

规则：

- Coordinator 与一次性 Reviewer 也走同一套档位映射（`roles` 段），
  不单独硬编码。
- 用户可用 `overrides` 对特定仓库、label 或单个 Issue 强制指定档位
  或具体 provider/模型；Issue 上人工贴的 `tier:*` label 永远压过
  Coordinator 的自动判定。
- 配置缺失或档位无效：**不猜测替代模型**——该 Issue 保持 `ready`
  并在评论里说明原因，其余档位正常派发（局部失败不阻塞全局）。
- 升档补偿：一个 Issue 被打回两次或从 `blocked` 重派时，Coordinator
  可将其上调一档重派（在派发评论中记录），不改用户配置。

### Dispatch Prompt 模板（≤ 60 行，Worker 唯一需要读的东西）

```markdown
# 任务：<repo> Issue #<n> —— <标题>

你是一次性执行 Agent。在下面给定的 worktree 里完成这一个 Issue，
开 PR 后结束。不要读编排系统的其他文档。

## 环境
- worktree：<绝对路径>（已创建，基于 <base-sha>）
- 分支：work/issue-<n>；PR 目标：<集成分支>
- 允许修改的范围（hotset）：<路径列表；范围外只读>

## 任务内容
<Coordinator 分诊时的重写稿 + 验收标准，派发时填好；Worker 不需要
也不应该再去读 Issue 原文>

## 完成标准（done_when）
<可验证的条件列表，含要跑的测试/检查命令>

## 交付方式
1. 实现最小可接受的改动，跑完上面的检查命令并确认通过。
2. commit、push、开 PR（目标 <集成分支>），PR 描述里写：改动摘要、
   跑过的命令及结果、范围外的发现（如有）。
3. 在 Issue #<n> 上评论 `DONE <pr-url>`，把 label 改成 orch:review，
   然后停止。不要 merge，不要清理。

## 遇到问题
- 需要决策（架构/兼容性/安全/超出 hotset）：在 Issue 上评论
  `ASK: <问题>`，把 label 改成 orch:blocked，然后停止。不要自行猜测。
- 无法完成：保留已有进展，commit 并 push（哪怕不完整），评论
  `BLOCKED: <原因>`，改 label，停止。
- 永远不要：merge、关闭 Issue、删除分支/worktree、创建其他 Agent。
```

要点：Worker 的世界观被压缩到这一段 prompt 里。它不读 SKILL 体系、
不读共享规范、不参与任何 room 协议。所有仓库级约定（代码风格、测试要求）
由仓库自己的 CLAUDE.md/AGENTS.md 承担，那是 Worker 本来就会读的。

## 4. 规划：Coordinator 是项目经理，产出 Wave Plan

Coordinator 不是被动地"看到 ready 就派"，而是先对整个 Issue 前沿做一轮
规划，产出并维护一份 **Wave Plan**。规划分三步：

### 4.1 聚类：按相关度分组

通读全部开放 Issue，按以下信号把 Issue 聚成组（cluster）：

- **代码重叠**：hotset 相交或落在同一模块/子系统；
- **语义关联**：同一 feature、同一 bug 根因、互相引用、共享验收标准；
- **显式依赖**：Issue 间标注的 blocks/blocked-by 关系。

同组内高度耦合的小 Issue 可以**合并派发**给同一个 Worker（一个分支、
一个 PR 交付多个 Issue），比拆开后互相等待更快；耦合松的保持独立。

合并派发的机制：选编号最小的成员为**主 Issue**；分支/worktree 沿用
`work/issue-<主>`；全部成员 Issue 同步迁移 label；dispatch prompt 的
"任务内容"与"done_when"合并列出全部成员；`DONE`/`ASK`/`BLOCKED` 评论
只发在主 Issue，其余成员各留一条指向主 Issue 的链接评论；PR 描述用
`Closes #x` 关联全部成员，merge 时一并关闭。

### 4.2 定级：按紧急度分层

给每个 cluster 定优先级（`P0` 线上故障/阻塞他人 → `P1` 当前迭代目标 →
`P2` 常规 → `P3` 机会性），依据：Issue 上的优先级 label、是否阻塞
其他 cluster、用户显式指令。同 cluster 内的 Issue 继承 cluster 的层级。

### 4.3 排 wave：以并行度为第一目标

Wave 是一次并行派发的批次。排布规则按顺序应用：

1. **冲突约束（硬）**：hotset 相交的 cluster 不进同一个 wave；
   有依赖关系的 cluster 按依赖顺序进不同 wave。
2. **优先级（软）**：高优先级 cluster 优先占用早期 wave 的槽位；
   但当高优先级 cluster 之间互相冲突时，允许低优先级、hotset 无关的
   cluster **提前补位**——槽位空转比乱序更贵。
3. **装箱**：在并发上限内尽量填满每个 wave。相互无冲突的 cluster
   永远不要串行等待。

关键性质——**wave 是流水线，不是屏障**：一个 Worker 交付并 merge 后，
它占用的 hotset 立即释放，下一 wave 中与"仍在飞行中的 hotset"不冲突的
Issue 即刻派发，不等本 wave 其余成员收尾。所谓 wave 边界只对
"互相冲突的两批工作"存在。

### 4.4 Plan 的存放与更新

Wave Plan 本身也是 GitHub 状态：写在一个置顶的 planning Issue（或仓库内
一个 `PLAN.md`）里，格式为简单的有序清单（wave → cluster → issues →
hotset → 优先级），每次重规划整体覆盖。它是 Coordinator 的工作记忆，
重启后据此恢复意图；但**派发事实**仍以 label 为准（§2），两者冲突时
以 label 为准、重新规划。

示例（注意 C4 演示 §4.3 规则 2：优先级虽低，但与在飞 hotset 无冲突，
直接补位并行，不被 wave 编号挡住）：

```text
# Wave Plan · 2026-07-19 15:00 · 并发上限 3
## 即刻（互不冲突，全部并行派发）
- C1 [P0] #101 #104  · hotset src/auth/**        · 同根因，合并派发
- C2 [P1] #118       · hotset docs/**
- C4 [P2] #110 #111  · hotset src/api/**         · 低优先级补位
## 等 C1 合并释放 src/auth/**
- C3 [P1] #102       · hotset src/auth/token.py  · 显式依赖 C1
## 等 C4 合并释放 src/api/**
- C5 [P2] #120       · hotset src/api/v2/**
```

重规划的触发：新 Issue 进入、优先级变化、某 cluster 交付或失败、
用户指令。重规划是廉价的（重写一个清单），鼓励频繁做，不要求增量修补。

## 5. 通信：异步单向，靠对账兜底

- **Worker → 世界**：只写 GitHub（Issue 评论 + PR）。写完不等任何确认。
- **Coordinator → Worker**：仅两种情况需要主动发消息——回答 `ASK`、
  review 打回修改。直接给该 Agent 发一条 prompt 即可；如果 Agent 已死，
  就基于其分支重建一个新 Worker（分支上的 WIP 就是全部交接材料）。
- **没有** WAKE/ACK/身份回执/投递事务。消息丢失的后果只是延迟，
  由下一轮 reconcile 补上。

### Coordinator 主循环

事件驱动 + 低频兜底轮询：

```text
loop:
  等待事件（Worker finish 通知 / 定时器，兜底间隔 10 分钟）
  reconcile:
    gh 拉取全部 orch:* Issue 与关联 PR、CI 状态
    对每个 Issue 按 label 分派动作：
      ready   → 若有空位（默认并发 3 个 Worker），按 Wave Plan（§4）
                选取与在飞 hotset 无冲突的最高优先级项派发；
                Plan 过期（新 Issue/优先级变化/交付释放了 hotset）则先重规划
      active  → 分支 30 分钟无 commit 且 Agent 已死 → 打回 ready 重派；
                否则不动（绝不打扰活着的 Worker）
      review  → 走验收（§6）
      blocked → 若是 ASK 且答案在授权范围内，直接回复并恢复 active；
                否则升级给人：在 Issue 上 @ 用户、评论 NEEDS-HUMAN 与
                具体问题，保持 blocked 并不再自动处理该 Issue，
                直到出现新的人类评论
    验收通过的 PR 按就绪顺序逐个 merge（§7）
```

reconcile 是幂等的：从 GitHub 状态出发，任何一步崩溃后重跑都收敛到
相同结果。这就是全部的容错机制。

## 6. 验收：CI 承重，人审分级

`orch:review` 的 Issue 由 Coordinator 处理：

1. **机器关卡（必过）**：CI 绿、diff 未越出 hotset、done_when 的命令可复现。
   仓库没有 CI 时，退化为 Coordinator 在该 PR 的干净 checkout 里亲自
   复跑 done_when 命令，以自己的执行结果为准。
2. **人审分级**：
   - 默认：Coordinator 自己读 diff 对照验收标准，通过即可。
   - Issue 带 `risk:high` label（安全/迁移/公共 API 变更）：临时创建一个
     一次性 Reviewer Agent 读同一个 PR，产出通过/打回意见后归档。
     不搞双轴、候选锁或结果聚合——一个认真读 diff 的 Reviewer 加上 CI，
     覆盖了旧设计双 Reviewer 的绝大部分价值。
3. 打回：在 PR 上留具体意见，给原 Worker 发一条 prompt（Agent 已死则
   基于其分支重建）；label 回到 `orch:active`。

## 7. 集成与清理

- 集成分支的 merge **只由 Coordinator 串行执行**，天然互斥，不需要 Lease。
  merge 前：确认 PR 基于最新集成分支（落后则先 rebase/update-branch 并
  等 CI 重跑）。这是全流程中唯一保留"事前校验"的地方。
- merge 后立即清理：关闭 Issue、删除远程分支、删 worktree、归档 Worker。
  清理前的唯一检查：worktree 无未提交改动（有则保留并留言，绝不 force）。
- Coordinator 永远不清理自己、不清理非自己创建的资源。

## 8. 并发与冲突：用 hotset 排班，别用锁

- Coordinator 在规划阶段（§4）给每个 Issue/cluster 声明 hotset
  （文件/目录列表）。hotset 相交的工作不同时在飞，仅此而已——
  没有运行时锁协议。
- 声明不准导致的冲突在 merge 时由 Git 暴露：后到的 PR rebase 解决，
  解决不了就打回 Worker。冲突是偶发成本，用"偶尔返工"支付，
  比"每次派发都做全套互斥校验"便宜得多。
- 默认并发：3 个 Worker/仓库。这是一个配置项，不是一套容量协议。

## 9. 明确删除的机制（及理由）

| 旧机制 | 处置 | 理由 |
|---|---|---|
| Campaign 层级 + Campaign Control Workspace | 删除 | 两层监督树对单仓库并发 ≤3 没有收益，建制成本却是小时级 |
| AGENT_READY/START 握手 | 删除 | 创建即授权；误创建的代价是一个可弃分支 |
| Material Delivery（WAKE/ACK/身份回执） | 删除 | 可靠性由 reconcile 幂等重放提供，不需要逐条投递事务 |
| Room 协议 + 2000 行 paseo_room.py | 删除 | GitHub 评论就是持久消息总线 |
| HEARTBEAT | 删除 | 活性看 commit/进程等客观信号，不靠自报 |
| 双轴 Reviewer + 候选锁 + 结果聚合 | 删除 | CI + 分级人审替代（§6） |
| Campaign 作为分组单位 | 由 cluster/wave 替代 | 分组是**规划概念**（一个清单条目），不再是需要建制、监督和清理的运行时实体 |
| Integration Lease | 删除 | Coordinator 串行 merge 天然互斥 |
| 全流程 readback | 收缩 | 只保留 merge 前基线检查与清理前脏检查 |
| Operator Relay | 删除 | 用户直接在 Issue 上评论，或直接和 Coordinator 对话 |

## 10. 交付物清单（给实现 Agent）

1. `skills/orchestrator/SKILL.md` —— Coordinator 的完整行为规范，
   目标 ≤ 300 行（即本方案 §2–§8 的可执行化，含 Wave Plan 清单格式）。
2. Dispatch prompt 模板（§3）—— 作为 SKILL 内嵌模板或独立模板文件。
3. Provider 三档配置的 schema 与示例文件（§3.1），含缺失/无效时的
   fail-closed 行为说明。
4. 一个薄工具脚本（可选，≤ 200 行）：封装 `gh` 的 label 迁移 + 结构化
   评论 + 前沿快照查询。**不做**策略引擎——决策留给 Coordinator 的判断，
   脚本只负责减少重复的 gh 调用。
5. 删除旧体系：`skills/github-work-orchestrator/`、
   `skills/github-issue-worker/`、`skills/github-issue-intake/`、
   `shared/` 下的协议文档。Issue 契约中仍有用的字段（hotset、done_when、
   验收标准）并入新 SKILL 的 Issue 就绪定义。
6. 验收本设计本身的标准：
   - 派发一个普通 Issue，从 Coordinator 决定派发到 Worker 首次 commit
     ≤ 10 分钟；
   - 杀死任意 Worker 后，下一轮 reconcile 能自动重派且不丢已 push 的进展；
   - Coordinator 重启后，仅凭 GitHub 状态恢复全部在途工作。
