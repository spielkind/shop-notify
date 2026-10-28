import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from shop_notify.db import (
    SCHEMA_VERSION,
    SORTS,
    DatabaseUnavailableError,
    Event,
    ListingStore,
    StoredListing,
)
from shop_notify.models import Listing, ListingImage, Money
from tests.helpers import make_listing, sync

HOUR = 3600


@pytest.fixture
def store() -> Iterator[ListingStore]:
    s = ListingStore(":memory:")
    yield s
    s.close()


def at(seconds: float) -> AbstractContextManager[Any]:
    return patch("shop_notify.db.time.time", return_value=seconds)


def ids(items: Sequence[Listing | Event]) -> list[int]:
    return [item.listing_id for item in items]


def events(store: ListingStore, listing_id: int | None = None) -> list[tuple[int, str]]:
    return [(e.listing_id, e.event) for e in store.events(listing_id=listing_id)]


def test_new_listings_are_returned_until_notified(store: ListingStore) -> None:
    items = [make_listing(1), make_listing(2)]

    result = sync(store, items)
    assert ids(result.pending) == [1, 2]
    assert result.new == 2
    store.mark_as_notified(items)
    assert sync(store, items).pending == []
    assert events(store) == [(1, "new"), (2, "new"), (1, "notified"), (2, "notified")]


def test_new_listings_keep_the_given_order(store: ListingStore) -> None:
    with at(100):
        sync(store, [make_listing(9)])  # pending, will vanish
    with at(200):
        assert ids(sync(store, [make_listing(3), make_listing(1), make_listing(2)]).pending) == [
            3,
            1,
            2,
            9,
        ]


def test_unnotified_listing_survives_disappearing(store: ListingStore) -> None:
    with at(100):
        sync(store, [make_listing(1)])
    with at(100 + 48 * HOUR):
        result = sync(store, [])
    assert ids(result.pending) == [1]
    assert result.missing == 0
    assert result.gone == []


def test_briefly_missing_listing_is_recorded_but_not_notified(store: ListingStore) -> None:
    with at(0):
        store.mark_as_notified(sync(store, [make_listing(1), make_listing(2)]).pending)
    with at(HOUR):
        result = sync(store, [make_listing(2)], forget_after=2 * HOUR)
        assert result.missing == 1
        assert result.gone == []
    with at(2 * HOUR):
        assert sync(store, [make_listing(2)], forget_after=2 * HOUR).missing == 0  # already
    with at(2 * HOUR + 60):
        result = sync(store, [make_listing(1), make_listing(2)], forget_after=2 * HOUR)
    assert result.pending == []
    assert result.back == 0
    missing = store.events(listing_id=1)[-1]
    assert missing.event == "missing"
    assert missing.details == {"since": 0, "seconds": 2 * HOUR + 60}


def test_listing_gone_for_longer_is_back_with_last_seen_and_old_price(
    store: ListingStore,
) -> None:
    old = make_listing(1).model_copy(
        update={"price": Money(amount=2499, divisor=100, currency_code="USD")}
    )
    new = old.model_copy(update={"price": Money(amount=1999, divisor=100, currency_code="USD")})
    with at(0):
        store.mark_as_notified(sync(store, [old]).pending)
    with at(HOUR):
        assert sync(store, [], forget_after=HOUR).missing == 1
    with at(HOUR + 1):
        assert sync(store, [], forget_after=HOUR).gone == [old]
    with at(2 * HOUR):
        result = sync(store, [new], forget_after=HOUR)

    assert result.back == 1
    [item] = result.pending
    assert item.back is not None
    assert item.back.last_seen.timestamp() == 0
    assert item.back.previous_price == old.price
    assert [e for _, e in events(store, 1)] == [
        "new",
        "notified",
        "gone",
        "price_changed",
        "back",
    ]
    assert store.events(listing_id=1)[3].details == {"old": "24.99 USD", "new": "19.99 USD"}

    # back info survives a failed notification until it was sent
    with at(2 * HOUR + 60):
        [item] = sync(store, [new], forget_after=HOUR).pending
    assert item.back is not None
    store.mark_as_notified([item])
    assert sync(store, [new]).pending == []


def test_back_without_price_change_has_no_previous_price(store: ListingStore) -> None:
    with at(0):
        store.mark_as_notified(sync(store, [make_listing(1)]).pending)
    with at(10):
        sync(store, [])
    with at(20):
        sync(store, [], forget_after=5)
    with at(30):
        [item] = sync(store, [make_listing(1)], forget_after=5).pending
    assert item.back is not None
    assert item.back.previous_price is None


