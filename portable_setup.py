"""Create a fresh local .env for an independent Find Hub Tracker installation."""
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Waleed Khan

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def validate_architecture(value: str) -> None:
    if value.strip().lower() not in ("linux/amd64", "linux/x86_64"):
        raise RuntimeError("This release candidate has only been validated for an x86_64/amd64 Linux Docker engine; ARM64 is not verified.")


def restrict(path: Path) -> None:
    if os.name == "nt":
        user = subprocess.check_output(["whoami"], text=True, stderr=subprocess.DEVNULL).strip()
        for target in (path,):
            subprocess.run(["icacls", str(target), "/inheritance:r", "/grant:r",
                            f"{user}:F", "SYSTEM:F"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        path.chmod(0o600)


def docker_ready() -> None:
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or newer is required for setup and backup tools.")
    if not shutil.which("docker"):
        raise RuntimeError("Docker CLI is not installed or is not on PATH.")
    info = subprocess.run(["docker", "info", "--format", "{{.OSType}}/{{.Architecture}}"],
                          check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True).stdout
    validate_architecture(info)
    subprocess.run(["docker", "compose", "version"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def initialize() -> None:
    docker_ready()
    env_path = ROOT / ".env"
    if env_path.exists():
        if env_path.is_symlink():
            raise RuntimeError("Existing .env is a symlink; refusing to follow it.")
        text = env_path.read_text(encoding="utf-8")
        keys = {line.partition("=")[0]: line.partition("=")[2]
                for line in text.splitlines() if "=" in line and not line.startswith("#")}
        required = ("DB_PASSWORD", "SESSION_KEY", "INGEST_KEY", "COLLECTOR_PASSWORD", "COLLECTOR_KEY")
        if any(not keys.get(key) for key in required):
            raise ValueError("Existing .env is incomplete; it was preserved without changes.")
        restrict(env_path)
        print("Existing local configuration validated and preserved. No secrets were displayed.")
        return
    values = {
        "COMPOSE_PROJECT_NAME": "findhub-tracker-friend",
        "DB_PASSWORD": secrets.token_urlsafe(36),
        "SESSION_KEY": secrets.token_urlsafe(48),
        "INGEST_KEY": secrets.token_urlsafe(48),
        "OWNER_ACTOR": "owner",
        "COLLECTOR_PASSWORD": secrets.token_urlsafe(30),
        "COLLECTOR_KEY": secrets.token_urlsafe(48),
        "DEMO_MODE": "0",
        "POLL_SECONDS": "600",
        "DISPLAY_TZ": "UTC",
        "SECURE_COOKIE": "0",
        "DASHBOARD_PORT": "8080",
        "COLLECTOR_PORT": "4321",
        "HTTPS_BIND_IP": "127.0.0.1",
        "HTTP_PORT": "80",
        "HTTPS_PORT": "443",
        "TRACKER_DOMAIN": "",
    }
    contents = "".join(f"{key}={value}\n" for key, value in values.items())
    fd, temp_name = tempfile.mkstemp(prefix=".env-", dir=ROOT)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, env_path)
        restrict(env_path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    print("Fresh local configuration created with unique random secrets. No credentials were displayed.")


def show_collector_login() -> None:
    env = (ROOT / ".env").read_text(encoding="utf-8")
    match = re.search(r"^COLLECTOR_PASSWORD=(.+)$", env, re.MULTILINE)
    if not match:
        raise RuntimeError("Collector password was not found in the local configuration.")
    print("Collector username: tracker")
    print("Collector password (shown only in this local terminal): " + match.group(1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "collector-login"))
    action = parser.parse_args().action
    try:
        initialize() if action == "init" else show_collector_login()
        return 0
    except Exception as error:
        print(f"Setup failed ({type(error).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
