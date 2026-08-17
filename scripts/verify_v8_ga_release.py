#!/usr/bin/env python3
"""Verify the GWO V8 GA release contract and clean-install smoke gate."""

from __future__ import annotations

import argparse
import dataclasses
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
from typing import Mapping, Protocol, Sequence


PYTHON = sys.executable
GA_VERSION = "8.0.0"
GA_RELEASE_RECORD_SCHEMA = "gwo-v8-ga-release-record.v1"
ALLOWED_METADATA_PATHS = (
    "CHANGELOG.md",
    "docs/e2e/gwo-v8-root-canary.md",
    "docs/releases/v8.0.0.md",
)
DYNAMIC_METADATA_FIELDS = frozenset(
    {
        "ci_conclusion",
        "ci_head_sha",
        "ci_run_id",
        "ci_url",
        "dynamic_pass_summary",
        "final_metadata_commit_sha",
        "main_sha",
        "metadata_commit_sha",
        "pytest_pass_count",
        "tag_candidate_sha",
    }
)
_STATIC_RECORD_FIELDS = frozenset(
    {
        "activation_receipt_digest",
        "canary_receipt_digest",
        "canary_target_sha",
        "default_writer_receipt_digest",
        "evidence_base_sha",
        "post_canary_changed_paths",
        "repository",
        "version",
    }
)
_SHA = re.compile(r"[0-9a-f]{40}\Z")


class ReleaseGateError(RuntimeError):
    """A named fail-closed outcome from the release gate."""

    def __init__(self, code: str, detail: str | None = None):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def digest_value(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _require_sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise ReleaseGateError(code)
    return value


def _require_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseGateError(code)
    return value


@dataclass(frozen=True, slots=True)
class GaReleaseRecord:
    """The committed, static half of the GA release subject."""

    version: str
    repository: str
    evidence_base_sha: str
    canary_target_sha: str
    canary_receipt_digest: str
    activation_receipt_digest: str
    default_writer_receipt_digest: str
    post_canary_changed_paths: tuple[str, ...] = ALLOWED_METADATA_PATHS

    def __post_init__(self) -> None:
        if self.version != GA_VERSION:
            raise ReleaseGateError("GA_VERSION_INVALID")
        _require_text(self.repository, "GA_REPOSITORY_INVALID")
        _require_sha(self.evidence_base_sha, "GA_STATIC_SHA_INVALID")
        _require_sha(self.canary_target_sha, "GA_STATIC_SHA_INVALID")
        for digest in (
            self.canary_receipt_digest,
            self.activation_receipt_digest,
            self.default_writer_receipt_digest,
        ):
            _require_text(digest, "GA_STATIC_RECEIPT_INVALID")
        if tuple(self.post_canary_changed_paths) != ALLOWED_METADATA_PATHS:
            raise ReleaseGateError("GA_METADATA_PATH_ALLOWLIST_INVALID")

    @classmethod
    def from_fixture(cls, fixture: object) -> "GaReleaseRecord":
        try:
            return cls(
                version=str(getattr(fixture, "version")),
                repository=str(getattr(fixture, "repository")),
                evidence_base_sha=str(getattr(fixture, "evidence_base_sha")),
                canary_target_sha=str(getattr(fixture, "canary_target_sha")),
                canary_receipt_digest=str(getattr(fixture, "canary_receipt_digest")),
                activation_receipt_digest=str(
                    getattr(fixture, "activation_receipt_digest")
                ),
                default_writer_receipt_digest=str(
                    getattr(fixture, "default_writer_receipt_digest")
                ),
            )
        except AttributeError as error:
            raise ReleaseGateError("GA_STATIC_RECORD_FIELDS_MISSING") from error

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "GaReleaseRecord":
        if not isinstance(raw, Mapping):
            raise ReleaseGateError("GA_STATIC_RECORD_NOT_OBJECT")
        if "schema" in raw and raw["schema"] != GA_RELEASE_RECORD_SCHEMA:
            raise ReleaseGateError("GA_RELEASE_RECORD_SCHEMA_INVALID")
        dynamic = DYNAMIC_METADATA_FIELDS.intersection(raw)
        if dynamic:
            raise ReleaseGateError("GA_STATIC_RECORD_CONTAINS_DYNAMIC_SHA_OR_CI")
        allowed = _STATIC_RECORD_FIELDS | {"schema"}
        unknown = set(raw) - allowed
        if unknown:
            raise ReleaseGateError("GA_STATIC_RECORD_FIELDS_INVALID")
        required = _STATIC_RECORD_FIELDS - {"post_canary_changed_paths"}
        if required - set(raw):
            raise ReleaseGateError("GA_STATIC_RECORD_FIELDS_MISSING")
        paths = raw.get("post_canary_changed_paths", ALLOWED_METADATA_PATHS)
        if not isinstance(paths, (list, tuple)):
            raise ReleaseGateError("GA_METADATA_PATH_ALLOWLIST_INVALID")
        try:
            normalized_paths = tuple(str(path) for path in paths)
            return cls(
                version=raw["version"],  # type: ignore[arg-type]
                repository=raw["repository"],  # type: ignore[arg-type]
                evidence_base_sha=raw["evidence_base_sha"],  # type: ignore[arg-type]
                canary_target_sha=raw["canary_target_sha"],  # type: ignore[arg-type]
                canary_receipt_digest=raw["canary_receipt_digest"],  # type: ignore[arg-type]
                activation_receipt_digest=raw["activation_receipt_digest"],  # type: ignore[arg-type]
                default_writer_receipt_digest=raw["default_writer_receipt_digest"],  # type: ignore[arg-type]
                post_canary_changed_paths=normalized_paths,
            )
        except KeyError as error:
            raise ReleaseGateError("GA_STATIC_RECORD_FIELDS_MISSING") from error


def write_ga_release_record(path: Path, fixture: object) -> Path:
    record = GaReleaseRecord.from_fixture(fixture)
    payload = {
        "schema": GA_RELEASE_RECORD_SCHEMA,
        **dataclasses.asdict(record),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))
    return path


