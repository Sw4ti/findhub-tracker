"""Publish an allowlisted snapshot of the pinned collector's local poll state."""
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Waleed Khan

import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml

INPUT = Path("/collector")
OUTPUT = Path("/status/devices.json")
ENDPOINT = "http://app:8080/api/ingest"


def attempt_errors(lines):
    for line in lines.splitlines():
        fields = line.split("\t", 3)
        if len(fields) != 4 or not fields[0].isdigit() or fields[2] != "webui.scheduler":
            continue
        match = re.match(r"Locate failed for (.*): .+ \(\d+(?:/\d+ before alerting| in a row)\)$", fields[3])
        if match:
            yield int(fields[0]), match[1]


def snapshot(document, lines):
    result = []
    errors = list(attempt_errors(lines))
    for device, record in document.get("devices", {}).items():
        config = record.get("config") or {}
        if not any(endpoint.get("url") == ENDPOINT for endpoint in config.get("endpoints", [])):
            continue
        label = config.get("display_name") or config.get("google_name") or device
        scheduler_name = config.get("display_name", device)
        attempt = (record.get("endpoint_state") or {}).get(ENDPOINT) or {}
        stamp = attempt.get("last_forward_time")
        outcome = attempt.get("last_forward_status", "")
        if not stamp:
            status = "pending"
        elif outcome == "ok":
            status = "reported"
        elif outcome.startswith("error") or any(
            name == scheduler_name and 0 <= stamp - when <= 10 for when, name in errors
        ):
            status = "error"
        elif outcome == "no location" or outcome.startswith("skipped"):
            status = "no-report"
        else:
            status = "unknown"
        result.append({"device": device, "label": label, "status": status,
                       "attempted": datetime.fromtimestamp(stamp, UTC).isoformat() if stamp else None})
    return result


def refresh():
    state = INPUT / "devices.yaml"
    document = (yaml.safe_load(state.read_text(encoding="utf-8")) or {}) if state.exists() else {}
    log = INPUT / "system.log"
    lines = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    data = {"updated": time.time(), "devices": snapshot(document, lines)}
    temp = OUTPUT.with_suffix(".tmp")
    temp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(temp, OUTPUT)


if __name__ == "__main__":
    while True:
        try:
            refresh()
        except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError) as error:
            print(f"Collector status snapshot unavailable ({type(error).__name__})", flush=True)
        time.sleep(15)