def test_title_change_is_recorded(store: ListingStore) -> None:
    store.mark_as_notified(sync(store, [make_listing(1)]).pending)
    sync(store, [make_listing(1, title="SALE")])

    [event] = [e for e in store.events(listing_id=1) if e.event == "title_changed"]
    assert event.details == {"old": "Item 1", "new": "SALE 1"}
    assert event.title == "SALE 1"


def test_incomplete_result_marks_nothing_missing(store: ListingStore) -> None:
    with at(0):
        store.mark_as_notified(sync(store, [make_listing(1), make_listing(2)]).pending)
    with at(10 * HOUR):
        result = sync(store, [make_listing(2)], complete=False, forget_after=HOUR)
    assert (result.missing, result.gone) == (0, [])
    assert events(store, 1) == [(1, "new"), (1, "notified")]


def test_first_run_items_are_recorded(store: ListingStore) -> None:
    store.mark_as_notified(sync(store, [make_listing(1)]).pending, sent=False)
    store.mark_as_notified([make_listing(1)])  # already marked: no new event
    assert events(store) == [(1, "new"), (1, "recorded")]


def test_new_event_contains_etsy_timestamps(store: ListingStore) -> None:
    item = make_listing(1).model_copy(
        update={
            "original_creation_timestamp": 10,
            "created_timestamp": 20,
            "state_timestamp": 30,
            "price": Money(amount=100, divisor=100, currency_code="EUR"),
        }
    )
    sync(store, [item])
    assert store.events()[0].details == {
        "price": "1.00 EUR",
        "created": 10,
        "renewed": 20,
        "active_since": 30,
        "modified": 1_700_000_000,
    }


def test_events_filtered_by_time(store: ListingStore) -> None:
    with at(100):
        sync(store, [make_listing(1)])
    with at(200):
        sync(store, [make_listing(2)])
    assert events(store) == [(1, "new"), (2, "new")]
    assert ids(store.events(since=150)) == [2]


def test_polls_are_recorded_and_expire() -> None:
    store = ListingStore(":memory:", poll_history_days=1)
    with at(0):
        store.record_poll(error="boom")
    with at(HOUR):
        result = sync(store, [make_listing(1)])
        store.record_poll(reported=2, received=1, complete=False, result=result)
    with at(2 * HOUR):
        assert store.incomplete_since() is not None
        assert store.incomplete_since().timestamp() == HOUR  # type: ignore[union-attr]
        store.record_poll(reported=1, received=1, complete=True)
        assert store.incomplete_since() is None

    polls = store.polls()
    assert [(p.error, p.complete, p.new) for p in polls] == [
        ("boom", None, 0),
        (None, False, 1),
        (None, True, 0),
    ]
    assert len(store.polls(since=HOUR + 1)) == 1
    with at(86400 + 1):
        store.record_poll()
    assert len(store.polls()) == 3  # the first one expired
    store.close()


