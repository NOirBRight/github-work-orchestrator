# GitHub and Paseo state rules

GitHub owns Issue lifecycle, dependencies, claims, PRs, checks, decisions, and
completion. Paseo owns runtime Agent sessions, parentage, labels, Workspaces,
worktrees, notifications, permissions, and rooms. Neither substitutes for the
other.

## Supervision and Workspace ownership

Keep one Repository Coordinator per repository. It is the single root Agent
with repository/role labels and survives every Campaign. Its Coordinator Home
Workspace stores the long-lived conversation; it need not itself be clean or on
`dev`. Select an Integration Control Worktree separately and pass its exact path
to integration Git commands.

Each Campaign has one coordinating Paseo Agent, user-visible as
`Campaign · <id> · <purpose>` and internally labeled role `orchestrator`. It is
a direct `subagent` of the Coordinator and may use its own Provider Binding.
Every new v4.3 Campaign receives a dedicated local Campaign Control Workspace
on `gwo/campaign/<id>`. Its branch is not pushed, has no PR, and carries no
feature commit. Legacy active Campaigns without one are not migrated.

If duplicate Coordinators exist, stop admission/integration, preserve both, and
require human durable handoff/adjudication. Unlabeled root Agents are foreign
and protected.

## Entry and Repository mailbox

Before GitHub reconciliation, run the entry policy. A stable root repository
Agent may become the Coordinator in place even when its home is dirty/non-dev.
Issue, Campaign, or Dispatch worktrees cannot be promoted. With an existing
Coordinator, an ordinary Task becomes a bounded Operator Relay: it posts one
sanitized request to the Repository Room, reads status once, wakes only an idle
Coordinator by Signal-ID, records a receipt, and idles. It does not read the
frontier, worktrees, or Campaign rooms.

The Repository Room is a persistent mailbox, not business truth. The
Coordinator replays it at startup, before wait, and before ending a turn.

## One editor per Issue

One claimed Issue maps to one `dispatch_id`, Paseo Worker, `work/issue-*`
branch, isolated worktree, and editor. Record repository/campaign/dispatch,
role, Issue, and branch labels. Provider-native subagent timelines are
read-only host evidence and cannot own a GWO Dispatch. GWO-owned Agents never
create Provider-native Agent/Task/Swarm children.

Claim and read back the Issue before create; read back exact parent, Provider,
mode, labels, Workspace, branch, and worktree before START.

## Execution and integration

Pin each Dispatch to exact `dev` SHA; PRs target `dev`. `main` receives only an
explicit verified release merge from `dev`. Coordinator/Campaign control
branches never carry feature commits; inline feature work also uses an isolated
`work/issue-*` worktree.

Different Campaigns execute concurrently only when canonical repository-relative
Hotsets do not overlap. Reject absolute paths, empty components, `.`/`..`, and
missing case-sensitivity evidence. One repository-scoped Integration Lease
serializes updates to `dev`, not implementation.

Require a clean Integration Control Worktree only at integration. Dirty/missing
control state holds candidates in `WAITING_INTEGRATION`, preserves user WIP,
and never triggers stash/reset/force-clean. If `dev` advanced, refresh the
pinned base and rerun affected evidence.

## Capacity

Defaults are one Campaign + three dedicated Worker slots + two dedicated Review
slots = six active Agents per Campaign. The global default is thirteen, exactly
two full Campaigns plus one Coordinator. Worker and Review slots are independent;
standard/strict work never reduces Worker parallelism below three. Foreign
active Paseo Agents consume global capacity. Empty UI drafts, archived Agents,
and terminal idle Relays do not.
Missing Reviewers retain their dedicated share of Campaign/global capacity;
foreign load may therefore shrink a standard/strict Worker wave without
reclassifying Review slots as Worker slots.

Plan and create the whole eligible Worker wave. Use one reusable Spec Reviewer
and one reusable Quality Reviewer per Campaign. They review one candidate at a
time; later verified candidates queue by ready time then Issue number. Partial
pair creation retains the successful axis. Re-read capacity before creating
each missing Reviewer and persist/read back the complete Candidate lock before
dispatching either review axis.

## Runtime and recovery

Campaign rooms and the Repository Room are replayable mailboxes, not authority
ledgers. Verify chat author against Paseo identity/parentage/labels and GitHub/Git
evidence. Reconcile on lost callback/restart; continue exact idle Agents,
preserve active/ambiguous WIP, and create successors only after terminal proof.
Silence and HEARTBEAT never authorize completion, replacement, or cleanup.

## Typed cleanup

Cleanup is event-triggered and fail closed. A Campaign cleans only its direct
Workers/Reviewers. After they are gone and `CAMPAIGN_CLOSED` is durable, the
Coordinator may archive that direct Campaign child only after an explicitly
read-backed empty direct-child enumeration. New Campaign cleanup then requires
its exact control worktree identity, unbound/clean state, zero unique commits,
and local-only branch before separately read-backed Workspace and branch
removal. Legacy Campaign cleanup is
explicitly Agent-only.

Never archive self/root/sibling/detached/foreign Agents, Coordinator Home,
Integration Control, actor control worktree, or dirty/active/ambiguous state.
Archive Agent first, read back archived + unbound, then authorize resource
actions. Protected plans contain no actions; `CAMPAIGN_CLOSED` never archives
the Coordinator.
