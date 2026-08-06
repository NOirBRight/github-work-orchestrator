"""Non-LLM event and timer wake handling for V8 Campaigns.

The Watchdog owns only rebuildable cursor and due-work projections.  It never
interprets a wake as lifecycle state; every accepted wake is handed to the
supplied ``WatchdogAdvancer`` after its source transaction commits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from pathlib import Path
import sqlite3
from typing import Mapping, NoReturn, Protocol
import uuid

from ._canonical import digest_value
from .execution_kernel import CampaignOutcome, CampaignStatus
from .plan_control import CampaignHandle


WATCHDOG_INPUT_INVALID = "WATCHDOG_INPUT_INVALID"
WATCHDOG_STORE_INVALID = "WATCHDOG_STORE_INVALID"
WATCHDOG_CURSOR_CONFLICT = "WATCHDOG_CURSOR_CONFLICT"
WATCHDOG_SOURCE_INVALID = "WATCHDOG_SOURCE_INVALID"

_ALLOWED_SOURCES = frozenset({"runtime", "candidate", "review", "hosted_check"})
_CURSOR_PATTERN = re.compile(r"[1-9][0-9]{0,18}\Z")
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_MAX_CURSOR = 2**63 - 1
_DUE_CLAIM_LEASE = timedelta(minutes=5)
_PATH_TYPE = type(Path())


class CampaignWatchdogError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _fail(code: str, detail: str) -> NoReturn:
    raise CampaignWatchdogError(code, detail)


def _validate_text(value: object, label: str, *, code: str, identity: bool = False) -> None:
    if type(value) is not str or not value:
        _fail(code, f"{label} must be non-empty exact text")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        _fail(code, f"{label} contains a non-Unicode-scalar character")
    if identity and not value.strip():
        _fail(code, f"{label} must not be blank text")


def _validate_campaign(handle: object, *, code: str = WATCHDOG_INPUT_INVALID) -> None:
    if type(handle) is not CampaignHandle:
        _fail(code, "Campaign identity must be an exact CampaignHandle")
    _validate_text(handle.repository, "Campaign repository", code=code, identity=True)
    _validate_text(handle.campaign_key, "Campaign key", code=code, identity=True)


def _validate_cursor(
    value: object,
    label: str,
    *,
    allow_none: bool = False,
    code: str = WATCHDOG_INPUT_INVALID,
) -> None:
    if value is None and allow_none:
        return
    if type(value) is not str or _CURSOR_PATTERN.fullmatch(value) is None:
        _fail(code, f"{label} must be canonical positive decimal text")
    if int(value) > _MAX_CURSOR:
        _fail(code, f"{label} exceeds the cursor range")


def _cursor_number(value: str | None) -> int | None:
    return None if value is None else int(value)


def _validate_digest(value: object, label: str, *, code: str) -> None:
    if type(value) is not str or _DIGEST_PATTERN.fullmatch(value) is None:
        _fail(code, f"{label} must be a SHA-256 digest")


def _validate_utc_timestamp(
    value: object,
    label: str,
    *,
    allow_none: bool = False,
    code: str = WATCHDOG_INPUT_INVALID,
) -> None:
    if value is None and allow_none:
        return
    if type(value) is not str:
        _fail(code, f"{label} must be canonical UTC text")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise CampaignWatchdogError(code, f"{label} is not a timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        _fail(code, f"{label} must use UTC")
    if parsed.isoformat() != value:
        _fail(code, f"{label} is not canonical UTC text")


def _validate_string_tuple(
    value: object,
    label: str,
    *,
    digest: bool = False,
    code: str = WATCHDOG_INPUT_INVALID,
) -> None:
    if type(value) is not tuple:
        _fail(code, f"{label} must be an exact tuple")
    for item in value:
        _validate_text(item, f"{label} entry", code=code, identity=True)
        if digest:
            _validate_digest(item, f"{label} entry", code=code)


@dataclass(frozen=True)
class WatchdogWake:
    cursor: str
    campaign: CampaignHandle
    source: str
    source_identity: str

    def __post_init__(self) -> None:
        if type(self) is not WatchdogWake:
            _fail(WATCHDOG_INPUT_INVALID, "WatchdogWake subclasses are not accepted")
        _validate_cursor(self.cursor, "wake cursor")
        _validate_campaign(self.campaign)
        if type(self.source) is not str or self.source not in _ALLOWED_SOURCES:
            _fail(WATCHDOG_SOURCE_INVALID, "wake source is not recognized")
        _validate_text(
            self.source_identity,
            "wake source identity",
            code=WATCHDOG_INPUT_INVALID,
            identity=True,
        )

    @property
    def wake_ref(self) -> str:
        return f"watchdog:{self.source}:{self.cursor}:{self.source_identity}"


@dataclass(frozen=True)
class WatchdogCampaignSnapshot:
    campaign: CampaignHandle
    status: CampaignStatus
    trusted_progress_digest: str
    next_check_at: str | None
    active_binding_ids: tuple[str, ...]
    diagnosed_binding_ids: tuple[str, ...]
    candidate_receipt_digests: tuple[str, ...]
    last_wake_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self) is not WatchdogCampaignSnapshot:
            _fail(
                WATCHDOG_INPUT_INVALID,
                "WatchdogCampaignSnapshot subclasses are not accepted",
            )
        _validate_campaign(self.campaign)
        if type(self.status) is not CampaignStatus:
            _fail(WATCHDOG_INPUT_INVALID, "Campaign status is not a closed CampaignStatus")
        _validate_digest(self.trusted_progress_digest, "trusted progress digest", code=WATCHDOG_INPUT_INVALID)
        _validate_utc_timestamp(
            self.next_check_at,
            "next_check_at",
            allow_none=True,
        )
        _validate_string_tuple(self.active_binding_ids, "active binding identities")
        _validate_string_tuple(self.diagnosed_binding_ids, "diagnosed binding identities")
        _validate_string_tuple(
            self.candidate_receipt_digests,
            "Candidate receipt digests",
            digest=True,
        )
        _validate_string_tuple(self.last_wake_refs, "last wake references")


@dataclass(frozen=True)
class WatchdogWakePage:
    events: tuple[WatchdogWake, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        if type(self) is not WatchdogWakePage:
            _fail(WATCHDOG_INPUT_INVALID, "WatchdogWakePage subclasses are not accepted")
        if type(self.events) is not tuple:
            _fail(WATCHDOG_INPUT_INVALID, "wake page events must be an exact tuple")
        previous: int | None = None
        for event in self.events:
            if type(event) is not WatchdogWake:
                _fail(WATCHDOG_INPUT_INVALID, "wake page contains a non-exact WatchdogWake")
            current = int(event.cursor)
            if previous is not None and current <= previous:
                _fail(WATCHDOG_CURSOR_CONFLICT, "wake page cursors must increase")
            previous = current
        _validate_cursor(self.next_cursor, "page next cursor", allow_none=True)
        if self.events and self.next_cursor != self.events[-1].cursor:
            _fail(
                WATCHDOG_CURSOR_CONFLICT,
                "a non-empty wake page must end at its last event cursor",
            )


class WatchdogEventSource(Protocol):
    def read(self, after_cursor: str | None) -> WatchdogWakePage: ...


class WatchdogCampaignSource(Protocol):
    def active_campaigns(self) -> tuple[CampaignHandle, ...]: ...

    def watchdog_snapshot(self, handle: CampaignHandle) -> WatchdogCampaignSnapshot: ...


class WatchdogAdvancer(Protocol):
    def advance(self, handle: CampaignHandle, wake_ref: str | None = None) -> CampaignOutcome: ...


def _wake_canonical(wake: WatchdogWake) -> dict[str, object]:
    return {
        "campaign": {
            "campaign_key": wake.campaign.campaign_key,
            "repository": wake.campaign.repository,
        },
        "cursor": wake.cursor,
        "source": wake.source,
        "source_identity": wake.source_identity,
        "wake_ref": wake.wake_ref,
    }


def _page_digest(page: WatchdogWakePage) -> str:
    return digest_value(
        {
            "events": [_wake_canonical(event) for event in page.events],
            "next_cursor": page.next_cursor,
        }
    )


def _validate_event_source(source: object, label: str) -> None:
    if not callable(getattr(source, "read", None)):
        _fail(WATCHDOG_SOURCE_INVALID, f"{label} must expose read(after_cursor)")


def _validate_active_campaigns(value: object) -> tuple[CampaignHandle, ...]:
    if type(value) is not tuple:
        _fail(WATCHDOG_SOURCE_INVALID, "active Campaigns must be an exact tuple")
    for handle in value:
        _validate_campaign(handle, code=WATCHDOG_SOURCE_INVALID)
    return value


def _validate_snapshot_for_handle(
    handle: CampaignHandle,
    snapshot: object,
) -> WatchdogCampaignSnapshot:
    if type(snapshot) is not WatchdogCampaignSnapshot:
        _fail(
            WATCHDOG_INPUT_INVALID,
            "Campaign source must return an exact WatchdogCampaignSnapshot",
        )
    if snapshot.campaign != handle:
        _fail(WATCHDOG_INPUT_INVALID, "Campaign snapshot identity does not match its handle")
    return snapshot


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS v8_watchdog_sources "
    "(stream TEXT PRIMARY KEY, cursor TEXT, page_digest TEXT NOT NULL);"
    "CREATE TABLE IF NOT EXISTS v8_watchdog_due "
    "(repository TEXT NOT NULL, campaign_key TEXT NOT NULL, next_check_at TEXT NOT NULL, "
    "progress_digest TEXT NOT NULL, PRIMARY KEY(repository, campaign_key));"
    "CREATE TABLE IF NOT EXISTS v8_watchdog_wakes "
    "(wake_ref TEXT PRIMARY KEY, stream TEXT NOT NULL, cursor TEXT NOT NULL, repository TEXT NOT NULL, "
    "campaign_key TEXT NOT NULL, source TEXT NOT NULL, source_identity TEXT NOT NULL);"
    "CREATE TABLE IF NOT EXISTS v8_watchdog_pending_wakes "
    "(wake_ref TEXT PRIMARY KEY, stream TEXT NOT NULL, cursor TEXT NOT NULL, repository TEXT NOT NULL, "
    "campaign_key TEXT NOT NULL, source TEXT NOT NULL, source_identity TEXT NOT NULL);"
    "CREATE TABLE IF NOT EXISTS v8_watchdog_due_claims "
    "(repository TEXT NOT NULL, campaign_key TEXT NOT NULL, claim_token TEXT NOT NULL, "
    "claimed_until TEXT NOT NULL, PRIMARY KEY(repository, campaign_key));"
)

_SCHEMA_COLUMNS = {
    "v8_watchdog_sources": ("stream", "cursor", "page_digest"),
    "v8_watchdog_due": (
        "repository",
        "campaign_key",
        "next_check_at",
        "progress_digest",
    ),
    "v8_watchdog_wakes": (
        "wake_ref",
        "stream",
        "cursor",
        "repository",
        "campaign_key",
        "source",
        "source_identity",
    ),
    "v8_watchdog_pending_wakes": (
        "wake_ref",
        "stream",
        "cursor",
        "repository",
        "campaign_key",
        "source",
        "source_identity",
    ),
    "v8_watchdog_due_claims": (
        "repository",
        "campaign_key",
        "claim_token",
        "claimed_until",
    ),
}


class CampaignWatchdog:
    def __init__(
        self,
        *,
        store_path: Path,
        event_sources: Mapping[str, WatchdogEventSource],
        campaign_source: WatchdogCampaignSource,
        advancer: WatchdogAdvancer,
    ) -> None:
        if type(self) is not CampaignWatchdog:
            _fail(WATCHDOG_SOURCE_INVALID, "CampaignWatchdog subclasses are not accepted")
        if type(store_path) is not _PATH_TYPE or type(event_sources) is not dict or not event_sources:
            _fail(
                WATCHDOG_SOURCE_INVALID,
                "exact store path and event source mapping are required",
            )
        for name, source in event_sources.items():
            if type(name) is not str or not name:
                _fail(WATCHDOG_SOURCE_INVALID, "stream names must be non-empty text")
            _validate_event_source(source, f"{name} event source")
        if not callable(getattr(campaign_source, "active_campaigns", None)) or not callable(
            getattr(campaign_source, "watchdog_snapshot", None)
        ):
            _fail(WATCHDOG_SOURCE_INVALID, "Campaign source does not expose the Watchdog protocol")
        if not callable(getattr(advancer, "advance", None)):
            _fail(WATCHDOG_SOURCE_INVALID, "advancer does not expose advance(handle, wake_ref)")

        self._store_path = store_path
        self._event_sources = dict(event_sources)
        self._campaign_source = campaign_source
        self._advancer = advancer

        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self._store_path)
            connection.executescript(_SCHEMA)
            self._verify_schema(connection)
        except CampaignWatchdogError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise CampaignWatchdogError(
                WATCHDOG_STORE_INVALID,
                "Watchdog SQLite store could not be initialized",
            ) from error
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _verify_schema(connection: sqlite3.Connection) -> None:
        for table, expected_columns in _SCHEMA_COLUMNS.items():
            try:
                rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
            except sqlite3.Error as error:
                raise CampaignWatchdogError(
                    WATCHDOG_STORE_INVALID,
                    "Watchdog SQLite schema could not be read",
                ) from error
            actual_columns = tuple(row[1] for row in rows)
            if actual_columns != expected_columns:
                raise CampaignWatchdogError(
                    WATCHDOG_STORE_INVALID,
                    f"Watchdog SQLite table {table} has an invalid schema",
                )

    def _read_saved_source(
        self,
        connection: sqlite3.Connection,
        stream: str,
    ) -> tuple[str | None, str] | None:
        try:
            row = connection.execute(
                "SELECT cursor, page_digest FROM v8_watchdog_sources WHERE stream=?",
                (stream,),
            ).fetchone()
        except sqlite3.Error as error:
            raise CampaignWatchdogError(
                WATCHDOG_STORE_INVALID,
                "Watchdog source cursor could not be read",
            ) from error
        if row is None:
            return None
        _validate_cursor(
            row[0],
            "saved source cursor",
            allow_none=True,
            code=WATCHDOG_STORE_INVALID,
        )
        _validate_digest(row[1], "saved page digest", code=WATCHDOG_STORE_INVALID)
        return row[0], row[1]

    def _read_pending_wakes(self) -> tuple[WatchdogWake, ...]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self._store_path)
            rows = connection.execute(
                "SELECT wake_ref, stream, cursor, repository, campaign_key, source, "
                "source_identity FROM v8_watchdog_pending_wakes "
                "ORDER BY stream, CAST(cursor AS INTEGER), wake_ref"
            ).fetchall()
            pending: list[WatchdogWake] = []
            for wake_ref, _stream, cursor, repository, campaign_key, source, source_identity in rows:
                wake = WatchdogWake(
                    cursor,
                    CampaignHandle(repository, campaign_key),
                    source,
                    source_identity,
                )
                if wake.wake_ref != wake_ref:
                    _fail(
                        WATCHDOG_STORE_INVALID,
                        "pending Watchdog wake reference does not match its payload",
                    )
                pending.append(wake)
            return tuple(pending)
        except CampaignWatchdogError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise CampaignWatchdogError(
                WATCHDOG_STORE_INVALID,
                "pending Watchdog wakes could not be read",
            ) from error
        finally:
            if connection is not None:
                connection.close()

    def _ack_pending_wake(self, wake_ref: str) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self._store_path)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM v8_watchdog_pending_wakes WHERE wake_ref=?",
                (wake_ref,),
            )
            connection.commit()
        except (OSError, sqlite3.Error) as error:
            self._rollback(connection)
            raise CampaignWatchdogError(
                WATCHDOG_STORE_INVALID,
                "pending Watchdog wake could not be acknowledged",
            ) from error
        finally:
            if connection is not None:
                connection.close()

    def _drain_pending_wakes(self, outcomes: list[CampaignOutcome]) -> None:
        for wake in self._read_pending_wakes():
            outcome = self._advancer.advance(wake.campaign, wake.wake_ref)
            self._ack_pending_wake(wake.wake_ref)
            outcomes.append(outcome)

    def _claim_due_work(
        self,
        now: str,
    ) -> tuple[tuple[CampaignHandle, str], ...]:
        now_at = datetime.fromisoformat(now)
        claimed_until = (now_at + _DUE_CLAIM_LEASE).isoformat()
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self._store_path)
            connection.execute("BEGIN IMMEDIATE")
            due = connection.execute(
                "SELECT repository, campaign_key, next_check_at "
                "FROM v8_watchdog_due WHERE next_check_at <= ? "
                "ORDER BY next_check_at, repository, campaign_key",
                (now,),
            ).fetchall()
            claims: list[tuple[CampaignHandle, str]] = []
            for repository, campaign_key, next_check_at in due:
                _validate_utc_timestamp(
                    next_check_at,
                    "saved due timestamp",
                    code=WATCHDOG_STORE_INVALID,
                )
                existing = connection.execute(
                    "SELECT claim_token, claimed_until FROM v8_watchdog_due_claims "
                    "WHERE repository=? AND campaign_key=?",
                    (repository, campaign_key),
                ).fetchone()
                if existing is not None:
                    _validate_text(
                        existing[0],
                        "saved due claim token",
                        code=WATCHDOG_STORE_INVALID,
                        identity=True,
                    )
                    _validate_utc_timestamp(
                        existing[1],
                        "saved due claim expiry",
                        code=WATCHDOG_STORE_INVALID,
                    )
                    if datetime.fromisoformat(existing[1]) > now_at:
                        continue
                claim_token = uuid.uuid4().hex
                connection.execute(
                    "INSERT INTO v8_watchdog_due_claims "
                    "(repository, campaign_key, claim_token, claimed_until) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(repository, campaign_key) DO UPDATE SET "
                    "claim_token=excluded.claim_token, claimed_until=excluded.claimed_until",
                    (repository, campaign_key, claim_token, claimed_until),
                )
                claims.append((CampaignHandle(repository, campaign_key), claim_token))
            connection.commit()
            return tuple(claims)
        except CampaignWatchdogError:
            self._rollback(connection)
            raise
        except (OSError, sqlite3.Error) as error:
            self._rollback(connection)
            raise CampaignWatchdogError(
                WATCHDOG_STORE_INVALID,
                "Watchdog due claims could not be acquired",
            ) from error
        finally:
            if connection is not None:
                connection.close()

    def _release_due_claim(self, handle: CampaignHandle, claim_token: str) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self._store_path)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM v8_watchdog_due_claims "
                "WHERE repository=? AND campaign_key=? AND claim_token=?",
                (handle.repository, handle.campaign_key, claim_token),
            )
            connection.commit()
        except (OSError, sqlite3.Error) as error:
            self._rollback(connection)
            raise CampaignWatchdogError(
                WATCHDOG_STORE_INVALID,
                "Watchdog due claim could not be released",
            ) from error
        finally:
            if connection is not None:
                connection.close()

    def _ack_due_claim(self, handle: CampaignHandle, claim_token: str) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self._store_path)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM v8_watchdog_due "
                "WHERE repository=? AND campaign_key=? "
                "AND EXISTS (SELECT 1 FROM v8_watchdog_due_claims "
                "WHERE repository=? AND campaign_key=? AND claim_token=?)",
                (
                    handle.repository,
                    handle.campaign_key,
                    handle.repository,
                    handle.campaign_key,
                    claim_token,
                ),
            )
            connection.execute(
                "DELETE FROM v8_watchdog_due_claims "
                "WHERE repository=? AND campaign_key=? AND claim_token=?",
                (handle.repository, handle.campaign_key, claim_token),
            )
            connection.commit()
        except (OSError, sqlite3.Error) as error:
            self._rollback(connection)
            raise CampaignWatchdogError(
                WATCHDOG_STORE_INVALID,
                "Watchdog due claim could not be acknowledged",
            ) from error
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _rollback(connection: sqlite3.Connection | None) -> None:
        if connection is not None:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass

    def rebuild_due_queue(self) -> None:
        active_campaigns = _validate_active_campaigns(
            self._campaign_source.active_campaigns()
        )
        snapshots: list[tuple[CampaignHandle, WatchdogCampaignSnapshot]] = []
        for handle in active_campaigns:
            snapshot = _validate_snapshot_for_handle(
                handle,
                self._campaign_source.watchdog_snapshot(handle),
            )
            snapshots.append((handle, snapshot))

        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self._store_path)
            connection.execute("BEGIN IMMEDIATE")
            active_keys: set[tuple[str, str]] = set()
            for handle, snapshot in snapshots:
                key = (handle.repository, handle.campaign_key)
                active_keys.add(key)
                if snapshot.status is CampaignStatus.COMPLETE or snapshot.next_check_at is None:
                    connection.execute(
                        "DELETE FROM v8_watchdog_due WHERE repository=? AND campaign_key=?",
                        key,
                    )
                    continue
                connection.execute(
                    "INSERT INTO v8_watchdog_due"
                    "(repository, campaign_key, next_check_at, progress_digest) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(repository, campaign_key) DO UPDATE SET "
                    "next_check_at=excluded.next_check_at, "
                    "progress_digest=excluded.progress_digest",
                    (
                        *key,
                        snapshot.next_check_at,
                        snapshot.trusted_progress_digest,
                    ),
                )

            for repository, campaign_key in connection.execute(
                "SELECT repository, campaign_key FROM v8_watchdog_due"
            ).fetchall():
                if (repository, campaign_key) not in active_keys:
                    connection.execute(
                        "DELETE FROM v8_watchdog_due WHERE repository=? AND campaign_key=?",
                        (repository, campaign_key),
                    )
            connection.commit()
        except BaseException as error:
            self._rollback(connection)
            if isinstance(error, CampaignWatchdogError):
                raise
            if isinstance(error, sqlite3.Error):
                raise CampaignWatchdogError(
                    WATCHDOG_STORE_INVALID,
                    "Watchdog due projection transaction failed",
                ) from error
            raise
        finally:
            if connection is not None:
                connection.close()

    def _read_page(
        self,
        source: WatchdogEventSource,
        after_cursor: str | None,
    ) -> WatchdogWakePage:
        try:
            page = source.read(after_cursor)
        except CampaignWatchdogError:
            raise
        except Exception as error:
            raise CampaignWatchdogError(
                WATCHDOG_SOURCE_INVALID,
                "Watchdog event source read failed",
            ) from error
        if type(page) is not WatchdogWakePage:
            _fail(WATCHDOG_INPUT_INVALID, "event source must return an exact WatchdogWakePage")
        return page

    @staticmethod
    def _validate_page_after_cursor(
        page: WatchdogWakePage,
        after_cursor: str | None,
    ) -> None:
        after_number = _cursor_number(after_cursor)
        next_number = _cursor_number(page.next_cursor)
        if after_number is None:
            return
        if next_number is None or next_number <= after_number:
            _fail(
                WATCHDOG_CURSOR_CONFLICT,
                "source page cursor is not newer than the saved cursor",
            )
        for event in page.events:
            if int(event.cursor) <= after_number:
                _fail(
                    WATCHDOG_CURSOR_CONFLICT,
                    "source page contains an event at or before the saved cursor",
                )

    def run_once(self, now: str) -> tuple[CampaignOutcome, ...]:
        _validate_utc_timestamp(now, "now")
        outcomes: list[CampaignOutcome] = []
        self._drain_pending_wakes(outcomes)
        for stream, source in sorted(self._event_sources.items()):
            after_cursor = self.read_cursor(stream)
            page = self._read_page(source, after_cursor)
            page_digest = _page_digest(page)
            connection: sqlite3.Connection | None = None
            try:
                connection = sqlite3.connect(self._store_path)
                connection.execute("BEGIN IMMEDIATE")
                saved = self._read_saved_source(connection, stream)

                if saved == (page.next_cursor, page_digest):
                    self._rollback(connection)
                    continue
                if saved is not None and saved[0] == page.next_cursor:
                    if not page.events:
                        self._rollback(connection)
                        continue
                    _fail(
                        WATCHDOG_CURSOR_CONFLICT,
                        "cursor was reused with a changed page",
                    )
                if saved is not None and saved[0] != after_cursor:
                    _fail(
                        WATCHDOG_CURSOR_CONFLICT,
                        "source cursor changed before page publication",
                    )
                if saved is None and after_cursor is not None:
                    _fail(
                        WATCHDOG_CURSOR_CONFLICT,
                        "source cursor disappeared before page publication",
                    )
                if (
                    saved is not None
                    and saved[0] is not None
                    and page.next_cursor is not None
                    and int(page.next_cursor) < int(saved[0])
                ):
                    connection.rollback()
                    continue
                self._validate_page_after_cursor(page, after_cursor)

                for wake in page.events:
                    accepted = connection.execute(
                        "INSERT OR IGNORE INTO v8_watchdog_wakes "
                        "(wake_ref, stream, cursor, repository, campaign_key, source, source_identity) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            wake.wake_ref,
                            stream,
                            wake.cursor,
                            wake.campaign.repository,
                            wake.campaign.campaign_key,
                            wake.source,
                            wake.source_identity,
                        ),
                    )
                    if accepted.rowcount == 1:
                        connection.execute(
                            "INSERT INTO v8_watchdog_pending_wakes "
                            "(wake_ref, stream, cursor, repository, campaign_key, source, source_identity) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                wake.wake_ref,
                                stream,
                                wake.cursor,
                                wake.campaign.repository,
                                wake.campaign.campaign_key,
                                wake.source,
                                wake.source_identity,
                            ),
                        )

                if saved is None:
                    connection.execute(
                        "INSERT INTO v8_watchdog_sources(stream, cursor, page_digest) "
                        "VALUES (?, ?, ?)",
                        (stream, page.next_cursor, page_digest),
                    )
                else:
                    published = connection.execute(
                        "UPDATE v8_watchdog_sources SET cursor=?, page_digest=? "
                        "WHERE stream=? AND cursor IS ? AND page_digest=?",
                        (
                            page.next_cursor,
                            page_digest,
                            stream,
                            saved[0],
                            saved[1],
                        ),
                    )
                    if published.rowcount != 1:
                        _fail(
                            WATCHDOG_CURSOR_CONFLICT,
                            "source cursor compare-and-swap failed",
                        )
                connection.commit()
            except BaseException as error:
                self._rollback(connection)
                if isinstance(error, CampaignWatchdogError):
                    raise
                if isinstance(error, sqlite3.Error):
                    raise CampaignWatchdogError(
                        WATCHDOG_STORE_INVALID,
                        "Watchdog source cursor transaction failed",
                    ) from error
                raise
            finally:
                if connection is not None:
                    connection.close()

            self._drain_pending_wakes(outcomes)

        self.rebuild_due_queue()
        due_claims = self._claim_due_work(now)
        advanced_due_work = False
        for handle, claim_token in due_claims:
            try:
                before = _validate_snapshot_for_handle(
                    handle,
                    self._campaign_source.watchdog_snapshot(handle),
                )
                if (
                    before.status is CampaignStatus.COMPLETE
                    or before.next_check_at is None
                    or before.next_check_at > now
                ):
                    self._release_due_claim(handle, claim_token)
                    continue
                outcome = self._advancer.advance(handle, None)
                advanced_due_work = True
                after = _validate_snapshot_for_handle(
                    handle,
                    self._campaign_source.watchdog_snapshot(handle),
                )
                if (
                    after.status is CampaignStatus.COMPLETE
                    or after.next_check_at != before.next_check_at
                ):
                    self._ack_due_claim(handle, claim_token)
                else:
                    self._release_due_claim(handle, claim_token)
                outcomes.append(outcome)
            except Exception:
                self._release_due_claim(handle, claim_token)
                raise

        if advanced_due_work:
            self.rebuild_due_queue()
        return tuple(outcomes)

    def read_cursor(self, stream: str) -> str | None:
        if type(stream) is not str or not stream:
            _fail(WATCHDOG_SOURCE_INVALID, "stream must be non-empty text")
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self._store_path)
            row = connection.execute(
                "SELECT cursor, page_digest FROM v8_watchdog_sources WHERE stream=?",
                (stream,),
            ).fetchone()
            if row is None:
                return None
            _validate_cursor(
                row[0],
                "saved source cursor",
                allow_none=True,
                code=WATCHDOG_STORE_INVALID,
            )
            _validate_digest(row[1], "saved page digest", code=WATCHDOG_STORE_INVALID)
            return row[0]
        except CampaignWatchdogError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise CampaignWatchdogError(
                WATCHDOG_STORE_INVALID,
                "Watchdog source cursor could not be read",
            ) from error
        finally:
            if connection is not None:
                connection.close()


__all__ = [
    "CampaignWatchdog",
    "CampaignWatchdogError",
    "WatchdogAdvancer",
    "WatchdogCampaignSnapshot",
    "WatchdogCampaignSource",
    "WatchdogEventSource",
    "WatchdogWake",
    "WatchdogWakePage",
    "WATCHDOG_CURSOR_CONFLICT",
    "WATCHDOG_INPUT_INVALID",
    "WATCHDOG_SOURCE_INVALID",
    "WATCHDOG_STORE_INVALID",
]