def test_migrates_a_database_from_version_1_0(tmp_path: Path) -> None:
    path = tmp_path / "shop-notify.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE listings (
            listing_id INTEGER PRIMARY KEY, request_date INTEGER NOT NULL,
            notified_date INTEGER, result TEXT);
        """
    )
    old.executemany(
        "INSERT INTO listings VALUES (?, ?, ?, ?)",
        [
            (1, 200, 100, make_listing(1).model_dump_json()),
            (2, 200, None, make_listing(2).model_dump_json()),
        ],
    )
    old.commit()
    old.close()

    store = ListingStore(path)
    assert (tmp_path / "shop-notify.db.bak-v0").is_file()
    assert events(store) == [(1, "imported"), (2, "imported")]
    assert [e.time.timestamp() for e in store.events()] == [100, 200]
    with at(300):
        assert ids(sync(store, [make_listing(1), make_listing(2)]).pending) == [2]
    store.close()

    # opening it again doesn't migrate twice
    store = ListingStore(path)
    assert len(store.events()) == 2
    store.close()
    db = sqlite3.connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    db.close()


def test_refuses_a_database_from_a_newer_version(tmp_path: Path) -> None:
    path = tmp_path / "shop-notify.db"
    db = sqlite3.connect(path)
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    db.close()
    with pytest.raises(RuntimeError, match="newer than this version"):
        ListingStore(path)


def test_new_database_needs_no_backup(tmp_path: Path) -> None:
    ListingStore(tmp_path / "new.db").close()
    assert [p.name for p in tmp_path.iterdir()] == ["new.db"]


def test_filtered_listings_are_rechecked_while_in_the_shop(store: ListingStore) -> None:
    with at(0):
        result = sync(store, [make_listing(1), make_listing(2)])
        store.mark_as_filtered([(result.pending[0], "exclude keyword 'item'")])
        store.mark_as_notified(result.pending[1:])
    with at(10):
        result = sync(store, [make_listing(1), make_listing(2)])
    assert result.pending == []
    assert ids(result.refilter) == [1]
    [event] = [e for e in store.events(listing_id=1) if e.event == "filtered"]
    assert event.details == {"reason": "exclude keyword 'item'"}

    # filtering again adds no event; notifying clears the flag
    store.mark_as_filtered([(make_listing(1), "again")])
    assert len(store.events(listing_id=1)) == 2
    store.mark_as_notified([make_listing(1)])
    with at(20):
        assert sync(store, [make_listing(1), make_listing(2)]).refilter == []
    assert [e.event for e in store.events(listing_id=1)] == ["new", "filtered", "notified"]


def test_filtered_listings_not_in_the_shop_are_not_rechecked(store: ListingStore) -> None:
    with at(0):
        result = sync(store, [make_listing(1)])
        store.mark_as_filtered([(item, "x") for item in result.pending])
    with at(10):
        assert sync(store, []).refilter == []


def test_seen_since(store: ListingStore) -> None:
    with at(100):
        sync(store, [make_listing(1), make_listing(2)])
    with at(200):
        sync(store, [make_listing(3), make_listing(2)])
    assert ids(store.seen_since(150)) == [3, 2]
    assert ids(store.seen_since(0)) == [3, 2, 1]


def test_polls_count_filtered_listings(store: ListingStore) -> None:
    result = sync(store, [make_listing(1)])
    result.filtered = 1
    store.record_poll(reported=1, received=1, complete=True, result=result)
    assert store.polls()[0].filtered == 1


def test_migrates_a_database_from_version_1_1(tmp_path: Path) -> None:
    path = tmp_path / "shop-notify.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE listings (
            listing_id INTEGER PRIMARY KEY, first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL, missing_since INTEGER, gone_since INTEGER,
            notified_date INTEGER, back TEXT, result TEXT NOT NULL);
        CREATE TABLE listing_events (
            id INTEGER PRIMARY KEY, listing_id INTEGER NOT NULL, time INTEGER NOT NULL,
            event TEXT NOT NULL, details TEXT);
        CREATE TABLE polls (
            id INTEGER PRIMARY KEY, time INTEGER NOT NULL, reported INTEGER,
            received INTEGER, complete INTEGER, new INTEGER NOT NULL DEFAULT 0,
            back INTEGER NOT NULL DEFAULT 0, missing INTEGER NOT NULL DEFAULT 0,
            gone INTEGER NOT NULL DEFAULT 0, error TEXT);
        PRAGMA user_version = 1;
        """
    )
    old.execute(
        "INSERT INTO listings (listing_id, first_seen, last_seen, notified_date, result) "
        "VALUES (1, 100, 100, 100, ?)",
        (make_listing(1).model_dump_json(),),
    )
    old.execute("INSERT INTO polls (time, new) VALUES (100, 1)")
    old.commit()
    old.close()

    store = ListingStore(path)
    assert (tmp_path / "shop-notify.db.bak-v1").is_file()
    assert store.polls()[0].filtered == 0
    with at(200):
        result = sync(store, [make_listing(1)])
    assert (result.pending, result.refilter) == ([], [])
    store.close()


# --- concurrent access and the read-only mode ------------------------------------


def test_a_file_database_uses_the_write_ahead_log(tmp_path: Path) -> None:
    path = tmp_path / "shop-notify.db"
    ListingStore(path).close()
    db = sqlite3.connect(path)
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    db.close()
    # nothing is left next to the database once it is closed
    assert [p.name for p in tmp_path.iterdir()] == ["shop-notify.db"]


