# Find Hub Tracker 0.1.0-rc1 — release candidate

This is a source/build package candidate for independent self-hosted installations. It is not a hosted service and does not include account credentials, device registrations, or history.

## Included

- Docker Compose dashboard, PostgreSQL, collector, discovery, and status services.
- Windows x64 and Linux x86_64 setup helpers.
- Fresh per-installation secret generation and interactive Argon2id password initialization.
- Independent backup/restore commands and required user guides.
- Synthetic-only tests and screenshots.

## Important limitations

- The Google integration is unofficial and may stop working if Google changes authentication, encrypted-location, or report interfaces.
- Google sign-in and encrypted-location confirmation must be completed by the user for their own account.
- ARM64 is not supported by this candidate.
- No project-wide license has been finalized. Do not redistribute until the maintainer approves the license.
- Windows installation was not validated on a working Docker Desktop engine for this candidate.

## Checksum

Verify the downloaded ZIP against the SHA-256 sidecar provided with the release. Do not use a ZIP whose checksum differs.