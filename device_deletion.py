"""Fail-closed primitives for permanent device deletion.

The HTTP and database adapters live in tracker.py/device_discovery.py. Keeping
eligibility and checkpoint validation pure makes the destructive gates easy to
exercise with synthetic records.
"""
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Waleed Khan

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
import tarfile
from tempfile import NamedTemporaryFile


class DeletionRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def deletion_eligibility(device: dict, discovery: dict, now: datetime,
                         max_age: timedelta = timedelta(minutes=35)) -> tuple[bool, str]:
    """Require an unambiguous, recent successful inventory omission."""
    if not device:
        return False, "Device registration was not found."
    if device.get("account_status") != "removed" or int(device.get("missing_successes") or 0) < 3:
        return False, "Google Find Hub has not confirmed this device removed."
    if not discovery or discovery.get("status") != "success":
        return False, "The latest Google device discovery did not succeed."
    succeeded = discovery.get("succeeded")
    if not isinstance(succeeded, datetime) or succeeded.tzinfo is None:
        return False, "A verified discovery timestamp is unavailable."
    age = now.astimezone(UTC) - succeeded.astimezone(UTC)
    if age < timedelta(0) or age > max_age:
        return False, "The successful Google discovery is stale."
    return True, ""


def tombstone_return_is_safe(tombstoned: bool, pending: bool,
                             registration_exists: bool, history_exists: bool) -> bool:
    """Permit same-ID re-pair only after a fresh successful inventory and clean local state."""
    return not pending and (not tombstoned or (not registration_exists and not history_exists))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_device_checkpoint(directory: Path, device: str, label: str,
                            registration: dict, detections: list[dict],
                            collector_entry: dict, captured_at: datetime) -> dict:
    """Write a private, atomic, checksummed recovery archive for one device."""
    if not device or not isinstance(collector_entry, dict):
        raise ValueError("Device checkpoint inputs are incomplete.")
    if captured_at.tzinfo is None:
        raise ValueError("Checkpoint timestamp must be timezone-aware.")
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Device checkpoint directory is unsafe.")
    os.chmod(directory, 0o700)
    payload = {
        "format": 1,
        "captured_utc": captured_at.astimezone(UTC).isoformat(),
        "device": device,
        "label": label,
        "registration": registration,
        "detections": detections,
        "collector_entry": collector_entry,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default).encode()
    archive_name = f"device-{hashlib.sha256(device.encode()).hexdigest()[:16]}-{captured_at.astimezone(UTC):%Y%m%dT%H%M%SZ}.tar.gz"
    target = directory / archive_name
    if target.exists() or target.is_symlink():
        raise FileExistsError("A device checkpoint already exists for this capture time.")
    with NamedTemporaryFile(prefix=".pending-", dir=directory, delete=False) as tmp:
        temporary = Path(tmp.name)
    try:
        os.chmod(temporary, 0o600)
        with tarfile.open(temporary, "w:gz") as archive:
            info = tarfile.TarInfo("device-checkpoint.json")
            info.size = len(encoded)
            info.mode = 0o600
            import io
            archive.addfile(info, io.BytesIO(encoded))
        if os.name == "posix":
            fd = os.open(temporary, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        digest = sha256(temporary)
        os.replace(temporary, target)
        os.chmod(target, 0o600)
        if os.name == "posix":
            dirfd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        return {"file": archive_name, "sha256": digest, "detections": len(detections),
                "captured_utc": payload["captured_utc"], "format": 1}
    finally:
        temporary.unlink(missing_ok=True)


def verify_device_checkpoint(path: Path, expected_device: str | None = None) -> dict:
    """Validate archive structure, mode, checksum-ready payload, and identity."""
    path = Path(path)
    if (path.is_symlink() or not path.is_file()
            or (os.name == "posix" and (path.stat().st_mode & 0o077
                                         or path.parent.is_symlink()
                                         or path.parent.stat().st_mode & 0o077))):
        raise ValueError("Device checkpoint permissions or file type are unsafe.")
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) != 1 or members[0].name != "device-checkpoint.json" or not members[0].isfile():
            raise ValueError("Device checkpoint archive structure is invalid.")
        member = members[0]
        if member.size < 1 or member.size > 128 * 1024 * 1024:
            raise ValueError("Device checkpoint payload size is invalid.")
        if PurePosixPath(member.name).is_absolute() or ".." in PurePosixPath(member.name).parts:
            raise ValueError("Device checkpoint contains an unsafe path.")
        source = archive.extractfile(member)
        if source is None:
            raise ValueError("Device checkpoint payload is unavailable.")
        payload = json.load(source)
    required = {"format", "captured_utc", "device", "label", "registration", "detections", "collector_entry"}
    if set(payload) != required or payload["format"] != 1 or not isinstance(payload["detections"], list):
        raise ValueError("Device checkpoint payload is incomplete.")
    if expected_device is not None and payload["device"] != expected_device:
        raise ValueError("Device checkpoint identity does not match the requested device.")
    if not isinstance(payload["registration"], dict) or not isinstance(payload["collector_entry"], dict):
        raise ValueError("Device checkpoint recovery state is invalid.")
    collector_entry = payload["collector_entry"]
    if (not isinstance(collector_entry.get("device_state"), dict)
            or not isinstance(collector_entry.get("forward_logs"), dict)):
        raise ValueError("Device checkpoint collector state is invalid.")
    return payload


def verify_checkpoint_manifest(path: Path, expected_sha256: str, expected_device: str,
                              expected_detections: int) -> dict:
    if not expected_sha256 or not hmac.compare_digest(sha256(Path(path)), expected_sha256):
        raise ValueError("Device checkpoint checksum verification failed.")
    payload = verify_device_checkpoint(path, expected_device)
    if len(payload["detections"]) != expected_detections:
        raise ValueError("Device checkpoint history count does not match the database snapshot.")
    return payload


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Unsupported checkpoint value: {type(value).__name__}")
