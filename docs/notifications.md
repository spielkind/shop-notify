# Notifications

Shop Notify sends notifications with [Apprise](https://github.com/caronc/apprise), so e-mail, [ntfy](https://ntfy.sh), Telegram, Signal, Discord, Pushover and [100+ more services](https://github.com/caronc/apprise/wiki) are supported – also several at once.

## Configuration
For more than one target, copy [`notify.example.yml`](../notify.example.yml) to `notify.yml`, adjust it and set `NOTIFY_CONFIG=notify.yml`:

```yaml
version: 1
urls:
  # One e-mail to several people who don't see each other's address
  - mailtos://you:app-password@gmail.com:
      - bcc: alice@example.com, bob@example.com
  # Push notification to your phone
  - ntfys://ntfy.sh/your-long-random-topic
```

For a single target, `NOTIFY_URLS` is enough, e.g. `NOTIFY_URLS="ntfys://ntfy.sh/your-long-random-topic"`. Both can be combined.

- **E-mail** gets one HTML message with all new items and their pictures. **Push and chat services** that support attachments (ntfy, Telegram, Signal, Pushover, …) get **one message per item with its picture** – up to `NOTIFY_IMAGES_MAX` items per poll, above that one summary message. Set `NOTIFY_IMAGES_IN_SUMMARY=true` to attach the pictures to the summary as well; for Telegram add `?album=yes` to the URL to get them as one album (ntfy shows only the first picture).
- The configuration is checked at startup; credentials are masked in the log.
- Deliveries are stored per target. Failed targets retry on the next eligible poll; successful targets are not retried just because another target failed. A partial failure is recorded as a poll error.
- `notify.yml` and `.env` contain passwords – both are ignored by git and never copied into the Docker image.

## Quiet hours and recipient digests

Both are optional and off by default. For example, in `.env`:

```dotenv
NOTIFY_QUIET_HOURS=22:00-07:00
NOTIFY_DIGEST=family:daily,oma:weekly
NOTIFY_DIGEST_TIME=08:00
NOTIFY_DIGEST_DAY=mon
```

Use Apprise tags in `notify.yml` to assign services to recipient groups:

```yaml
urls:
  - ntfys://ntfy.sh/family-random-topic:
      tag: family
  - mailtos://you:app-password@gmail.com:
      - to: oma@example.com
        tag: oma
  - ntfys://ntfy.sh/my-immediate-topic
```

- `family` gets a daily summary at 08:00; `oma` gets a weekly summary on Monday
  at 08:00. Daily/weekly are the supported cadences. A service may have **at most
  one configured digest-group tag**; ambiguous services are rejected at startup.
  Other tags don't change delivery timing. Untagged destinations and the console
  remain immediate, subject to quiet hours.
- All times use the poller's local timezone (`TZ` in Docker, otherwise the system
  timezone), not the recipient's. Times must be `HH:MM`; days are `mon` through
  `sun`. Quiet hours include the start and exclude the end, may cross midnight,
  and must have different start/end times. DST follows local wall-clock time.
- Quiet hours gate **every route**, including immediate targets. Polling, state
  tracking and filtering continue. A digest due during quiet hours is delivered
  on the first successful poll after quiet hours, not the next day's slot.
- Slots and pending deliveries survive restarts. A new group starts at its next
  scheduled slot; empty digests are not sent. Downtime produces one current
  summary, not one message for every missed day/week. Failed digest targets retry
  on subsequent eligible polls without waiting another day/week. New arrivals
  after that digest attempt belong to the next slot, not extra summaries for
  targets that already succeeded.
- Digests and batches held by quiet hours always use summary mode, even below
  `NOTIFY_IMAGES_MAX`, including after a restart. Ordinary immediate batches keep
  their per-item behavior. Summary image attachments still follow `NOTIFY_IMAGES`
  and `NOTIFY_IMAGES_IN_SUMMARY`.
- Pending events coalesce by listing and target, using the usual event priority.
  Messages show the latest stored title, price, quantity and image. Obsolete price
  changes are dropped when the current price no longer meets the configured
  threshold/direction relative to their reference. A queued `new` notification is
  dropped for every still-waiting target once the listing is gone or known sold out,
  even if another target in the same group already received it. Actual `sold_out`
  events are not discarded by this cleanup. A brief unexplained absence is not
  enough to drop `new` until `FORGET_AFTER_HOURS` expires.
- The first-run recording/suppression behavior is unchanged (the initial console
  listing is also suppressed during quiet hours). Upgrading fans out
  existing pending notifications to the configured routes. Already-notified
  listings are **not backfilled** when adding a group. During the one-time migration,
  groups inherit the legacy notified price baseline, or the current price if none
  exists. Groups added after initialization start from the **current listing price**;
  existing groups keep their baselines. Future deliveries update each group's
  baseline independently.
- With no delivery routes, polls do not acknowledge queued notifications, and the
  initial fan-out of legacy pending notifications waits until a route is enabled.
  A first-run-only console is not a delivery route; first-run suppression and
  filtering still apply.
