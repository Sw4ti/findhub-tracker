"""Build a deterministic source ZIP from the Git-tracked release workspace."""
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Waleed Khan

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
INVENTORY = ROOT / "PACKAGE_INVENTORY.txt"
OUTPUT_DIR = ROOT / "release-artifacts"
RELEASE_NAME = f"findhub-tracker-{VERSION}"


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "-z"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    names = [name for name in result.stdout.decode("utf-8").split("\0") if name]
    paths = []
    for name in names:
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Tracked path escapes the repository root.")
        path = ROOT.joinpath(*relative.parts)
        if path.is_file() and path != INVENTORY:
            paths.append(path)
    if not paths:
        raise RuntimeError("No Git-tracked release files found. Stage the reviewed source first.")
    return sorted(paths, key=lambda path: path.relative_to(ROOT).as_posix().casefold())


def make_inventory(paths: list[Path]) -> bytes:
    rows = [f"Find Hub Tracker {VERSION} source release", "SHA-256, bytes, path (inventory excludes itself)", ""]
    for path in paths:
        data = path.read_bytes()
        relative = path.relative_to(ROOT).as_posix()
        rows.append(f"{hashlib.sha256(data).hexdigest()}  {len(data):>9}  {relative}")
    return ("\n".join(rows) + "\n").encode("utf-8")


def build() -> tuple[Path, str, int]:
    paths = tracked_files()
    inventory = make_inventory(paths)
    INVENTORY.write_bytes(inventory)
    output = OUTPUT_DIR / f"{RELEASE_NAME}.zip"
    sidecar = OUTPUT_DIR / f"{RELEASE_NAME}.zip.sha256"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".release-", suffix=".zip", dir=OUTPUT_DIR)
    os.close(fd)
    Path(temporary_name).unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(temporary_name, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in paths:
                relative = path.relative_to(ROOT).as_posix()
                add_file(archive, f"{RELEASE_NAME}/{relative}", path.read_bytes())
            add_bytes(archive, f"{RELEASE_NAME}/PACKAGE_INVENTORY.txt", inventory)
        with zipfile.ZipFile(temporary_name, "r") as archive:
            if archive.testzip() is not None:
                raise RuntimeError("Release ZIP CRC validation failed.")
        temporary = Path(temporary_name)
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        sys.path.insert(0, str(ROOT))
        from release_privacy_scan import scan
        if scan(temporary) != 0:
            raise RuntimeError("Release privacy scan rejected the ZIP.")
        temporary.replace(output)
        with sidecar.open("w", encoding="ascii", newline="\n") as stream:
            stream.write(f"{digest}  {output.name}\n")
        return output, digest, len(paths) + 1
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def add_file(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    add_bytes(archive, name, data)


def add_bytes(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


if __name__ == "__main__":
    try:
        artifact, checksum, count = build()
        print(f"PASS: created {artifact.name}; files={count}; SHA-256={checksum}")
    except Exception as error:
        print(f"Release build failed ({type(error).__name__}).", file=sys.stderr)
        raise SystemExit(1)
