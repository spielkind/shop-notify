# Security

This document describes what Shop Notify protects, what it does not protect, and why we
believe its security requirements are met (assurance case). How to report a
vulnerability is described in [SECURITY.md](../SECURITY.md); the components and data
flows are described in [architecture.md](architecture.md).

## Security requirements

Shop Notify handles no payments. Its assets are the **credentials in its configuration**,
the **integrity of the notifications** and – with the optional [web UI](web-ui.md) – the
**accounts of the UI** and the **history it shows**. It must:

1. **Keep credentials confidential**: the Etsy API key and shared secret and the
   passwords and tokens in notification URLs must not appear in logs, notifications,
   images, the repository or error messages.
2. **Treat shop content as untrusted**: titles, URLs and image URLs from the Etsy API
   must not lead to code execution, injection into the database or HTML/markup injection
   into notifications, and a malformed response must not stop the service.
3. **Only talk to the configured services over authenticated channels**: Etsy and
   images via HTTPS with certificate verification; notification services as configured
   by the operator.
4. **Run with least privilege**: no root and no write access outside its data directory.
   The poller listens on no port; the optional [read-only API](api.md) only reads the
   database, needs no credentials and is not reachable from other machines by default.
5. **Deliver unmodified software**: releases and images must be traceable to the
   source code in this repository and verifiable by users.
6. **Show the history to signed-in users only** (web UI): passwords are only stored as
   hashes and never logged, sessions can't be guessed or read by scripts, guessing
   passwords is slowed down, and shop content can't inject scripts into the pages.

## What is not protected

Users should be aware of these limits:

- **Anyone with access to the host or the container** can read `.env`, `notify.yml`
  and the database. Protect these files with file permissions (the systemd unit uses
  `UMask=0077` and a private state directory); Shop Notify does not encrypt them.
- **Notification services see the content of the notifications.** Some services are
  public by design: an ntfy topic on `ntfy.sh` without an account can be read by anyone
  who guesses its name – use a long, random topic or your own server.
- **Notification transport security depends on the URL.** Apprise uses TLS where the
  service supports it; an SMTP URL with `mode=insecure` or a plain `http://` webhook
  sends data unencrypted. The operator chooses the targets.
- **Content of listings is shown as is.** A shop owner can put misleading text or links
  in a listing; Shop Notify escapes it but cannot judge whether it is trustworthy.
- **The optional API has no authentication.** Anyone who can reach it can read the
  history – titles, prices and poll errors. It listens on `127.0.0.1` by default and
  publishes no port in `compose.yaml`; to reach it from elsewhere, put a reverse proxy
  with authentication in front of it.
- **All accounts of the web UI see everything.** There are no roles; every account
  can read the whole history. Accounts are managed by whoever can run commands on the
  server.
- **The web UI relies on the proxy in front of it** for TLS, for passing on the host
  name and for an honest `X-Forwarded-For`: if the UI is reached without that proxy,
  the client address used to limit failed sign-ins can be spoofed (the limit per
  account still applies). The limit is kept in memory and starts afresh when the UI
  restarts.
- **Availability depends on Etsy.** If the API is unavailable or the quota is used up,
  notifications are delayed; a failed delivery is retried with the next poll.
- **No protection against a compromised Etsy API, notification service or Python
  package index** beyond TLS and the pinned, hash-checked dependencies.

## Threat model

