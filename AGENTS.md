# Repository instructions for contributors and Codex

## Before changing files
- Read this file, `README.md`, `CHANGELOG.md`, and relevant security/configuration documentation.
- Treat this as an independently installed project. Never copy a live installation or production workspace into the repository.
- Keep passwords, hashes, Google session state, device identifiers, GPS history, database files, backup archives, SSH keys, and personal infrastructure details out of source, tests, screenshots, and Git history.
- Use synthetic names, dates, coordinates, and credentials in tests and screenshots. Keep `.env.example` placeholder-only; never commit `.env`.

## Development checks
- Host setup tools require Python 3.11 or newer; use pinned dependency manifests.
- Run `npm ci`, `npm run build`, `node --test test_map_selection_summary.cjs`, and relevant Python tests.
- Run PostgreSQL integration tests only against an isolated test database/Compose project with synthetic data and credentials.
- Validate Compose using example values; do not start, stop, or change another user's Docker project.
- Run `python release_privacy_scan.py .`, `git diff --cached --check`, and inspect the full staged path list/diff before every commit.
- Update documentation and `CHANGELOG.md` for functional, setup, security, or compatibility changes.

## Security and project boundaries
- Google Find Hub integration is unofficial. Never bypass Google authentication, encrypted-location confirmation, or rate limits.
- Do not weaken session, CSRF, collector bearer authentication, owner authorization, backup, or deletion safeguards.
- Keep third-party copyrights, SPDX notices, license texts, and attribution intact. Original first-party code is GPL-3.0-only; third-party components retain their own terms.
- Do not add dependencies, ports, or privileged container access without documenting rationale and security impact.

## GitHub publishing
- Confirm `gh auth status`, account, and repository before GitHub operations.
- Never push, publish a release, force-push, delete branches, or trigger deployments without explicit owner authorization.
- Prefer feature branches and pull requests for future changes. GitHub changes must never auto-deploy to a personal production Mini PC.
- Before committing: run relevant tests and privacy scan, inspect staged paths and diff, and use a focused commit message.
