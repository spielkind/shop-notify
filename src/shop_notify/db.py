"""SQLite storage of listings, their history and the polls.

Schema version 4 (see ``docs/architecture.md`` for a diagram):

- ``listings``: one row per listing ever seen, with its latest state; rows are kept
  when a listing leaves the shop (``gone_since``). ``pending`` holds the notification
  waiting to be sent for a listing (the notification queue); ``image_url`` and
  ``thumbnail_url`` its main image, looked up again when the listing changes.
- ``listing_events``: append-only history per listing.
- ``polls``: one row per poll, kept for ``poll_history_days``.
"""

import json
import logging
import sqlite3
import time
from collections.abc import Collection, Iterable, Sequence
from contextlib import closing, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from shop_notify.models import DEFAULT_EVENTS, EVENTS, Listing, ListingImage, Money, Notice

log = logging.getLogger(__name__)

SCHEMA_VERSION = 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    listing_id     INTEGER PRIMARY KEY,
    first_seen     INTEGER NOT NULL,
    last_seen      INTEGER NOT NULL,
    missing_since  INTEGER,
    gone_since     INTEGER,
    sold_out_since INTEGER,
    notified_date  INTEGER,
    notified_price TEXT,
    filtered       INTEGER NOT NULL DEFAULT 0,
    pending        TEXT,
    result         TEXT NOT NULL,
    image_url      TEXT,
    thumbnail_url  TEXT,
    image_checked  INTEGER
);
CREATE TABLE IF NOT EXISTS listing_events (
    id         INTEGER PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listings (listing_id),
    time       INTEGER NOT NULL,
    event      TEXT NOT NULL,
    details    TEXT
);
CREATE INDEX IF NOT EXISTS listing_events_listing ON listing_events (listing_id, time);
CREATE INDEX IF NOT EXISTS listing_events_time ON listing_events (time);
CREATE TABLE IF NOT EXISTS polls (
    id        INTEGER PRIMARY KEY,
    time      INTEGER NOT NULL,
    reported  INTEGER,
    received  INTEGER,
    complete  INTEGER,
    new       INTEGER NOT NULL DEFAULT 0,
    back      INTEGER NOT NULL DEFAULT 0,
    missing   INTEGER NOT NULL DEFAULT 0,
    gone      INTEGER NOT NULL DEFAULT 0,
    filtered  INTEGER NOT NULL DEFAULT 0,
    restocked INTEGER NOT NULL DEFAULT 0,
    sold_out  INTEGER NOT NULL DEFAULT 0,
    error     TEXT
);
CREATE INDEX IF NOT EXISTS polls_time ON polls (time);
"""

# Schema of versions up to 1.0.x (user_version 0)
_LEGACY_COLUMNS = {"listing_id", "request_date", "notified_date", "result"}

# Notices for a listing that (re)appears; a filtered one is checked again later
_APPEARANCES = ("new", "back", "restocked")

# Etsy states recorded for a listing that went missing; once one is known, the
# state has been looked up and doesn't need to be requested again
_MISSING_STATES = ("sold_out", "inactive", "expired", "removed")


@dataclass(frozen=True)
class EventSettings:
    """Which events are notified, and their thresholds."""

    enabled: frozenset[str] = DEFAULT_EVENTS
    price_change_percent: Decimal = Decimal(5)
    """Minimum price change for price_drop and price_rise."""
    low_stock: int = 1
    """low_stock is notified when the quantity drops to this value or below."""


@dataclass
class SyncResult:
    """What changed in the shop since the previous poll."""

    new: int = 0
    back: int = 0
    restocked: int = 0
    missing: int = 0
    sold_out: int = 0
    gone: list[Listing] = field(default_factory=list)
    newly_missing: list[int] = field(default_factory=list)
    """Listings missing for the first time; their state is looked up at Etsy."""
    refilter: list[Listing] = field(default_factory=list)
    """Listings in the shop that were filtered out before; checked again every poll."""
    filtered: int = 0


class DatabaseUnavailableError(Exception):
    """The database can't be read: it doesn't exist yet or has another schema version."""


@dataclass(frozen=True)
class Event:
    id: int
    listing_id: int
    time: datetime
    event: str
    details: dict[str, Any]
    title: str


@dataclass(frozen=True)
class Poll:
    id: int
    time: datetime
    reported: int | None
    received: int | None
    complete: bool | None
    new: int
    back: int
    missing: int
    gone: int
    filtered: int
    restocked: int
    sold_out: int
    error: str | None


@dataclass(frozen=True)
class StoredListing:
    """The latest state of a listing as recorded in the database."""

    listing: Listing
    status: str
    """``listed``, ``missing``, ``sold_out`` or ``gone``."""
    first_seen: datetime
    last_seen: datetime
    missing_since: datetime | None
    gone_since: datetime | None
    sold_out_since: datetime | None
    notified: datetime | None
    """When it was last notified, or filtered out if ``filtered`` is set."""
    filtered: bool
    pending: str | None
    """The event of a notification waiting to be sent."""
    image_url: str | None
    """The main image, 570 pixels wide; None until looked up, or if there is none."""
    thumbnail_url: str | None
    """The main image, 170 x 135 pixels."""


# Conditions for the status of a listing, see StoredListing.status
STATUSES = {
    "listed": "missing_since IS NULL AND gone_since IS NULL",
    "missing": "missing_since IS NOT NULL AND gone_since IS NULL AND sold_out_since IS NULL",
    "sold_out": "sold_out_since IS NOT NULL AND gone_since IS NULL",
    "gone": "gone_since IS NOT NULL",
}

_STORED_COLUMNS = """
    result, first_seen, last_seen, missing_since, gone_since, sold_out_since,
    notified_date, filtered, pending, image_url, thumbnail_url
