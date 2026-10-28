import json
import logging
import os
import signal
import socket
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from shop_notify import __main__ as cli
from shop_notify import __version__, api
from shop_notify.config import ApiSettings
from shop_notify.db import SCHEMA_VERSION, ListingStore
from shop_notify.models import ListingImage, Money
from tests.helpers import make_listing, sync

SCHEMA_FILE = Path(__file__).parent.parent / "docs" / "schema" / "api.schema.json"


def at(seconds: float) -> Any:
    return patch("shop_notify.db.time.time", return_value=seconds)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """Two listings: a ring that sells out, and a mug; three polls, the last one failed."""
    path = tmp_path / "shop-notify.db"
    ring = make_listing(1, "Silver ring").model_copy(
        update={"price": Money(amount=2500, divisor=100, currency_code="EUR")}
    )
    mug = make_listing(2, "Mug 50%")
    with ListingStore(path) as store:
        with at(1_000):
            store.mark_as_notified(sync(store, [ring, mug]).pending)
            store.record_poll(reported=2, received=2, complete=True)
        with at(2_000):
            store.record_states({1: "sold_out"}, store.sync([mug]))
            store.record_poll(reported=1, received=1, complete=True)
        with at(3_000):
            store.record_poll(error="Etsy is down")
    return path


@pytest.fixture
def server(db: Path) -> Iterator[httpx.Client]:
    with serving(db) as client:
        yield client


@contextmanager
def serving(db_path: Path) -> Iterator[httpx.Client]:
    """Serve the API for ``db_path`` on a free port and return a client for it."""
    server = api.ApiServer(("127.0.0.1", 0), db_path)
    # a short poll interval, so that shutting down doesn't slow the tests down
    threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True).start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}") as client:
            yield client
    finally:
        server.shutdown()
        server.server_close()


def ids(response: httpx.Response, key: str) -> list[tuple[int, str]]:
    return [(item["listing_id"], item["event"]) for item in response.json()[key]]


# --- endpoints ---------------------------------------------------------------------


def test_health(server: httpx.Client) -> None:
    response = server.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert (body["version"], body["schema_version"]) == (__version__, SCHEMA_VERSION)
    assert body["last_poll"]["error"] == "Etsy is down"
    assert body["last_poll"]["time"] == "1970-01-01T00:50:00Z"
    assert body["error"] is None


def test_responses_are_json_that_is_not_cached(server: httpx.Client) -> None:
    response = server.get("/api/health")
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    # no Python version in the Server header
    assert response.headers["server"].strip() == "shop-notify"


def test_health_without_a_readable_database(tmp_path: Path) -> None:
    with serving(tmp_path / "missing.db") as client:
        response = client.get("/api/health")
    assert response.status_code == 503
    body = response.json()
    assert (body["status"], body["last_poll"]) == ("unavailable", None)
    assert "No database at" in body["error"]


def test_polls_newest_first_in_pages(server: httpx.Client) -> None:
    first = server.get("/api/polls", params={"limit": 2}).json()
    assert [p["error"] for p in first["polls"]] == ["Etsy is down", None]
    assert first["polls"][1]["received"] == 1
    assert first["next"] == first["polls"][1]["id"]

    rest = server.get("/api/polls", params={"limit": 2, "before": first["next"]}).json()
    assert [p["received"] for p in rest["polls"]] == [2]
    assert rest["next"] is None

    since = server.get("/api/polls", params={"since": "1970-01-01T00:30:00Z"}).json()
    assert len(since["polls"]) == 2


def test_events_newest_first_in_pages(server: httpx.Client) -> None:
    response = server.get("/api/events")
    assert ids(response, "events") == [
        (1, "sold_out"),
        (2, "notified"),
        (1, "notified"),
        (2, "new"),
        (1, "new"),
    ]
    assert response.json()["next"] is None
    first = response.json()["events"][0]
    assert first["title"] == "Silver ring 1"
    assert first["time"] == "1970-01-01T00:33:20Z"

    page = server.get("/api/events", params={"limit": 3}).json()
    assert len(page["events"]) == 3
    rest = server.get("/api/events", params={"limit": 3, "before": page["next"]}).json()
    assert [e["event"] for e in rest["events"]] == ["new", "new"]
    assert rest["next"] is None


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"event": "sold_out,new"}, [(1, "sold_out"), (2, "new"), (1, "new")]),
        ({"q": "RING"}, [(1, "sold_out"), (1, "notified"), (1, "new")]),
        ({"q": "50%"}, [(2, "notified"), (2, "new")]),
        ({"listing_id": 2, "event": "new"}, [(2, "new")]),
        ({"since": "1970-01-01T00:20:00+00:00"}, [(1, "sold_out")]),
    ],
)
def test_events_can_be_filtered(
    server: httpx.Client, params: dict[str, Any], expected: list[tuple[int, str]]
) -> None:
    assert ids(server.get("/api/events", params=params), "events") == expected


