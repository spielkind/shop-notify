import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr, ValidationError

from shop_notify import __main__ as cli
from shop_notify.config import NotifySettings, Settings
from shop_notify.db import SCHEMA_VERSION, EventSettings, ListingStore
from shop_notify.etsy import EtsyClient
from shop_notify.filters import Filters
from shop_notify.models import EVENTS, Listing, Money, Notice
from shop_notify.notifications import (
    AppriseNotifier,
    ConsoleNotifier,
    MultiNotifier,
    NotificationError,
)
from shop_notify.outbox import Outbox
from shop_notify.scheduling import Schedule, clock, digest_groups
from tests.helpers import make_listing


@pytest.fixture(autouse=True)
def utc() -> Iterator[None]:
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    yield
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time.tzset()


def timestamp(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def client(items: list[Listing]) -> MagicMock:
    result = MagicMock(spec=EtsyClient)
    result.quota = None
    result.listings_complete = True
    result.listings_received = result.listings_reported = len(items)
    result.active_listings.return_value = items
    result.listing_images.return_value = {}
    result.listing_states.return_value = {}
    return result


def value(money: Money | None) -> int:
    assert money is not None
    return int(money.value)


def notice(item: Listing) -> Notice:
    assert item.event is not None
    return item.event


def priced(value: int, listing_id: int = 1, quantity: int | None = None) -> Listing:
    return make_listing(listing_id).model_copy(
        update={"price": Money(amount=value, divisor=1, currency_code="EUR"), "quantity": quantity}
    )


@pytest.mark.parametrize("value", ["8:00", "24:00", "12:60", "", " 08:00"])
def test_invalid_clock(value: str) -> None:
    with pytest.raises(ValueError, match="HH:MM"):
        clock(value)


@pytest.mark.parametrize(
    "value", ["family", ":daily", "x:monthly", "x:daily,x:weekly", "all:daily"]
)
def test_invalid_digest(value: str) -> None:
    with pytest.raises(ValueError):
        digest_groups(value)


@pytest.mark.parametrize(
    "values",
    [
        {"notify_digest_time": "25:00"},
        {"notify_digest_day": "monday"},
        {"notify_quiet_hours": "22:00"},
        {"notify_quiet_hours": "07:00-07:00"},
        {"notify_quiet_hours": "bad-07:00"},
        {"notify_digest": "x:hourly"},
    ],
)
def test_invalid_settings(values: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        NotifySettings.model_validate(values)


def test_schedule_boundaries_and_next_slots() -> None:
    assert digest_groups(" family:daily, ,oma:weekly ") == {"family": "daily", "oma": "weekly"}
    schedule = Schedule({"family": "daily", "oma": "weekly"}, "22:00-07:00")
    for value, expected in [("06:59", True), ("07:00", False), ("21:59", False), ("22:00", True)]:
        assert schedule.quiet(timestamp(f"2026-10-26T{value}")) is expected
    daytime = Schedule(quiet_hours="09:00-17:00")
    assert daytime.quiet(timestamp("2026-10-26T09:00"))
    assert not daytime.quiet(timestamp("2026-10-26T17:00"))
    assert not Schedule().quiet(0)
    assert schedule.next_slot("family", timestamp("2026-10-26T07:59")) == timestamp(
        "2026-10-26T08:00"
    )
    assert schedule.next_slot("family", timestamp("2026-10-26T08:00")) == timestamp(
        "2026-10-27T08:00"
    )
    assert schedule.next_slot("oma", timestamp("2026-10-27T08:00")) == timestamp("2026-11-02T08:00")
    assert schedule.next_slot("oma", timestamp("2026-10-26T08:00")) == timestamp("2026-11-02T08:00")


def notifier_config(path: Path) -> AppriseNotifier:
    path.write_text(
        "urls:\n  - shoptest://family:\n      tag: family\n  - shoptest://oma:\n      tag: oma\n  - shoptest://immediate\n"
    )
    return AppriseNotifier(config=path, digest_groups=("family", "oma"))


def test_tag_validation_and_summary(tmp_path: Path, sent: list[dict[str, Any]]) -> None:
    path = tmp_path / "notify.yml"
    path.write_text("urls:\n  - shoptest://x:\n      tag: family, oma\n")
    with pytest.raises(ValueError, match="multiple configured"):
        AppriseNotifier(config=path, digest_groups=("family", "oma"))
    notifier = notifier_config(path)
    target = next(key for key, group in notifier.routes.items() if group == "family")
    notifier._targets[0].apprise[0].attachment_support = True
    assert notifier.deliver(target, [make_listing(1), make_listing(2)], summary=True)
    assert len(sent) == 1
    assert "2 new" in sent[0]["title"]
    assert notifier.deliver(target, [make_listing(1), make_listing(2)], summary=False)
    assert len(sent) == 3


def test_quiet_digest_current_data_and_restart(tmp_path: Path, sent: list[dict[str, Any]]) -> None:
    notifier = notifier_config(tmp_path / "notify.yml")
    schedule = Schedule({"family": "daily", "oma": "weekly"}, "22:00-09:00")
    path = tmp_path / "test.db"
    start = timestamp("2026-10-25T23:00")  # Sunday
    with patch("time.time", return_value=start), ListingStore(path) as store:
        assert (
            cli.run_once(
                client([priced(30)]), store, notifier, 1, notify_existing=True, schedule=schedule
            )
            == 0
        )
        assert store.pending()
    with (
        patch("time.time", return_value=timestamp("2026-10-26T08:00")),
        ListingStore(path) as store,
    ):
        assert cli.run_once(client([priced(20)]), store, notifier, 1, schedule=schedule) == 0
    assert not sent
    with (
        patch("time.time", return_value=timestamp("2026-10-26T09:00")),
        ListingStore(path) as store,
    ):
        assert cli.run_once(client([priced(18)]), store, notifier, 1, schedule=schedule) == 1
        assert not store.pending()
        assert store.outbox is not None
        assert all(p.value == 18 for p in store.outbox.prices(1).values() if p)
    assert len(sent) == 3
    assert all("18.00" in message["body"] for message in sent)


def test_quiet_immediate_delivery_is_summary_after_restart_with_attachments(
    tmp_path: Path, sent: list[dict[str, Any]]
) -> None:
    path = tmp_path / "test.db"
    schedule = Schedule(quiet_hours="22:00-07:00")
    start = timestamp("2026-10-26T23:00")
    notifier = AppriseNotifier(urls="shoptest://push")
    notifier._targets[0].apprise[0].attachment_support = True
    with patch("time.time", return_value=start), ListingStore(path) as store:
        store.sync([priced(30), priced(40, 2)])
        store.mark_as_notified(store.pending())
        outbox = store.configure_delivery(notifier.routes, schedule)
        target = next(iter(notifier.routes))
        assert not outbox.quiet_deferred(target, [])
        with outbox.db:
            # Replacing a coalesced notice must not clear the quiet-deferred marker.
            outbox.queue(1, [Notice(kind="sold_out")])
            outbox.queue(1, [Notice(kind="low_stock")])
            outbox.queue(2, [Notice(kind="low_stock")])
        row = outbox.db.execute(
            "SELECT notice, quiet_deferred FROM notification_outbox "
            "WHERE listing_id = 1 AND target = ?",
            (target,),
        ).fetchone()
        assert row is not None
        assert Notice.model_validate_json(row[0]).kind == "low_stock"
        assert row[1] == 1
        assert not outbox.ready(start)

    notifier = AppriseNotifier(urls="shoptest://push")
    notifier._targets[0].apprise[0].attachment_support = True
    with (
        patch("time.time", return_value=timestamp("2026-10-27T08:00")),
        ListingStore(path) as store,
    ):
        assert (
            cli.run_once(client([priced(30), priced(40, 2)]), store, notifier, 1, schedule=schedule)
            == 2
        )

    assert len(sent) == 1
    assert sent[0]["title"] == "2 almost sold out in the shop"


def test_ordinary_immediate_delivery_keeps_per_item_messages(
    tmp_path: Path, sent: list[dict[str, Any]]
) -> None:
    notifier = AppriseNotifier(urls="shoptest://push")
    notifier._targets[0].apprise[0].attachment_support = True
    with ListingStore(tmp_path / "test.db") as store:
        assert (
            cli.run_once(
                client([priced(30), priced(40, 2)]),
                store,
                notifier,
                1,
                notify_existing=True,
                schedule=Schedule(),
            )
            == 2
        )

    assert [message["title"] for message in sent] == [
        "New in the shop: Item 1",
        "New in the shop: Item 2",
    ]


def test_partial_failure_retries_only_failed_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    notifier = AppriseNotifier(urls="shoptest://success shoptest://fail")
    schedule = Schedule()
    with (
        ListingStore(path) as store,
        patch.object(notifier, "deliver", side_effect=[True, False]) as send,
    ):
        with pytest.raises(NotificationError, match="1 target"):
            cli.run_once(
                client([priced(30)]), store, notifier, 1, notify_existing=True, schedule=schedule
            )
        failed_key = send.call_args_list[-1].args[0]
        assert store.polls()[-1].error
    # Disabling all routes must also preserve an already-initialized retry queue.
    with ListingStore(path) as store:
        assert (
            cli.run_once(client([priced(30)]), store, MultiNotifier([]), 1, schedule=schedule) == 0
        )
        assert store.pending()
    # Reordering destinations on restart must not replay successful targets.
    notifier = AppriseNotifier(urls="shoptest://fail shoptest://success")
    with ListingStore(path) as store, patch.object(notifier, "deliver", return_value=True) as send:
        assert cli.run_once(client([priced(25)]), store, notifier, 1, schedule=schedule) == 1
        send.assert_called_once()
        assert send.call_args.args[0] == failed_key
        assert send.call_args.args[1][0].price.value == 25
        assert not store.pending()


def test_first_run_and_refilter(tmp_path: Path, sent: list[dict[str, Any]]) -> None:
    notifier = MultiNotifier(
        [ConsoleNotifier(first_run_only=True), AppriseNotifier(urls="shoptest://x")]
    )
    with ListingStore(tmp_path / "test.db") as store:
        assert cli.run_once(client([priced(30)]), store, notifier, 1, schedule=Schedule()) == 0
        assert not sent
        assert cli.run_once(client([priced(30)]), store, notifier, 1, schedule=Schedule()) == 0
        # Use the actual title to keep the test independent of the listing factory wording.
        filters = Filters.from_settings(exclude=priced(20, 2).title)
        assert (
            cli.run_once(
                client([priced(30), priced(20, 2)]),
                store,
                notifier,
                1,
                schedule=Schedule(),
                filters=filters,
            )
            == 0
        )
        assert (
            cli.run_once(
                client([priced(30), priced(20, 2)]), store, notifier, 1, schedule=Schedule()
            )
            == 1
        )
        assert len(sent) == 1
        assert (
            cli.run_once(
                client([priced(30), priced(20, 2)]), store, notifier, 1, schedule=Schedule()
            )
            == 0
        )


@pytest.mark.parametrize("sold_out", [True, False])
def test_stale_new_is_dropped(tmp_path: Path, sold_out: bool) -> None:
    notifier = ConsoleNotifier()
    schedule = Schedule(quiet_hours="22:00-07:00")
    with ListingStore(tmp_path / "test.db") as store:
        with patch("time.time", return_value=timestamp("2026-10-26T23:00")):
            cli.run_once(
                client([priced(30)]), store, notifier, 1, notify_existing=True, schedule=schedule
            )
        missing = client([])
        missing.listing_states.return_value = {1: "sold_out"} if sold_out else {1: "inactive"}
        with patch("time.time", return_value=timestamp("2026-10-27T08:00")):
            assert (
                cli.run_once(missing, store, notifier, 1, schedule=schedule, forget_after_hours=0)
                == 0
            )
            assert not store.pending()


@pytest.mark.parametrize(
    ("state", "forget_after_hours", "expected"),
    [("sold_out", 24, ["sold_out"]), ("inactive", 0, []), ("inactive", 24, ["new"])],
)
def test_partial_new_delivery_drops_unavailable_but_keeps_real_events(
    tmp_path: Path, state: str, forget_after_hours: float, expected: list[str]
) -> None:
    path = tmp_path / "test.db"
    notifier = AppriseNotifier(urls="shoptest://a shoptest://b")
    events = EventSettings(enabled=frozenset({"new", "sold_out"}))
    with (
        patch("time.time", return_value=timestamp("2026-10-26T12:00")),
        ListingStore(path, events=events) as store,
        patch.object(notifier, "deliver", side_effect=[True, False]) as send,
    ):
        with pytest.raises(NotificationError):
            cli.run_once(
                client([priced(30)]), store, notifier, 1, notify_existing=True, schedule=Schedule()
            )
        succeeded, failed = [call.args[0] for call in send.call_args_list]

    missing = client([])
    missing.listing_states.return_value = {1: state}
    with (
        patch("time.time", return_value=timestamp("2026-10-26T13:00")),
        ListingStore(path, events=events) as store,
        patch.object(notifier, "deliver", return_value=True) as send,
    ):
        assert cli.run_once(
            missing,
            store,
            notifier,
            1,
            schedule=Schedule(),
            forget_after_hours=forget_after_hours,
        ) == len(expected)
        assert [item.kind for call in send.call_args_list for item in call.args[1]] == expected
        if expected:
            assert send.call_args.args[0] == (succeeded if state == "sold_out" else failed)
        assert not store.pending()


@pytest.mark.parametrize("first_run_only", [False, True])
def test_v4_pending_migration_waits_for_real_routes(
    tmp_path: Path, first_run_only: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "test.db"
    with ListingStore(path) as store:
        store.sync([priced(30), priced(40, 2)])
        store.mark_as_notified([priced(40, 2)])
    with closing(sqlite3.connect(path)) as db, db:
        for table in (
            "delivery_groups",
            "recipient_prices",
            "notification_outbox",
            "delivery_metadata",
        ):
            db.execute(f"DROP TABLE {table}")
        db.execute("PRAGMA user_version = 4")

    settings = Settings.model_validate(
        {
            "etsy_api_key": "key",
            "etsy_shared_secret": "secret",
            "etsy_shop_id": 1,
            "notify_console": False,
        }
    )
    disabled = (
        ConsoleNotifier(first_run_only=True) if first_run_only else cli.build_notifier(settings)
    )
    assert isinstance(disabled, (ConsoleNotifier, MultiNotifier))
    assert not disabled.routes
    with ListingStore(path) as store:
        assert (
            cli.run_once(
                client([priced(30), priced(40, 2)]), store, disabled, 1, schedule=Schedule()
            )
            == 0
        )
        assert (
            cli.run_once(
                client([priced(30), priced(40, 2), priced(50, 3)]),
                store,
                disabled,
                1,
                schedule=Schedule(),
            )
            == 0
        )
        assert [item.listing_id for item in store.pending()] == [1, 3]
        assert store.outbox is not None
        assert not store.outbox.db.execute("SELECT 1 FROM delivery_metadata").fetchall()
    assert not capsys.readouterr().out

    settings.notify_urls = SecretStr("shoptest://enabled")
    enabled = cli.build_notifier(settings)
    assert isinstance(enabled, AppriseNotifier)
    with ListingStore(path) as store, patch.object(enabled, "deliver", return_value=True) as send:
        assert (
            cli.run_once(
                client([priced(30), priced(40, 2), priced(50, 3)]),
                store,
                enabled,
                1,
                schedule=Schedule(),
            )
            == 2
        )
        send.assert_called_once()
        assert [item.listing_id for item in send.call_args.args[1]] == [1, 3]
        assert not store.pending()
        send.reset_mock()
        assert (
            cli.run_once(
                client([priced(30), priced(40, 2), priced(50, 3)]),
                store,
                enabled,
                1,
                schedule=Schedule(),
            )
            == 0
        )
        send.assert_not_called()


def test_v4_migration_pending_fanout_no_backfill_and_group_baselines(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    with ListingStore(path) as store:
        store.sync([priced(30), priced(40, 2)])
        store.mark_as_notified([priced(40, 2)])
        # Migration preserves the legacy baseline even when the current price differs.
        store.sync([priced(30), priced(35, 2)])
    with closing(sqlite3.connect(path)) as db, db:
        for table in (
            "delivery_groups",
            "recipient_prices",
            "notification_outbox",
            "delivery_metadata",
        ):
            db.execute(f"DROP TABLE {table}")
        db.execute("PRAGMA user_version = 4")
    schedule = Schedule({"family": "daily", "oma": "weekly"})
    with ListingStore(path) as store:
        outbox = store.configure_delivery({"one": "family", "two": "oma"}, schedule)
        ready = outbox.ready(timestamp("2100-01-01T10:00"))
        assert {key: [item.listing_id for item in items] for key, items in ready.items()} == {
            "one": [1],
            "two": [1],
        }
        assert value(outbox.prices(2)["family"]) == 40
        assert value(outbox.prices(2)["oma"]) == 40
        assert path.with_name("test.db.bak-v4").is_file()
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_per_group_prices_and_priority(tmp_path: Path) -> None:
    settings = EventSettings(enabled=frozenset(EVENTS))
    with ListingStore(tmp_path / "test.db", events=settings) as store:
        outbox = store.configure_delivery(
            {"immediate": "", "digest": "family"}, Schedule({"family": "daily"})
        )
        store.sync([priced(100, quantity=10)])
        all_ready = outbox.ready(timestamp("2100-01-01T08:00"))
        outbox.acknowledge("immediate", all_ready["immediate"])
        outbox.acknowledge("digest", all_ready["digest"])
        store.sync([priced(80, quantity=1)])
        immediate = outbox.ready(timestamp("2100-01-01T08:00"))["immediate"]
        assert immediate[0].kind == "price_drop"
        outbox.acknowledge("immediate", immediate)
        store.sync([priced(90, quantity=2)])
        store.sync([priced(85, quantity=3)])
        ready = outbox.ready(timestamp("2100-01-01T08:00"))
        assert ready["immediate"][0].kind == "price_rise"  # previous rise is still pending
        assert value(notice(ready["digest"][0]).previous_price) == 100
        assert value(ready["digest"][0].price) == 85
        assert notice(ready["digest"][0]).quantity is None
        store.record_states({1: "sold_out"}, store.sync([], complete=False))
        ready = outbox.ready(timestamp("2100-01-01T08:00"))
        assert {items[0].kind for items in ready.values()} == {"sold_out"}
        for target, items in ready.items():
            outbox.acknowledge(target, items)
        assert value(outbox.prices(1)[""]) == 80
        assert value(outbox.prices(1)["family"]) == 100


def test_outbox_reconfiguration_and_new_group(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    with ListingStore(path) as store:
        store.sync([priced(30)])
        store.mark_as_notified(store.pending())
        outbox = store.configure_delivery({"old": ""}, Schedule())
        with outbox.db:
            outbox.queue(1, [Notice(kind="back")])
    with ListingStore(path) as store:
        outbox = store.configure_delivery({"new": "family"}, Schedule({"family": "daily"}))
        assert not outbox.ready(timestamp("2100-01-01T08:00"))
        assert value(outbox.prices(1)["family"]) == 30
        outbox.queue(1, [])


def test_later_group_starts_at_current_price_without_changing_existing_baseline(
    tmp_path: Path,
) -> None:
    path = tmp_path / "test.db"
    schedule = Schedule({"old": "daily", "new": "daily"})
    due = timestamp("2100-01-01T08:00")
    with ListingStore(path) as store:
        outbox = store.configure_delivery({"old-target": "old"}, schedule)
        store.sync([priced(100)])
        outbox.acknowledge("old-target", outbox.ready(due)["old-target"])
        # Price drops are not enabled yet, leaving the old group's baseline at 100.
        store.sync([priced(80)])
        assert value(outbox.prices(1)["old"]) == 100

    with ListingStore(
        path, events=EventSettings(enabled=frozenset({"new", "price_drop"}))
    ) as store:
        outbox = store.configure_delivery({"old-target": "old", "new-target": "new"}, schedule)
        assert {group: value(price) for group, price in outbox.prices(1).items()} == {
            "old": 100,
            "new": 80,
        }
        assert not outbox.ready(due)  # Adding a group must not backfill listings.
        store.sync([priced(78)])
        ready = outbox.ready(due)
        assert set(ready) == {"old-target"}  # 22% below 100, but only 2.5% below 80.
        assert value(notice(ready["old-target"][0]).previous_price) == 100
        outbox.acknowledge("old-target", ready["old-target"])
        assert {group: value(price) for group, price in outbox.prices(1).items()} == {
            "old": 78,
            "new": 80,
        }
        store.sync([priced(74)])
        ready = outbox.ready(due)
        assert set(ready) == {"old-target", "new-target"}
        assert value(notice(ready["old-target"][0]).previous_price) == 78
        assert value(notice(ready["new-target"][0]).previous_price) == 80
        assert all(items[0].kind == "price_drop" for items in ready.values())


def test_console_multi_routes_and_unknown_target(capsys: pytest.CaptureFixture[str]) -> None:
    console = ConsoleNotifier()
    multi = MultiNotifier([console])
    assert multi.routes == {"console": ""}
    assert multi.deliver("console", [priced(30)])
    assert "30.00" in capsys.readouterr().out
    with pytest.raises(ValueError, match="unknown"):
        multi.deliver("absent", [])
    assert not ConsoleNotifier(first_run_only=True).routes


@pytest.mark.parametrize(
    ("kind", "current", "previous", "expected"),
    [
        ("new", 10, 20, False),
        ("price_drop", 10, 20, False),
        ("price_drop", 20, 20, True),
        ("price_rise", 30, 20, False),
        ("price_rise", 10, 20, True),
        ("price_drop", 99, 100, True),
        ("price_drop", 95, 100, False),
        ("price_rise", 101, 100, True),
        ("price_rise", 105, 100, False),
        ("price_rise", 1, 0, True),
    ],
)
def test_obsolete_price(kind: str, current: int, previous: int, expected: bool) -> None:
    item = priced(current)
    item.event = Notice(kind=kind, previous_price=priced(previous).price)
    assert Outbox._obsolete_price(item) is expected


def test_obsolete_price_missing_and_currency() -> None:
    item = priced(10)
    assert not Outbox._obsolete_price(item)
    item.event = Notice(kind="price_drop")
    assert Outbox._obsolete_price(item)
    item.event = Notice(
        kind="price_drop", previous_price=Money(amount=20, divisor=1, currency_code="USD")
    )
    assert Outbox._obsolete_price(item)
    item.price = None
    assert Outbox._obsolete_price(item)


def test_obsolete_queued_price_is_removed_and_quantity_refreshed(tmp_path: Path) -> None:
    with ListingStore(
        tmp_path / "test.db", events=EventSettings(enabled=frozenset(EVENTS))
    ) as store:
        outbox = store.configure_delivery({"one": ""}, Schedule())
        store.sync([priced(30, quantity=10)])
        outbox.acknowledge("one", outbox.ready(time.time())["one"])
        store.sync([priced(20, quantity=1)])
        store.sync([priced(40, quantity=3)])
        assert not outbox.ready(time.time())
        outbox.queue(1, [Notice(kind="low_stock", quantity=1)])
        assert notice(outbox.ready(time.time())["one"][0]).quantity == 3


def test_partial_drop_rebound_does_not_requeue_successful_targets(tmp_path: Path) -> None:
    notifier = AppriseNotifier(urls="shoptest://a shoptest://b")
    with ListingStore(
        tmp_path / "test.db", events=EventSettings(enabled=frozenset({"new", "price_drop"}))
    ) as store:
        with patch.object(notifier, "deliver", return_value=True):
            cli.run_once(
                client([priced(100)]), store, notifier, 1, notify_existing=True, schedule=Schedule()
            )
        with patch.object(notifier, "deliver", side_effect=[True, False]) as send:
            with pytest.raises(NotificationError):
                cli.run_once(client([priced(80)]), store, notifier, 1, schedule=Schedule())
            failed = send.call_args_list[-1].args[0]
        assert store.outbox is not None
        assert value(store.outbox.prices(1)[""]) == 80
        with patch.object(notifier, "deliver", return_value=True) as send:
            cli.run_once(client([priced(90)]), store, notifier, 1, schedule=Schedule())
            send.assert_called_once()
            assert send.call_args.args[0] == failed
        assert value(store.outbox.prices(1)[""]) == 80


def test_daily_weekly_slots_and_empty_digests(tmp_path: Path) -> None:
    notifier = notifier_config(tmp_path / "notify.yml")
    schedule = Schedule({"family": "daily", "oma": "weekly"})
    with (
        ListingStore(tmp_path / "test.db") as store,
        patch.object(notifier, "deliver", return_value=True) as send,
    ):
        with patch("time.time", return_value=timestamp("2026-10-27T07:00")):
            cli.run_once(
                client([priced(30)]), store, notifier, 1, notify_existing=True, schedule=schedule
            )
            assert [notifier.routes[c.args[0]] for c in send.call_args_list] == [""]
        send.reset_mock()
        with patch("time.time", return_value=timestamp("2026-10-27T08:01")):
            cli.run_once(client([priced(30)]), store, notifier, 1, schedule=schedule)
            assert [notifier.routes[c.args[0]] for c in send.call_args_list] == ["family"]
        send.reset_mock()
        with patch("time.time", return_value=timestamp("2026-10-28T08:01")):
            cli.run_once(client([priced(30)]), store, notifier, 1, schedule=schedule)
            send.assert_not_called()  # empty daily summary
        with patch("time.time", return_value=timestamp("2026-11-02T08:01")):
            cli.run_once(client([priced(30)]), store, notifier, 1, schedule=schedule)
            assert [notifier.routes[c.args[0]] for c in send.call_args_list] == ["oma"]


def test_group_baselines_missing_prices_and_partial_new(tmp_path: Path) -> None:
    with ListingStore(tmp_path / "test.db") as store:
        outbox = store.configure_delivery({"a": "", "b": ""}, Schedule())
        store.sync([make_listing(1)])
        outbox.acknowledge("a", outbox.ready(time.time())["a"])
        assert outbox.prices(1)[""] is None
        store.sync([priced(30)])
        outbox.acknowledge("b", outbox.ready(time.time())["b"])
        assert value(outbox.prices(1)[""]) == 30
        with outbox.db:
            outbox.queue(1, [Notice(kind="new")])
        outbox.acknowledge("a", outbox.ready(time.time())["a"])
        store.sync([priced(40)])
        outbox.acknowledge("b", outbox.ready(time.time())["b"])
        assert value(outbox.prices(1)[""]) == 30


def test_failed_digest_keeps_due_slot_but_new_arrivals_wait(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    config = tmp_path / "notify.yml"
    config.write_text(
        "urls:\n  - shoptest://a:\n      tag: family\n  - shoptest://b:\n      tag: family\n"
    )
    notifier = AppriseNotifier(config=config, digest_groups=("family",))
    schedule = Schedule({"family": "daily"})
    with ListingStore(path) as store:
        with patch("time.time", return_value=timestamp("2026-10-27T07:00")):
            cli.run_once(
                client([priced(30)]), store, notifier, 1, notify_existing=True, schedule=schedule
            )
        with (
            patch("time.time", return_value=timestamp("2026-10-27T08:01")),
            patch.object(notifier, "deliver", side_effect=[True, False]) as send,
        ):
            with pytest.raises(NotificationError):
                cli.run_once(client([priced(30)]), store, notifier, 1, schedule=schedule)
            failed_key = send.call_args_list[-1].args[0]
    with ListingStore(path) as store:
        with (
            patch("time.time", return_value=timestamp("2026-10-27T09:00")),
            patch.object(notifier, "deliver", return_value=True) as send,
        ):
            cli.run_once(client([priced(25), priced(40, 2)]), store, notifier, 1, schedule=schedule)
            send.assert_called_once()
            assert send.call_args.args[0] == failed_key
            assert [item.listing_id for item in send.call_args.args[1]] == [1]
        with (
            patch("time.time", return_value=timestamp("2026-10-28T08:01")),
            patch.object(notifier, "deliver", return_value=True) as send,
        ):
            cli.run_once(client([priced(25), priced(40, 2)]), store, notifier, 1, schedule=schedule)
            assert send.call_count == 2
            assert all([item.listing_id for item in c.args[1]] == [2] for c in send.call_args_list)


def test_migrated_filtered_listing_is_not_mistaken_for_delivered(tmp_path: Path) -> None:
    with ListingStore(tmp_path / "test.db") as store:
        store.sync([priced(30)])
        store.mark_as_filtered([(store.pending()[0], "exclude")])
        outbox = store.configure_delivery({"one": ""}, Schedule())
        store.queue_refiltered([priced(30)])
        result = store.sync([])
        store.record_states({1: "sold_out"}, result)
        assert not outbox.ready(time.time())
        assert not store.pending()


def test_quiet_first_run_still_records_without_console_delivery(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with (
        ListingStore(tmp_path / "test.db") as store,
        patch("time.time", return_value=timestamp("2026-10-26T23:00")),
    ):
        assert (
            cli.run_once(
                client([priced(30)]),
                store,
                ConsoleNotifier(),
                1,
                schedule=Schedule(quiet_hours="22:00-07:00"),
            )
            == 0
        )
        assert not store.pending()
        assert store.events()[-1].event == "recorded"
        assert not capsys.readouterr().out


def test_schedule_dst_local_wall_clock() -> None:
    os.environ["TZ"] = "Europe/Berlin"
    time.tzset()
    schedule = Schedule({"family": "daily"})
    before = timestamp("2026-03-28T08:00")
    next_day = schedule.next_slot("family", before)
    assert datetime.fromtimestamp(next_day) == datetime(2026, 3, 29, 8)
    assert next_day - before == 23 * 3600
    before = timestamp("2026-10-24T08:00")
    next_day = schedule.next_slot("family", before)
    assert datetime.fromtimestamp(next_day) == datetime(2026, 10, 25, 8)
    assert next_day - before == 25 * 3600