def test_closing_writes_the_log_into_the_database_file(tmp_path: Path) -> None:
    path = tmp_path / "shop-notify.db"
    store = ListingStore(path)
    # an open reader (the API) keeps SQLite from doing this by itself
    reader = ListingStore(path, readonly=True)
    sync(store, [make_listing(1)])
    store.close()
    shutil.copyfile(path, tmp_path / "copy.db")
    reader.close()
    copy = sqlite3.connect(tmp_path / "copy.db")
    assert copy.execute("SELECT listing_id FROM listings").fetchall() == [(1,)]
    copy.close()


def test_the_migration_backup_includes_the_write_ahead_log(tmp_path: Path) -> None:
    path = tmp_path / "shop-notify.db"
    old = sqlite3.connect(path)
    old.execute("PRAGMA journal_mode = WAL")
    old.execute("PRAGMA wal_autocheckpoint = 0")
    old.execute(
        "CREATE TABLE listings (listing_id INTEGER PRIMARY KEY, request_date INTEGER NOT NULL, "
        "notified_date INTEGER, result TEXT)"
    )
    old.commit()
    # while another connection is open, closing doesn't move the log into the database
    other = sqlite3.connect(path)
    other.execute("SELECT * FROM listings").fetchall()
    old.execute(
        "INSERT INTO listings VALUES (1, 100, 100, ?)", (make_listing(1).model_dump_json(),)
    )
    old.commit()
    old.close()
    assert (tmp_path / "shop-notify.db-wal").stat().st_size > 0

    ListingStore(path).close()
    other.close()
    backup = sqlite3.connect(tmp_path / "shop-notify.db.bak-v0")
    assert backup.execute("SELECT listing_id FROM listings").fetchall() == [(1,)]
    backup.close()


def test_a_readonly_store_needs_an_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "shop-notify.db"
    with pytest.raises(DatabaseUnavailableError, match=r"No database at .*shop-notify\.db"):
        ListingStore(path, readonly=True)
    assert not path.exists()


@pytest.mark.parametrize("version", [0, SCHEMA_VERSION - 1, SCHEMA_VERSION + 1])
def test_a_readonly_store_needs_the_current_schema(tmp_path: Path, version: int) -> None:
    path = tmp_path / "shop-notify.db"
    db = sqlite3.connect(path)
    db.execute(f"PRAGMA user_version = {version}")
    db.close()
    with pytest.raises(DatabaseUnavailableError, match=f"schema version {version}, but"):
        ListingStore(path, readonly=True)
    # nothing was migrated
    db = sqlite3.connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == version
    db.close()


def test_a_readonly_store_reports_a_file_that_is_no_database(tmp_path: Path) -> None:
    path = tmp_path / "shop-notify.db"
    path.write_text("not a database" * 100)
    with pytest.raises(DatabaseUnavailableError, match=r"Can't read .*not a database"):
        ListingStore(path, readonly=True)


def test_a_readonly_store_reads_but_never_writes(tmp_path: Path) -> None:
    path = tmp_path / "shop-notify.db"
    with ListingStore(path) as store:
        sync(store, [make_listing(1)])
    with ListingStore(path, readonly=True) as reader:
        assert events(reader) == [(1, "new")]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.record_poll(error="x")


def test_readers_are_not_blocked_while_the_poller_writes(tmp_path: Path) -> None:
    path = tmp_path / "shop-notify.db"
    with ListingStore(path) as store:
        sync(store, [make_listing(1)])
    # an exclusive write transaction blocks readers unless the database uses the WAL
    writer = sqlite3.connect(path, isolation_level=None)
    writer.execute("BEGIN EXCLUSIVE")
    writer.execute("INSERT INTO polls (time) VALUES (1)")
    reader = (
        "import sys; from shop_notify.db import ListingStore\n"
        "with ListingStore(sys.argv[1], readonly=True) as store:\n"
        "    print(len(store.events()), len(store.polls()))\n"
    )
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-c", reader, str(path)], capture_output=True, text=True, check=True
    )
    writer.execute("COMMIT")
    writer.close()
    # the reader sees the last committed state without waiting for the writer
    assert done.stdout.split() == ["1", "0"]
    assert time.monotonic() - started < 4


# --- listings, paging and filtering ----------------------------------------------