def test_a_listing_with_its_events(server: httpx.Client) -> None:
    response = server.get("/api/listings/1")
    assert response.status_code == 200
    body = response.json()
    listing = body["listing"]
    assert listing["status"] == "sold_out"
    assert listing["listing"]["title"] == "Silver ring 1"
    assert listing["listing"]["price"] == {"amount": 2500, "divisor": 100, "currency_code": "EUR"}
    assert listing["sold_out_since"] == "1970-01-01T00:33:20Z"
    assert (listing["filtered"], listing["pending"]) == (False, None)
    assert [e["event"] for e in body["events"]] == ["sold_out", "notified", "new"]


def listing_ids(response: httpx.Response) -> list[int]:
    return [item["listing"]["listing_id"] for item in response.json()["listings"]]


def test_listings_in_the_shop_by_default(server: httpx.Client) -> None:
    response = server.get("/api/listings")
    assert response.status_code == 200
    assert listing_ids(response) == [2]
    body = response.json()
    assert body["counts"] == {"listed": 1, "missing": 0, "sold_out": 1, "gone": 0}
    assert body["next"] is None
    mug = body["listings"][0]
    assert (mug["status"], mug["listing"]["title"]) == ("listed", "Mug 50% 2")
    assert (mug["image_url"], mug["thumbnail_url"]) == (None, None)


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"status": "all"}, [2, 1]),
        ({"status": "sold_out"}, [1]),
        ({"status": "gone"}, []),
        ({"status": "all", "q": "ring"}, [1]),
        ({"status": "all", "limit": 1, "before": 2}, [1]),
        ({"status": "all", "sort": "oldest"}, [1, 2]),
        ({"status": "all", "sort": "title"}, [2, 1]),
    ],
)
def test_listings_can_be_filtered(
    server: httpx.Client, params: dict[str, Any], expected: list[int]
) -> None:
    assert listing_ids(server.get("/api/listings", params=params)) == expected


def test_listings_in_pages(server: httpx.Client) -> None:
    page = server.get("/api/listings", params={"status": "all", "limit": 1}).json()
    assert [item["listing"]["listing_id"] for item in page["listings"]] == [2]
    assert page["next"] == 2


def test_listings_with_their_images(server: httpx.Client, db: Path) -> None:
    with ListingStore(db) as store:
        store.record_images(
            [2],
            {2: ListingImage("https://i.etsystatic.com/2.jpg", "https://i.etsystatic.com/2s.jpg")},
        )
    mug = server.get("/api/listings").json()["listings"][0]
    assert (mug["image_url"], mug["thumbnail_url"]) == (
        "https://i.etsystatic.com/2.jpg",
        "https://i.etsystatic.com/2s.jpg",
    )
    assert server.get("/api/listings/2").json()["listing"]["image_url"] == mug["image_url"]


@pytest.mark.parametrize(
    ("query", "error"),
    [
        ("status=sold", "status: Input should be 'listed', 'missing', 'sold_out', 'gone' or 'all'"),
        ("since=2026-01-01T00:00:00Z", "since: Extra inputs are not permitted"),
        ("limit=0", "limit: Input should be greater than or equal to 1"),
        ("sort=price", "sort: Input should be 'newest', 'oldest'"),
        ("sort=title&before=2", "before: only works with sort=newest"),
    ],
)
def test_invalid_listing_parameters(server: httpx.Client, query: str, error: str) -> None:
    response = server.get(f"/api/listings?{query}")
    assert response.status_code == 400
    assert error in response.json()["error"]


def test_an_unknown_listing(server: httpx.Client) -> None:
    response = server.get("/api/listings/3")
    assert response.status_code == 404
    assert response.json() == {"error": "Listing 3 is not in the database"}


@pytest.mark.parametrize(
    "path", ["/", "/api", "/api/listing", "/api/listings/abc", "/api/listings/0", "/api/polls/"]
)
def test_unknown_endpoints(server: httpx.Client, path: str) -> None:
    response = server.get(path)
    assert response.status_code == 404
    assert response.json()["error"].startswith("No such endpoint")


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_the_api_is_read_only(server: httpx.Client, db: Path, method: str) -> None:
    before = db.read_bytes()
    response = server.request(method, "/api/events", json={"event": "new"})
    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
    assert response.json() == {"error": "Use GET for /api/events"}
    assert db.read_bytes() == before


