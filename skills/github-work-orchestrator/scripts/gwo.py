#!/usr/bin/env python3
"""gwo CLI: the stdlib-only GWO V7 coordination kernel entry point.

Resolves caller identity from the spawn-injected ``GWO_AGENT_ID`` environment
variable on every write and delegates state transitions to the gwo store. Each
command is one explicit SQLite transaction; failures roll back cleanly without
partial authority or lifecycle state. GitHub is the only durable business
truth; the store is a rebuildable coordination cache.

Invoke as ``python <skill>/scripts/gwo.py <command> ...``. State lives under
``GWO_HOME`` (default ``~/.gwo/<repo-slug>/state.db``) in WAL mode.

See docs/design/gwo-v7-architecture.md and ADRs 0007-0009.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import gwo_store  # type: ignore[import-not-found]  # noqa: E402


REPOSITORY_RE = "^[^/\\s]+/[^/\\s]+$"
NO_DEFAULT_REPOSITORY = "__REQUIRED__"


def _repository(value: str) -> str:
    import re

    if not re.fullmatch(REPOSITORY_RE, value.strip()):
        raise argparse.ArgumentTypeError("repository must be owner/repo")
    return value.strip()


def _repository_default() -> str | None:
    """Return the environment fallback only when it is a valid owner/repo."""
    import re

    value = os.environ.get("GWO_REPOSITORY")
    if value is None:
        return NO_DEFAULT_REPOSITORY
    if re.fullmatch(REPOSITORY_RE, value.strip()):
        return value.strip()
    return NO_DEFAULT_REPOSITORY


def _store(args: argparse.Namespace) -> gwo_store.Store:
    return gwo_store.Store.connect(args.gwo_home, args.repository)


def _emit(payload: Any) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
    sys.stdout.write("\n")


def _fail(message: str, code: int = 1) -> int:
    sys.stderr.write(f"error: {message}\n")
    return code


def _controlled_error(error: BaseException) -> int:
    """Convert any domain/validation error into a controlled CLI error.

    Catches RuntimeError subclasses (StoreError, MailboxError, StatusError,
    EntitlementError, SignalIdError, DeliveryError, TransitionError),
    argparse.ArgumentTypeError (malformed JSON), and any other Exception
    that is not SystemExit/KeyboardInterrupt. Never lets a traceback reach
    the user for domain validation failures.
    """
    if isinstance(error, SystemExit):
        raise error
    if isinstance(error, KeyboardInterrupt):
        raise error
    return _fail(str(error))


def cmd_coordinator(args: argparse.Namespace) -> int:
    store = None
    try:
        store = _store(args)
        if args.action == "claim":
            store.claim_coordinator()
            _emit({"repo": args.repository, "holder": store.coordinator_holder()})
            return 0
        if args.action == "release":
            store.release_coordinator()
            _emit({"repo": args.repository, "holder": None})
            return 0
        return _fail(f"unknown coordinator action: {args.action}")
    except Exception as error:
        return _controlled_error(error)
    finally:
        if store is not None:
            store.close()


def cmd_task(args: argparse.Namespace) -> int:
    store = None
    try:
        store = _store(args)
        if args.action == "create":
            task = store.create_task(
                issue=args.issue,
                group_label=args.group,
                risk=args.risk,
                hotset=args.hotset,
                deps=args.deps,
                created_by=args.created_by,
            )
            _emit(task)
            return 0
        if args.action == "list":
            _emit(store.list_tasks())
            return 0
        if args.action == "update":
            task = store.update_task(
                task_id=args.task_id,
                status=args.status,
                hotset=args.hotset,
                deps=args.deps,
            )
            _emit(task)
            return 0
        return _fail(f"unknown task action: {args.action}")
    except Exception as error:
        return _controlled_error(error)
    finally:
        if store is not None:
            store.close()


def cmd_dispatch(args: argparse.Namespace) -> int:
    store = None
    try:
        store = _store(args)
        if args.action == "create":
            dispatch = store.create_dispatch(
                task_id=args.task_id,
                agent_id=args.agent_id,
                worktree=args.worktree,
                branch=args.branch,
                dispatched_by=args.dispatched_by,
            )
            _emit(dispatch)
            return 0
        return _fail(f"unknown dispatch action: {args.action}")
    except Exception as error:
        return _controlled_error(error)
    finally:
        if store is not None:
            store.close()


def cmd_done(args: argparse.Namespace) -> int:
    store = None
    try:
        store = _store(args)
        evidence = _json_value(args.evidence)
        dispatch = store.mark_done(
            task_id=args.task_id,
            dispatch_id=args.dispatch_id,
            status=args.status,
            actor=args.actor,
            evidence=evidence,
        )
        _emit(dispatch)
        return 0
    except Exception as error:
        return _controlled_error(error)
    finally:
        if store is not None:
            store.close()


def _json_value(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        result = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON: {error}")
    if not isinstance(result, dict):
        raise ValueError("expected a JSON object")
    return result


def _json_list_or_obj(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON: {error}")


def cmd_send(args: argparse.Namespace) -> int:
    store = None
    try:
        store = _store(args)
        payload = _json_value(args.payload)
        msg = store.send(
            to_agent=args.to,
            event_type=args.type,
            payload=payload,
            signal_id=args.signal_id,
            in_reply_to=args.in_reply_to,
        )
        _emit(msg)
        return 0
    except Exception as error:
        return _controlled_error(error)
    finally:
        if store is not None:
            store.close()


def cmd_ask(args: argparse.Namespace) -> int:
    store = None
    try:
        store = _store(args)
        payload = _json_value(args.payload)
        msg = store.ask(
            to_agent=args.to,
            payload=payload,
            signal_id=args.signal_id,
            timeout=args.timeout,
        )
        _emit(msg)
        return 0
    except Exception as error:
        return _controlled_error(error)
    finally:
        if store is not None:
            store.close()


def cmd_inbox(args: argparse.Namespace) -> int:
    store = None
    try:
        store = _store(args)
        messages = store.inbox(
            agent_id=args.agent_id,
            ack_on_read=args.ack_on_read,
            dispatch_id=args.dispatch_id,
            wait=args.wait,
        )
        _emit(messages)
        return 0
    except Exception as error:
        return _controlled_error(error)
    finally:
        if store is not None:
            store.close()


def cmd_agent(args: argparse.Namespace) -> int:
    store = None
    try:
        store = _store(args)
        if args.action == "status":
            snapshot_path = getattr(args, "readback_snapshot", None)
            status = store.agent_status(
                args.agent_id, readback_snapshot_path=snapshot_path
            )
            _emit(status)
            return 0
        if args.action == "register":
            row = store.register_agent(
                agent_id=args.agent_id,
                adapter=args.adapter,
                runtime_ref=args.runtime_ref,
                role=args.role,
                group_label=args.group_label,
                session_id=args.session_id,
                pid=args.pid,
            )
            _emit(row)
            return 0
        return _fail(f"unknown agent action: {args.action}")
    except Exception as error:
        return _controlled_error(error)
    finally:
        if store is not None:
            store.close()


def cmd_config(args: argparse.Namespace) -> int:
    try:
        import gwo_status
        if args.action == "check":
            result = gwo_status.preflight_config(args.gwo_home, args.repository)
            _emit(result)
            return 0 if result["valid"] else 1
        return _fail(f"unknown config action: {args.action}")
    except Exception as error:
        return _fail(str(error))


def cmd_doctor(args: argparse.Namespace) -> int:
    store = None
    try:
        store = _store(args)
        if args.action == "rebuild":
            result = store.doctor_rebuild(
                github_snapshot=_json_list_or_obj(args.github_snapshot),
                adapter_listing=_json_list_or_obj(args.adapter_listing),
                git_worktrees=_json_list_or_obj(args.git_worktrees),
            )
            _emit(result)
            return 0
        return _fail(f"unknown doctor action: {args.action}")
    except Exception as error:
        return _controlled_error(error)
    finally:
        if store is not None:
            store.close()


def cmd_guard(args: argparse.Namespace) -> int:
    """Run deterministic plan guards without touching the store."""
    if args.action == "check-dag":
        import gwo_dag  # type: ignore[import-not-found]  # noqa: E402
        return gwo_dag.main(args)
    return _fail(f"unknown guard action: {args.action}")


def cmd_review(args: argparse.Namespace) -> int:
    store = None
    try:
        store = _store(args)
        if args.action == "round-create":
            row = store.issue_review_round(
                dispatch_id=args.dispatch_id,
                round=args.round,
                candidate_sha=args.candidate_sha,
                base_sha=args.base_sha,
                diff_digest=args.diff_digest,
                acceptance_digest=args.acceptance_digest,
                scope=args.scope,
                prior_round_id=args.prior_round_id,
                round_id=args.round_id,
                issued_by=args.issued_by,
            )
            _emit(row)
            return 0
        if args.action == "result-create":
            row = store.submit_review_result(
                round_id=args.round_id,
                axis=args.axis,
                verdict=args.verdict,
                findings=_json_value(args.findings),
                agent_id=args.agent_id,
                candidate_sha=args.candidate_sha,
                base_sha=args.base_sha,
                diff_digest=args.diff_digest,
                acceptance_digest=args.acceptance_digest,
                scope=args.scope,
                round=args.round,
                prior_round_id=args.prior_round_id,
            )
            _emit(row)
            return 0
        return _fail(f"unknown review action: {args.action}")
    except Exception as error:
        return _controlled_error(error)
    finally:
        if store is not None:
            store.close()


def cmd_lease(args: argparse.Namespace) -> int:
    store = None
    try:
        store = _store(args)
        if args.action == "acquire":
            row = store.acquire_integration_lease(
                scope=args.scope,
            )
            _emit(row)
            return 0
        if args.action == "release":
            row = store.release_integration_lease(
                scope=args.scope,
            )
            _emit(row)
            return 0
        if args.action == "chain-append":
            row = store.append_integration_chain(
                scope=args.scope,
                candidate_sha=args.candidate_sha,
                task_id=args.task_id,
            )
            _emit(row)
            return 0
        if args.action == "chain-list":
            rows = store.list_integration_chain(scope=args.scope)
            _emit(rows)
            return 0
        return _fail(f"unknown lease action: {args.action}")
    except Exception as error:
        return _controlled_error(error)
    finally:
        if store is not None:
            store.close()


def _json_list(value: str | None) -> list[str] | None:
    if value is None:
        return None
    try:
        result = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON: {error}")
    if not isinstance(result, list):
        raise ValueError("expected a JSON list")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gwo.py",
        description="GWO V7 coordination kernel (stdlib-only, WAL store).",
    )
    parser.add_argument(
        "--gwo-home",
        type=Path,
        default=None,
        help="GWO_HOME directory (defaults to $GWO_HOME or ~/.gwo)",
    )
    parser.add_argument(
        "--repository",
        type=_repository,
        default=_repository_default(),
        help="repository as owner/repo (default: $GWO_REPOSITORY if valid)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    coordinator = sub.add_parser("coordinator", help="claim or release the coordinator lock")
    coordinator.add_argument("action", choices=("claim", "release"))
    coordinator.set_defaults(func=cmd_coordinator)

    task = sub.add_parser("task", help="create, list, or update tasks")
    task_sub = task.add_subparsers(dest="action", required=True)

    task_create = task_sub.add_parser("create")
    task_create.add_argument("--issue", type=int, required=True)
    task_create.add_argument("--group", required=True)
    task_create.add_argument("--risk", required=True, choices=("fast", "standard", "strict"))
    task_create.add_argument("--hotset", type=_json_list, default=None)
    task_create.add_argument("--deps", type=_json_list, default=None)
    task_create.add_argument("--created-by", default=None, help=argparse.SUPPRESS)
    task_create.set_defaults(func=cmd_task)

    task_list = task_sub.add_parser("list")
    task_list.set_defaults(func=cmd_task)

    task_update = task_sub.add_parser("update")
    task_update.add_argument("task_id")
    task_update.add_argument("--status", default=None)
    task_update.add_argument("--hotset", type=_json_list, default=None)
    task_update.add_argument("--deps", type=_json_list, default=None)
    task_update.set_defaults(func=cmd_task)

    dispatch = sub.add_parser("dispatch", help="store-only dispatch")
    dispatch_sub = dispatch.add_subparsers(dest="action", required=True)

    dispatch_create = dispatch_sub.add_parser("create")
    dispatch_create.add_argument("--task-id", required=True)
    dispatch_create.add_argument("--agent-id", required=True)
    dispatch_create.add_argument("--worktree", required=True)
    dispatch_create.add_argument("--branch", required=True)
    dispatch_create.add_argument("--dispatched-by", default=None, help=argparse.SUPPRESS)
    dispatch_create.set_defaults(func=cmd_dispatch)

    done = sub.add_parser("done", help="mark a dispatch done/blocked/stopped")
    done.add_argument("--task-id", required=True)
    done.add_argument("--dispatch-id", required=True)
    done.add_argument("--status", required=True, choices=("done", "blocked", "stopped"))
    done.add_argument("--evidence", default=None, help="JSON object terminal evidence")
    done.add_argument("--actor", default=None, help=argparse.SUPPRESS)
    done.set_defaults(func=cmd_done)

    send = sub.add_parser("send", help="post one mailbox event")
    send.add_argument("--to", required=True, help="recipient agent id")
    send.add_argument("--type", required=True, help="event type")
    send.add_argument("--signal-id", required=True, dest="signal_id")
    send.add_argument("--payload", default=None, help="JSON object payload")
    send.add_argument("--in-reply-to", default=None, dest="in_reply_to")
    send.set_defaults(func=cmd_send)

    ask = sub.add_parser("ask", help="post an ask event (blocking sugar over send)")
    ask.add_argument("--to", required=True, help="recipient agent id")
    ask.add_argument("--signal-id", required=True, dest="signal_id")
    ask.add_argument("--payload", default=None, help="JSON object payload")
    ask.add_argument("--timeout", type=float, default=30.0)
    ask.set_defaults(func=cmd_ask)

    inbox = sub.add_parser("inbox", help="read/wait for mailbox events")
    inbox.add_argument("--agent-id", required=True, dest="agent_id")
    inbox.add_argument("--ack-on-read", action="store_true", dest="ack_on_read")
    inbox.add_argument("--dispatch-id", default=None, dest="dispatch_id")
    inbox.add_argument("--wait", type=float, default=None)
    inbox.set_defaults(func=cmd_inbox)

    agent = sub.add_parser("agent", help="agent status and registration")
    agent_sub = agent.add_subparsers(dest="action", required=True)

    agent_status = agent_sub.add_parser("status")
    agent_status.add_argument("agent_id")
    agent_status.add_argument(
        "--readback-snapshot", default=None, dest="readback_snapshot",
        help="JSON file with runtime readback snapshot",
    )
    agent_status.set_defaults(func=cmd_agent)

    agent_register = agent_sub.add_parser("register")
    agent_register.add_argument("--agent-id", required=True, dest="agent_id")
    agent_register.add_argument("--adapter", required=True)
    agent_register.add_argument("--runtime-ref", default=None, dest="runtime_ref")
    agent_register.add_argument("--role", required=True)
    agent_register.add_argument("--group-label", default=None, dest="group_label")
    agent_register.add_argument("--session-id", default=None, dest="session_id")
    agent_register.add_argument("--pid", type=int, default=None)
    agent_register.set_defaults(func=cmd_agent)

    config = sub.add_parser("config", help="configuration validation")
    config_sub = config.add_subparsers(dest="action", required=True)
    config_check = config_sub.add_parser("check")
    config_check.set_defaults(func=cmd_config)

    doctor = sub.add_parser("doctor", help="store rebuild and recovery")
    doctor_sub = doctor.add_subparsers(dest="action", required=True)
    doctor_rebuild = doctor_sub.add_parser("rebuild")
    doctor_rebuild.add_argument("--github-snapshot", required=True, dest="github_snapshot")
    doctor_rebuild.add_argument("--adapter-listing", required=True, dest="adapter_listing")
    doctor_rebuild.add_argument("--git-worktrees", required=True, dest="git_worktrees")
    doctor_rebuild.set_defaults(func=cmd_doctor)

    guard = sub.add_parser("guard", help="deterministic plan guards")
    guard_sub = guard.add_subparsers(dest="action", required=True)
    guard_check = guard_sub.add_parser("check-dag", help="validate a DAG plan")
    guard_check.add_argument("--plan", required=True, help="path to JSON plan or '-' for stdin")
    guard_check.set_defaults(func=cmd_guard)

    review = sub.add_parser("review", help="issue and reference review rounds")
    review_sub = review.add_subparsers(dest="action", required=True)
    review_create = review_sub.add_parser("round-create", help="issue a review-round identity")
    review_create.add_argument("--dispatch-id", required=True)
    review_create.add_argument("--round", type=int, required=True)
    review_create.add_argument("--candidate-sha", required=True)
    review_create.add_argument("--base-sha", required=True)
    review_create.add_argument("--diff-digest", required=True)
    review_create.add_argument("--acceptance-digest", required=True)
    review_create.add_argument("--scope", required=True, choices=("full", "delta"))
    review_create.add_argument("--prior-round-id", default=None, dest="prior_round_id")
    review_create.add_argument("--issued-by", default=None, help=argparse.SUPPRESS)
    review_create.add_argument("--round-id", default=None, help=argparse.SUPPRESS)
    review_create.set_defaults(func=cmd_review)

    review_result = review_sub.add_parser("result-create", help="record a reviewer result")
    review_result.add_argument("--round-id", required=True)
    review_result.add_argument("--axis", required=True, choices=("spec", "quality", "combined"))
    review_result.add_argument("--verdict", required=True, choices=("approved", "rejected", "needs_work", "withdrawn"))
    review_result.add_argument("--findings", default=None, help="JSON object findings")
    review_result.add_argument("--agent-id", default=None, help=argparse.SUPPRESS)
    review_result.add_argument("--candidate-sha", default=None, help=argparse.SUPPRESS)
    review_result.add_argument("--base-sha", default=None, help=argparse.SUPPRESS)
    review_result.add_argument("--diff-digest", default=None, help=argparse.SUPPRESS)
    review_result.add_argument("--acceptance-digest", default=None, help=argparse.SUPPRESS)
    review_result.add_argument("--scope", default=None, help=argparse.SUPPRESS)
    review_result.add_argument("--round", type=int, default=None, help=argparse.SUPPRESS)
    review_result.add_argument("--prior-round-id", default=None, help=argparse.SUPPRESS)
    review_result.set_defaults(func=cmd_review)

    lease = sub.add_parser("lease", help="repository Integration Lease")
    lease_sub = lease.add_subparsers(dest="action", required=True)
    lease_acquire = lease_sub.add_parser("acquire", help="acquire the Integration Lease")
    lease_acquire.add_argument("--scope", required=True)
    lease_acquire.add_argument("--agent-id", default=None, help=argparse.SUPPRESS)
    lease_acquire.set_defaults(func=cmd_lease)

    lease_release = lease_sub.add_parser("release", help="release the Integration Lease")
    lease_release.add_argument("--scope", required=True)
    lease_release.add_argument("--agent-id", default=None, help=argparse.SUPPRESS)
    lease_release.set_defaults(func=cmd_lease)

    lease_chain_append = lease_sub.add_parser("chain-append", help="append an integration chain node")
    lease_chain_append.add_argument("--scope", required=True)
    lease_chain_append.add_argument("--candidate-sha", required=True)
    lease_chain_append.add_argument("--task-id", required=True)
    lease_chain_append.set_defaults(func=cmd_lease)

    lease_chain_list = lease_sub.add_parser("chain-list", help="list the integration chain")
    lease_chain_list.add_argument("--scope", required=True)
    lease_chain_list.set_defaults(func=cmd_lease)

    return parser


def _require_repository(args: argparse.Namespace) -> None:
    if getattr(args, "repository", None) == NO_DEFAULT_REPOSITORY:
        raise argparse.ArgumentError(None, "--repository is required unless GWO_REPOSITORY contains a valid owner/repo value")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _require_repository(args)
    gwo_home = args.gwo_home
    if gwo_home is None:
        env_home = os.environ.get("GWO_HOME")
        if env_home:
            gwo_home = Path(env_home)
        else:
            gwo_home = Path.home() / ".gwo"
    args.gwo_home = gwo_home
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())