def load_ga_release_record(path: Path) -> GaReleaseRecord:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseGateError("GA_RELEASE_RECORD_UNREADABLE") from error
    if not isinstance(raw, Mapping) or raw.get("schema") != GA_RELEASE_RECORD_SCHEMA:
        raise ReleaseGateError("GA_RELEASE_RECORD_SCHEMA_INVALID")
    return GaReleaseRecord.from_mapping(raw)


@dataclass(frozen=True, slots=True)
class CleanInstallResult:
    surfaces: tuple[str, str, str]
    public_names: tuple[str, str, str]
    source_checkout_imported: bool


def run(
    arguments: Sequence[str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(arguments),
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr or completed.stdout
        raise ReleaseGateError("GA_COMMAND_FAILED", detail)
    return completed


def _stage_install_source(source: Path, run_root: Path) -> Path:
    source = source.resolve()
    sync_source = source / "scripts" / "sync_orchestrator.py"
    skills_source = source / "skills"
    if not sync_source.is_file() or not skills_source.is_dir():
        raise ReleaseGateError("GA_CLEAN_INSTALL_SOURCE_INVALID")
    staging = Path(tempfile.mkdtemp(prefix=".release-source-", dir=run_root))
    (staging / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(sync_source, staging / "scripts" / "sync_orchestrator.py")
    shutil.copytree(
        skills_source,
        staging / "skills",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return staging


def _public_import_smoke(install_root: Path, source_root: Path, run_root: Path) -> None:
    python_root = (install_root / "orchestrator" / "scripts").resolve()
    code = f"""
import sys
from pathlib import Path

source_package_root = (
    Path({str(source_root)!r}).resolve()
    / "skills"
    / "orchestrator"
    / "scripts"
)
installed_root = Path({str(python_root)!r}).resolve()
sys.path.insert(0, str(installed_root))
import gwo_v8
from gwo_v8 import advance, inspect, start

expected = ("advance", "inspect", "start")
if gwo_v8.__all__ != expected:
    raise SystemExit("unexpected public GWO V8 API")
if not all(callable(operation) for operation in (start, advance, inspect)):
    raise SystemExit("public GWO V8 operation is not callable")
for name, module in tuple(sys.modules.items()):
    if name != "gwo_v8" and not name.startswith("gwo_v8."):
        continue
    origin = getattr(module, "__file__", None)
    if origin is None:
        raise SystemExit(f"module has no origin: {{name}}")
    origin_path = Path(origin).resolve()
    if installed_root not in origin_path.parents:
        raise SystemExit(f"GWO V8 import escaped install root: {{origin_path}}")
    if source_package_root in origin_path.parents:
        raise SystemExit(f"GWO V8 import used source checkout: {{origin_path}}")
print("advance,inspect,start")
"""
    smoke = subprocess.run(
        [PYTHON, "-I", "-c", code],
        cwd=run_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if smoke.returncode != 0 or smoke.stdout.strip() != "advance,inspect,start":
        detail = smoke.stderr or smoke.stdout
        raise ReleaseGateError("GA_CLEAN_INSTALL_PUBLIC_IMPORT_FAILED", detail)


def clean_install_and_smoke(source: Path, run_root: Path) -> CleanInstallResult:
    """Install a source snapshot into three temporary surfaces and smoke it."""

    source = source.resolve()
    run_root = run_root.resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    staging = _stage_install_source(source, run_root)
    surfaces = (".agents", ".codex", ".claude")
    roots = tuple(run_root / surface / "skills" for surface in surfaces)
    sync_script = staging / "scripts" / "sync_orchestrator.py"
    common = [
        PYTHON,
        str(sync_script),
        "--root",
        str(staging),
        "--backup-root",
        str(run_root / "install-backups"),
    ]
    install = [*common, "--install"]
    check = [*common, "--check"]
    for root in roots:
        install.extend(("--install-root", str(root)))
        check.extend(("--install-root", str(root)))
    run(install, cwd=run_root)
    run(check, cwd=run_root)
    for root in roots:
        _public_import_smoke(root, source.resolve(), run_root)
    return CleanInstallResult(surfaces, ("advance", "inspect", "start"), False)


@dataclass(frozen=True, slots=True)
class CiReadback:
    run_id: int
    head_sha: str
    conclusion: str
    pytest_pass_count: int


@dataclass(frozen=True, slots=True)
class ReleaseGateReceipt:
    version: str
    repository: str
    evidence_base_sha: str
    canary_target_sha: str
    tag_candidate_sha: str
    ci_run_id: int
    ci_head_sha: str
    pytest_pass_count: int
    canary_receipt_digest: str
    activation_receipt_digest: str
    default_writer_receipt_digest: str

    @classmethod
    def from_exact(
        cls,
        record: GaReleaseRecord,
        canary: object,
        activation: object,
        admission: object,
        ci: object,
        main_sha: str,
    ) -> "ReleaseGateReceipt":
        del admission
        return cls(
            version=record.version,
            repository=record.repository,
            evidence_base_sha=record.evidence_base_sha,
            canary_target_sha=str(getattr(canary, "canary_target_sha")),
            tag_candidate_sha=main_sha,
            ci_run_id=int(getattr(ci, "run_id")),
            ci_head_sha=str(getattr(ci, "head_sha")),
            pytest_pass_count=int(getattr(ci, "pytest_pass_count")),
            canary_receipt_digest=record.canary_receipt_digest,
            activation_receipt_digest=record.activation_receipt_digest,
            default_writer_receipt_digest=record.default_writer_receipt_digest,
        )


class GitAncestryReadback(Protocol):
    def is_ancestor(self, ancestor: str, descendant: str) -> bool: ...

    def changed_paths(self, ancestor: str, descendant: str) -> tuple[str, ...]: ...


def _attribute(value: object, name: str, code: str) -> object:
    try:
        return getattr(value, name)
    except AttributeError as error:
        raise ReleaseGateError(code) from error


def verify_pre_tag(
    record: GaReleaseRecord,
    *,
    main_sha: str,
    canary: object,
    activation: object,
    admission: object,
    ci: CiReadback,
    git: GitAncestryReadback,
) -> ReleaseGateReceipt:
    try:
        ci_run_id = int(_attribute(ci, "run_id", "GA_EXACT_CI_REQUIRED"))
        ci_head_sha = str(_attribute(ci, "head_sha", "GA_EXACT_CI_REQUIRED"))
        conclusion = str(_attribute(ci, "conclusion", "GA_EXACT_CI_REQUIRED"))
        pytest_pass_count = int(
            _attribute(ci, "pytest_pass_count", "GA_EXACT_CI_REQUIRED")
        )
    except (TypeError, ValueError) as error:
        raise ReleaseGateError("GA_EXACT_CI_REQUIRED") from error
    if (
        not isinstance(main_sha, str)
        or _SHA.fullmatch(main_sha) is None
        or _SHA.fullmatch(ci_head_sha) is None
        or main_sha != ci_head_sha
        or conclusion != "success"
        or ci_run_id < 1
        or pytest_pass_count < 1
    ):
        raise ReleaseGateError("GA_EXACT_CI_REQUIRED")

    if (
        _attribute(canary, "canary_target_sha", "GA_CANARY_RECEIPT_MISMATCH")
        != record.canary_target_sha
        or _attribute(canary, "receipt_digest", "GA_CANARY_RECEIPT_MISMATCH")
        != record.canary_receipt_digest
    ):
        raise ReleaseGateError("GA_CANARY_RECEIPT_MISMATCH")

    activation_fields = (
        _attribute(activation, "receipt_digest", "GA_ACTIVATION_READBACK_INVALID"),
        _attribute(activation, "repository", "GA_ACTIVATION_READBACK_INVALID"),
        _attribute(activation, "writer_generation", "GA_ACTIVATION_READBACK_INVALID"),
    )
    if activation_fields != (
        record.activation_receipt_digest,
        record.repository,
        "v8",
    ):
        raise ReleaseGateError("GA_ACTIVATION_READBACK_INVALID")
    activation_id = _attribute(
        activation, "activation_id", "GA_ACTIVATION_READBACK_INVALID"
    )
    if not isinstance(activation_id, str) or not activation_id:
        raise ReleaseGateError("GA_ACTIVATION_READBACK_INVALID")

    admission_fields = (
        getattr(
            getattr(admission, "mode", None), "value", getattr(admission, "mode", None)
        ),
        _attribute(admission, "repository", "GA_DEFAULT_WRITER_READBACK_INVALID"),
        _attribute(
            admission, "writer_generation", "GA_DEFAULT_WRITER_READBACK_INVALID"
        ),
        _attribute(admission, "activation_id", "GA_DEFAULT_WRITER_READBACK_INVALID"),
        _attribute(
            admission,
            "acceptance_receipt_digest",
            "GA_DEFAULT_WRITER_READBACK_INVALID",
        ),
        _attribute(admission, "receipt_digest", "GA_DEFAULT_WRITER_READBACK_INVALID"),
    )
    if admission_fields != (
        "default_v8",
        record.repository,
        "v8",
        activation_id,
        record.canary_receipt_digest,
        record.default_writer_receipt_digest,
    ):
        raise ReleaseGateError("GA_DEFAULT_WRITER_READBACK_INVALID")

    try:
        if not git.is_ancestor(
            record.evidence_base_sha, main_sha
        ) or not git.is_ancestor(record.canary_target_sha, main_sha):
            raise ReleaseGateError("GA_CANARY_SHA_NOT_ANCESTOR")
        changed_paths = tuple(
            str(path) for path in git.changed_paths(record.evidence_base_sha, main_sha)
        )
    except ReleaseGateError:
        raise
    except Exception as error:
        raise ReleaseGateError("GA_GIT_READBACK_FAILED", str(error)) from error
    if (
        len(changed_paths) != len(set(changed_paths))
        or set(changed_paths) != set(record.post_canary_changed_paths)
        or set(changed_paths) - set(ALLOWED_METADATA_PATHS)
    ):
        raise ReleaseGateError("GA_POST_CANARY_DELTA_NOT_METADATA_ONLY")

    return ReleaseGateReceipt.from_exact(
        record, canary, activation, admission, ci, main_sha
    )


def parse_pytest_count(log: str) -> int:
    matches = re.findall(r"(\d+) passed", log)
    if not matches:
        raise ReleaseGateError("GA_CI_PYTEST_COUNT_MISSING")
    return int(matches[-1])


@dataclass(frozen=True, slots=True)
class GitCliReadback:
    repository: str

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        return (
            subprocess.run(
                ("git", "merge-base", "--is-ancestor", ancestor, descendant),
                check=False,
            ).returncode
            == 0
        )

    def changed_paths(self, ancestor: str, descendant: str) -> tuple[str, ...]:
        output = subprocess.check_output(
            ("git", "diff", "--name-only", f"{ancestor}..{descendant}"),
            text=True,
        )
        return tuple(line for line in output.splitlines() if line)


def _snapshot(path: Path) -> SimpleNamespace:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseGateError("GA_RECEIPT_UNREADABLE") from error
    if not isinstance(payload, Mapping):
        raise ReleaseGateError("GA_RECEIPT_NOT_OBJECT")
    return SimpleNamespace(**payload)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _required_path(value: Path | None, code: str) -> Path:
    if value is None:
        raise ReleaseGateError(code)
    return value


def verify_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--pre-tag", action="store_true")
    mode.add_argument("--post-release", action="store_true")
    parser.add_argument("--main-sha")
    parser.add_argument("--record", type=Path)
    parser.add_argument("--canary", type=Path)
    parser.add_argument("--activation", type=Path)
    parser.add_argument("--default-writer", type=Path)
    parser.add_argument("--ci-run", type=int)
    parser.add_argument("--repository", default="NOirBRight/github-work-orchestrator")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--run-root", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.pre_tag:
            record = load_ga_release_record(
                _required_path(args.record, "GA_RELEASE_RECORD_REQUIRED")
            )
            main_sha = args.main_sha
            ci_run_id = args.ci_run
            if not isinstance(main_sha, str) or ci_run_id is None:
                raise ReleaseGateError("GA_PRE_TAG_INPUTS_REQUIRED")
            run_json = json.loads(
                subprocess.check_output(
                    (
                        "gh",
                        "run",
                        "view",
                        str(ci_run_id),
                        "--repo",
                        args.repository,
                        "--json",
                        "headSha,conclusion",
                    ),
                    text=True,
                )
            )
            log = subprocess.check_output(
                (
                    "gh",
                    "run",
                    "view",
                    str(ci_run_id),
                    "--repo",
                    args.repository,
                    "--log",
                ),
                text=True,
            )
            ci = CiReadback(
                run_id=ci_run_id,
                head_sha=str(run_json["headSha"]),
                conclusion=str(run_json["conclusion"]),
                pytest_pass_count=parse_pytest_count(log),
            )
            receipt = verify_pre_tag(
                record,
                main_sha=main_sha,
                canary=_snapshot(
                    _required_path(args.canary, "GA_CANARY_RECEIPT_REQUIRED")
                ),
                activation=_snapshot(
                    _required_path(args.activation, "GA_ACTIVATION_RECEIPT_REQUIRED")
                ),
                admission=_snapshot(
                    _required_path(
                        args.default_writer,
                        "GA_DEFAULT_WRITER_RECEIPT_REQUIRED",
                    )
                ),
                ci=ci,
                git=GitCliReadback(args.repository),
            )
            _write_json(
                _required_path(args.output, "GA_OUTPUT_REQUIRED"),
                dataclasses.asdict(receipt),
            )
            return 0

        if args.post_release:
            tag = args.tag
            run_root = _required_path(args.run_root, "GA_RUN_ROOT_REQUIRED")
            if not isinstance(tag, str) or not tag:
                raise ReleaseGateError("GA_TAG_REQUIRED")
            output = _required_path(args.output, "GA_OUTPUT_REQUIRED")
            run_root.mkdir(parents=True, exist_ok=True)
            source = run_root / "tag-source"
            source.mkdir(parents=True, exist_ok=True)
            archive = subprocess.check_output(("git", "archive", "--format=tar", tag))
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
                tar.extractall(source, filter="data")
            result = clean_install_and_smoke(source, run_root)
            _write_json(output, dataclasses.asdict(result))
            return 0
        return 3
    except (
        ReleaseGateError,
        subprocess.CalledProcessError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        if isinstance(error, ReleaseGateError):
            print(f"error: {error.code}", file=sys.stderr)
        else:
            print(f"error: {error}", file=sys.stderr)
        return 2


RELEASE_CONTRACT = """# GWO V8 GA Release Contract

Schema: `gwo-v8-ga-release-record.v1`

The committed record freezes `evidence_base_sha`, `canary_target_sha`, the
Canary/Activation/default-writer receipt digests, and the exact metadata path
allow-list. It deliberately contains no tag-candidate SHA, final metadata commit SHA,
CI run ID, or pytest count. The pre-tag command obtains those
dynamic values only from exact merged-main and CI readback, then writes a
separate `ReleaseGateReceipt`.

The pre-tag gate fails closed unless the CI head SHA equals the tag-candidate
SHA, the CI conclusion is `success`, a pytest pass count is read from the
exact CI log, both static SHAs are ancestors of the candidate, and the
post-Canary delta is exactly the metadata allow-list. The post-release gate
archives the tag into an isolated temporary source and installs both Skill
packages into temporary `.agents`, `.codex`, and `.claude` surfaces before
smoking only the public `start`, `advance`, and `inspect` operations.
"""


def write_release_contract(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RELEASE_CONTRACT, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(verify_main())