- With delivery routes enabled, removing/reassigning a target drops its old queued
  deliveries. Changing its URL or credentials creates a new target identity; old
  deliveries aren't replayed.
  Restart the poller after configuration changes. Pending slots are retained for
  existing groups, so cadence/time changes take effect after their current slot.
- This is **not exactly-once delivery**: a crash after a service accepts a message
  but before SQLite records success may cause a duplicate. A per-item batch that
  partly fails may also repeat items for that failed target. No credentials are
  stored in the outbox; target identities are hashes.

## Testing notifications
Send a sample notification with two example items to all configured targets – no Etsy key needed, the database is not touched:

```sh
uv run shop-notify test-notify                                   # local
docker compose run --rm shop-notify test-notify                  # Docker
cd /opt/shop-notify && sudo -u shop-notify .venv/bin/shop-notify test-notify   # systemd
```

```text
  ✓ mailtos://you:****@gmail.com/
  ✗ ntfys://ntfy.sh/your-topic

1 of 2 target(s) succeeded.
```

The subject starts with `[Test]`. Use `--url` to try a single Apprise URL before adding it to the configuration, e.g. `shop-notify test-notify --url "ntfys://ntfy.sh/your-topic"`. The exit code is `0` if all targets succeeded, `1` if any failed and `2` for configuration errors.

## Message templates
E-mails are sent as HTML with the item's main image, title, price and date; push and chat services get one message per item (`item_subject.txt.j2`, `item_body.txt.j2`) or the compact plain-text summary shown above (`subject.txt.j2`, `body.txt.j2`).

The built-in messages are available in English and German (`NOTIFY_LANGUAGE=de`); dates and prices are formatted accordingly, e.g. `Oct 14, 2026, 19:46` / `€24.50` or `14.10.2026, 19:46 Uhr` / `24,50 €`.

To customise them, copy any of the [built-in templates](../src/shop_notify/templates/) (`subject.txt.j2`, `body.txt.j2`, `body.html.j2`, `item_subject.txt.j2`, `item_body.txt.j2` from the folder of your language) into a directory and set `NOTIFY_TEMPLATE_DIR` to it – files you don't copy keep their default. Templates use [Jinja2](https://jinja.palletsprojects.com/templates/); the variables are `item` (per-item templates only), `listings` (with `title`, `url`, `price`, `image_url`, `listed` – when the item went online, `last_modified`, and `back` for items back in the shop with `back.last_seen`, `back.gone_for` in seconds and `back.previous_price` if the price changed), `shop_name`, `initial` (true for the [first run](../README.md#first-run)) and `test` (true for [test notifications](#testing-notifications)), plus `item.kind` and `item.event` for [events](events.md), and the filters `summary`, `event_label`, `subject_prefix`, `price_note`, `when`, `duration`, `localtime`, `money` and `ago` (e.g. `{{ item.back.gone_for | ago }}` → "4 weeks ago") format dates, prices and durations in the configured language. Values are HTML-escaped automatically in `*.html.j2`.

## Setting up common services

### E-mail via Gmail
Recipients don't need to do anything. You need a [Google App Password](https://support.google.com/accounts/answer/185833) (requires 2-step verification). Google shows it in groups of four – leave out the spaces:

```yaml
  - mailtos://yourname:abcdefghijklmnop@gmail.com:
      - bcc: alice@example.com, bob@example.com
        from: Shop Notify <yourname@gmail.com>
```

`yourname` is the part of your Gmail address before the `@`. Without `to`/`bcc` the mail goes to your own address. Gmail allows about 500 recipients per day. Recipients in `bcc` don't see each other.

### ntfy
No account needed: install the [ntfy app](https://ntfy.sh) (Android/iOS) or open <https://ntfy.sh/app> and subscribe to a long, random topic name – everyone who knows the name can read it.

```yaml
  - ntfys://ntfy.sh/shopwatch-k7f3q9x2
```

### Telegram
1. Talk to [@BotFather](https://t.me/BotFather), send `/newbot` and pick a name; you get a **bot token** like `123456789:AAH…` (the number before the colon is the bot's ID and part of the token).
2. Open your bot (`https://t.me/<bot username>`), press **Start** and send any message.
3. Find your **chat ID**: open `https://api.telegram.org/bot<TOKEN>/getUpdates` and look for `"chat":{"id": …}` – or ask [@userinfobot](https://t.me/userinfobot). Group IDs are negative; add the bot to the group first. The `id` in `getMe` is the bot itself, not your chat.

```yaml
  - tgram://123456789:AAH-your-bot-token/987654321
  # several pictures as one album (NOTIFY_IMAGES_IN_SUMMARY):
  # - tgram://123456789:AAH-your-bot-token/987654321?album=yes
```

If `getUpdates` returns an empty `result`, the bot hasn't received a message in the last 24 hours – send another one and reload.

### WhatsApp
Apprise supports WhatsApp only through Meta's WhatsApp Business Cloud API: it needs a Meta developer account, a separate sender number and – for messages outside a 24-hour window – pre-approved message templates, which may cost money. For personal notifications e-mail, Telegram, Signal or ntfy are much simpler.
