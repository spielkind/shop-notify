"""Read-only JSON API over the recorded history (``shop-notify api``, see docs/api.md).

The API runs as its own process next to the poller. Every request opens the database
read-only, so the API never changes it and doesn't need the Etsy credentials. Routes are
looked up by method and path, so that validated write operations can be added later.
"""

import json
import logging
import re
import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import FrameType
from typing import Any, Literal, cast
from urllib.parse import parse_qs, urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from pydantic.json_schema import GenerateJsonSchema

from shop_notify import __version__
from shop_notify.config import ApiSettings
from shop_notify.db import (
    SCHEMA_VERSION,
    DatabaseUnavailableError,
    Event,
    ListingStore,
    Poll,
    StoredListing,
)

log = logging.getLogger(__name__)

MAX_LIMIT = 500
MAX_PARAMETERS = 20


# -- responses ----------------------------------------------------------------


@dataclass(frozen=True)
class Health:
    status: str
    """``ok``, or ``unavailable`` if the database can't be read (HTTP 503)."""
    version: str
    schema_version: int
    last_poll: Poll | None
    error: str | None = None


@dataclass(frozen=True)
class PollPage:
    polls: list[Poll]
    """Newest first."""
    next: int | None
    """Pass as ``before`` to get the next (older) page; null on the last page."""


@dataclass(frozen=True)
class EventPage:
    events: list[Event]
    """Newest first."""
    next: int | None
    """Pass as ``before`` to get the next (older) page; null on the last page."""


@dataclass(frozen=True)
class ListingCounts:
    """Number of listings per status."""

    listed: int
    missing: int
    sold_out: int
    gone: int


@dataclass(frozen=True)
class ListingPage:
    listings: list[StoredListing]
    """The newest (first seen) first."""
    counts: ListingCounts
    """Of all listings, whatever the filters."""
    next: int | None
    """Pass as ``before`` to get the next page; null on the last page."""


@dataclass(frozen=True)
class ListingDetail:
    listing: StoredListing
    events: list[Event]
    """All events of the listing, newest first."""


@dataclass(frozen=True)
class Error:
    error: str


RESPONSES: tuple[type, ...] = (Health, PollPage, EventPage, ListingPage, ListingDetail, Error)
_ADAPTERS: dict[type, TypeAdapter[Any]] = {t: TypeAdapter(t) for t in RESPONSES}


class _SchemaWithoutFieldTitles(GenerateJsonSchema):
    """Field titles only repeat the names, and give generated client types needless aliases."""

    def field_title_should_be_set(self, schema: Any) -> bool:
        return False


def schema() -> dict[str, Any]:
    """JSON schema of the responses, kept in docs/schema/api.schema.json for the web UI."""
    _, result = TypeAdapter.json_schemas(
        [(t.__name__, "serialization", _ADAPTERS[t]) for t in RESPONSES],
        title="Shop Notify API",
        description=f"Responses of the read-only API, schema version {SCHEMA_VERSION}",
        schema_generator=_SchemaWithoutFieldTitles,
    )
    return result


# -- query parameters ---------------------------------------------------------


class _PageQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    since: AwareDatetime | None = None
    before: int | None = Field(default=None, gt=0)
    limit: int = Field(default=100, ge=1, le=MAX_LIMIT)


class _EventQuery(_PageQuery):
    event: str | None = Field(default=None, pattern=r"^[a-z_]+(,[a-z_]+)*$")
    q: str | None = Field(default=None, min_length=1, max_length=200)
    listing_id: int | None = Field(default=None, gt=0)


class _ListingQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["listed", "missing", "sold_out", "gone", "all"] = "listed"
    q: str | None = Field(default=None, min_length=1, max_length=200)
    sort: Literal["newest", "oldest", "price_asc", "price_desc", "title", "changed", "stock"] = (
        "newest"
    )
    before: int | None = Field(default=None, gt=0)
    limit: int = Field(default=100, ge=1, le=MAX_LIMIT)


