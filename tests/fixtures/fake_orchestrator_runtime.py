"""External CLI shims for the production-path Orchestrator E2E."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys


def _load_state() -> dict:
    return json.loads(Path(os.environ["ORCH_E2E_STATE"]).read_text(encoding="utf-8"))


def _save_state(state: dict) -> None:
    Path(os.environ["ORCH_E2E_STATE"]).write_text(json.dumps(state), encoding="utf-8")


def _issue_node(state: dict) -> dict:
    return {
        "number": 7,
        "title": "Park and resume production path",
        "body": "",
        "updatedAt": "2026-07-20T01:00:00Z",
        "labels": {"nodes": [{"name": f"orch:{state['label']}"}]},
        "milestone": None,
        "assignees": {"nodes": []},
        "comments": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {
                    "databaseId": 99,
                    "body": state["record_body"],
                    "createdAt": "2026-07-20T01:00:00Z",
                    "updatedAt": "2026-07-20T01:00:00Z",
                    "author": {"login": "owner"},
                }
            ],
        },
    }


def _connection(nodes: list[dict]) -> dict:
    return {
        "totalCount": len(nodes),
        "pageInfo": {"hasNextPage": False},
        "nodes": nodes,
    }


def _frontier_issue_node(state: dict, *, detailed: bool) -> dict:
    labels = [{"name": "ready-for-agent"}]
    if state.get("label"):
        labels.append({"name": f"orch:{state['label']}"})
    comments = []
    if detailed and state.get("record_body"):
        comments.append(
            {
                "databaseId": 91,
                "body": state["record_body"],
                "createdAt": "2026-07-20T01:00:00Z",
                "updatedAt": "2026-07-20T01:00:00Z",
                "author": {"login": "owner"},
            }
        )
    return {
        "number": 23,
        "title": "Parallel Frontier candidate",
        "body": "Reporter context only",
        "updatedAt": "2026-07-20T01:00:00Z",
        "labels": {"nodes": labels},
        "milestone": None,
        "assignees": {"nodes": []},
        "comments": {
            "pageInfo": {"hasNextPage": False},
            "nodes": comments,
        },
    }


def _gh_frontier(args: list[str], state: dict) -> int:
    if args[:2] == ["api", "graphql"]:
        query = next(value[6:] for value in args if value.startswith("query="))
        if "readyIssues:issues" in query:
            issue = _frontier_issue_node(state, detailed=True)
            repository = {
                "ref": {"target": {"oid": state["base_sha"]}},
                "readyIssues": _connection(
                    [issue] if state.get("label") == "ready" else []
                ),
                "activeIssues": _connection([]),
                "blockedIssues": _connection([]),
                "pullRequests": _connection([]),
            }
        elif "i23:issue(number:23)" in query:
            repository = {"i23": _frontier_issue_node(state, detailed=True)}
        else:
            aliases = re.findall(r"(l\d+):issues", query) or ["issues"]
            repository = {
                alias: _connection([_frontier_issue_node(state, detailed=False)])
                for alias in aliases
            }
        print(json.dumps({"data": {"repository": repository}}))
        return 0
    if args[:2] == ["api", "--method"]:
        body = next(value[5:] for value in args if value.startswith("body="))
        state["record_body"] = body
        state["operations"].append("admit_record")
        _save_state(state)
        print(json.dumps({"id": 91, "body": body}))
        return 0
    if args[:2] == ["issue", "edit"]:
        label = args[args.index("--add-label") + 1]
        state["label"] = label.removeprefix("orch:")
        state["operations"].append(f"set_state:{state['label']}")
        _save_state(state)
        return 0
    if args[:2] == ["issue", "view"]:
        print(json.dumps({"labels": [{"name": f"orch:{state['label']}"}]}))
        return 0
    print(json.dumps({"error": {"message": f"unexpected fake gh args: {args}"}}))
    return 2


def _gh(args: list[str]) -> int:
    state = _load_state()
    if (
        args[:3] == ["api", "--method", "GET"]
        and len(args) > 3
        and "/contents/.gwo-v8/legacy-writer-fence.json" in args[3]
    ):
        print("HTTP 404: Not Found", file=sys.stderr)
        return 1
    if state.get("scenario") == "frontier":
        return _gh_frontier(args, state)
    if args[:2] == ["api", "graphql"]:
        issue = _issue_node(state)
        repository = {
            "ref": {"target": {"oid": state["base_sha"]}},
            "readyIssues": _connection([issue] if state["label"] == "ready" else []),
            "activeIssues": _connection([issue] if state["label"] == "active" else []),
            "blockedIssues": _connection(
                [issue] if state["label"] == "blocked" else []
            ),
            "pullRequests": _connection([]),
        }
        print(json.dumps({"data": {"repository": repository}}))
        return 0
    if args[:2] == ["api", "--method"]:
        body = next(value[5:] for value in args if value.startswith("body="))
        state["record_body"] = body
        state["operations"].append("update_record")
        _save_state(state)
        print(json.dumps({"body": body}))
        return 0
    if args[:2] == ["issue", "edit"]:
        label = args[args.index("--add-label") + 1]
        state["label"] = label.removeprefix("orch:")
        state["operations"].append(f"set_state:{state['label']}")
        _save_state(state)
        return 0
    if args[:2] == ["issue", "view"]:
        print(json.dumps({"labels": [{"name": f"orch:{state['label']}"}]}))
        return 0
    print(json.dumps({"error": {"message": f"unexpected fake gh args: {args}"}}))
    return 2


def _paseo(args: list[str]) -> int:
    if args and args[0] == "inspect":
        agent_id = args[1]
        if agent_id == "root-a":
            payload = {
                "Id": "root-a",
                "Provider": "codex",
                "Model": "gpt-5.6",
                "Thinking": "high",
                "Mode": "full-access",
                "RuntimeSettings": {"features": {"plan_mode": False}},
                "Status": "running",
                "Archived": False,
                "Cwd": os.environ["ORCH_E2E_ROOT_CWD"],
                "Worktree": {"Id": "stable-dev"},
                "ParentAgentId": None,
            }
        elif agent_id == "worker-7":
            payload = {
                "Id": "worker-7",
                "Status": Path(os.environ["ORCH_E2E_WORKER_STATE"])
                .read_text(encoding="utf-8")
                .strip(),
                "Archived": False,
                "Cwd": os.environ["ORCH_E2E_WORKER_CWD"],
                "Worktree": {"Id": "workspace-7"},
                "ParentAgentId": "root-a",
            }
        else:
            return 2
        print(json.dumps(payload))
        return 0
    if args and args[0] == "ls" and "--label" in args:
        print(json.dumps([{"id": "worker-7"}]))
        return 0
    print(json.dumps({"error": {"message": f"unexpected fake paseo args: {args}"}}))
    return 2


def _git(args: list[str]) -> int:
    if args == ["remote", "get-url", "origin"]:
        print("https://github.com/owner/repo.git")
        return 0
    if "ls-remote" in args:
        print(f"{os.environ['ORCH_E2E_BASE_SHA']}\trefs/heads/dev")
        return 0
    if args[:2] == ["cat-file", "-e"] and args[2].startswith(
        os.environ["ORCH_E2E_BASE_SHA"]
    ):
        return 0
    result = subprocess.run(
        [os.environ["ORCH_E2E_REAL_GIT"], *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def main() -> int:
    tool, *args = sys.argv[1:]
    if tool == "gh":
        return _gh(args)
    if tool == "paseo":
        return _paseo(args)
    if tool == "git":
        return _git(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
