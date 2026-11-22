# Find Hub Tracker 0.1.0-rc1 — prerelease candidate

SPDX-License-Identifier: GPL-3.0-only
Copyright (C) 2026 Waleed Khan

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
- Original Find Hub Tracker code is licensed under GPL-3.0-only. Third-party components retain their own notices and licenses; see `THIRD_PARTY_LICENSES.md`.
- Windows installation was not validated on a working Docker Desktop engine for this candidate.

## Checksum

Verify the downloaded ZIP against the SHA-256 sidecar provided with the release. Do not use a ZIP whose checksum differs.
