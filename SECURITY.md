# Security and privacy

## Defaults

- Every install creates distinct random database, session, ingestion, collector HTTP Basic and collector encryption secrets. The ZIP contains none of them.
- Dashboard password is chosen interactively and stored only as Argon2id in a project-specific volume, readable by the non-root app UID. The setup helper never echoes password/hash.
- Dashboard sessions are server-side, revocable, CSRF-protected, HttpOnly/SameSite cookies with finite lifetime. The 30-day option is persistent, not permanent.
- Dashboard/API and collector host ports bind to loopback. PostgreSQL, discovery and status have no host-published ports. Dashboard/database networks are internal; the app and optional Caddy use a project-scoped bridge for host-port forwarding/proxying, and only the collector has its separate Google egress network. Each Compose project has uniquely scoped volumes/networks.
- App/discovery containers drop Linux capabilities, run unprivileged where possible, use read-only root filesystems and bounded logs; PostgreSQL/collector restart unless stopped.
- Health endpoints reveal no device or location data. Backups cover database, collector authentication/state, deletion recovery checkpoints, password hash and secrets; they are outside Docker volumes, checksummed, locked against overlap and restricted to the local owner.

## Personal data and external services

The tracker stores precise location history locally. Collector state includes Google authentication/encryption state; protect the host, `.env`, Docker volumes and backups as credentials. Never send these files to support or include them in the release ZIP. Browser map tiles are fetched from OpenStreetMap tile infrastructure, which can observe requested map areas; consult its current tile usage policy before changing traffic volume.

This community integration is unofficial, not supported or endorsed by Google, and relies on private/reverse-engineered behavior in the pinned project. Google can change or restrict access. Use only an account and devices you are authorized to access, follow Google terms, and do not attempt to bypass account verification or end-to-end encryption.

## Remote access

The default is local-only. The optional Caddy profile provides TLS, not a second authentication factor. The dashboard uses a single password, with rate limiting; it does not provide Authelia/TOTP. Prefer a VPN/private access method. Do not publish the dashboard, collector configuration or database directly to the internet. Do not port-forward 4321 or 5432. If enabling LAN/Internet Caddy access, configure host firewall rules yourself, validate DNS/certificate routing, use a unique high-entropy password, and understand that password-only access protects sensitive location history.

## Release contents

Review the ZIP inventory and `.sha256` sidecar before sharing. The automated scan must reject `.env`, secrets, credentials, tokens, database/backup archives, collector `devices.yaml`/`auth.yaml`, private keys, production domains/IPs, personal device names/IDs and coordinates. Build/test outputs and local screenshots are not release content.

## Incident response

If the host or a backup is exposed, stop the affected project, preserve evidence, rotate the tracker password, collector Basic password, session key, ingestion key and collector encryption key only with a planned authentication-state backup/recovery procedure. Revoke the Google session in the collector/Google account. Do not delete the database volume as a response shortcut.
