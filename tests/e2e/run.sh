#!/usr/bin/env bash
# End-to-end test of the Docker image: a fake Etsy API and Mailpit (SMTP server with
# an HTTP API) stand in for the real services.
#
#   1. first run: the existing listings are recorded, no mail is sent
#   2. a new listing appears: exactly one HTML mail with title, price and image
#   2b. a new "Reserved listing for …" is held back by NOTIFY_EXCLUDE_REGEX: no mail
#   3. further polls, one of them without an old listing (gap in Etsy's index):
#      no duplicate mail, nothing gone
#   4. an old listing is gone for longer than FORGET_AFTER_HOURS and comes back with a
#      new price: one "back in the shop" mail with the old price and when it was last seen
#   5. a new listing appears and then sells out while the state lookup is temporarily
#      unavailable: the first lookup fails and is retried on a later poll, which records
#      the sold-out state
#   6. the sold-out listing is restocked with a single item: one "back in stock" mail
#      (NOTIFY_EVENTS includes restocked)
#   7. a second, hardened container serves the read-only API from the same database
#      while the poller keeps running
#   8. the web UI: an account is created with shop-notify-ui; signing in is needed, and
#      requests for another host name are rejected
#   9-11. the UI behind Caddy (the Caddyfile of the repository), nginx and Apache (the
#      examples of docs/web-ui.md): sign in, the pages show the history, sign out
#
# The ports (18080-18088, 18443, 13000) don't clash with a real installation on the host.
#
# Usage: tests/e2e/run.sh [IMAGE] [UI_IMAGE]
#   (default: shop-notify:ci shop-notify-ui:ci; needs docker, curl, jq, openssl, python3)
set -euo pipefail

IMAGE=${1:-shop-notify:ci}
UI_IMAGE=${2:-shop-notify-ui:ci}
MAILPIT=axllent/mailpit:v1.31.6@sha256:23fe39e81bde596a918000b3135c0f85dc8bb4c9ea6be274b051143f3e300387
ETSY_PORT=18080
SMTP_PORT=11025
MAILPIT_API=http://localhost:18025/api/v1
API_PORT=18081
# Ports of the web UI and the proxies in front of it: not 3000, 80 and 443, so that the
# test can run next to a real installation
UI_PORT=${E2E_UI_PORT:-13000}
HTTP_PORT=${E2E_HTTP_PORT:-18088}
HTTPS_PORT=${E2E_HTTPS_PORT:-18443}
API=http://localhost:$API_PORT/api
DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$DIR/../.." && pwd)
# Reverse proxies in front of the UI; Caddy as in compose.yaml
CADDY=$(sed -n 's/^ *image: \(caddy:.*\)$/\1/p' "$ROOT/compose.yaml")
NGINX=nginx:1.30-alpine@sha256:47928bf67eb62a1863cb08d9ecb52d3e704304ba614eaee17884204677702951
HTTPD=httpd:2.4.69-alpine@sha256:070da7661f0be8a05430b2c125c0cde9e53176b4048c813024021969a0e3893c
UI_PASSWORD="e2e password $RANDOM$RANDOM"