class _BadRequestError(Exception):
    pass


def _validate[Q: BaseModel](model: type[Q], params: dict[str, str]) -> Q:
    try:
        return model.model_validate(params)
    except ValidationError as e:
        raise _BadRequestError(
            "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
        ) from e


def _parameters(query: str) -> dict[str, str]:
    try:
        parsed = parse_qs(query, keep_blank_values=True, max_num_fields=MAX_PARAMETERS)
    except ValueError as e:
        raise _BadRequestError(str(e)) from e
    repeated = sorted(name for name, values in parsed.items() if len(values) > 1)
    if repeated:
        raise _BadRequestError(f"parameter given more than once: {', '.join(repeated)}")
    return {name: values[0] for name, values in parsed.items()}


def _next(items: list[Any], limit: int) -> int | None:
    """ID to continue after, if there are more items than the page holds."""
    return items[limit - 1].id if len(items) > limit else None


# -- endpoints ----------------------------------------------------------------

Payload = tuple[HTTPStatus, Any]
Handler = Callable[[Path, re.Match[str], dict[str, str]], Payload]


def _no_parameters(params: dict[str, str]) -> None:
    if params:
        raise _BadRequestError(f"unexpected parameter(s): {', '.join(sorted(params))}")


def _health(db_path: Path, _match: re.Match[str], params: dict[str, str]) -> Payload:
    _no_parameters(params)
    try:
        with ListingStore(db_path, readonly=True) as store:
            polls = store.polls(newest_first=True, limit=1)
    except DatabaseUnavailableError as e:
        unavailable = Health("unavailable", __version__, SCHEMA_VERSION, None, str(e))
        return HTTPStatus.SERVICE_UNAVAILABLE, unavailable
    return HTTPStatus.OK, Health("ok", __version__, SCHEMA_VERSION, polls[0] if polls else None)


def _polls(db_path: Path, _match: re.Match[str], params: dict[str, str]) -> Payload:
    query = _validate(_PageQuery, params)
    with ListingStore(db_path, readonly=True) as store:
        polls = store.polls(
            since=query.since.timestamp() if query.since else 0,
            before=query.before,
            limit=query.limit + 1,
            newest_first=True,
        )
    return HTTPStatus.OK, PollPage(polls[: query.limit], _next(polls, query.limit))


def _events(db_path: Path, _match: re.Match[str], params: dict[str, str]) -> Payload:
    query = _validate(_EventQuery, params)
    with ListingStore(db_path, readonly=True) as store:
        events = store.events(
            listing_id=query.listing_id,
            since=query.since.timestamp() if query.since else 0,
            kinds=query.event.split(",") if query.event else (),
            text=query.q,
            before=query.before,
            limit=query.limit + 1,
            newest_first=True,
        )
    return HTTPStatus.OK, EventPage(events[: query.limit], _next(events, query.limit))


def _listings(db_path: Path, _match: re.Match[str], params: dict[str, str]) -> Payload:
    query = _validate(_ListingQuery, params)
    if query.before is not None and query.sort != "newest":
        raise _BadRequestError("before: only works with sort=newest")
    with ListingStore(db_path, readonly=True) as store:
        listings = store.listings(
            status=None if query.status == "all" else query.status,
            text=query.q,
            sort=query.sort,
            before=query.before,
            limit=query.limit + 1,
        )
        counts = ListingCounts(**store.listing_counts())
    last = listings[query.limit - 1].listing.listing_id if len(listings) > query.limit else None
    return HTTPStatus.OK, ListingPage(listings[: query.limit], counts, last)


def _listing(db_path: Path, match: re.Match[str], params: dict[str, str]) -> Payload:
    _no_parameters(params)
    listing_id = int(match["listing_id"])
    with ListingStore(db_path, readonly=True) as store:
        listing = store.listing(listing_id)
        if listing is None:
            return HTTPStatus.NOT_FOUND, Error(f"Listing {listing_id} is not in the database")
        events = store.events(listing_id=listing_id, newest_first=True)
    return HTTPStatus.OK, ListingDetail(listing, events)


