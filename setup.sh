#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Waleed Khan

set -eu
cd "$(dirname "$0")"
python3 --version
python3 portable_setup.py init
docker compose --parallel 1 build app device-discovery collector collector-status auth-setup
docker compose run --rm auth-setup
docker compose up -d --wait db app collector collector-status device-discovery
printf '%s\n' 'Find Hub Tracker is running. Dashboard: http://127.0.0.1:8080' \
  'Collector setup: http://127.0.0.1:4321/auth (HTTP Basic credentials are separate).' \
  'Run: python3 portable_setup.py collector-login to display its generated login in this terminal.'
