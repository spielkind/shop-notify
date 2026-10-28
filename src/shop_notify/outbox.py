"""Durable, coalescing delivery queue; network I/O never runs in a transaction."""

import sqlite3
import time
from collections.abc import Iterable
from decimal import Decimal

from shop_notify.models import EVENTS, Listing, Money, Notice
from shop_notify.scheduling import Schedule

SCHEMA = """
CREATE TABLE IF NOT EXISTS delivery_groups (
    name TEXT PRIMARY KEY,
    digest_slot REAL
);
CREATE TABLE IF NOT EXISTS recipient_prices (
    listing_id INTEGER NOT NULL REFERENCES listings(listing_id),
    group_name TEXT NOT NULL,
    price TEXT,
    known INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (listing_id, group_name)
);
CREATE TABLE IF NOT EXISTS notification_outbox (
    listing_id INTEGER NOT NULL REFERENCES listings(listing_id),
    target TEXT NOT NULL,
    group_name TEXT NOT NULL,
    notice TEXT NOT NULL,
    queued REAL NOT NULL,
    digest_slot REAL,
    quiet_deferred INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (listing_id, target)
);
CREATE TABLE IF NOT EXISTS delivery_metadata (key TEXT PRIMARY KEY);
"""


class Outbox:
    def __init__(
        self,
        db: sqlite3.Connection,
        routes: dict[str, str],
        schedule: Schedule,
        price_change_percent: Decimal = Decimal(5),
    ) -> None:
        self.price_change_percent = price_change_percent
        self.db = db
        self.routes = routes
        self.schedule = schedule
        # No delivery routes (including a first-run-only console) must not consume
        # the legacy pending migration or discard deliveries while disabled.
        if not routes:
            return
        now = time.time()
        with db:
            for group in set(routes.values()):
                slot = schedule.next_slot(group, now) if group in schedule.groups else None
                db.execute("INSERT OR IGNORE INTO delivery_groups VALUES (?, ?)", (group, slot))
                self._baselines(group)
            # Removed/reassigned targets must not hold the queue open forever. A change
            # of credentials changes the hashed identity; it never exposes a URL in SQLite.
            for target, group in db.execute("SELECT target, group_name FROM notification_outbox"):
                if routes.get(target) != group:
                    db.execute("DELETE FROM notification_outbox WHERE target = ?", (target,))
            migrated = db.execute("SELECT 1 FROM delivery_metadata WHERE key = 'initialized'")
            if migrated.fetchone() is None:
                for listing_id, notice in db.execute(
                    "SELECT listing_id, pending FROM listings WHERE pending IS NOT NULL"
                ).fetchall():
                    self.queue(listing_id, [Notice.model_validate_json(notice)])
                db.execute("INSERT INTO delivery_metadata VALUES ('initialized')")

    def _baselines(self, group: str, listing_id: int | None = None) -> None:
        # Only the one-time migration inherits legacy lowest prices. Later groups
        # start at the current price; INSERT OR IGNORE preserves existing baselines.
        self.db.execute(
            """INSERT OR IGNORE INTO recipient_prices
               SELECT listing_id, ?, CASE WHEN EXISTS (
                           SELECT 1 FROM delivery_metadata WHERE key = 'initialized'
                       ) THEN json_extract(result, '$.price')
                       ELSE COALESCE(notified_price, json_extract(result, '$.price')) END,
                      notified_date IS NOT NULL AND (filtered = 0 OR EXISTS (
                          SELECT 1 FROM listing_events e WHERE e.listing_id = listings.listing_id
                              AND e.event IN ('notified', 'recorded')
                      )) FROM listings
               WHERE ? IS NULL OR listing_id = ?""",
            (group, listing_id, listing_id),
        )

    def prices(self, listing_id: int) -> dict[str, Money | None]:
        return {
            group: Money.model_validate_json(price) if price else None
            for group, price in self.db.execute(
                "SELECT group_name, price FROM recipient_prices WHERE listing_id = ?",
                (listing_id,),
            )
            if group in self.routes.values()
        }

    def queue(
        self,
        listing_id: int,
        candidates: Iterable[Notice],
        *,
        per_group: dict[str, list[Notice]] | None = None,
    ) -> None:
        candidates = list(candidates)
        for group in set(self.routes.values()):
            self._baselines(group, listing_id)
            notices = per_group[group] if per_group is not None else candidates
            if not notices:
                continue
            notice = notices[0]
            for target, target_group in self.routes.items():
                if group != target_group:
                    continue
                row = self.db.execute(
                    "SELECT notice, digest_slot, quiet_deferred FROM notification_outbox "
                    "WHERE listing_id = ? AND target = ?",
                    (listing_id, target),
                ).fetchone()
                if row:
                    waiting = Notice.model_validate_json(row[0]).kind
                    if not (notice.kind == "sold_out" and waiting != "new") and (
                        EVENTS.index(waiting) <= EVENTS.index(notice.kind)
                    ):
                        continue
                slot = (
                    row[1]
                    if row
                    else self.db.execute(
                        "SELECT digest_slot FROM delivery_groups WHERE name = ?", (group,)
                    ).fetchone()[0]
                )
                quiet_deferred = bool(row and row[2]) or self.schedule.quiet(time.time())
                self.db.execute(
                    """INSERT OR REPLACE INTO notification_outbox
                       (listing_id, target, group_name, notice, queued, digest_slot, quiet_deferred)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        listing_id,
                        target,
                        group,
                        notice.model_dump_json(),
                        time.time(),
                        slot,
                        quiet_deferred,
                    ),
                )

    def discard(self, listing_id: int) -> None:
        self.db.execute("DELETE FROM notification_outbox WHERE listing_id = ?", (listing_id,))

    def record(self, listing_id: int) -> None:
        """First-run suppression is a baseline, not a future delivery."""
        self.discard(listing_id)
        self.db.execute(
            """UPDATE recipient_prices SET known = 1,
                price = (SELECT json_extract(result, '$.price') FROM listings WHERE listing_id = ?)
               WHERE listing_id = ?""",
            (listing_id, listing_id),
        )

    def ready(self, now: float) -> dict[str, list[Listing]]:
        if not self.routes:
            return {}
        with self.db:
            self.db.execute(
                """DELETE FROM notification_outbox AS o
                   WHERE json_extract(notice, '$.kind') = 'new'
                     AND EXISTS (SELECT 1 FROM listings l
                         WHERE l.listing_id = o.listing_id
                           AND (l.gone_since IS NOT NULL OR l.sold_out_since IS NOT NULL))"""
            )
            self._clear_finished()
        if self.schedule.quiet(now):
            with self.db:
                self.db.execute("UPDATE notification_outbox SET quiet_deferred = 1")
            return {}
        batches: dict[str, list[Listing]] = {}
        for target, data, notice in self.db.execute(
            """SELECT o.target, l.result, o.notice
               FROM notification_outbox o JOIN listings l USING(listing_id)
               WHERE o.digest_slot IS NULL OR o.digest_slot <= ?
               ORDER BY o.queued, o.listing_id""",
            (now,),
        ).fetchall():
            item = Listing.model_validate_json(data)
            item.event = Notice.model_validate_json(notice)
            if self._obsolete_price(item, self.price_change_percent):
                with self.db:
                    self.db.execute(
                        "DELETE FROM notification_outbox WHERE listing_id = ? AND target = ?",
                        (item.listing_id, target),
                    )
                    self._clear_finished()
                continue
            # Quantity and listing data are current, not a snapshot of the enqueue poll.
            if item.event.quantity is not None:
                item.event = item.event.model_copy(update={"quantity": item.quantity})
            batches.setdefault(target, []).append(item)
        return batches

    def quiet_deferred(self, target: str, items: Iterable[Listing]) -> bool:
        listing_ids = [item.listing_id for item in items]
        if not listing_ids:
            return False
        placeholders = ",".join("?" for _ in listing_ids)
        return (
            self.db.execute(
                f"""SELECT 1 FROM notification_outbox
                    WHERE target = ? AND quiet_deferred = 1
                      AND listing_id IN ({placeholders}) LIMIT 1""",
                (target, *listing_ids),
            ).fetchone()
            is not None
        )

    @staticmethod
    def _obsolete_price(item: Listing, threshold: Decimal = Decimal(5)) -> bool:
        notice = item.event
        if notice is None or item.kind not in ("price_drop", "price_rise"):
            return False
        old = notice.previous_price
        if item.price is None or old is None or item.price.currency_code != old.currency_code:
            return True
        if not old.value or abs((item.price.value - old.value) / old.value * 100) < threshold:
            return True
        if item.kind == "price_drop":
            return item.price.value >= old.value
        return item.price.value <= old.value

    def acknowledge(self, target: str, items: Iterable[Listing]) -> None:
        group = self.routes[target]
        with self.db:
            for item in items:
                self.db.execute(
                    "DELETE FROM notification_outbox WHERE target = ? AND listing_id = ?",
                    (target, item.listing_id),
                )
                self._acknowledge_price(group, item)
                self.db.execute(
                    """UPDATE listings SET notified_date = ?, notified_price = CASE
                        WHEN ? IS NULL THEN notified_price
                        WHEN ? IN ('price_rise', 'sold_out') AND notified_price IS NOT NULL
                            THEN notified_price ELSE ? END WHERE listing_id = ?""",
                    (
                        int(time.time()),
                        item.price.model_dump_json() if item.price else None,
                        item.kind,
                        item.price.model_dump_json() if item.price else None,
                        item.listing_id,
                    ),
                )
                self.db.execute(
                    """INSERT INTO listing_events(listing_id, time, event, details)
                       VALUES (?, ?, 'notified', json_object('kind', ?, 'group', ?))""",
                    (item.listing_id, int(time.time()), item.kind, group),
                )
            self._clear_finished()

    def _acknowledge_price(self, group: str, item: Listing) -> None:
        price, known = self.db.execute(
            "SELECT price, known FROM recipient_prices WHERE listing_id = ? AND group_name = ?",
            (item.listing_id, group),
        ).fetchone()
        baseline = Money.model_validate_json(price) if price else None
        keep = baseline is not None and (
            item.kind in ("price_rise", "sold_out")
            or (
                known
                and item.kind in ("new", "price_drop")
                and item.price is not None
                and item.price.currency_code == baseline.currency_code
                and item.price.value >= baseline.value
            )
        )
        if item.price is not None and not keep:
            price = item.price.model_dump_json()
        # Update on *each* success, not only after the whole group succeeds: a
        # retry at a rebounded price mustn't notify successful targets again.
        # Keep the lowest price across partial deliveries of the same drop/new.
        self.db.execute(
            "UPDATE recipient_prices SET price = ?, known = 1 "
            "WHERE listing_id = ? AND group_name = ?",
            (price, item.listing_id, group),
        )

    def advance_slots(self, now: float) -> None:
        if self.schedule.quiet(now):
            return
        # Failed rows keep their own due slot. New arrivals belong to the next
        # slot, rather than producing extra digests for already-successful targets.
        with self.db:
            for group in set(self.routes.values()) & self.schedule.groups.keys():
                self.db.execute(
                    "UPDATE delivery_groups SET digest_slot = ? "
                    "WHERE name = ? AND digest_slot <= ?",
                    (self.schedule.next_slot(group, now), group, now),
                )

    def _clear_finished(self) -> None:
        self.db.execute(
            """UPDATE listings SET pending = NULL, filtered = 0
               WHERE pending IS NOT NULL AND NOT EXISTS
               (SELECT 1 FROM notification_outbox o WHERE o.listing_id = listings.listing_id)"""
        )