cleanup() {
  status=$?
  if ((status != 0)); then
    echo "--- shop-notify log ---"
    docker logs shop-notify-e2e 2>&1 | tail -40 || true
    echo "--- API log ---"
    docker logs shop-notify-e2e-api 2>&1 | tail -20 || true
    echo "--- UI log ---"
    docker logs shop-notify-e2e-ui 2>&1 | tail -20 || true
    echo "--- proxy log ---"
    docker logs shop-notify-e2e-proxy 2>&1 | tail -20 || true
  fi
  docker rm -f shop-notify-e2e shop-notify-e2e-api shop-notify-e2e-ui shop-notify-e2e-proxy \
    mailpit-e2e >/dev/null 2>&1 || true
  [[ -n "${WORK:-}" ]] && rm -rf "$WORK"
  [[ -n "${MAIL:-}" ]] && rm -f "$MAIL"
  [[ -n "${ETSY_PID:-}" ]] && kill "$ETSY_PID" 2>/dev/null
  exit "$status"
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

# wait_for SECONDS COMMAND… – retry until the command succeeds
wait_for() {
  local seconds=$1; shift
  for ((i = 0; i < seconds; i++)); do
    "$@" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

# log_has PATTERN – the shop-notify log contains PATTERN (read completely first: with
# pipefail, "docker logs | grep -q" fails when grep exits early)
log_has() { local log; log=$(docker logs shop-notify-e2e 2>&1); grep -q -- "$1" <<<"$log"; }

mail_count() { curl -sf "$MAILPIT_API/messages" | jq -r .total; }
one_mail() { [[ $(mail_count) == 1 ]]; }
two_mails() { [[ $(mail_count) == 2 ]]; }
three_mails() { [[ $(mail_count) == 3 ]]; }
four_mails() { [[ $(mail_count) == 4 ]]; }
first_run_logged() { log_has "First run: 3 existing item"; }

# check_mail JQ_FILTER MESSAGE – the filter must be true for the received mail
check_mail() { jq -e "$1" "$MAIL" >/dev/null || fail "$2"; }

python3 "$DIR/fake_etsy.py" "$ETSY_PORT" &
ETSY_PID=$!
docker run -d --name mailpit-e2e --network host "$MAILPIT" \
  --smtp 127.0.0.1:$SMTP_PORT --listen 127.0.0.1:18025 >/dev/null
wait_for 30 curl -sf "$MAILPIT_API/messages" || fail "Mailpit did not start"
wait_for 10 curl -sf "http://localhost:$ETSY_PORT/image.png" || fail "fake Etsy did not start"

MAIL_URL="mailto://localhost:$SMTP_PORT?mode=insecure&from=shop-notify@example.com&to=me@example.com" # gitleaks:allow
docker run -d --name shop-notify-e2e --network host \
  -e ETSY_API_KEY=e2e-key -e ETSY_SHARED_SECRET=e2e-secret \
  -e ETSY_API_ENDPOINT="http://localhost:$ETSY_PORT/v3/application/" \
  -e ETSY_SHOP_NAME=TestShop -e POLL_INTERVAL=10 -e FORGET_AFTER_HOURS=0.01 \
  -e 'NOTIFY_EXCLUDE_REGEX=^reserved\b' -e NOTIFY_EVENTS=new,back,restocked \
  -e NOTIFY_URLS="$MAIL_URL" \
  "$IMAGE" >/dev/null

echo "1. first run is silent"
wait_for 60 first_run_logged || fail "first run not logged"
sleep 3
[[ $(mail_count) == 0 ]] || fail "mail sent on the first run"

echo "2. a new listing is notified once"
curl -sf -X POST "http://localhost:$ETSY_PORT/_add" >/dev/null
wait_for 60 one_mail || fail "no mail for the new listing"
id=$(curl -sf "$MAILPIT_API/messages" | jq -r '.messages[0].ID')
MAIL=$(mktemp)
curl -sf "$MAILPIT_API/message/$id" > "$MAIL"
echo "   subject: $(jq -r .Subject "$MAIL")"
check_mail '(.Subject + .HTML) | contains("Handmade test item 4")' "title missing"
check_mail '.HTML | contains("12.54")' "price missing"
check_mail '(.HTML | contains("image.png")) or ((.Inline // []) | length > 0) or ((.Attachments // []) | length > 0)' "image missing"
check_mail '.HTML | contains("Handmade test item 3") | not' "old listing included"

echo "2b. a reserved listing is filtered"
curl -sf -X POST "http://localhost:$ETSY_PORT/_add_reserved" >/dev/null
reserved_filtered() {
  log_has "Filtered (exclude regex '^reserved.b'): Reserved listing for Martin"
}
wait_for 30 reserved_filtered || fail "reserved listing was not filtered"
[[ $(mail_count) == 1 ]] || fail "mail sent for a reserved listing"
docker exec shop-notify-e2e shop-notify history --polls | grep -q "1 filtered" \
  || fail "filtered listing not counted in the poll history"

echo "3. no duplicate on the next polls, even if a listing is briefly missing"
curl -sf -X POST "http://localhost:$ETSY_PORT/_hide_once" >/dev/null
sleep 25
[[ $(mail_count) == 1 ]] || fail "duplicate mail"
log_has "No new items this time" || fail "no further poll logged"
log_has "No longer in the shop" && fail "briefly missing listing is gone"

echo "4. a listing that was gone is notified as back, with its old price"
curl -sf -X POST "http://localhost:$ETSY_PORT/_hide_for/50" >/dev/null
wait_for 90 two_mails || fail "no mail for the listing back in the shop"
log_has "No longer in the shop: Handmade test item 1" \
  || fail "listing was not marked as gone"
id=$(curl -sf "$MAILPIT_API/messages" | jq -r '.messages[0].ID')
curl -sf "$MAILPIT_API/message/$id" > "$MAIL"
echo "   subject: $(jq -r .Subject "$MAIL")"
check_mail '.Subject == "1 item back in TestShop"' "unexpected subject"
check_mail '.HTML | contains("Back in the shop")' "back badge missing"
check_mail '.HTML | contains("13.51") and contains("12.51")' "new or old price missing"
check_mail '.HTML | contains("last seen")' "last seen missing"

echo "5. a new listing appears and then sells out while the state lookup is failing"
# the newest listing sells out while the state lookup is temporarily unavailable, so the
# first lookup fails; once it recovers the lookup is retried and the sold-out state is recorded
curl -sf -X POST "http://localhost:$ETSY_PORT/_add" >/dev/null
wait_for 60 three_mails || fail "no mail for the new listing before it sells out"
curl -sf -X POST "http://localhost:$ETSY_PORT/_fail_batch_for/25" >/dev/null
curl -sf -X POST "http://localhost:$ETSY_PORT/_sell_out_for/120" >/dev/null
lookup_failed() { log_has "Could not look up the state of missing listings"; }
wait_for 30 lookup_failed || fail "failed state lookup not logged"
sold_out_logged() { log_has "Listing 1006 is no longer listed: sold out"; }
wait_for 60 sold_out_logged || fail "sold-out state not recorded after the lookup recovered"

echo "6. the restocked listing is notified as back in stock"
wait_for 120 four_mails || fail "no mail for the restocked listing"
id=$(curl -sf "$MAILPIT_API/messages" | jq -r '.messages[0].ID')
curl -sf "$MAILPIT_API/message/$id" > "$MAIL"
echo "   subject: $(jq -r .Subject "$MAIL")"
check_mail '.Subject == "1 back in stock in TestShop"' "unexpected subject"
check_mail '.HTML | contains("Back in stock")' "back-in-stock badge missing"
check_mail '.HTML | contains("sold out for") and contains("only 1 left")' "details missing"

echo "7. the read-only API serves the history from a second, hardened container"
# same image and database volume as the poller; both use the database at the same time
docker run -d --name shop-notify-e2e-api --network host --volumes-from shop-notify-e2e \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  -e API_PORT="$API_PORT" "$IMAGE" api >/dev/null
wait_for 30 curl -sf "$API/health" || fail "API not healthy"
check_api() { curl -sf "$API/$1" | jq -e "$2" >/dev/null || fail "$3"; }
check_api health '.status == "ok" and .last_poll.error == null' "no successful last poll"
check_api 'events?event=sold_out' '[.events[].listing_id] | index(1006) != null' \
  "sold-out event missing"
check_api listings/1006 \
  '.listing.status == "listed" and ([.events[].event] | index("restocked") != null)' \
  "restocked listing not shown as listed"
# the poller looked up the images of the listings in the shop
check_api listings \
  '.counts.listed >= 4 and ([.listings[] | select(.image_url == null)] | length) == 0' \
  "listings without their images"
check_api 'listings?sort=title' \
  '[.listings[].listing.title] == ([.listings[].listing.title] | sort_by(ascii_downcase))' \
  "listings not sorted by title"
[[ $(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/events") == 405 ]] \
  || fail "the API accepted a POST"

echo "8. the web UI needs an account and only answers for its host name"
WORK=$(mktemp -d)
docker run -d --name shop-notify-e2e-ui --network host --read-only --tmpfs /data:uid=1000,gid=1000 \
  --cap-drop ALL --security-opt no-new-privileges \
  -e UI_DOMAIN=localhost -e API_URL="http://127.0.0.1:$API_PORT" \
  -e PORT="$UI_PORT" -e ADDRESS_HEADER=x-forwarded-for -e XFF_DEPTH=1 "$UI_IMAGE" >/dev/null
wait_for 30 curl -sf http://127.0.0.1:$UI_PORT/healthz || fail "UI did not start"
[[ $(curl -s -H "Host: localhost" http://127.0.0.1:$UI_PORT/login) == *"No account has been set up yet"* ]] \
  || fail "no hint to create an account"
printf '%s\n' "$UI_PASSWORD" \
  | docker exec -i shop-notify-e2e-ui shop-notify-ui set-password admin --password-stdin >/dev/null \
  || fail "account not created"
[[ $(curl -s -o /dev/null -w '%{http_code}' -H "Host: evil.example" http://127.0.0.1:$UI_PORT/login) == 421 ]] \
  || fail "request for another host name answered"

# A test certificate for localhost at the path of the examples (after the host name changed)
mkdir -p "$WORK/certs"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
  -addext subjectAltName=DNS:localhost -keyout "$WORK/certs/privkey.pem" \
  -out "$WORK/certs/fullchain.pem" 2>/dev/null
chmod 644 "$WORK/certs/"*.pem
# example LANGUAGE – the code block of docs/web-ui.md, for localhost and the ports of the
# test instead of the placeholder and 80, 443 and 3000. nginx passes on the host name
# with the port ($http_host), as the browser's origin then has one.
example() {
  sed -n "/^\`\`\`$1\$/,/^\`\`\`\$/p" "$ROOT/docs/web-ui.md" | sed '1d;$d' | sed -E \
    -e 's/shop\.example\.com/localhost/g' -e "s/127\.0\.0\.1:3000/127.0.0.1:$UI_PORT/g" \
    -e "s/(listen (\[::\]:)?)443 /\1$HTTPS_PORT /; s/(listen (\[::\]:)?)80;/\1$HTTP_PORT;/" \
    -e "s/\*:443>/*:$HTTPS_PORT>/; s/\*:80>/*:$HTTP_PORT>/" \
    -e 's/(proxy_set_header Host )\$host;/\1$http_host;/'
}

UI=https://localhost:$HTTPS_PORT
# ui_flow PROXY – sign in through the proxy, read the pages, sign out
ui_flow() {
  local jar="$WORK/cookies" page="$WORK/page.html"
  rm -f "$jar"
  wait_for 30 curl -skf "$UI/healthz" || fail "$1: UI not reachable"
  [[ $(curl -sk -o /dev/null -w '%{http_code} %{redirect_url}' "$UI/events") \
    == "303 $UI/login?redirectTo=%2Fevents" ]] || fail "$1: not redirected to the login page"
  [[ $(curl -sk -o /dev/null -w '%{http_code}' -H 'Accept: text/html' -H "Origin: $UI" \
    --data 'username=admin&password=wrong' "$UI/login") == 400 ]] || fail "$1: wrong password accepted"
  [[ $(curl -sk -c "$jar" -o /dev/null -w '%{http_code}' -H 'Accept: text/html' -H "Origin: $UI" \
    --data-urlencode username=admin --data-urlencode "password=$UI_PASSWORD" "$UI/login") == 303 ]] \
    || fail "$1: sign-in failed"
  grep -q "__Host-session" "$jar" || fail "$1: no session cookie"
  curl -skf -b "$jar" "$UI/" > "$page" || fail "$1: overview not shown"
  grep -q "Last poll" "$page" || fail "$1: no last poll on the overview"
  grep -q "<script" "$page" && fail "$1: the page contains a script"
  curl -skf -b "$jar" "$UI/events?event=sold_out" | grep -q "Handmade test item 6" \
    || fail "$1: sold-out event not listed"
  curl -skf -b "$jar" "$UI/listings/1006" | grep -q "In the shop" \
    || fail "$1: restocked listing not shown as in the shop"
  curl -skf -b "$jar" "$UI/listings" > "$page" || fail "$1: listings not shown"
  grep -q 'class="card"' "$page" && grep -q "Handmade test item 6" "$page" \
    || fail "$1: no listing cards"
  grep -q 'aria-current="page">In the shop' "$page" || fail "$1: no status tabs"
  curl -skf -b "$jar" "$UI/listings?sort=price_asc" | grep -q '<option value="price_asc" selected' \
    || fail "$1: no sorting"
  curl -sk -b "$jar" -D - -o /dev/null "$UI/polls" | grep -qi "^content-security-policy: default-src 'none'" \
    || fail "$1: no Content-Security-Policy"
  [[ $(curl -sk -b "$jar" -o /dev/null -w '%{http_code}' -X POST -H "Origin: $UI" "$UI/logout") == 303 ]] \
    || fail "$1: sign-out failed"
  [[ $(curl -sk -b "$jar" -o /dev/null -w '%{http_code}' "$UI/") == 303 ]] \
    || fail "$1: still signed in after signing out"
  docker rm -f shop-notify-e2e-proxy >/dev/null
}

echo "9. the web UI behind Caddy"
docker run -d --name shop-notify-e2e-proxy --network host --read-only --tmpfs /data --tmpfs /config \
  --cap-drop ALL --cap-add NET_BIND_SERVICE --security-opt no-new-privileges \
  -e UI_DOMAIN=localhost -e UI_UPSTREAM="127.0.0.1:$UI_PORT" \
  -e HTTP_PORT="$HTTP_PORT" -e HTTPS_PORT="$HTTPS_PORT" \
  -e ACME_CA=https://acme-staging-v02.api.letsencrypt.org/directory \
  -v "$ROOT/Caddyfile:/etc/caddy/Caddyfile:ro" "$CADDY" >/dev/null
ui_flow Caddy

echo "10. the web UI behind nginx"
example nginx > "$WORK/nginx.conf"
docker run -d --name shop-notify-e2e-proxy --network host \
  -v "$WORK/nginx.conf:/etc/nginx/conf.d/default.conf:ro" \
  -v "$WORK/certs:/etc/letsencrypt/live/localhost:ro" "$NGINX" >/dev/null
ui_flow nginx

echo "11. the web UI behind Apache"
example apache > "$WORK/shop-notify.conf"
# what a2enmod does on Debian, for the official image
docker run --rm "$HTTPD" cat /usr/local/apache2/conf/httpd.conf \
  | sed -E -e 's/^#(LoadModule (ssl|socache_shmcb|proxy|proxy_http|headers)_module )/\1/' \
    -e "s/^Listen 80\$/Listen $HTTP_PORT/" > "$WORK/httpd.conf"
printf 'Listen %s\nInclude conf/extra/shop-notify.conf\n' "$HTTPS_PORT" >> "$WORK/httpd.conf"
docker run -d --name shop-notify-e2e-proxy --network host \
  -v "$WORK/httpd.conf:/usr/local/apache2/conf/httpd.conf:ro" \
  -v "$WORK/shop-notify.conf:/usr/local/apache2/conf/extra/shop-notify.conf:ro" \
  -v "$WORK/certs:/etc/letsencrypt/live/localhost:ro" "$HTTPD" >/dev/null
ui_flow Apache

echo "OK"
