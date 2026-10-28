# Architecture

Shop Notify is a single, long-running Python process. It polls the Etsy Open API,
remembers which listings it has seen in a local SQLite database and sends new ones to
notification services via [Apprise](https://github.com/caronc/apprise). Optionally, a
second process serves the recorded history as a [read-only JSON API](api.md), and a
[web UI](web-ui.md) shows it to signed-in users.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="how-it-works-dark.svg">
  <img alt="Shop Notify polls the Etsy Open API, compares the active listings with its SQLite database and sends new items via Apprise to e-mail, push and chat services." src="how-it-works-light.svg">
</picture>

## Components

All modules are in [`src/shop_notify/`](../src/shop_notify):

| Module | Responsibility |
|---|---|
| `__main__.py` | Command line (`run`, `healthcheck`, `test-notify`, `history`, `check-filters`, `api`), logging, the poll loop (`run_once`), first-run handling, quota warning, signal handling |
| `api.py` | Read-only JSON API of the history (`shop-notify api`, see [api.md](api.md)): a small HTTP server from the standard library; every request opens the database read-only |
| `config.py` | Settings from environment variables and `.env` ([pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)); credentials are `SecretStr` and never printed |
| `etsy.py` | `EtsyClient`: HTTP client for the Etsy Open API v3 ([httpx](https://www.python-httpx.org/)) with pagination, retries when rate limited, quota tracking; validates every response |
| `models.py` | `Listing` and `Money` – the only listing fields that are used and stored |
| `db.py` | `ListingStore`: SQLite database of all listings, their history and the polls, with automatic schema migration (see [Database](#database)) |
| `filters.py` | `Filters`: include/exclude rules (keywords, regular expressions) and price limits from `.env` and `filters.yml` (see [filters.md](filters.md)) |
| `notifications.py` | `MessageRenderer` (Jinja templates, en/de, plain text and HTML), `AppriseNotifier` (e-mail: one HTML mail; push/chat: one message per item with image), `ConsoleNotifier` |
| `systemd.py` | `sd_notify` messages (ready, watchdog, status) when running as a systemd service |
| `templates/` | Built-in message templates per language; can be replaced with `NOTIFY_TEMPLATE_DIR` |

The web UI is a separate [SvelteKit](https://svelte.dev/docs/kit) app in [`ui/`](../ui) (TypeScript, Node.js), with its own image `shop-notify-ui`:

| Part | Responsibility |
|---|---|
| `src/routes/` | The pages, rendered only on the server (`csr = false`): overview, events, polls, listing, sign-in |
| `src/hooks.server.ts` | Every request: host name check, session, redirect to the sign-in, security headers |
| `src/lib/server/` | Accounts and sessions (own SQLite database with `node:sqlite`, scrypt hashes), limit of failed sign-ins, API client, the `shop-notify-ui` command |
| `src/lib/i18n/` | All texts per language, checked for completeness by the type checker |
| `src/lib/api-types.ts` | Types of the API responses, generated from [`api.schema.json`](api.schema.json) |

## Data flow of a poll

1. `EtsyClient.active_listings` fetches all active listings of the shop, newest first,
   page by page. Each listing is validated on its own; invalid ones are logged and
   skipped.
2. `ListingStore.sync` stores the current listings, records changes of price, title
   and quantity, and queues a notification (`pending`) for the enabled
   [events](events.md): new, back, restocked, price drop or rise, low stock. A queued
   notification stays until it was sent, so failed ones are retried.
   - A notified listing that is missing from a complete response is marked as
     *missing*; if it comes back before `FORGET_AFTER_HOURS` (24 h) nothing is sent.
   - After that it counts as *gone*. If it comes back later, it is notified as *back in
     the shop* with its previous price and when it was last seen.
   - Etsy's count of listings is sometimes ahead of the list it returns. Such an
     incomplete response doesn't mark anything as missing; it is logged, with a warning
     after one hour, and processed anyway after `FORGET_AFTER_HOURS`.
   - For listings missing for the first time, `EtsyClient.listing_states` asks Etsy
     why (`sold_out`, `inactive`, `expired`, removed); a sold-out listing is notified
     as *restocked* as soon as it's back.
3. The main image of new and changed listings in the shop is looked up
   (`EtsyClient.listing_images`, up to 100 per poll, i.e. one request) and stored, for
   the notifications and the web UI. A shop seen for the first time is filled in over a
   few polls.
4. The listings to notify pass through the [filters](filters.md). Those that don't
   are marked as *filtered*; while they are in the shop they are checked again on
   every poll and notified once they pass (e.g. after a title change).
5. On the first run (empty database) the new listings are only recorded and printed to
   the console. Otherwise the notification is rendered with the stored images and sent
   to every target. Only after that are the listings marked as notified – if sending fails, they
   are sent again with the next poll.
6. The poll is recorded (counts, completeness, changes, errors), and a heartbeat file is updated (`healthcheck`) and, under systemd, the watchdog is
   notified. The process waits `POLL_INTERVAL` seconds and starts again.

The life cycle of a listing:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="lifecycle-dark.svg">
  <img alt="Lifecycle of a listing: New, then Notified once sent, then Gone once it has been gone from the shop for 24 hours; if it comes back it is notified as back in the shop. Failed notifications are retried with the next poll." src="lifecycle-light.svg">
</picture>

## Database

A single SQLite file (`DB_PATH`). Listings are never deleted, events are only appended;
polls are kept for `POLL_HISTORY_DAYS`. `shop-notify history` shows the content.

The poller is the only process that writes to the database. It uses SQLite's
[write-ahead log](https://www.sqlite.org/wal.html), so that the [API](api.md) can read
while the poller writes, without either waiting for the other. While the database is in
use, the files `-wal` and `-shm` lie next to it; the write-ahead log doesn't work on
network file systems such as NFS or SMB.

```mermaid
erDiagram
    listings ||--o{ listing_events : has
    listings {
        INTEGER listing_id PK "Etsy listing ID"
        INTEGER first_seen "first poll that returned it"
        INTEGER last_seen "last poll that returned it"
        INTEGER missing_since "not returned since (complete polls), else NULL"
        INTEGER gone_since "missing for longer than FORGET_AFTER_HOURS, else NULL"
        INTEGER sold_out_since "Etsy reported it as sold out, else NULL"
        INTEGER notified_date "last notified or recorded, NULL if never"
        TEXT notified_price "lowest notified price, for price drops (JSON)"
        INTEGER filtered "1 if held back by the filters"
        TEXT pending "queued notification: kind and details (JSON), else NULL"
        TEXT result "latest state: title, URL, price, Etsy timestamps (JSON)"
        TEXT image_url "main image, 570 pixels wide, else NULL"
        TEXT thumbnail_url "main image, 170 x 135 pixels, else NULL"
        INTEGER image_checked "Etsy's last_modified_timestamp when the image was looked up"
    }
    listing_events {
        INTEGER id PK
        INTEGER listing_id FK
        INTEGER time
        TEXT event
        TEXT details "JSON, depends on the event"
    }
    polls {
        INTEGER id PK
        INTEGER time
        INTEGER reported "active listings according to Etsy"
        INTEGER received "distinct listings actually returned"
        INTEGER complete
        INTEGER new
        INTEGER back
        INTEGER missing
        INTEGER gone
        INTEGER filtered
        INTEGER restocked
        INTEGER sold_out
        TEXT error "set for failed polls"
    }
```

All times are Unix timestamps (UTC). Events:

| Event | When | Details |
|---|---|---|
| `new` | first seen | price and Etsy's timestamps: `created`, `renewed`, `active_since`, `modified` |
| `imported` | taken over from a database of version 1.0 | – |
| `notified` / `recorded` | notification sent / only recorded (first run) | `kind` unless `new` |
| `missing` | came back within `FORGET_AFTER_HOURS` | `since` (last seen), `seconds` missing |
| `gone` | missing for longer than `FORGET_AFTER_HOURS` | – |
| `filtered` | held back by the filters | `reason`, e.g. `exclude regex '^reserved\b'` |
| `back` | came back after being gone | `last_seen` |
| `sold_out`, `inactive`, `expired`, `removed` | Etsy's state of a listing that went missing | – |
| `restocked` | a sold-out listing is active again | `sold_out_since` |
| `low_stock` | quantity dropped to `NOTIFY_LOW_STOCK` or below | `quantity` |
| `price_changed`, `title_changed` | price or title differs from the last poll | `old`, `new` |

**Migrations:** the schema version is stored in `PRAGMA user_version`. On startup an older
database is upgraded automatically after a backup copy (`<DB_PATH>.bak-v<version>`) was
made; a database of a newer version is refused. Version 1 (Shop Notify 1.1) replaced the
table of 1.0 (`listing_id`, `request_date`, `notified_date`, `result`); version 2
(Shop Notify 1.2) added the `filtered` columns; version 3 (Shop Notify 1.3) replaced
`back` with the notification queue `pending` and added `sold_out_since`, `notified_price`
and the poll counts `restocked` and `sold_out`; version 4 (Shop Notify 1.6) added the
images `image_url`, `thumbnail_url` and `image_checked`.

## External interfaces and trust boundaries

| Interface | Direction | Data | Trust |
|---|---|---|---|
| Configuration (`.env`, environment, `notify.yml`) | in | Etsy credentials, notification URLs with passwords and tokens | trusted – provided by the operator |
| Etsy Open API (`openapi.etsy.com`, HTTPS) | in | listing titles, URLs, prices, image URLs | **untrusted** – content is written by shop owners |
| Images (Etsy CDN, HTTPS) | in | listing pictures | **untrusted** |
| Notification services (SMTP, Telegram, ntfy, … via Apprise) | out | rendered messages and images | trusted with the content of the notifications |
| SQLite database, heartbeat and log file | local | listing IDs and fields above; no credentials | local file system of the operator |
| Optional [read-only API](api.md) (HTTP, `127.0.0.1:8080` by default) | in | GET requests; answers with the history from the database | **untrusted** requests – no authentication, so only for the same machine or a private network |
| Optional [web UI](web-ui.md) (HTTPS via Caddy, nginx or Apache) | in | sign-in, page requests from browsers | **untrusted** – everything but `/login` needs a valid session; the UI reaches the API over an internal network only |

The poller opens no network ports and needs no inbound connections; only the optional API
and web UI listen on one.

## Deployment

- **Docker** ([`Dockerfile`](../Dockerfile), [docker.md](docker.md)): multi-stage build
  with uv, runtime image without build tools and pip, non-root user `1000:1000`, data
  in the volume `/data`, built-in health check. Images for amd64 and arm64 are
  published to GHCR with SBOM and signed provenance.
- **systemd** ([systemd.md](systemd.md)): `Type=notify` service with watchdog and a
  sandboxed unit (dedicated user, read-only system, no new privileges, restricted
  system calls).
- **Locally** with `uv run shop-notify`.
- **Web UI** ([`ui/Dockerfile`](../ui/Dockerfile), [web-ui.md](web-ui.md)): Compose
  profiles add the API, the UI and optionally Caddy for HTTPS. The UI image contains
  the bundled app and Node.js without npm, runs as user `1000`; the API is only on an
  internal network without internet access.

## Build and release

- Dependencies are pinned in `uv.lock`; base images are pinned by digest.
- CI (GitHub Actions) runs lint, type checks, tests with 100 % coverage, secret and
  dependency scans, builds the image for both platforms, scans it and runs an
  [end-to-end test](../tests/e2e/run.sh) against a fake Etsy API and Mailpit.
- A signed tag `vX.Y.Z` on `main` triggers the release workflow (Python packages, SBOM,
  provenance, immutable GitHub release) and the publication of the images. See
  [CONTRIBUTING.md](../CONTRIBUTING.md#releasing).

Security aspects of this design are described in [security.md](security.md).