| Threat | Entry point | Mitigation |
|---|---|---|
| Credentials leak through logs or bug reports | log output, `--help`, exceptions | `SecretStr` in the settings; notification targets are logged with Apprise's privacy mode (passwords masked); the Etsy key is only sent as an HTTP header |
| Credentials committed to git | developer workstation | `.env` and `notify.yml` in `.gitignore`; gitleaks in pre-commit and CI with rules for notification URLs; GitHub secret scanning with push protection |
| Credentials baked into the image | Docker build | `.dockerignore`; configuration only via `env_file` and mounted files at runtime |
| HTML injection via listing titles | Etsy API → e-mail | Jinja2 autoescaping for HTML templates, `StrictUndefined` |
| SQL injection | Etsy API → SQLite | parameterised queries only |
| Reading or changing data through the API | optional API | disabled by default; listens on `127.0.0.1`, no published port in `compose.yaml`; only `GET`; the database is opened read-only (`mode=ro`) for every request; the API process gets no `.env` and no credentials |
| Malicious API requests | optional API | parameters validated with pydantic (unknown or repeated ones rejected, at most 20, `limit` ≤ 500, search text ≤ 200 characters); parameterised queries, LIKE wildcards escaped; request timeout; unexpected errors only logged, not returned; container memory, CPU and PID limits |
| Slow regular expressions (ReDoS) | filters | patterns come from the operator, titles are at most 140 characters; invalid patterns are rejected at startup |
| Guessing passwords | web UI sign-in | at least 12 characters; scrypt hashes (N=2^15, r=8, p=3); at most 5 failed sign-ins per account and 20 per client address in 15 minutes; the same answer and about the same time for unknown accounts |
| Stolen or forged sessions | web UI | 256-bit random tokens, stored only as SHA-256 hashes; `__Host-` cookie with `HttpOnly`, `Secure` and `SameSite=Lax`; expire after `UI_SESSION_DAYS`; ended by signing out, changing the password or `logout-all` |
| Script injection (XSS) via listing titles, URLs or images | Etsy API → web UI | Svelte escapes all output; no scripts at all (server-side rendering only, CSP `script-src 'none'`, `default-src 'none'`); links and images only to Etsy over HTTPS |
| Cross-site requests (CSRF), clickjacking | web UI | SvelteKit's origin check for every form; `SameSite=Lax`; `frame-ancestors 'none'` and `X-Frame-Options: DENY` |
| Redirect to another site after sign-in | web UI | only local paths are accepted as target |
| Requests for other host names (DNS rebinding, misconfigured proxy) | web UI | rejected with 421 unless the host is `UI_DOMAIN` |
| Account takeover | web UI | no sign-up and no password reset over the web; accounts only via `shop-notify-ui` on the server |
| Malformed or hostile API responses | Etsy API | pydantic validation of every item; invalid items are skipped and logged; timeouts and bounded retries |
| Man-in-the-middle | network | HTTPS with certificate verification (httpx defaults, not configurable); the default Etsy endpoint is HTTPS |
| Privilege escalation after a compromise | runtime | non-root container user, no pip/build tools in the image; sandboxed systemd unit (`NoNewPrivileges`, `ProtectSystem=strict`, system call filter, no capabilities) |
| Vulnerable dependencies | supply chain | `uv.lock` with hashes, Dependabot (with 7-day cooldown), pip-audit in CI, Trivy scans of every image and weekly scans of the released image |
| Malicious or compromised CI actions | supply chain | actions pinned by commit SHA (enforced by repository setting), read-only default token, minimal job permissions, zizmor and actionlint |
| Tampered releases | distribution | signed tags that must point to `main`; immutable GitHub releases; SBOM and signed build provenance (Sigstore) for packages and images |
| Unauthorised changes to `main` | repository | ruleset: pull requests with required checks, signed commits, no force pushes or deletion |

## Assurance case

**Claim:** Shop Notify meets the security requirements above.

**Arguments and evidence:**

1. **Small attack surface.** The poller opens no ports, accepts no inbound
   connections and has a single untrusted input (Etsy API responses and images).
   Everything else is configuration supplied by the operator. The optional API is a
   separate process that is disabled by default, only reads, holds no credentials and
   validates every request; the optional web UI is a separate container that sends no
   scripts, needs a session for everything but the sign-in and reaches the API over an
   internal network only.
   *Evidence:* [architecture.md](architecture.md#external-interfaces-and-trust-boundaries).
2. **Untrusted input is validated and encoded at the boundaries** (requirement 2).
   Responses are parsed into pydantic models item by item; data reaches SQLite only
   through parameterised queries and HTML only through autoescaped templates; no input
   is passed to a shell, `eval` or the file system.
   *Evidence:* `etsy.py`, `db.py`, `notifications.py`; tests for invalid responses,
   escaping and property-based tests with Hypothesis.
3. **Secrets are typed and kept out of outputs** (requirement 1). *Evidence:*
   `SecretStr` in `config.py`, tests that check that the log does not contain secrets,
   gitleaks and GitHub push protection.
4. **Secure defaults and least privilege** (requirements 3 and 4). TLS certificate
   verification is always on; the container runs as `1000:1000`; the systemd unit is
   sandboxed. *Evidence:* `Dockerfile`, CI smoke test (`id -u` = 1000),
   `contrib/systemd/shop-notify.service`.
5. **Common weaknesses are addressed by tooling** (CWE/OWASP classes such as injection,
   hard-coded credentials, vulnerable components). *Evidence:* CodeQL, ruff, mypy
   strict, gitleaks, pip-audit, Trivy, zizmor, hadolint – all run on
   every pull request and must pass.
6. **Verified supply chain** (requirement 5). *Evidence:* pinned dependencies and base
   images, signed tags, immutable releases, SBOM and provenance attestations
   (`gh attestation verify`), OpenSSF Scorecard.
7. **Behaviour is tested end to end.** 100 % line and branch coverage (Python and the
   UI's server code) plus an end-to-end test of the Docker images against a fake Etsy
   API and a mail server, including signing in to the web UI behind Caddy, nginx and
   Apache (requirement 6).

**Design principles applied** (Saltzer and Schroeder): economy of mechanism (one
process, few dependencies), fail-safe defaults (TLS verification, silent first run,
no notification on doubt), least privilege, complete mediation of untrusted input at
the API boundary, separation of configuration from code, and open design (all security
measures are public in this repository).

**Residual risks** are listed under [What is not protected](#what-is-not-protected).
