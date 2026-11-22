# Troubleshooting

Run commands from the extracted installation directory. Do not post raw logs or config files publicly; redact URLs containing credentials, device IDs, coordinates, cookies and account names.

## Docker/Compose unavailable

- `docker info` must succeed and `docker compose version` must report v2.
- On Windows, switch Docker Desktop to Linux containers and ensure its engine is running.
- Check available Docker Desktop memory; the collector embeds Chrome and needs more memory than the dashboard.

## Port already in use

Change `DASHBOARD_PORT` or `COLLECTOR_PORT` in the local `.env` to an unused local port, then run `docker compose up -d --no-deps app collector`. Keep both host addresses loopback-only.

## Dashboard not healthy or login not configured

- `docker compose ps` reports per-service health.
- Check sanitized recent logs with `docker compose logs --tail=80 app db`.
- If password initialization failed, rerun `docker compose run --rm auth-setup`; the helper will not overwrite an existing hash unless `--rotate` is provided.
- Do not remove the auth/database volumes to troubleshoot.

## Collector login / Google sign-in

- The collector login is HTTP Basic (`tracker` plus the separate generated local collector password), not Google authentication. Run `python portable_setup.py collector-login` in a private local terminal to view it.
- Once inside, use the collector's Config → Google account → Sign in with Google flow. Complete account verification and encrypted location authorization. The Google sign-in is hosted by the pinned upstream integration, not the tracker dashboard.
- If Google blocks sign-in, encrypted reports are unavailable, or the embedded browser fails, do not bypass Google's controls. Check `docker compose logs --tail=80 collector`; redact account details and tokens before sharing logs. Review the pinned upstream README/issues.
- Discovery errors may mean authentication/authorization is pending, the device category is unsupported, or Google returned no location. The dashboard never fabricates live locations.

## No map detections

Confirm a device is visible on the collector's Devices page, forwarding is enabled for it, and forwarding targets `http://app:8080/api/ingest` with the local `INGEST_KEY`. Do not copy credentials from another installation. Synthetic demo mode is opt-in and clearly identified.

## Backup or restore error

The backup tool reports failure type/stage without printing credentials. Verify service health and free disk, then run `verify` on any completed archive. A stale `.backup.lock` is not removed automatically; verify no backup is running before an operator removes only this project's lock file. Failed restores leave their isolated project for inspection; never point restore at the active project.