@dataclass(frozen=True)
class _Route:
    method: str
    path: re.Pattern[str]
    handler: Handler


_ROUTES = (
    _Route("GET", re.compile(r"/api/health"), _health),
    _Route("GET", re.compile(r"/api/polls"), _polls),
    _Route("GET", re.compile(r"/api/events"), _events),
    _Route("GET", re.compile(r"/api/listings"), _listings),
    # up to 18 digits, so that the ID fits into an SQLite integer
    _Route("GET", re.compile(r"/api/listings/(?P<listing_id>[1-9][0-9]{0,17})"), _listing),
)


def _dispatch(
    method: str, path: str, query: str, db_path: Path, headers: dict[str, str]
) -> Payload:
    matches = [(r, m) for r in _ROUTES if (m := r.path.fullmatch(path))]
    if not matches:
        return HTTPStatus.NOT_FOUND, Error(f"No such endpoint: {path}")
    found = next(((r, m) for r, m in matches if r.method == method), None)
    if found is None:
        headers["Allow"] = ", ".join(sorted({r.method for r, _ in matches}))
        return HTTPStatus.METHOD_NOT_ALLOWED, Error(f"Use {headers['Allow']} for {path}")
    route, match = found
    try:
        return route.handler(db_path, match, _parameters(query))
    except _BadRequestError as e:
        return HTTPStatus.BAD_REQUEST, Error(str(e))
    except DatabaseUnavailableError as e:
        return HTTPStatus.SERVICE_UNAVAILABLE, Error(str(e))
    except Exception:
        log.exception("Error answering %s %s", method, path)
        return HTTPStatus.INTERNAL_SERVER_ERROR, Error("Internal error")


def handle(method: str, target: str, db_path: Path) -> tuple[HTTPStatus, bytes, dict[str, str]]:
    """Answer a request: status, JSON body and extra headers."""
    url = urlsplit(target)
    headers: dict[str, str] = {}
    status, payload = _dispatch(method, url.path, url.query, db_path, headers)
    return status, _ADAPTERS[type(payload)].dump_json(payload), headers


# -- server -------------------------------------------------------------------


class ApiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], db_path: Path) -> None:
        self.db_path = db_path
        super().__init__(address, _RequestHandler)


class _RequestHandler(BaseHTTPRequestHandler):
    server_version = "shop-notify"
    sys_version = ""
    # Seconds a client may take to send its request
    timeout = 10

    def _answer(self) -> None:
        db_path = cast(ApiServer, self.server).db_path
        status, body, headers = handle(self.command, self.path, db_path)
        self.send_response(status)
        for name, value in {
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            **headers,
        }.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    # Write methods are answered with 405 until the API supports writing
    def do_GET(self) -> None:
        self._answer()

    def do_POST(self) -> None:
        self._answer()

    def do_PUT(self) -> None:
        self._answer()

    def do_PATCH(self) -> None:
        self._answer()

    def do_DELETE(self) -> None:
        self._answer()

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("%s %s", self.address_string(), format % args)


def serve(settings: ApiSettings) -> int:
    """Serve the API until SIGINT or SIGTERM; returns the exit code."""
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    try:
        server = ApiServer((settings.api_host, settings.api_port), settings.db_path)
    except OSError as e:
        log.error("Can't serve the API on %s:%d: %s", settings.api_host, settings.api_port, e)
        return 1

    stop = threading.Event()

    def _handle_signal(signum: int, _frame: FrameType | None) -> None:
        log.info("Received %s, shutting down", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    log.info(
        "Shop Notify %s API at http://%s:%d/api/ reading %s",
        __version__,
        settings.api_host,
        server.server_port,
        settings.db_path,
    )
    threading.Thread(target=server.serve_forever, name="api", daemon=True).start()
    try:
        stop.wait()
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    print(json.dumps(schema(), indent=2))
