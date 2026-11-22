# Configuration and Google account setup

## Collector privacy and crowdsourced reports

`EID_UPLOAD` defaults to `off`. The pinned upstream collector documentation states that uploads of rotating encrypted identifiers contribute to Google's crowdsourced location network. Leaving this disabled is the privacy-preserving choice, but can reduce or stop crowdsourced location reports after a period of use. Review the pinned upstream collector documentation and your own privacy preference before setting `EID_UPLOAD=on` in the local `.env`; this changes what the collector shares with Google. The portable package never includes an account or turns this on silently.

## Instance isolation

`portable_setup.py init` creates a fresh `.env`. Never reuse another installation's `.env`, Google state, dashboard password or encryption key. `COMPOSE_PROJECT_NAME` scopes containers, networks and named volumes. Change dashboard/collector ports if occupied. Keep the project directory private; `.env` contains database, session, collector and ingestion secrets.

The display timezone starts at UTC to avoid inheriting the original operator's locale. Set `DISPLAY_TZ` in `.env` to the owner's IANA timezone (for example `Australia/Sydney`) before use; the dashboard formats timestamps using the API's configured zone while the database retains UTC.

Default bindings are `127.0.0.1:8080` (dashboard) and `127.0.0.1:4321` (collector). PostgreSQL and device discovery have no host-published ports. Do not change the collector/dashboard bindings to `0.0.0.0` for convenience.

The Compose `dashboard` and `database` networks are internal-only. The collector alone has an additional egress network for Google. The app and optional Caddy share a project-scoped bridge network so loopback host-port forwarding and the reverse proxy work; only the app and Caddy join it, and the dashboard host binding remains loopback-only by default. The status reader and password initializer have no network.

## Dashboard login

The first-run `auth-setup` service uses Argon2id. It reads the password through hidden terminal input and writes the hash into a project-specific Docker volume owned by the app's unprivileged UID. Set at least 14 characters and use a unique password. Password rotation: `docker compose run --rm auth-setup --rotate`; this does not change the collector's separate Basic password. Remembered dashboard sessions are finite (30 days if selected); ordinary sessions expire sooner.

## Google authentication and collection

1. Open `http://127.0.0.1:4321/auth`. The browser challenge is the collector's local HTTP Basic authentication, independent of dashboard login.
2. Use the generated local collector credentials (`python portable_setup.py collector-login`) and go to Config → Google account → **Sign in with Google**.
3. Sign in using the friend's own Google account. Complete Google prompts and the encrypted location-report authorization. The project does not accept or bundle someone else's session cookies/tokens.
4. Verify account status and devices in the collector. The discovery service refreshes the device inventory every 30 minutes. Configure the collector's forwarding UI to POST reports to `http://app:8080/api/ingest` with `Authorization: Bearer <INGEST_KEY>` and the JSON fields described in the upstream collector's pinned README. Keep the collector's throttling enabled and only forward devices the owner chooses.
5. Check the dashboard for real reports. Do not treat demo/synthetic data as Google verification.

The integration is unofficial and not a Google API/SDK. It depends on the pinned upstream implementation, embedded browser, Google account policies and encrypted-report behavior. Google may change or block the flow. Reauthentication/verification may be required. Earbuds and non-location device categories may not be supported by the pinned integration.

## Optional HTTPS with Caddy

The optional `compose.https.yaml` adds a separate Caddy container scoped to this Compose project. It does not alter any other proxy or application. Set a DNS hostname you control in `TRACKER_DOMAIN`; Caddy obtains a certificate if DNS and inbound TCP 80/443 are publicly reachable. Initially leave `HTTPS_BIND_IP=127.0.0.1` to keep HTTPS host ports local. For LAN use, set this only to the server's LAN address and restrict access with the host firewall; do not expose the service to the public internet without reviewing your threat model. Do not configure router port forwarding unless you intentionally want public access. The dashboard uses password-only login (no Authelia/TOTP); TLS does not add MFA.

Start optional HTTPS explicitly with `docker compose -f compose.yaml -f compose.https.yaml --profile https up -d --build --wait`. Validate the merged configuration first with the same `-f` arguments and `config -q`. The app raw port remains loopback-bound; collector remains loopback-bound. A browser-facing Caddy endpoint is the only configured external route, and it proxies only to the dashboard.

## Update procedure

1. Read the new version's release notes, source commit and third-party licenses. Keep this installation's directory and secrets out of the download/archive scan.
2. Run and verify `python portable_backup.py backup`.
3. Extract the reviewed package into a separate candidate directory and compare its published SHA-256.
4. From the installation directory, validate `docker compose config -q`, then build the reviewed app/collector candidates.
5. Apply only after reviewing changes and taking a recoverable snapshot. Compose update commands are project-scoped; don't use global Docker cleanup.
6. Verify login, health, report forwarding and history. Preserve the prior image/package and backup until acceptance.
# Collector privacy and crowdsourced reports

`EID_UPLOAD` defaults to `off`. The collector's upstream documentation states that uploads of rotating encrypted identifiers contribute to Google's crowdsourced location network. Leaving this disabled is the privacy-preserving choice, but can reduce or stop crowdsourced location reports after a period of use. Review the pinned upstream collector documentation and your own privacy preference before setting `EID_UPLOAD=on` in the local `.env`; this changes what the collector shares with Google. The portable package never includes an account or turns this on silently.
