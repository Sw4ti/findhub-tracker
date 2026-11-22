"""Reject personal/runtime secrets from a portable candidate directory or ZIP."""
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Waleed Khan

import argparse
import ipaddress
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile

FORBIDDEN_NAMES = {".env", "auth.yaml", "devices.yaml", "auth.json", "secrets.json",
                   "id_rsa", "id_ed25519", "id_ecdsa", "dashboard_password_hash"}
FORBIDDEN_SUFFIXES = (".dump", ".sqlite", ".sqlite3", ".db", ".pem", ".p12", ".pfx", ".key", ".tar.gz")
PRIVATE_PATTERNS = [
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    re.compile(rb"\bya29\.[A-Za-z0-9_-]{20,}"),
    re.compile(rb"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.I),
]
PRIVATE_TEXT_PATTERNS = [
    re.compile(rb"\b[a-z0-9-]+\.duckdns\.org\b", re.I),
    re.compile(rb"(?i)\b[a-z]:\\users\\[^\\\r\n]+"),
    re.compile(rb"(?i)/opt/[a-z0-9._/-]+"),
]
IPV4 = re.compile(rb"\b(?:\d{1,3}\.){3}\d{1,3}\b")
SECRET_ASSIGNMENT = re.compile(rb"(?im)^\s*(?:DB_PASSWORD|SESSION_KEY|INGEST_KEY|COLLECTOR_PASSWORD|COLLECTOR_KEY)\s*=\s*[^\r\n]+")
GENERATED_DIRS = {".git", "node_modules", "dist", "__pycache__", ".venv", "qa-artifacts", "backups", "release-artifacts"}


def forbidden_name(name):
    path = PurePosixPath(name.replace("\\", "/"))
    lowered = path.name.casefold()
    if lowered in FORBIDDEN_NAMES:
        return True
    if lowered.startswith(".env.") and lowered != ".env.example":
        return True
    if lowered.endswith(FORBIDDEN_SUFFIXES):
        return True
    return any(part.casefold() in {"secrets", "backups", "rollback", "rollbacks", "auth"} for part in path.parts)


def check_content(name, data):
    if forbidden_name(name):
        return f"forbidden runtime/secret file: {name}"
    if any(pattern.search(data) for pattern in PRIVATE_PATTERNS):
        return f"sensitive token, device identifier or private-key pattern: {name}"
    suffix = name.casefold()
    if suffix.endswith(".env.example"):
        for line in data.splitlines():
            if SECRET_ASSIGNMENT.search(line) and not line.rstrip().endswith(b"="):
                return f"non-empty secret template value: {name}"
    elif suffix.endswith((".env", ".yaml", ".yml", ".toml", ".ini", ".conf")) and SECRET_ASSIGNMENT.search(data):
        return f"secret assignment in packaged file: {name}"
    text = data.decode("utf-8", errors="ignore").casefold()
    if any(pattern.search(data) for pattern in PRIVATE_TEXT_PATTERNS):
        return f"production-specific hostname, address or path: {name}"
    for match in IPV4.finditer(data):
        try:
            if ipaddress.ip_address(match.group().decode("ascii")).is_global:
                return f"public IPv4 address: {name}"
        except ValueError:
            continue
    return None


def scan(target):
    failures = []
    count = 0
    if target.is_dir():
        for path in target.rglob("*"):
            if any(part.casefold() in GENERATED_DIRS for part in path.relative_to(target).parts):
                continue
            if path.is_file():
                count += 1
                issue = check_content(path.relative_to(target).as_posix(), path.read_bytes())
                if issue:
                    failures.append(issue)
    elif zipfile.is_zipfile(target):
        with zipfile.ZipFile(target) as archive:
            for item in archive.infolist():
                if item.is_dir():
                    continue
                count += 1
                issue = check_content(item.filename, archive.read(item))
                if issue:
                    failures.append(issue)
    else:
        raise ValueError("Scan target must be a directory or ZIP candidate.")
    if failures:
        for issue in failures:
            print("FAIL: " + issue, file=sys.stderr)
        return 1
    print(f"PASS: scanned {count} release files; no forbidden secrets, runtime data, production identifiers or paths found.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    try:
        raise SystemExit(scan(args.target.resolve()))
    except Exception as error:
        print(f"Privacy scan failed ({type(error).__name__}).", file=sys.stderr)
        raise SystemExit(2)
