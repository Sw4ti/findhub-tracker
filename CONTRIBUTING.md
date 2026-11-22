# Contributing

Thank you for contributing. The Google Find Hub integration is unofficial and may stop working when Google changes its services.

## Safe reports and changes

- Search existing issues and use the supplied issue templates.
- Never include passwords, tokens, cookies, TOTP material, device identifiers, personal device names, GPS coordinates/history, database files, backup archives, or real-account screenshots.
- Share only minimal, redacted logs. Include OS/architecture and Docker/Compose versions where relevant.
- Reproduce bugs with synthetic devices and an isolated database; do not test destructive behavior on a real tracker.

## Development checks

1. Use a separate installation, synthetic credentials, and isolated test data.
2. Install frontend dependencies with `npm ci`; run `npm run build` and `node --test test_map_selection_summary.cjs`.
3. Run relevant Python tests with Python 3.11 or newer. PostgreSQL tests must use an isolated database only.
4. Validate the Compose configuration with synthetic values; do not start unrelated projects.
5. Run `python release_privacy_scan.py .`, inspect `git diff --check`, and review every staged file.
6. Update relevant documentation and `CHANGELOG.md`.

## Pull requests

Keep changes focused. Describe user impact, tests, security considerations, and limitations. Screenshots must use synthetic data. No pull request automatically deploys to a personal installation.
