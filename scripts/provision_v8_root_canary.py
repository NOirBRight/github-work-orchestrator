import argparse
import dataclasses
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence


ROOT_REPOSITORY = "NOirBRight/github-work-orchestrator"
APPROVAL = "CREATE-GWO-V8-GA-ROOT-CANARY-TICKETS"


@dataclass(frozen=True, slots=True)
class RootCanaryTicketSpec:
    key: str
    title: str
    path: str
    expected_assurance: str
    expected_batch: str
    acceptance: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RootCanaryTicketReadback:
    number: int
    state: str
    labels: tuple[str, ...]
    body: str
    comments: tuple[str, ...]
    blocked_by: tuple[int, ...]
    blocker_states: tuple[tuple[int, str], ...]
    contract_digest: str


@dataclass(frozen=True, slots=True)
class RootCanaryManifestEntry:
    key: str
    ticket_key: str
    readback: RootCanaryTicketReadback

    def canonical(self) -> dict[str, object]:
        return {
            "key": self.key,
            "ticket_key": self.ticket_key,
            **dataclasses.asdict(self.readback),
        }


class RootCanaryProvisionError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class GitHubIssuePort(Protocol):
    def find_exact_title(
        self, repository: str, title: str
    ) -> RootCanaryTicketReadback | None: ...

    def create_issue(
        self, repository: str, title: str, body: str, labels: tuple[str, ...]
    ) -> RootCanaryTicketReadback: ...

    def read_complete(self, repository: str, number: int) -> RootCanaryTicketReadback: ...


def root_ticket_specs() -> tuple[RootCanaryTicketSpec, ...]:
    return (
        RootCanaryTicketSpec(
            "alpha",
            "GWO V8 GA Canary A: document Candidate receipt readback",
            "docs/canary/gwo-v8-ga-alpha.md",
            "standard",
            "multi",
            ("Add the named document only.", "Repository validation passes."),
        ),
        RootCanaryTicketSpec(
            "beta",
            "GWO V8 GA Canary B: document permission binding readback",
            "docs/canary/gwo-v8-ga-beta.md",
            "standard",
            "multi",
            ("Add the named document only.", "Repository validation passes."),
        ),
        RootCanaryTicketSpec(
            "gamma",
            "GWO V8 GA Canary C: document restart reconstruction",
            "docs/canary/gwo-v8-ga-gamma.md",
            "standard",
            "multi",
            ("Add the named document only.", "Repository validation passes."),
        ),
        RootCanaryTicketSpec(
            "delta",
            "GWO V8 GA Canary D: update the protected GA marker",
            "docs/canary/protected/gwo-v8-ga-delta.md",
            "strict",
            "singleton",
            ("Add the protected marker only.", "Repository validation passes."),
        ),
    )


def canonical_body(spec: RootCanaryTicketSpec) -> str:
    acceptance = "\n".join(f"- [ ] {item}" for item in spec.acceptance)
    return (
        "## Outcome\n"
        f"Create `{spec.path}` as one GA root-Canary marker.\n\n"
        "## Acceptance criteria\n"
        f"{acceptance}\n\n"
        "## Scope exclusions\n"
        "- Do not edit another path.\n"
        "- Do not change dependencies, labels, authority, or release state.\n"
    )


