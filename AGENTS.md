# Repository instructions for Codex and contributors

## Before changing files
- Read this file, `README.md`, `CHANGELOG.md`, and the relevant design/security documentation.
- Treat this repository as an independently installable project. Never copy files from a live installation or production workspace.
- Keep credentials, password hashes, Google session state, device identifiers, location history, database files, backup archives, SSH keys, and personal infrastructure details out of source, tests, screenshots, and Git history.
- Use synthetic devices, names, dates, coordinates, and credentials in tests and screenshots.
- Keep `.env.example` limited to placeholders. Do not commit `.env` files.

## Development and testing
- Use the pinned dependency lockfiles. Python host setup helpers require Python 3.11 or newer; containers use their pinned base images.
- Run `npm ci`, `npm run build`, `node --test test_map_selection_summary.cjs`, and the documented Python tests relevant to your changes.
- Run PostgreSQL integration tests only against an isolated test database/Compose project with synthetic credentials and data. Never point tests at a user's installation.
- Validate Compose files with example-only environment values. Do not start, stop, or reconfigure another user's Docker projects.
- Run `python release_privacy_scan.py .` before staging. Review the full `git diff --cached --check` and staged file list before committing.
- Document tests, known limitations, configuration changes, and security impact in the relevant documentation and `CHANGELOG.md`.

## Security and project boundaries
- Google Find Hub support is unofficial. Do not bypass Google authentication, encrypted-location confirmation, or rate limits.
- Do not weaken session, CSRF, collector bearer authentication, owner authorization, backup, or device deletion protections.
- Never include real account material, live GPS data, production configuration, or private server details in examples.
- Do not add dependencies or expose network ports without documenting the reason and threat impact.

## GitHub publishing
- Check `gh auth status` and confirm the account and target repository before GitHub operations.
- The repository-wide license is pending maintainer approval. Do not add or infer a `LICENSE` until the owner approves the license and copyright notice.
- Do not push, publish a release, force-push, delete branches, or trigger deployment unless the owner explicitly approves that exact action.
- Prefer a feature branch and pull request for future changes. Never configure GitHub changes to auto-deploy to a personal production Mini PC.
- Before every commit, run tests, the privacy scan, `git diff --cached --check`, and inspect every staged path and diff. Use a concise, meaningful commit message.