"""


def _stored(row: Sequence[Any]) -> StoredListing:
    result, first_seen, last_seen, missing, gone, sold_out, notified, filtered, pending = row[:9]
    if gone is not None:
        status = "gone"
    elif sold_out is not None:
        status = "sold_out"
    elif missing is not None:
        status = "missing"
    else:
        status = "listed"
    return StoredListing(
        listing=Listing.model_validate_json(result),
        status=status,
        first_seen=_ts(first_seen),
        last_seen=_ts(last_seen),
        missing_since=_optional_ts(missing),
        gone_since=_optional_ts(gone),
        sold_out_since=_optional_ts(sold_out),
        notified=_optional_ts(notified),
        filtered=bool(filtered),
        pending=Notice.model_validate_json(pending).kind if pending else None,
        image_url=row[9],
        thumbnail_url=row[10],
    )


def _ts(value: int) -> datetime:
    return datetime.fromtimestamp(value, tz=UTC)


def _optional_ts(value: int | None) -> datetime | None:
    return None if value is None else _ts(value)


def _open_readonly(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    if not path.is_file():
        raise DatabaseUnavailableError(f"No database at {path} - has Shop Notify run yet?")
    db = sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True)
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError as e:
        db.close()
        raise DatabaseUnavailableError(f"Can't read {path}: {e}") from e
    if version != SCHEMA_VERSION:
        db.close()
        raise DatabaseUnavailableError(
            f"Database {path} has schema version {version}, but this version of Shop Notify "
            f"reads version {SCHEMA_VERSION}"
        )
    return db


def _money(value: str | None) -> Money | None:
    return Money.model_validate_json(value) if value else None


def _percent(new: Money, old: Money) -> Decimal | None:
    if new.currency_code != old.currency_code or not old.value:
        return None
    return (new.value - old.value) / old.value * 100


class ListingStore:
    """Persists the listings of a shop, their history and the polls.

    With ``readonly=True`` the database is only read (for the API): it must exist and have
    the current schema version, and is never migrated or changed.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        poll_history_days: float = 14,
        events: EventSettings | None = None,
        readonly: bool = False,
    ) -> None:
        self._poll_history = poll_history_days * 86400
        self.events_settings = events or EventSettings()
        self.last_state_lookup_error: str | None = None
        """The last Etsy state-lookup error, to avoid repeating the warning every poll."""
        self.last_image_lookup_error: str | None = None
        """The last Etsy image-lookup error, likewise."""
        self._readonly = readonly
        if readonly:
            self._db = _open_readonly(path)
            return
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA foreign_keys = ON")
        try:
            self._migrate(path)
            # Readers (the API) and the poller don't block each other
            self._db.execute("PRAGMA journal_mode = WAL")
        except BaseException:
            self._db.close()
            raise

    def close(self) -> None:
        if not self._readonly:
            # Write the log into the database file, so that the file alone is complete
            # once the poller has stopped - SQLite skips this while a reader is open
            with suppress(sqlite3.Error):
                self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    # -- schema ---------------------------------------------------------------

    def _migrate(self, path: Path | str) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database {path} has schema version {version}, which is newer than this "
                f"version of Shop Notify supports ({SCHEMA_VERSION}) - please update"
            )
        if version == SCHEMA_VERSION:
            return
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(listings)")}
        if columns and str(path) != ":memory:":
            backup = Path(f"{path}.bak-v{version}")
            log.info("Upgrading database schema to version %d, backup: %s", SCHEMA_VERSION, backup)
            # SQLite's backup also copies changes that are still in the write-ahead log
            with closing(sqlite3.connect(backup)) as copy:
                self._db.backup(copy)
        legacy = columns == _LEGACY_COLUMNS
        with self._db:
            if legacy:
                self._db.execute("ALTER TABLE listings RENAME TO listings_v0")
            elif columns:
                self._upgrade(version)
            self._db.executescript(_SCHEMA)
            if legacy:
                self._db.execute(
                    """
                    INSERT INTO listings (listing_id, first_seen, last_seen, notified_date, result)
                    SELECT listing_id, MIN(request_date, COALESCE(notified_date, request_date)),
                           request_date, notified_date, result
                    FROM listings_v0
                    """
                )
                self._db.execute(
                    """
                    INSERT INTO listing_events (listing_id, time, event)
                    SELECT listing_id, first_seen, 'imported' FROM listings
                    """
                )
                self._db.execute(
                    'UPDATE listings SET pending = \'{"kind": "new"}\' WHERE notified_date IS NULL'
                )
                self._db.execute("DROP TABLE listings_v0")
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _upgrade(self, version: int) -> None:
        """Upgrade the tables of schema version 1, 2 or 3."""
        alter = self._db.execute
        if version < 3:
            self._upgrade_to_3(version)
        # 4: the main image of each listing, and when it was looked up
        for column in ("image_url TEXT", "thumbnail_url TEXT", "image_checked INTEGER"):
            alter(f"ALTER TABLE listings ADD COLUMN {column}")

    def _upgrade_to_3(self, version: int) -> None:
        alter = self._db.execute
        if version < 2:
            for table in ("listings", "polls"):
                alter(f"ALTER TABLE {table} ADD COLUMN filtered INTEGER NOT NULL DEFAULT 0")
        for column in ("sold_out_since INTEGER", "notified_price TEXT", "pending TEXT"):
            alter(f"ALTER TABLE listings ADD COLUMN {column}")
        for column in ("restocked", "sold_out"):
            alter(f"ALTER TABLE polls ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
        alter(
            """
            UPDATE listings SET pending = CASE
                WHEN back IS NOT NULL THEN json_set(back, '$.kind', 'back')
                ELSE '{"kind": "new"}' END
            WHERE notified_date IS NULL
            """
        )
        alter(
            """
            UPDATE listings SET notified_price = json_extract(result, '$.price')
            WHERE notified_date IS NOT NULL AND json_extract(result, '$.price') IS NOT NULL
            """
        )
        alter("ALTER TABLE listings DROP COLUMN back")

    # -- polling --------------------------------------------------------------

    def is_empty(self) -> bool:
        return self._db.execute("SELECT 1 FROM listings LIMIT 1").fetchone() is None

    def sync(
        self, listings: Iterable[Listing], *, complete: bool = True, forget_after: float = 86400
    ) -> SyncResult:
        """Store the current listings, record what changed and queue notifications.

        Listings missing from a ``complete`` result are marked as missing; once they have
        not been seen for more than ``forget_after`` seconds, they count as gone and are
        notified as back if they come back. A listing known to be sold out is notified as
        restocked as soon as it is back. An incomplete result never marks anything as
        missing or gone. The notifications are read with ``pending``.
        """
        now = int(time.time())
        listings = list(listings)
        result = SyncResult()
        rows = {
            row[0]: row
            for row in self._db.execute(
                """
                SELECT listing_id, last_seen, missing_since, gone_since, sold_out_since,
                       notified_price, pending, result
                FROM listings
                """
            )
        }
        with self._db:
            for item in listings:
                row = rows.get(item.listing_id)
                if row is None:
                    self._insert(item, now)
                    result.new += 1
                else:
                    kind = self._update(item, row, now)
                    if kind == "back":
                        result.back += 1
                    elif kind == "restocked":
                        result.restocked += 1
            if complete:
                self._mark_missing(listings, now, forget_after, result)

        position = {item.listing_id: index for index, item in enumerate(listings)}
        result.refilter = [
            Listing.model_validate_json(row[0])
            for row in self._db.execute(
                """
                SELECT result FROM listings
                WHERE filtered = 1 AND pending IS NULL AND last_seen = ?
                ORDER BY listing_id
                """,
                (now,),
            )
        ]
        result.refilter.sort(key=lambda item: position[item.listing_id])
        return result

    def pending(self, order: Sequence[Listing] = ()) -> list[Listing]:
        """The listings with a queued notification, in the given order, others last."""
        position = {item.listing_id: index for index, item in enumerate(order)}
        items = []
        for data, pending in self._db.execute(
            "SELECT result, pending FROM listings WHERE pending IS NOT NULL ORDER BY listing_id"
        ):
            item = Listing.model_validate_json(data)
            item.event = Notice.model_validate_json(pending)
            items.append(item)
        return sorted(items, key=lambda item: position.get(item.listing_id, len(position)))

    def _event(self, listing_id: int, now: int, event: str, **details: Any) -> None:
        self._db.execute(
            "INSERT INTO listing_events (listing_id, time, event, details) VALUES (?, ?, ?, ?)",
            (listing_id, now, event, json.dumps(details) if details else None),
        )

    def _queue(self, listing_id: int, current: str | None, candidates: Iterable[Notice]) -> None:
        """Queue the first enabled notice, unless a more important one is waiting."""
        enabled = self.events_settings.enabled
        notice = next((n for n in candidates if n.kind in enabled), None)
        if notice is None:
            return
        if current is not None:
            waiting = Notice.model_validate_json(current).kind
            # A new sale replaces anything but "new"; otherwise the more important one wins
            if notice.kind == "sold_out" and waiting != "new":
                pass
            elif EVENTS.index(waiting) <= EVENTS.index(notice.kind):
                return
        self._db.execute(
            "UPDATE listings SET pending = ? WHERE listing_id = ?",
            (notice.model_dump_json(exclude_none=True), listing_id),
        )

    def _low_quantity(self, item: Listing) -> int | None:
        low = self.events_settings.low_stock
        return item.quantity if item.quantity is not None and item.quantity <= low else None

    def _insert(self, item: Listing, now: int) -> None:
        self._db.execute(
            "INSERT INTO listings (listing_id, first_seen, last_seen, result) VALUES (?, ?, ?, ?)",
            (item.listing_id, now, now, item.model_dump_json()),
        )
        etsy = {
            "price": str(item.price) if item.price else None,
            "quantity": item.quantity,
            "created": item.original_creation_timestamp,
            "renewed": item.created_timestamp,
            "active_since": item.state_timestamp,
            "modified": item.last_modified_timestamp,
        }
        self._event(item.listing_id, now, "new", **{k: v for k, v in etsy.items() if v})
        self._queue(item.listing_id, None, [Notice(kind="new", quantity=self._low_quantity(item))])

    def _update(self, item: Listing, row: tuple[Any, ...], now: int) -> str | None:
        """Update a known listing; returns "back" or "restocked" if it (re)appeared."""
        _, last_seen, missing_since, gone_since, sold_out_since, notified, pending, data = row
        old = Listing.model_validate_json(data)
        if old.title != item.title:
            self._event(item.listing_id, now, "title_changed", old=old.title, new=item.title)
        if old.price != item.price:
            self._event(
                item.listing_id,
                now,
                "price_changed",
                old=str(old.price) if old.price else None,
                new=str(item.price) if item.price else None,
            )
        previous = old.price if old.price != item.price else None
        low = self._low_quantity(item)
        candidates: list[Notice] = []
        appeared = None
        if sold_out_since is not None:
            appeared = "restocked"
            self._event(item.listing_id, now, "restocked", sold_out_since=sold_out_since)
            candidates.append(
                Notice(
                    kind="restocked",
                    sold_out_since=_ts(sold_out_since),
                    previous_price=previous,
                    quantity=low,
                )
            )
        if gone_since is not None:
            appeared = appeared or "back"
            self._event(item.listing_id, now, "back", last_seen=last_seen)
            candidates.append(
                Notice(kind="back", last_seen=_ts(last_seen), previous_price=previous, quantity=low)
            )
        elif missing_since is not None and sold_out_since is None:
            self._event(item.listing_id, now, "missing", since=last_seen, seconds=now - last_seen)
        candidates += self._price_notices(item, old, _money(notified))
        threshold = self.events_settings.low_stock
        if low is not None and (old.quantity is None or old.quantity > threshold):
            self._event(item.listing_id, now, "low_stock", quantity=low)
            candidates.append(Notice(kind="low_stock", quantity=low))
        self._db.execute(
            """
            UPDATE listings SET last_seen = ?, missing_since = NULL, gone_since = NULL,
                sold_out_since = NULL, result = ?
            WHERE listing_id = ?
            """,
            (now, item.model_dump_json(), item.listing_id),
        )
        self._queue(item.listing_id, pending, candidates)
        return appeared

    def _price_notices(self, item: Listing, old: Listing, notified: Money | None) -> list[Notice]:
        if item.price is None or old.price is None or item.price == old.price:
            return []
        threshold = self.events_settings.price_change_percent
        # A drop counts from the lowest notified price, so that flipping prices don't
        # cause a notification every time; a rise from the previous price
        reference = notified or old.price
        drop = _percent(item.price, reference)
        if drop is not None and drop < 0 and -drop >= threshold:
            return [Notice(kind="price_drop", previous_price=reference)]
        rise = _percent(item.price, old.price)
        if rise is not None and rise >= threshold:
            return [Notice(kind="price_rise", previous_price=old.price)]
        return []

    def _mark_missing(
        self, listings: Sequence[Listing], now: int, forget_after: float, result: SyncResult
    ) -> None:
        present = [item.listing_id for item in listings]
        placeholders = ",".join("?" * len(present))
        absent = f"listing_id NOT IN ({placeholders})" if present else "1"
        # Only listings that were notified: pending new ones are sent anyway
        result.newly_missing = [
            row[0]
            for row in self._db.execute(
                f"""
                UPDATE listings SET missing_since = ?
                WHERE {absent} AND notified_date IS NOT NULL
                    AND missing_since IS NULL AND gone_since IS NULL
                RETURNING listing_id
                """,  # only placeholders are interpolated
                (now, *present),
            ).fetchall()
        ]
        result.missing = len(result.newly_missing)
        cutoff = int(now - forget_after)
        result.gone = [
            Listing.model_validate_json(row[0])
            for row in self._db.execute(
                """
                UPDATE listings SET gone_since = ?
                WHERE missing_since IS NOT NULL AND gone_since IS NULL AND last_seen < ?
                RETURNING result
                """,
                (now, cutoff),
            ).fetchall()
        ]
        for item in result.gone:
            self._event(item.listing_id, now, "gone")

    def missing_without_state(self) -> list[int]:
        """Missing listings whose Etsy state hasn't been looked up yet.

        Covers listings whose state lookup failed before, so that it is retried on a
        later poll instead of leaving them in an unknown state forever. Only a state
        recorded since the current ``missing_since`` counts: a listing that went
        missing again after an earlier state is still looked up afresh.
        """
        placeholders = ",".join("?" * len(_MISSING_STATES))
        return [
            row[0]
            for row in self._db.execute(
                f"""
                SELECT listing_id FROM listings
                WHERE missing_since IS NOT NULL AND gone_since IS NULL
                    AND sold_out_since IS NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM listing_events e
                        WHERE e.listing_id = listings.listing_id
                            AND e.event IN ({placeholders})
                            AND e.time >= listings.missing_since
                    )
                ORDER BY listing_id
                """,  # only placeholders are interpolated
                _MISSING_STATES,
            )
        ]

    def record_states(self, states: dict[int, str], result: SyncResult) -> None:
        """Record the Etsy state of listings that went missing (sold_out, inactive, …)."""
        now = int(time.time())
        with self._db:
            for listing_id, state in states.items():
                known = self._db.execute(
                    "SELECT 1 FROM listings WHERE listing_id = ?", (listing_id,)
                ).fetchone()
                if state == "active" or known is None:
                    continue  # a gap in Etsy's listing index, or not ours
                self._event(listing_id, now, state)
                if state != "sold_out":
                    continue
                first_seen, pending = self._db.execute(
                    "UPDATE listings SET sold_out_since = ? WHERE listing_id = ? "
                    "RETURNING first_seen, pending",
                    (now, listing_id),
                ).fetchone()
                result.sold_out += 1
                self._queue(
                    listing_id, pending, [Notice(kind="sold_out", first_seen=_ts(first_seen))]
                )

    def mark_as_notified(self, listings: Iterable[Listing], *, sent: bool = True) -> None:
        """Mark listings as notified; ``sent=False`` if they were only recorded."""
        now = int(time.time())
        with self._db:
            for item in listings:
                updated = self._db.execute(
                    """
                    UPDATE listings SET notified_date = ?, filtered = 0, pending = NULL,
                        notified_price = CASE
                            WHEN json_extract(result, '$.price') IS NULL THEN notified_price
                            WHEN json_extract(pending, '$.kind') IN ('price_rise', 'sold_out')
                                AND notified_price IS NOT NULL THEN notified_price
                            ELSE json_extract(result, '$.price') END
                    WHERE listing_id = ? AND (pending IS NOT NULL OR filtered = 1)
                    """,
                    (now, item.listing_id),
                ).rowcount
                if updated:
                    details = {"kind": item.kind} if item.kind != "new" else {}
                    self._event(item.listing_id, now, "notified" if sent else "recorded", **details)

    def mark_as_filtered(self, listings: Iterable[tuple[Listing, str]]) -> None:
        """Mark pending listings as filtered out, so they are not notified.

        Filtered new listings (and those back in the shop or restocked) are checked again
        on every poll while they are in the shop; other notices are just dropped.
        """
        now = int(time.time())
        with self._db:
            for item, reason in listings:
                updated = self._db.execute(
                    """
                    UPDATE listings SET notified_date = ?, pending = NULL,
                        filtered = CASE WHEN ? THEN 1 ELSE filtered END
                    WHERE listing_id = ? AND pending IS NOT NULL
                    """,
                    (now, item.kind in _APPEARANCES, item.listing_id),
                ).rowcount
                if updated:
                    self._event(item.listing_id, now, "filtered", reason=reason)

    def record_poll(
        self,
        *,
        reported: int | None = None,
        received: int | None = None,
        complete: bool | None = None,
        result: SyncResult | None = None,
        error: str | None = None,
    ) -> None:
        """Record a poll and delete polls older than the configured retention."""
        now = int(time.time())
        result = result or SyncResult()
        with self._db:
            self._db.execute(
                """
                INSERT INTO polls (time, reported, received, complete, new, back, missing,
                                   gone, filtered, restocked, sold_out, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    reported,
                    received,
                    complete,
                    result.new,
                    result.back,
                    result.missing,
                    len(result.gone),
                    result.filtered,
                    result.restocked,
                    result.sold_out,
                    error,
                ),
            )
            self._db.execute("DELETE FROM polls WHERE time < ?", (now - self._poll_history,))

    def incomplete_since(self) -> datetime | None:
        """Start of the current series of incomplete polls, if the last one was incomplete."""
        row = self._db.execute(
            """
            SELECT MIN(time) FROM polls
            WHERE complete = 0
                AND time > COALESCE((SELECT MAX(time) FROM polls WHERE complete = 1), 0)
            """
        ).fetchone()
        return _ts(row[0]) if row[0] is not None else None

    # -- history --------------------------------------------------------------

    def events(
        self,
        *,
        listing_id: int | None = None,
        since: float = 0,
        kinds: Collection[str] = (),
        text: str | None = None,
        before: int | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> list[Event]:
        """The recorded events, oldest first unless ``newest_first``.

        ``kinds`` keeps only these events, ``text`` those of listings whose title contains
        it (ignoring the case of ASCII letters). ``before`` keeps the events older than the
        event with this ID, to page through them ``limit`` at a time.
        """
        query = """
            SELECT e.id, e.listing_id, e.time, e.event, e.details, l.result
            FROM listing_events e JOIN listings l USING (listing_id)
            WHERE e.time >= ?
        """
        params: list[Any] = [since]
        if listing_id is not None:
            query += " AND e.listing_id = ?"
            params.append(listing_id)
        if kinds:
            # only placeholders are interpolated, here and for the sort order below
            query += f" AND e.event IN ({','.join('?' * len(kinds))})"
            params += kinds
        if text:
            query += r" AND json_extract(l.result, '$.title') LIKE ? ESCAPE '\'"
            params.append(f"%{_escape_like(text)}%")
        if before is not None:
            query += " AND (e.time, e.id) < (SELECT time, id FROM listing_events WHERE id = ?)"
            params.append(before)
        order = "DESC" if newest_first else "ASC"
        query += f" ORDER BY e.time {order}, e.id {order}"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        return [
            Event(
                id=row[0],
                listing_id=row[1],
                time=_ts(row[2]),
                event=row[3],
                details=json.loads(row[4]) if row[4] else {},
                title=Listing.model_validate_json(row[5]).title,
            )
            for row in self._db.execute(query, params)
        ]

    def listing(self, listing_id: int) -> StoredListing | None:
        """The latest recorded state of a listing, or None if it isn't known."""
        row = self._db.execute(
            f"SELECT {_STORED_COLUMNS} FROM listings WHERE listing_id = ?",  # constant
            (listing_id,),
        ).fetchone()
        return None if row is None else _stored(row)

    def listings(
        self,
        *,
        status: str | None = None,
        text: str | None = None,
        before: int | None = None,
        limit: int | None = None,
    ) -> list[StoredListing]:
        """The listings, the newest (first seen) first.

        ``status`` (a key of ``STATUSES``) and ``text`` (in the title, ignoring the case of
        ASCII letters) filter them; ``before`` keeps those after the listing with this ID
        in this order, to page through them ``limit`` at a time.
        """
        query = f"SELECT {_STORED_COLUMNS} FROM listings WHERE 1"
        params: list[Any] = []
        if status is not None:
            query += f" AND {STATUSES[status]}"  # only the constant conditions above
        if text:
            query += r" AND json_extract(result, '$.title') LIKE ? ESCAPE '\'"
            params.append(f"%{_escape_like(text)}%")
        if before is not None:
            query += (
                " AND (first_seen, listing_id) <"
                " (SELECT first_seen, listing_id FROM listings WHERE listing_id = ?)"
            )
            params.append(before)
        query += " ORDER BY first_seen DESC, listing_id DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        return [_stored(row) for row in self._db.execute(query, params)]

    def listing_counts(self) -> dict[str, int]:
        """How many listings have each status."""
        sums = ", ".join(f"COALESCE(SUM({condition}), 0)" for condition in STATUSES.values())
        row = self._db.execute(f"SELECT {sums} FROM listings").fetchone()  # constant
        return dict(zip(STATUSES, row, strict=True))

    # -- images ---------------------------------------------------------------

    def listings_needing_images(self, limit: int) -> list[int]:
        """Listings in the shop whose image wasn't looked up since they last changed."""
        return [
            row[0]
            for row in self._db.execute(
                f"""
                SELECT listing_id FROM listings
                WHERE {STATUSES["listed"]}
                    AND (image_checked IS NULL
                        OR image_checked != json_extract(result, '$.last_modified_timestamp'))
                ORDER BY first_seen DESC, listing_id DESC LIMIT ?
                """,  # only the constant condition is interpolated
                (limit,),
            )
        ]

    def record_images(self, listing_ids: Iterable[int], images: dict[int, ListingImage]) -> None:
        """Store the images looked up for ``listing_ids``; those without one have none."""
        with self._db:
            for listing_id in listing_ids:
                image = images.get(listing_id)
                self._db.execute(
                    """
                    UPDATE listings SET image_url = ?, thumbnail_url = ?,
                        image_checked = json_extract(result, '$.last_modified_timestamp')
                    WHERE listing_id = ?
                    """,
                    (
                        image.url if image else None,
                        image.thumbnail_url if image else None,
                        listing_id,
                    ),
                )

    def image_urls(self, listing_ids: Sequence[int]) -> dict[int, str]:
        """The stored main images (570 pixels wide) of these listings, if any."""
        if not listing_ids:
            return {}
        placeholders = ",".join("?" * len(listing_ids))
        return dict(
            self._db.execute(
                f"""
                SELECT listing_id, image_url FROM listings
                WHERE listing_id IN ({placeholders}) AND image_url IS NOT NULL
                """,  # only placeholders are interpolated
                listing_ids,
            ).fetchall()
        )

    def seen_since(self, since: float) -> list[Listing]:
        """The latest state of the listings seen in the shop since ``since``, newest first."""
        return [
            Listing.model_validate_json(row[0])
            for row in self._db.execute(
                "SELECT result FROM listings WHERE last_seen >= ? "
                "ORDER BY first_seen DESC, listing_id DESC",
                (since,),
            )
        ]

    def polls(
        self,
        *,
        since: float = 0,
        before: int | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> list[Poll]:
        """The recorded polls, oldest first unless ``newest_first``.

        ``before`` keeps the polls older than the poll with this ID, to page through them
        ``limit`` at a time.
        """
        query = """
            SELECT id, time, reported, received, complete, new, back, missing, gone, filtered,
                   restocked, sold_out, error
            FROM polls WHERE time >= ?
        """
        params: list[Any] = [since]
        if before is not None:
            query += " AND (time, id) < (SELECT time, id FROM polls WHERE id = ?)"
            params.append(before)
        order = "DESC" if newest_first else "ASC"
        query += f" ORDER BY time {order}, id {order}"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        return [
            Poll(
                id=row[0],
                time=_ts(row[1]),
                reported=row[2],
                received=row[3],
                complete=None if row[4] is None else bool(row[4]),
                new=row[5],
                back=row[6],
                missing=row[7],
                gone=row[8],
                filtered=row[9],
                restocked=row[10],
                sold_out=row[11],
                error=row[12],
            )
            for row in self._db.execute(query, params)
        ]


def _escape_like(text: str) -> str:
    """Make ``text`` match literally in a LIKE pattern with ``ESCAPE '\\'``."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
