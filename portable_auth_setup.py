"""Initialize or restore the dashboard Argon2id hash in its private Docker volume."""
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Waleed Khan

import argparse
import getpass
import os
import re
import sys
import tempfile
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

AUTH_DIR = Path("/run/auth")
AUTH_FILE = AUTH_DIR / "dashboard_password_hash"
OWNER_UID = 65532
OWNER_GID = 65532


def install(raw: bytes, replace: bool = False) -> None:
    if not raw.endswith(b"\n"):
        raw += b"\n"
    encoded = raw.decode("ascii").strip()
    if not encoded.startswith("$argon2id$"):
        raise ValueError("The supplied password hash is not Argon2id.")
    try:
        PasswordHasher().check_needs_rehash(encoded)
    except (InvalidHashError, VerificationError, ValueError) as error:
        raise ValueError("The supplied password hash is invalid.") from error
    AUTH_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    if AUTH_FILE.exists() and not replace:
        raise FileExistsError("A dashboard hash already exists; use --rotate to replace it.")
    fd, temporary = tempfile.mkstemp(prefix=".hash-", dir=AUTH_DIR)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write((encoded + "\n").encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, OWNER_UID, OWNER_GID)
        os.replace(temporary, AUTH_FILE)
        os.chown(AUTH_FILE, OWNER_UID, OWNER_GID)
        os.chmod(AUTH_FILE, 0o600)
        os.chown(AUTH_DIR, OWNER_UID, OWNER_GID)
        os.chmod(AUTH_DIR, 0o700)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rotate", action="store_true", help="replace an existing hash after a hidden prompt")
    parser.add_argument("--restore-from-stdin", action="store_true", help="restore a verified Argon2id hash from backup input")
    args = parser.parse_args()
    try:
        if args.restore_from_stdin:
            raw = sys.stdin.buffer.read(4096)
            if not raw or len(raw) >= 4096:
                raise ValueError("Backup hash input is empty or too large.")
            install(raw, replace=False)
        else:
            if AUTH_FILE.exists() and not args.rotate:
                raise FileExistsError("A dashboard hash already exists; use --rotate to replace it.")
            first = getpass.getpass("New unique dashboard password (at least 14 characters): ")
            second = getpass.getpass("Repeat dashboard password: ")
            if len(first) < 14:
                raise ValueError("Password must contain at least 14 characters.")
            if first != second:
                raise ValueError("Password entries did not match.")
            install((PasswordHasher().hash(first) + "\n").encode("ascii"), replace=args.rotate)
        print("Dashboard password hash installed securely; password and hash were not displayed.")
        return 0
    except Exception as error:
        print(f"Dashboard password setup failed ({type(error).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