def provision_root_tickets(
    github: GitHubIssuePort,
    repository: str,
    approval: str | None,
    *,
    read_only: bool = False,
) -> tuple[RootCanaryManifestEntry, ...]:
    if repository != ROOT_REPOSITORY or (not read_only and approval != APPROVAL):
        raise RootCanaryProvisionError("ROOT_CANARY_APPROVAL_REQUIRED")

    result = []
    for spec in root_ticket_specs():
        body = canonical_body(spec)
        issue = github.find_exact_title(repository, spec.title)
        if issue is None:
            if read_only:
                raise RootCanaryProvisionError("ROOT_TICKET_MISSING")
            issue = github.create_issue(repository, spec.title, body, ("ready-for-agent",))

        readback = github.read_complete(repository, issue.number)
        if (
            readback.state != "OPEN"
            or readback.labels != ("ready-for-agent",)
            or readback.body != body
            or readback.blocked_by
            or any(state != "CLOSED" for _, state in readback.blocker_states)
        ):
            raise RootCanaryProvisionError("ROOT_TICKET_NOT_READY")
        result.append(
            RootCanaryManifestEntry(
                key=spec.key,
                ticket_key=f"issue:{readback.number}",
                readback=readback,
            )
        )
    return tuple(result)


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def digest_value(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class GhIssuePort:
    run_gh_json: Callable[[tuple[str, ...]], object]

    def find_exact_title(
        self, repository: str, title: str
    ) -> RootCanaryTicketReadback | None:
        rows = tuple(
            self.run_gh_json(
                ("issue", "list", "--repo", repository, "--state", "all", "--json", "number,title")
            )
        )
        matches = tuple(row for row in rows if row["title"] == title)
        if len(matches) > 1:
            raise RootCanaryProvisionError("ROOT_TICKET_TITLE_DUPLICATE")
        return None if not matches else self.read_complete(repository, int(matches[0]["number"]))

    def create_issue(
        self, repository: str, title: str, body: str, labels: tuple[str, ...]
    ) -> RootCanaryTicketReadback:
        created = self.run_gh_json(
            (
                "issue",
                "create",
                "--repo",
                repository,
                "--title",
                title,
                "--body",
                body,
                "--label",
                labels[0],
            )
        )
        return self.read_complete(repository, int(created["number"]))

    def read_complete(self, repository: str, number: int) -> RootCanaryTicketReadback:
        raw = dict(
            self.run_gh_json(
                (
                    "issue",
                    "view",
                    str(number),
                    "--repo",
                    repository,
                    "--json",
                    "number,state,body,labels,comments,blockedBy",
                )
            )
        )
        labels = tuple(item["name"] for item in raw.get("labels", []))
        comments = tuple(item["body"] for item in raw.get("comments", []))
        blocked = tuple(int(item["number"]) for item in raw.get("blockedBy", []))
        blocker_states = tuple(
            sorted(
                (int(item["number"]), str(item["state"]))
                for item in raw.get("blockedBy", [])
            )
        )
        without_digest = {
            "number": int(raw["number"]),
            "state": str(raw["state"]),
            "labels": labels,
            "body": str(raw["body"]),
            "comments": comments,
            "blocked_by": blocked,
            "blocker_states": blocker_states,
        }
        return RootCanaryTicketReadback(
            **without_digest, contract_digest=digest_value(without_digest)
        )


def write_ticket_manifest(
    path: Path, entries: tuple[RootCanaryManifestEntry, ...]
) -> None:
    refs = [
        f"github://{ROOT_REPOSITORY}/issues/{entry.readback.number}"
        for entry in entries
    ]
    payload = {
        "schema": "gwo-v8-root-canary-tickets.v1",
        "repository": ROOT_REPOSITORY,
        "ready_refs": refs,
        "tickets": [entry.canonical() for entry in entries],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


def write_ticket_runbook(path: Path) -> None:
    sections = [
        "# GWO V8 Root Canary Ticket Contract",
        "",
        f"Repository: `{ROOT_REPOSITORY}`",
        "",
        f"Approval: `{APPROVAL}`",
        "",
    ]
    for spec in root_ticket_specs():
        sections.extend(
            [
                f"## {spec.key}: {spec.title}",
                "",
                f"- Path: `{spec.path}`",
                f"- Assurance: `{spec.expected_assurance}`",
                f"- Batch: `{spec.expected_batch}`",
                "",
                canonical_body(spec),
                "",
            ]
        )
    sections.append(
        "Readback command: `py -3.13 scripts/provision_v8_root_canary.py "
        "--repository NOirBRight/github-work-orchestrator --read-only "
        "--output tickets-readback.json`."
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sections) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--approval")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--read-only", action="store_true")
    args = parser.parse_args(argv)
    github = GhIssuePort(
        run_gh_json=lambda command: json.loads(
            subprocess.check_output(("gh", *command), text=True)
        )
    )
    approval = None if args.read_only else args.approval
    entries = provision_root_tickets(
        github, args.repository, approval, read_only=args.read_only
    )
    write_ticket_manifest(args.output, entries)
    if not args.read_only:
        write_ticket_runbook(Path("docs/e2e/gwo-v8-root-canary-tickets.md"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