def test_listing_state(store: ListingStore) -> None:
    with at(100):
        store.mark_as_notified(sync(store, [make_listing(1), make_listing(2)]).pending)
    assert store.listing(3) is None
    listed = store.listing(1)
    assert listed is not None
    assert (listed.status, listed.listing.title, listed.pending) == ("listed", "Item 1", None)
    assert listed.first_seen.timestamp() == 100
    assert listed.notified is not None and listed.notified.timestamp() == 100
    assert listed.missing_since is None

    with at(200):
        result = store.sync([make_listing(2)])
        store.record_states({1: "sold_out"}, result)
    sold_out = store.listing(1)
    assert sold_out is not None
    assert (sold_out.status, sold_out.pending) == ("sold_out", None)
    assert sold_out.sold_out_since is not None and sold_out.sold_out_since.timestamp() == 200

    # 1 hasn't been seen for longer than forget_after, 2 is only missing
    with at(300):
        store.sync([], forget_after=150)
    gone, missing = store.listing(1), store.listing(2)
    assert gone is not None and gone.gone_since is not None
    assert missing is not None and missing.missing_since is not None
    assert (gone.status, missing.status) == ("gone", "missing")

    with at(400):
        result = store.sync([make_listing(3)])
        store.mark_as_filtered([(item, "x") for item in store.pending()])
        store.sync([make_listing(3), make_listing(4)])
    filtered, new = store.listing(3), store.listing(4)
    assert filtered is not None and filtered.filtered
    assert new is not None and new.pending == "new"


def test_events_can_be_filtered_and_paged(store: ListingStore) -> None:
    with at(100):
        store.mark_as_notified(
            sync(store, [make_listing(1, "Silver ring"), make_listing(2, "Mug 50%_off")]).pending
        )
    with at(200):
        store.record_states({1: "sold_out"}, store.sync([make_listing(2, "Mug 50%_off")]))

    newest = store.events(newest_first=True)
    assert [(e.listing_id, e.event) for e in newest] == [
        (1, "sold_out"),
        (2, "notified"),
        (1, "notified"),
        (2, "new"),
        (1, "new"),
    ]
    assert [e.id for e in newest] == sorted((e.id for e in newest), reverse=True)

    def titles(**kwargs: Any) -> list[int]:
        return ids(store.events(**kwargs))

    assert titles(kinds=["sold_out", "new"]) == [1, 2, 1]
    assert titles(text="SILVER") == [1, 1, 1]
    # LIKE wildcards in the search text match literally
    assert titles(text="50%_") == [2, 2]
    assert titles(text="%") == [2, 2]
    assert titles(text="r_ng") == []

    first = store.events(newest_first=True, limit=2)
    second = store.events(newest_first=True, limit=2, before=first[-1].id)
    rest = store.events(newest_first=True, limit=2, before=second[-1].id)
    assert [*first, *second, *rest] == newest
    assert store.events(before=999) == []


def test_polls_can_be_paged_newest_first(store: ListingStore) -> None:
    for second in (100, 200, 300):
        with at(second):
            store.record_poll(error=str(second))
    newest = store.polls(newest_first=True)
    assert [p.error for p in newest] == ["300", "200", "100"]
    page = store.polls(newest_first=True, limit=2)
    assert [p.error for p in store.polls(newest_first=True, before=page[-1].id)] == ["100"]
    assert [p.error for p in store.polls(since=200)] == ["200", "300"]


# --- listings and their images ----------------------------------------------------


def stored_ids(items: Sequence[StoredListing]) -> list[int]:
    return [item.listing.listing_id for item in items]


def test_migrates_a_database_from_version_1_3(tmp_path: Path) -> None:
    path = tmp_path / "shop-notify.db"
    with ListingStore(path) as store:
        sync(store, [make_listing(1)])
    old = sqlite3.connect(path)
    for column in ("image_url", "thumbnail_url", "image_checked"):
        old.execute(f"ALTER TABLE listings DROP COLUMN {column}")
    old.execute("PRAGMA user_version = 3")
    old.commit()
    old.close()

    with ListingStore(path) as store:
        assert store.listings_needing_images(10) == [1]
        listing = store.listing(1)
        assert listing is not None and listing.image_url is None
    assert (tmp_path / "shop-notify.db.bak-v3").is_file()