@pytest.mark.parametrize(
    ("query", "error"),
    [
        ("limit=0", "limit: Input should be greater than or equal to 1"),
        ("limit=501", "limit: Input should be less than or equal to 500"),
        ("limit=x", "limit: Input should be a valid integer"),
        ("before=0", "before: Input should be greater than 0"),
        ("since=2026-10-01", "since: Input should have timezone info"),
        ("event=Sold+Out", "event: String should match pattern"),
        ("q=", "q: String should have at least 1 character"),
        ("q=" + "x" * 201, "q: String should have at most 200 characters"),
        ("listing_id=-1", "listing_id: Input should be greater than 0"),
        ("foo=1", "foo: Extra inputs are not permitted"),
        ("limit=1&limit=2", "parameter given more than once: limit"),
        ("&".join(f"p{i}=1" for i in range(21)), "Max number of fields exceeded"),
    ],
)
def test_invalid_parameters(server: httpx.Client, query: str, error: str) -> None:
    response = server.get(f"/api/events?{query}")
    assert response.status_code == 400
    assert error in response.json()["error"]


@pytest.mark.parametrize("path", ["/api/health?x=1", "/api/listings/1?x=1"])
def test_endpoints_without_parameters(server: httpx.Client, path: str) -> None:
    response = server.get(path)
    assert response.status_code == 400
    assert response.json() == {"error": "unexpected parameter(s): x"}


def test_a_database_that_disappears(server: httpx.Client, db: Path) -> None:
    db.unlink()
    response = server.get("/api/events")
    assert response.status_code == 503
    assert "No database at" in response.json()["error"]


def test_a_database_with_another_schema(server: httpx.Client, db: Path) -> None:
    connection = sqlite3.connect(db)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    connection.close()
    assert server.get("/api/polls").status_code == 503
    assert server.get("/api/health").json()["status"] == "unavailable"


def test_unexpected_errors_are_logged_but_not_shown(
    server: httpx.Client, caplog: pytest.LogCaptureFixture
) -> None:
    with patch.object(ListingStore, "polls", side_effect=RuntimeError("secret detail")):
        response = server.get("/api/polls")
    assert response.status_code == 500
    assert response.json() == {"error": "Internal error"}
    assert "secret detail" not in response.text
    assert "Error answering GET /api/polls" in caplog.text
    assert "secret detail" in caplog.text


def test_requests_are_logged_at_debug_level(
    server: httpx.Client, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger="shop_notify.api"):
        server.get("/api/health")
    assert '"GET /api/health HTTP/1.1" 200' in caplog.text


# --- running the API -----------------------------------------------------------------
# serve() configures logging itself, so its messages are checked on stderr


@pytest.mark.usefixtures("restore_signals", "restore_logging")
def test_serve_until_terminated(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    servers: list[api.ApiServer] = []

    class Recorded(api.ApiServer):
        def __init__(self, *args: Any) -> None:
            super().__init__(*args)
            servers.append(self)

    answers: list[int] = []

    def use_and_stop() -> None:
        while not servers:
            threading.Event().wait(0.01)
        port = servers[0].server_port
        answers.append(httpx.get(f"http://127.0.0.1:{port}/api/health").status_code)
        os.kill(os.getpid(), signal.SIGTERM)

    client = threading.Thread(target=use_and_stop)
    client.start()
    with patch.object(api, "ApiServer", Recorded):
        code = api.serve(ApiSettings(db_path=db, api_port=0))
    client.join()
    assert (code, answers) == (0, [200])
    err = capsys.readouterr().err
    assert f"API at http://127.0.0.1:{servers[0].server_port}/api/ reading {db}" in err
    assert "Received SIGTERM, shutting down" in err


@pytest.mark.usefixtures("restore_logging")
def test_serve_needs_a_free_port(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        assert api.serve(ApiSettings(db_path=db, api_port=port)) == 1
    assert f"Can't serve the API on 127.0.0.1:{port}" in capsys.readouterr().err


def test_main_runs_the_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key, value in {"DB_PATH": str(tmp_path / "x.db"), "API_PORT": "9999"}.items():
        monkeypatch.setenv(key, value)
    calls: list[ApiSettings] = []

    def fake_serve(settings: ApiSettings) -> int:
        calls.append(settings)
        return 0

    monkeypatch.setattr(api, "serve", fake_serve)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["api"])
    assert exit_info.value.code == 0
    [settings] = calls
    assert (settings.db_path, settings.api_host, settings.api_port) == (
        tmp_path / "x.db",
        "127.0.0.1",
        9999,
    )


def test_the_documented_schema_is_up_to_date() -> None:
    assert json.loads(SCHEMA_FILE.read_text(encoding="utf-8")) == api.schema(), (
        "Update it with: uv run python -m shop_notify.api > docs/schema/api.schema.json"
    )
