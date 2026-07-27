"""Lazy production adapters owned by the V3 PlanControl deep module."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Mapping
from urllib.parse import quote

from ._v3_canonical import digest, strict_json_bytes
from ._v3_types import Content, PlanControlError, WriterWitness


_ISSUE_REF = re.compile(r"^issue:([1-9][0-9]*)$")
_WRITER_PATH = ".gwo-v8/writer-transition.json"


class GitHubCliGateway:
    """Authenticated GitHub source and content CAS over the ``gh`` CLI."""

    def __init__(
        self,
        executable: str,
        *,
        command_timeout_seconds: int,
    ):
        if not executable or command_timeout_seconds < 1:
            raise PlanControlError(
                "PRODUCTION_CONFIG_INVALID",
                "GitHub executable and positive timeout are required",
            )
        self.executable = executable
        self.command_timeout_seconds = command_timeout_seconds

    def _invoke(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [self.executable, *args]
        try:
            return subprocess.run(
                command,
                input=input_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.command_timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise PlanControlError(
                "GITHUB_TIMEOUT",
                "GitHub operation exceeded the configured bound",
            ) from error
        except OSError as error:
            raise PlanControlError(
                "GITHUB_EXECUTABLE_UNAVAILABLE",
                "configured GitHub executable could not start",
            ) from error

    @staticmethod
    def _detail(result: subprocess.CompletedProcess[str]) -> str:
        return (
            result.stderr.strip()
            or result.stdout.strip()
            or "GitHub operation failed"
        )

    def api_json(self, endpoint: str, *, paginate: bool = False) -> Any:
        args = ["api", "--method", "GET", endpoint]
        if paginate:
            args.extend(["--paginate", "--slurp"])
        result = self._invoke(args)
        if result.returncode != 0:
            raise PlanControlError(
                "GITHUB_SOURCE_READ_FAILED", self._detail(result)
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise PlanControlError(
                "GITHUB_SOURCE_READ_INVALID",
                "GitHub source response is not JSON",
            ) from error

    def read(
        self,
        repository: str,
        branch: str,
        path: str,
    ) -> Content | None:
        endpoint = f"repos/{repository}/contents/{path}"
        result = self._invoke(
            [
                "api",
                "--method",
                "GET",
                endpoint,
                "-f",
                f"ref={branch}",
            ]
        )
        if result.returncode != 0:
            detail = self._detail(result)
            lowered = detail.casefold()
            if "404" in lowered or "not found" in lowered:
                return None
            raise PlanControlError("DURABLE_READ_FAILED", detail)
        try:
            payload = json.loads(result.stdout)
            encoded = str(payload["content"]).replace("\n", "")
            content = base64.b64decode(encoded, validate=True)
            blob_sha = payload["sha"]
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise PlanControlError(
                "DURABLE_READ_INVALID",
                "GitHub returned an invalid content blob",
            ) from error
        if not isinstance(blob_sha, str) or not blob_sha:
            raise PlanControlError(
                "DURABLE_READ_INVALID",
                "GitHub content blob omitted its identity",
            )
        return Content(content=content, blob_sha=blob_sha)

    def compare_and_swap(
        self,
        repository: str,
        branch: str,
        path: str,
        content: bytes,
        *,
        expected_blob_sha: str | None,
        message: str,
    ) -> Content:
        request: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content).decode("ascii"),
            "branch": branch,
        }
        if expected_blob_sha is not None:
            request["sha"] = expected_blob_sha
        result = self._invoke(
            [
                "api",
                "--method",
                "PUT",
                f"repos/{repository}/contents/{path}",
                "--input",
                "-",
            ],
            input_text=json.dumps(request),
        )
        if result.returncode != 0:
            raise PlanControlError(
                "DURABLE_STATE_AMBIGUOUS", self._detail(result)
            )
        readback = self.read(repository, branch, path)
        if readback is None or readback.content != content:
            raise PlanControlError(
                "DURABLE_STATE_AMBIGUOUS",
                "GitHub content CAS did not read back exact bytes",
            )
        return readback


class GitHubCampaignSource:
    """Read complete selected Tickets, native blockers, target, and policy."""

    def __init__(
        self,
        gateway: GitHubCliGateway,
        *,
        policy_path: str,
    ):
        path_parts = policy_path.replace("\\", "/").split("/")
        if (
            not policy_path
            or policy_path.startswith(("/", "\\"))
            or any(part in {"", ".", ".."} for part in path_parts)
        ):
            raise PlanControlError(
                "PRODUCTION_CONFIG_INVALID",
                "repository policy path must be relative",
            )
        self.gateway = gateway
        self.policy_path = policy_path

    @staticmethod
    def _issue_number(ticket_key: str) -> int:
        matched = _ISSUE_REF.fullmatch(ticket_key)
        if matched is None:
            raise PlanControlError(
                "READY_REF_INVALID",
                "production Ticket refs must use issue:<positive-number>",
            )
        return int(matched.group(1))

    @staticmethod
    def _labels(value: Any) -> list[str]:
        if not isinstance(value, list):
            raise PlanControlError(
                "GITHUB_SOURCE_READ_INVALID",
                "GitHub Ticket labels are not a list",
            )
        labels: list[str] = []
        for item in value:
            name = item.get("name") if isinstance(item, dict) else item
            if not isinstance(name, str) or not name:
                raise PlanControlError(
                    "GITHUB_SOURCE_READ_INVALID",
                    "GitHub Ticket label is invalid",
                )
            labels.append(name)
        return sorted(set(labels))

    def _blockers(
        self,
        repository: str,
        issue_number: int,
    ) -> list[dict[str, str]]:
        payload = self.gateway.api_json(
            (
                f"repos/{repository}/issues/{issue_number}/"
                "dependencies/blocked_by"
            ),
            paginate=True,
        )
        if not isinstance(payload, list):
            raise PlanControlError(
                "GITHUB_SOURCE_READ_INVALID",
                "GitHub native blockers are not a list",
            )
        nodes = (
            [item for page in payload for item in page]
            if all(isinstance(page, list) for page in payload)
            else payload
        )
        blockers: list[dict[str, str]] = []
        for item in nodes:
            if not isinstance(item, dict):
                raise PlanControlError(
                    "GITHUB_SOURCE_READ_INVALID",
                    "GitHub native blocker is malformed",
                )
            number = item.get("number")
            state = item.get("state")
            if (
                isinstance(number, bool)
                or not isinstance(number, int)
                or number < 1
                or not isinstance(state, str)
                or state.casefold() not in {"open", "closed"}
            ):
                raise PlanControlError(
                    "GITHUB_SOURCE_READ_INVALID",
                    "GitHub native blocker identity is invalid",
                )
            blockers.append(
                {"key": f"issue:{number}", "state": state.casefold()}
            )
        if len({item["key"] for item in blockers}) != len(blockers):
            raise PlanControlError(
                "GITHUB_SOURCE_READ_INVALID",
                "GitHub native blockers contain duplicates",
            )
        return sorted(blockers, key=lambda item: item["key"])

    def _ticket(self, repository: str, ticket_key: str) -> dict[str, Any]:
        issue_number = self._issue_number(ticket_key)
        value = self.gateway.api_json(
            f"repos/{repository}/issues/{issue_number}"
        )
        if not isinstance(value, dict):
            raise PlanControlError(
                "GITHUB_SOURCE_READ_INVALID",
                "GitHub Ticket response is not an object",
            )
        title = value.get("title")
        body = value.get("body")
        state = value.get("state")
        source_ref = value.get("html_url")
        updated_at = value.get("updated_at")
        if (
            value.get("number") != issue_number
            or "pull_request" in value
            or not isinstance(title, str)
            or not title
            or not isinstance(body, str)
            or not body
            or not isinstance(state, str)
            or state.casefold() != "open"
            or not isinstance(source_ref, str)
            or not source_ref
            or not isinstance(updated_at, str)
            or not updated_at
        ):
            raise PlanControlError(
                "GITHUB_SOURCE_READ_INVALID",
                "GitHub Ticket contract or identity is incomplete",
            )
        labels = self._labels(value.get("labels"))
        blockers = self._blockers(repository, issue_number)
        source_facts = {
            "repository": repository,
            "number": issue_number,
            "state": "open",
            "title": title,
            "body": body,
            "labels": labels,
            "native_blockers": blockers,
            "updated_at": updated_at,
            "ref": source_ref,
        }
        return {
            "key": ticket_key,
            "labels": labels,
            "source": {
                "ref": source_ref,
                "digest": digest(strict_json_bytes(source_facts)),
            },
            "contract": {"title": title, "body": body},
            "native_blockers": blockers,
        }

    def snapshot(
        self,
        repository: str,
        ready_refs: tuple[str, ...],
    ) -> dict[str, Any]:
        repository_parts = repository.split("/")
        if (
            len(repository_parts) != 2
            or any(
                not part or part in {".", ".."} for part in repository_parts
            )
        ):
            raise PlanControlError(
                "GITHUB_REPOSITORY_INVALID",
                "repository must be an owner/name identity",
            )
        repository_value = self.gateway.api_json(f"repos/{repository}")
        if not isinstance(repository_value, dict):
            raise PlanControlError(
                "GITHUB_SOURCE_READ_INVALID",
                "GitHub repository response is not an object",
            )
        target_branch = repository_value.get("default_branch")
        if not isinstance(target_branch, str) or not target_branch:
            raise PlanControlError(
                "GITHUB_SOURCE_READ_INVALID",
                "GitHub repository has no default branch",
            )
        target_value = self.gateway.api_json(
            (
                f"repos/{repository}/git/ref/heads/"
                f"{quote(target_branch, safe='')}"
            )
        )
        target_sha = (
            (target_value.get("object") or {}).get("sha")
            if isinstance(target_value, dict)
            else None
        )
        if not isinstance(target_sha, str) or not target_sha:
            raise PlanControlError(
                "GITHUB_SOURCE_READ_INVALID",
                "GitHub target ref has no commit identity",
            )
        policy_blob = self.gateway.read(
            repository, target_branch, self.policy_path
        )
        if policy_blob is None:
            raise PlanControlError(
                "POLICY_WITNESS_MISSING",
                "repository policy document does not exist",
            )
        try:
            policy = json.loads(policy_blob.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PlanControlError(
                "POLICY_WITNESS_INVALID",
                "repository policy document is not JSON",
            ) from error
        campaign_facts = {
            "repository": repository,
            "target_branch": target_branch,
            "target_sha": target_sha,
            "ready_refs": list(ready_refs),
        }
        return {
            "repository": repository,
            "target_branch": target_branch,
            "campaign_source": {
                "ref": (
                    f"github://{repository}/refs/heads/"
                    f"{target_branch}@{target_sha}"
                ),
                "digest": digest(strict_json_bytes(campaign_facts)),
            },
            "policy": policy,
            "tickets": [
                self._ticket(repository, ticket_key)
                for ticket_key in ready_refs
            ],
        }


class BoundedPlanningPass:
    """Provider-neutral synchronous Planning Pass process with a hard bound."""

    def __init__(self, executable: str, *, timeout_seconds: int):
        if not executable or timeout_seconds < 1:
            raise PlanControlError(
                "PRODUCTION_CONFIG_INVALID",
                "planner executable and positive timeout are required",
            )
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    @classmethod
    def _mutable(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: cls._mutable(child) for key, child in value.items()
            }
        if isinstance(value, tuple):
            return [cls._mutable(child) for child in value]
        return value

    def plan(
        self,
        snapshot: object,
        planning_action_id: str,
        *,
        coordinator_profile_ref: str | None,
    ) -> Mapping[str, Any]:
        request = {
            "schema_version": 1,
            "planning_action_id": planning_action_id,
            "coordinator_profile_ref": coordinator_profile_ref,
            "snapshot": self._mutable(snapshot),
        }
        command = [self.executable]
        if os.name == "nt" and Path(self.executable).suffix.casefold() in {
            ".bat",
            ".cmd",
        }:
            command = [
                os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe"),
                "/d",
                "/s",
                "/c",
                subprocess.list2cmdline([self.executable]),
            ]
        try:
            result = subprocess.run(
                command,
                input=strict_json_bytes(request).decode("utf-8"),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise PlanControlError(
                "PLANNING_PASS_TIMEOUT",
                "Campaign Planning Pass exceeded its configured bound",
            ) from error
        except OSError as error:
            raise PlanControlError(
                "PLANNING_EXECUTABLE_UNAVAILABLE",
                "configured Campaign planner could not start",
            ) from error
        if result.returncode != 0:
            raise PlanControlError(
                "PLANNING_PASS_FAILED",
                result.stderr.strip()
                or result.stdout.strip()
                or "Campaign planner failed",
            )
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise PlanControlError(
                "PLANNING_PASS_INVALID",
                "Campaign planner response is not JSON",
            ) from error
        if not isinstance(value, dict):
            raise PlanControlError(
                "PLANNING_PASS_INVALID",
                "Campaign planner response is not an object",
            )
        return value


class GitHubWriterWitness:
    """Read the established repository-global writer transition without mutation."""

    def __init__(
        self,
        gateway: GitHubCliGateway,
        *,
        branch: str,
    ):
        self.gateway = gateway
        self.branch = branch

    def read(self, repository: str) -> WriterWitness:
        blob = self.gateway.read(repository, self.branch, _WRITER_PATH)
        if blob is None:
            raise PlanControlError(
                "WRITER_AUTHORITY_NOT_READY",
                "durable writer transition record is missing",
            )
        try:
            value = json.loads(blob.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PlanControlError(
                "WRITER_AUTHORITY_NOT_READY",
                "durable writer transition record is not JSON",
            ) from error
        if (
            not isinstance(value, dict)
            or set(value) != {"schema_version", "current", "records"}
            or value["schema_version"] != 1
            or not isinstance(value.get("current"), dict)
            or not isinstance(value.get("records"), list)
        ):
            raise PlanControlError(
                "WRITER_AUTHORITY_NOT_READY",
                "durable writer transition record is malformed",
            )
        current = value["current"]
        record_id = current.get("record_id")
        record_fields = {
            "record_id",
            "repository",
            "kind",
            "status",
            "previous_writer_generation",
            "writer_generation",
            "activation_id",
            "plan_digest",
            "canary_evidence_digest",
            "canary_evidence_refs",
            "canary_manifest_ref",
            "worker_capacity",
            "coordinator_capacity",
            "reason",
            "created_at",
        }
        matches = [
            record
            for record in value["records"]
            if isinstance(record, dict)
            and record.get("record_id") == record_id
        ]
        if (
            set(current)
            != {"repository", "writer_generation", "record_id"}
            or current.get("repository") != repository
            or not isinstance(current.get("writer_generation"), str)
            or not current["writer_generation"]
            or not isinstance(record_id, str)
            or not record_id
            or len(matches) != 1
        ):
            raise PlanControlError(
                "WRITER_AUTHORITY_NOT_READY",
                "durable current writer identity is invalid",
            )
        record = matches[0]
        allowed = (
            set(record) == record_fields
            and record.get("repository") == repository
            and record.get("writer_generation")
            == current["writer_generation"]
            and record.get("status") == "cut_over"
            and isinstance(record.get("activation_id"), str)
            and bool(record["activation_id"])
            and isinstance(record.get("worker_capacity"), int)
            and not isinstance(record.get("worker_capacity"), bool)
            and record["worker_capacity"] > 0
            and isinstance(record.get("coordinator_capacity"), int)
            and not isinstance(record.get("coordinator_capacity"), bool)
            and record["coordinator_capacity"] > 0
        )
        witness_facts = {"current": current, "record": record}
        return WriterWitness(
            repository=repository,
            writer_generation=current["writer_generation"],
            v8_start_allowed=allowed,
            digest=digest(strict_json_bytes(witness_facts)),
        )


def _positive_environment(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as error:
        raise PlanControlError(
            "PRODUCTION_CONFIG_INVALID",
            f"{name} must be a positive integer",
        ) from error
    if value < 1:
        raise PlanControlError(
            "PRODUCTION_CONFIG_INVALID",
            f"{name} must be a positive integer",
        )
    return value


def _configured_executable(environment_name: str, default: str) -> str:
    configured = os.environ.get(environment_name)
    if configured:
        return configured
    return shutil.which(default) or default


def _journal_path(repository: str) -> Path:
    configured_home = os.environ.get("GWO_HOME")
    root = (
        Path(configured_home).expanduser()
        if configured_home
        else Path.home() / ".gwo"
    )
    repository_key = digest(repository.encode("utf-8"))[:24]
    directory = root / "v8" / repository_key
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "plan-control-v3.sqlite3"


def build_production_control(repository: str):
    """Construct the real PlanControl only when public ``start`` is invoked."""

    from ._v3_github_control import GitHubV3Control
    from ._v3_journal import SQLiteV3Journal
    from .plan_control import _PlanControl

    branch = os.environ.get("GWO_V3_CONTROL_BRANCH", "gwo-control")
    policy_path = os.environ.get("GWO_V3_POLICY_PATH", ".gwo/policy.json")
    gateway = GitHubCliGateway(
        _configured_executable("GWO_GH_PATH", "gh"),
        command_timeout_seconds=_positive_environment(
            "GWO_GITHUB_TIMEOUT_SECONDS", 30
        ),
    )
    return _PlanControl(
        source=GitHubCampaignSource(gateway, policy_path=policy_path),
        planner=BoundedPlanningPass(
            _configured_executable(
                "GWO_PLANCONTROL_PLANNER_PATH", "gwo-plan"
            ),
            timeout_seconds=_positive_environment(
                "GWO_PLANNING_TIMEOUT_SECONDS", 300
            ),
        ),
        journal=SQLiteV3Journal(_journal_path(repository)),
        durable=GitHubV3Control(
            gateway,
            branch=branch,
            root=os.environ.get("GWO_V3_CONTROL_ROOT", ".gwo/v3"),
            cas_attempts=_positive_environment("GWO_V3_CAS_ATTEMPTS", 8),
        ),
        writer=GitHubWriterWitness(gateway, branch=branch),
        max_snapshot_bytes=_positive_environment(
            "GWO_PLANCONTROL_MAX_SNAPSHOT_BYTES", 1_000_000
        ),
    )
