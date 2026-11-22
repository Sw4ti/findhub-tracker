# Contributing

Thanks for helping improve Find Hub Tracker. The integration with Google Find Hub is unofficial and may stop working when Google changes its services.

## Before opening an issue or pull request

- Search existing issues and use the relevant GitHub issue template.
- Do not include passwords, tokens, cookies, TOTP material, device identifiers, personal device names, GPS coordinates/history, database files, backup archives, or screenshots from a real account.
- Reduce logs to the smallest useful excerpt and redact account, device, location, and authentication details.
- For bug reports, include OS/architecture, Docker Engine/Desktop and Compose versions, and reproducible synthetic steps.

## Development checks

1. Use a separate development installation with synthetic credentials and an isolated database.
2. Install frontend dependencies with `npm ci`.
3. Run `npm run build` and `node --test test_map_selection_summary.cjs`.
4. Run relevant Python tests using Python 3.11 or newer. PostgreSQL integration tests must use an isolated test database only.
5. Validate the Compose configuration with example values.
6. Run `python release_privacy_scan.py .` and inspect `git diff --check` before submitting.
7. Update documentation and `CHANGELOG.md` when behavior, setup, security, or compatibility changes.

## Pull requests

- Keep changes focused and explain the user impact, security considerations, tests, and known limitations.
- Include screenshots only when they contain synthetic information.
- Do not submit production credentials/data or changes that rely on private server configuration.
- The project license is pending maintainer approval. Do not add license headers or claim a project-wide license until the repository owner finalizes it.

Maintainers review and merge changes. No pull request automatically deploys to a personal Find Hub Tracker installation.