def test_listings_by_status_newest_first(store: ListingStore) -> None:
    with at(100):
        store.mark_as_notified(sync(store, [make_listing(1, "Silver ring")]).pending)
    with at(200):
        store.mark_as_notified(sync(store, [make_listing(i) for i in (1, 2, 3, 4)]).pending)
    with at(300):
        result = store.sync([make_listing(1, "Silver ring"), make_listing(2)])
        store.record_states({3: "sold_out"}, result)
    with at(400):
        store.sync([make_listing(1, "Silver ring"), make_listing(2)], forget_after=150)

    # 4 was only missing at 300 and has been gone since 400; 3 sold out
    assert stored_ids(store.listings()) == [4, 3, 2, 1]
    assert stored_ids(store.listings(status="listed")) == [2, 1]
    assert stored_ids(store.listings(status="sold_out")) == []  # gone too, since 400
    assert stored_ids(store.listings(status="gone")) == [4, 3]
    assert store.listing_counts() == {"listed": 2, "missing": 0, "sold_out": 0, "gone": 2}
    assert stored_ids(store.listings(text="RING")) == [1]
    assert stored_ids(store.listings(text="%")) == []

    first = store.listings(limit=3)
    assert stored_ids(first) == [4, 3, 2]
    assert stored_ids(store.listings(limit=3, before=first[-1].listing.listing_id)) == [1]


def test_listings_in_every_order(store: ListingStore) -> None:
    def item(
        listing_id: int, title: str, price: Money | None, changed: int, stock: int | None
    ) -> Listing:
        listing = make_listing(listing_id, title)
        return listing.model_copy(
            update={"price": price, "last_modified_timestamp": changed, "quantity": stock}
        )

    with at(100):
        sync(store, [item(1, "b", Money(amount=500, divisor=100, currency_code="EUR"), 30, 2)])
    with at(200):
        sync(
            store,
            [
                item(1, "b", Money(amount=500, divisor=100, currency_code="EUR"), 30, 2),
                # 4.50 is less than 5.00, although the amount is larger
                item(2, "A", Money(amount=4500, divisor=1000, currency_code="EUR"), 10, None),
                item(3, "c", None, 20, 5),
            ],
        )
    expected = {
        "newest": [3, 2, 1],
        "oldest": [1, 2, 3],
        "price_asc": [2, 1, 3],
        "price_desc": [1, 2, 3],
        "title": [2, 1, 3],
        "changed": [1, 3, 2],
        "stock": [3, 1, 2],
    }
    assert set(expected) == set(SORTS)
    for sort, ids in expected.items():
        assert stored_ids(store.listings(sort=sort)) == ids, sort
    with pytest.raises(ValueError, match="before only works"):
        store.listings(sort="title", before=1)


def test_listing_counts_with_missing_and_sold_out(store: ListingStore) -> None:
    assert store.listing_counts() == {"listed": 0, "missing": 0, "sold_out": 0, "gone": 0}
    with at(100):
        store.mark_as_notified(sync(store, [make_listing(1), make_listing(2)]).pending)
    with at(200):
        store.record_states({1: "sold_out"}, store.sync([]))
    assert store.listing_counts() == {"listed": 0, "missing": 1, "sold_out": 1, "gone": 0}
    assert [item.status for item in store.listings()] == ["missing", "sold_out"]


def test_images_are_looked_up_once_per_change(store: ListingStore) -> None:
    with at(100):
        sync(store, [make_listing(1), make_listing(2), make_listing(3)])
    assert store.listings_needing_images(2) == [3, 2]
    assert store.listings_needing_images(10) == [3, 2, 1]

    images = {
        1: ListingImage("https://i.etsystatic.com/1.jpg", "https://i.etsystatic.com/1s.jpg"),
        3: ListingImage("https://i.etsystatic.com/3.jpg", None),
    }
    store.record_images([1, 2, 3], images)
    # 2 has no image: it is not looked up again either
    assert store.listings_needing_images(10) == []
    assert store.image_urls([1, 2, 3]) == {
        1: "https://i.etsystatic.com/1.jpg",
        3: "https://i.etsystatic.com/3.jpg",
    }
    assert store.image_urls([]) == {}
    listing = store.listing(1)
    assert listing is not None
    assert (listing.image_url, listing.thumbnail_url) == (
        "https://i.etsystatic.com/1.jpg",
        "https://i.etsystatic.com/1s.jpg",
    )

    # a changed listing is looked up again, one that left the shop isn't
    changed = make_listing(1).model_copy(update={"last_modified_timestamp": 1_700_000_100})
    with at(200):
        store.mark_as_notified(store.pending())
        store.sync([changed, make_listing(2)])
    assert store.listings_needing_images(10) == [1]
