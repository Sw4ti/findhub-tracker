# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Waleed Khan

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
python --version
if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11 or newer and ensure python is on PATH.' }
python .\portable_setup.py init
if ($LASTEXITCODE -ne 0) { throw 'Prerequisite check or first-run configuration failed.' }
docker compose --parallel 1 build app device-discovery collector collector-status auth-setup
if ($LASTEXITCODE -ne 0) { throw 'Image build failed. Do not continue to startup.' }
docker compose run --rm auth-setup
if ($LASTEXITCODE -ne 0) { throw 'Dashboard password setup failed. No application services were started.' }
docker compose up -d --wait db app collector collector-status device-discovery
if ($LASTEXITCODE -ne 0) { throw 'Startup or health checks failed. See TROUBLESHOOTING.md.' }
Write-Host 'Find Hub Tracker is running. Dashboard: http://127.0.0.1:8080'
Write-Host 'Collector setup: http://127.0.0.1:4321/auth (HTTP Basic credentials are separate).'
Write-Host 'Run: python .\portable_setup.py collector-login to display its generated login in this terminal.'
