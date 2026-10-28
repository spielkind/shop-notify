# Web UI

A small web interface for the history that Shop Notify records: the last poll at a glance, the listings of the shop with their pictures (searchable and sortable by date, price, title or stock), the events of all listings with filters, the polls, and every listing with its state and history. It only shows data – changing filters or settings is not part of it yet.

- **Optional:** the poller and the command line work as before without it.
- **Signed in only:** one or more accounts, managed on the server with `shop-notify-ui`.
- **No scripts:** the pages are rendered on the server and contain no JavaScript; the Content-Security-Policy forbids scripts altogether.
- **Pictures from Etsy:** the browser loads the listing pictures directly from Etsy's image servers (`i.etsystatic.com`, without a referrer) – the only other site the pages contact. The poller looks up the picture of new and changed listings; for a shop it sees for the first time, they appear within a few polls.
- **Separated:** the UI runs in its own container and reads the history through the [read-only API](api.md), which only it can reach; it never opens the poller's database. Accounts and sessions are kept in the UI's own database.

```
Browser ──HTTPS──▶ Caddy, nginx or Apache ──▶ UI ──▶ API (read-only) ──▶ shop-notify.db
                   (TLS, Let's Encrypt)        :3000    internal network only
```

## Requirements

- Docker Compose with [`compose.yaml`](../compose.yaml) as in the [Docker guide](docker.md).
- A host name of its own for the UI, e.g. `shop.example.com`, whose DNS record points to the server. A path below another site (`example.com/shop/`) is not supported.
- Ports 80 and 443 reachable from the internet, so that Let's Encrypt can issue the certificate (port 80 is needed for it, also with your own proxy and certbot).

Choose how HTTPS is provided – **with Caddy**, a container that does it all, or **with nginx or Apache** if one already runs on the server.

## With Caddy

1. Download [`Caddyfile`](../Caddyfile) next to `compose.yaml`.
2. In `.env`:

   ```sh
   COMPOSE_PROFILES=caddy
   UI_DOMAIN=shop.example.com
   # first with Let's Encrypt's staging environment (the certificate isn't trusted yet)
   ACME_CA=https://acme-staging-v02.api.letsencrypt.org/directory
   ```

3. Start it and create an account – the password is asked for twice and not shown:

   ```sh
   mkdir -p ui-data && sudo chown 1000:1000 ui-data
   docker compose up -d
   docker compose exec ui shop-notify-ui set-password admin
   ```

4. Open `https://shop.example.com` – the browser warns about the staging certificate. If the page loads, remove `ACME_CA` from `.env` and run `docker compose up -d` again: Caddy then gets a trusted certificate and renews it on its own.

The certificates are kept in the volume `caddy-data`; deleting it means new certificates, and Let's Encrypt limits how many can be issued per week.

## With nginx or Apache

Run the UI without Caddy; it is then only reachable on `127.0.0.1:3000` of the server (`UI_PORT` changes the port):

```sh
COMPOSE_PROFILES=ui
UI_DOMAIN=shop.example.com
```

```sh
mkdir -p ui-data && sudo chown 1000:1000 ui-data
docker compose up -d
docker compose exec ui shop-notify-ui set-password admin
```

