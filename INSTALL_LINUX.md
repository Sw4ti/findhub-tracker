# Install on Linux

## Prerequisites

- An x86_64/amd64 Linux system with Docker Engine, Compose v2 plugin, BuildKit and Python 3.11+. ARM64 is not validated in this release candidate; setup fails closed on unverified engine architectures.
- At least 4 CPU cores, 5 GB RAM available to Docker and 10 GB free disk. Collector builds require internet access and the browser runtime is memory intensive. The setup preflight accepts x86_64/amd64 only; ARM64 is not verified.
- The account must be permitted to use Docker. Docker-group membership is effectively root; use the host's normal secure Docker administration policy.

## Fresh installation

1. Extract the ZIP into a new user-owned directory, for example `~/findhub-tracker`.
2. From that directory run `docker info` and `docker compose version`.
3. Run `sh ./setup.sh`. It creates `.env` with unique random secrets, builds the pinned app and collector, prompts for a dashboard password without echo, and starts only this Compose project. The dashboard password/hash are not printed.
4. Open `http://127.0.0.1:8080` and sign in with the password you chose.
5. Run `python3 portable_setup.py collector-login` in a local terminal to show the generated collector password, then open `http://127.0.0.1:4321/auth`. Sign in using HTTP Basic username `tracker` and that password.
6. In collector Config choose **Sign in with Google** and authenticate your own Google account. Complete Google's encrypted-location confirmation when prompted. Verify your devices appear before configuring forwarding.
7. Configure the collector forwarding endpoint as described in `CONFIGURATION.md`.

The dashboard and collector bind to `127.0.0.1` by default. No firewall or router rule is changed. Use SSH port forwarding to reach a remote host; do not bind either raw port publicly.

## Manage and uninstall

Use `docker compose ps`, `docker compose logs --tail=100 app`, `docker compose stop`. `docker compose down` removes only containers/network in this project and preserves this project's named volumes. Export/verify a backup before any uninstall. Data removal is separate: inspect `docker volume ls` and remove only volumes bearing this exact project label after a verified export. Never run system-wide prune commands for this app.
