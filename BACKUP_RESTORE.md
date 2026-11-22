# Backup and restore

## Contents and protection

Backups include the PostgreSQL custom-format dump, collector `/data` state (Google auth/encrypted state and device configuration), device-deletion recovery checkpoints, the Argon2id dashboard password hash and the installation `.env` (database/session/collector encryption credentials). They contain highly sensitive account and location information. The script stores snapshots outside Docker volumes, applies owner-only permissions/ACLs, verifies a SHA-256 manifest and ZIP CRC, prevents overlapping backups with an exclusive lock, and retains the 14 newest verified snapshots. Never attach a backup to chat or email. Protect the disk with BitLocker or Linux full-disk encryption; encrypt off-host copies with a tool such as age.

## Create and verify

From the installation directory:

```text
python portable_backup.py backup
python portable_backup.py verify backups/<snapshot-name>.zip
```

If this installation was started with multiple Compose files (for example the optional HTTPS overlay), run the backup with the same file set so Compose does not try to reconcile networks or services against a partial configuration. Set `PORTABLE_COMPOSE_FILES` using the platform's path separator (`:` on Linux/macOS, `;` on Windows), for example `PORTABLE_COMPOSE_FILES=compose.yaml:compose.https.yaml` on Linux. The restore intentionally starts only the fresh project's database, dashboard and status reader from the base file; collector and discovery stay stopped.

Linux users may use `python3`. Keep backups outside Docker volumes and preferably copy encrypted backups to a separate drive/device. Schedule the same command with Windows Task Scheduler or a user-owned systemd timer if desired; run it only when the Compose services are healthy. The script's lock rejects overlap.

## Restore without overwriting a live installation

Restore always uses a **new** extracted package directory, a new Compose project name, new host ports, and fresh project-named volumes. It never targets the existing installation's volume names. Keep the currently active Google collector stopped while validating the recovery copy; do not operate two live collectors for the same account.

1. Extract a reviewed copy of the same compatible package into a new directory, for example `~/findhub-restore` or `%USERPROFILE%\FindHubRestore`.
2. Ensure it has no `.env`; choose a never-used project name and unused local ports.
3. Run from the original installation directory:

```text
python portable_backup.py verify backups/<snapshot-name>.zip
python portable_backup.py restore backups/<snapshot-name>.zip --target <new-package-directory> --project findhub-restore --dashboard-port 18080 --collector-port 14321
```

The restore creates only the selected project's new database, collector, status, auth and checkpoint volumes; restores the dump, collector state, deletion checkpoints and hash; and starts only the database and dashboard. It deliberately leaves the collector, discovery and status reader stopped. The status reader is not started because it reports unhealthy while its collector is intentionally stopped. Review history and login on `http://127.0.0.1:18080` before considering any cutover. Collector-state restore includes Google authentication state and must be treated as credential transfer. A restored project must not poll while the original collector is active.

If restore fails, preserve the named recovery project for diagnosis. Do not use `down -v` as a generic fix. Inspect only containers/volumes whose Compose project label exactly matches the chosen recovery project. The original installation is unaffected.