Then forward the host name to it. The proxy has to pass on the host name, the client address (`X-Forwarded-For`, used to limit failed sign-ins) and that the request came in over HTTPS. The UI sets its own security headers. Get the certificate e.g. with [certbot](https://certbot.eff.org/) (`certbot certonly --webroot` or the plugin for your server).

### nginx

```nginx
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name shop.example.com;

    ssl_certificate /etc/letsencrypt/live/shop.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/shop.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}

server {
    listen 80;
    listen [::]:80;
    server_name shop.example.com;
    return 301 https://$host$request_uri;
}
```

### Apache

Needs the modules `ssl`, `proxy`, `proxy_http` and `headers` (`a2enmod ssl proxy proxy_http headers` on Debian and Ubuntu). Apache adds `X-Forwarded-For` by itself.

```apache
<VirtualHost *:443>
    ServerName shop.example.com
    SSLEngine on
    SSLCertificateFile /etc/letsencrypt/live/shop.example.com/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/shop.example.com/privkey.pem

    ProxyPreserveHost On
    ProxyPass / http://127.0.0.1:3000/
    ProxyPassReverse / http://127.0.0.1:3000/
    RequestHeader set X-Forwarded-Proto "https"
</VirtualHost>

<VirtualHost *:80>
    ServerName shop.example.com
    Redirect permanent / https://shop.example.com/
</VirtualHost>
```

Both examples are tested as they are (with `localhost` and a test certificate) in the end-to-end test.

If there is more than one proxy in front of the UI that adds to `X-Forwarded-For` (e.g. a load balancer and nginx), set `UI_PROXY_DEPTH` to their number.

## Accounts

```sh
docker compose exec ui shop-notify-ui set-password admin     # create the account or change its password
docker compose exec ui shop-notify-ui list-users             # the accounts and their active sessions
docker compose exec ui shop-notify-ui logout-all admin       # end all sessions of an account
echo "$PASSWORD" | docker compose exec -T ui shop-notify-ui set-password admin --password-stdin
```

- Only who can run commands on the server can manage accounts – there is no sign-up or password reset by e-mail; a forgotten password is simply set again.
- A password needs at least 12 characters. It is stored as a [scrypt](https://en.wikipedia.org/wiki/Scrypt) hash; changing it ends all sessions of the account.
- A sign-in lasts `UI_SESSION_DAYS` (30) days, or until signing out. After 5 failed attempts for an account (or 20 from one address) within 15 minutes, signing in is blocked for the rest of that time.

## Settings

In `.env`; only the UI and Caddy get them – the UI doesn't see the Etsy credentials or the notification targets.

| Variable | Default | Meaning |
|---|---|---|
| `COMPOSE_PROFILES` | – | `caddy` for the UI with Caddy, `ui` for the UI behind your own proxy |
| `UI_DOMAIN` | – | Host name of the UI; requests for other names are rejected |
| `UI_PORT` | `3000` | Port on `127.0.0.1` for your own proxy |
| `UI_SESSION_DAYS` | `30` | Days a sign-in lasts (1–365) |
| `UI_LANGUAGE` | `en` | Language of the UI |
| `TZ` | `UTC` | Time zone of the times shown |
| `UI_PROXY_DEPTH` | `1` | Proxies in front of the UI that add to `X-Forwarded-For` |
| `ACME_CA` | Let's Encrypt | Certificate authority of Caddy, e.g. the staging environment while trying it out |

## Backups and updates

- Back up `ui-data/` (accounts and sessions) along with `data/`. Without it, the accounts have to be created again – the history itself is in `data/`.
- Update everything together: `docker compose pull && docker compose up -d`. The UI and the poller have the same version number; the UI only works with the API of the same release.
- To turn the UI off, remove `COMPOSE_PROFILES` from `.env` and run `docker compose up -d --remove-orphans`.

## Languages

All texts are in [`ui/src/lib/i18n/`](../ui/src/lib/i18n/): `types.ts` defines every text, `en.ts` has the English ones. To add a language, copy `en.ts` (e.g. to `de.ts`), translate it including `locale`, and add it to `LANGUAGES` in `index.ts`; the type check reports any text that is missing. Then set `UI_LANGUAGE`.

## Troubleshooting

| Problem | Solution |
|---|---|
| `This server only answers for …` (421) | The proxy doesn't pass on the host name (`proxy_set_header Host $host`, `ProxyPreserveHost On`), or `UI_DOMAIN` doesn't match the name in the browser |
| `403 Cross-site … forbidden` when signing in | The proxy doesn't pass on the host name, or the page was opened under another name than `UI_DOMAIN` |
| "The history is not available right now" | The API isn't running or the poller is upgrading its database – `docker compose ps`, `docker compose logs api` |
| Caddy gets no certificate | Check that `UI_DOMAIN` points to the server and ports 80 and 443 are open: `docker compose logs caddy` |
| No account set up yet | `docker compose exec ui shop-notify-ui set-password admin` |
