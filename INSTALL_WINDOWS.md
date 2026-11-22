# Install on Windows

## Prerequisites

- Windows 10/11 x64 and an x86_64/amd64 Docker Linux engine. ARM64 is not validated in this candidate.
- Docker Desktop using **Linux containers** and Docker Compose v2 (`docker compose version`). Allocate at least 4 CPU cores, 5 GB RAM and 10 GB free disk to Docker Desktop. The collector includes a browser runtime.
- Python 3.11 or newer on `PATH`.
- Internet access during image builds and Google sign-in.

## Fresh installation

1. Extract this ZIP to a new directory, for example `%USERPROFILE%\FindHubTracker`.
2. Open PowerShell in that directory. Confirm `docker info` succeeds and `docker compose version` reports Compose v2.
3. Run `Set-ExecutionPolicy -Scope Process Bypass` only if your current PowerShell policy blocks the local script; this affects this PowerShell process only.
4. Run `./setup.ps1`. It generates a new private `.env`, builds the pinned application/collector images, asks for a unique dashboard password in hidden input, and starts only this Compose project. Password input and hashes are not displayed.
5. Open `http://127.0.0.1:8080` and sign in with the dashboard password you chose.
6. To access collector setup, run `python .\portable_setup.py collector-login` in this local PowerShell window, then open `http://127.0.0.1:4321/auth`. Use HTTP Basic username `tracker` and the displayed local collector password. Do not paste it in a URL, chat or screenshot.
7. In collector Config, use its **Sign in with Google** control and authenticate the Google account that owns the friend's devices. Complete Google's encrypted-location confirmation if requested. Check that the collector reports the account and devices before enabling forwarding.
8. Configure forwarding as described in `CONFIGURATION.md`. Initially use a synthetic/demo report if you want to validate the dashboard before enabling real-device forwarding.

The dashboard and collector ports bind to loopback only. Other computers on the LAN cannot connect by default. For remote use, prefer a private VPN or configure the optional HTTPS proxy after reviewing its firewall and TLS requirements.

## Manage

From this folder: `docker compose ps`, `docker compose logs --tail=100 app`, and `docker compose stop`. `docker compose down` removes only this project's containers and network; it preserves this project's named volumes. Never add `-v` unless intentionally uninstalling and after exporting a verified backup.
