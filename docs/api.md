# Read-only API

`shop-notify api` serves the recorded history as JSON: the polls, the events of all listings and the state of a single listing. It's meant for tools that show the history – for example the planned web UI ([#30](https://github.com/spielkind/shop-notify/issues/30)) – and works as an alternative to `shop-notify history`.

- **Read-only:** the API runs as its own process next to the poller and opens the database read-only for every request. It never changes the database, and only `GET` requests are answered.
- **No credentials:** it only needs the database – not the Etsy API key or the notification targets.
- **Disabled by default:** it only runs when started explicitly.

> [!IMPORTANT]
> The API has no authentication. Only make it reachable from the same machine or a private network – for example other containers – and put a reverse proxy with authentication in front of it if it has to be reached from elsewhere.

## Running

With Docker Compose, the `api` service of [`compose.yaml`](../compose.yaml) is started with the `api` profile. It uses the same image and `./data` directory as the poller, gets no `.env`, publishes no port and runs hardened like the poller:

```sh
docker compose --profile api up -d
docker compose exec api python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8080/api/health').read().decode())"
```

To reach it from the host for testing, add `ports: ["127.0.0.1:8080:8080"]` to the `api` service.

Without Docker:

```sh
shop-notify api   # http://127.0.0.1:8080/api/
```

| Variable | Default | Meaning |
|---|---|---|
| `DB_PATH` | `shop-notify.db` | The poller's database |
| `API_HOST` | `127.0.0.1` | Address to listen on; `0.0.0.0` inside a container |
| `API_PORT` | `8080` | Port to listen on |
| `LOG_LEVEL` | `INFO` | `DEBUG` also logs every request |

The API can be started before the poller: until the database exists (or while it's being upgraded to a new schema version), it answers with `503`.

## Endpoints

All responses are JSON. Times are UTC in ISO 8601 (`2026-10-26T11:54:15Z`); a `since` parameter needs a time zone, e.g. `Z` (write `+01:00` as `%2B01:00` in a URL).

### `GET /api/health`

Whether the database can be read, and the last poll:

```json
{"status": "ok", "version": "1.4.0", "schema_version": 3,
 "last_poll": {"id": 4711, "time": "2026-10-26T11:54:15Z", "reported": 124, "received": 124,
               "complete": true, "new": 0, "back": 0, "missing": 0, "gone": 0, "filtered": 0,
               "restocked": 0, "sold_out": 0, "error": null},
 "error": null}
```

`503` with `"status": "unavailable"` and the reason in `error` if the database doesn't exist yet or has a schema version this version can't read.

### `GET /api/polls`

The polls of the last `POLL_HISTORY_DAYS`, newest first: `{"polls": [...], "next": 4690}`.

| Parameter | Meaning |
|---|---|
| `since` | Only polls from this time on |
| `limit` | Polls per page, 1–500 (default 100) |
| `before` | Continue after this poll ID – the `next` of the previous page |

### `GET /api/events`

The [events](events.md) of all listings, newest first: `{"events": [...], "next": 812}`. Each event has its `id`, `listing_id`, `time`, `event`, `details` and the current `title` of the listing.

| Parameter | Meaning |
|---|---|
| `event` | Only these events, comma-separated, e.g. `sold_out,restocked` |
| `q` | Only listings whose title contains this text (ignoring the case of ASCII letters) |
| `listing_id` | Only this listing |
| `since`, `limit`, `before` | As for `/api/polls` |

### `GET /api/listings/{id}`

A listing with its latest state and all its events, newest first:

```json
{"listing": {"listing": {"listing_id": 1550875589, "title": "…", "url": "…", "price": {"amount": 2500, "divisor": 100, "currency_code": "EUR"}, …},
             "status": "sold_out", "first_seen": "…", "last_seen": "…",
             "missing_since": "…", "gone_since": null, "sold_out_since": "…",
             "notified": "…", "filtered": false, "pending": null},
 "events": [...]}
```

`status` is `listed`, `missing`, `sold_out` or `gone`. `notified` is when the listing was last notified – or filtered out, if `filtered` is set. `pending` is the event of a notification waiting to be sent. `404` if the listing is unknown.

### Paging

`next` is `null` on the last page. To get the following page, repeat the request with `before` set to `next` and the same filters:

```sh
curl 'http://127.0.0.1:8080/api/events?event=sold_out&limit=50'
curl 'http://127.0.0.1:8080/api/events?event=sold_out&limit=50&before=812'
```

### Errors

| Status | When |
|---|---|
| `400` | Invalid, unknown or repeated parameter |
| `404` | Unknown endpoint or listing |
| `405` | Any method but `GET` |
| `503` | Database missing, being upgraded or of another schema version |
| `500` | Unexpected error – the details are logged, not returned |

The body is always `{"error": "…"}`, except for `/api/health`.

## Response schema

[`api.schema.json`](api.schema.json) is the JSON schema of all responses, e.g. to generate types for a client. A test makes sure it matches the code; after changing a response, update it with:

```sh
uv run python -m shop_notify.api > docs/api.schema.json
```
