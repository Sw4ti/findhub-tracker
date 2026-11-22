from __future__ import annotations

"""Create, verify, and restore portable snapshots without touching other projects.

Snapshots include PostgreSQL, collector state, the local dashboard Argon2id hash,
and this installation's .env (which contains encryption/authentication secrets).
Treat every snapshot as highly sensitive.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
EXPECTED = {"tracker.dump", "collector-state.tar.gz", "device-checkpoints.tar.gz",
            "dashboard_password_hash", ".env", "manifest.json"}
NAME = re.compile(r"^findhub-\d{8}T\d{12}Z\.zip$")
PROJECT = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")
RETENTION = 14


def restrict(path: Path, directory=False):
    if os.name == "nt":
        user = subprocess.check_output(["whoami"], text=True, stderr=subprocess.DEVNULL).strip()
        rights = f"{user}:(OI)(CI)F" if directory else f"{user}:F"
        system = "SYSTEM:(OI)(CI)F" if directory else "SYSTEM:F"
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", rights, system],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        path.chmod(0o700 if directory else 0o600)


def compose(*args, cwd=ROOT, input_data=None, output=None):
    compose_files = os.environ.get("PORTABLE_COMPOSE_FILES", "compose.yaml").split(os.pathsep)
    command = ["docker", "compose"]
    for compose_file in compose_files:
        if compose_file:
            command.extend(("-f", compose_file))
    command.extend(args)
    if output:
        with open(output, "wb") as stream:
            return subprocess.run(command, cwd=cwd, input=input_data, stdout=stream,
                                  stderr=subprocess.DEVNULL, check=True)
    return subprocess.run(command, cwd=cwd, input=input_data, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_collector_tar(path: Path):
    import tarfile
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or member.issym() or member.islnk() or member.isdev():
                raise ValueError("Collector archive contains an unsafe path or entry type.")


def validate_collector_stream(stream):
    import tarfile
    with tarfile.open(fileobj=stream, mode="r|gz") as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or member.issym() or member.islnk() or member.isdev():
                raise ValueError("Collector archive contains an unsafe path or entry type.")


def verify_snapshot(snapshot: Path, extract_to: Path | None = None):
    with zipfile.ZipFile(snapshot, "r") as archive:
        names = archive.namelist()
        if archive.testzip() is not None or len(names) != len(EXPECTED) or set(names) != EXPECTED:
            raise ValueError("Snapshot ZIP integrity or file inventory check failed.")
        if archive.getinfo("manifest.json").file_size > 65536:
            raise ValueError("Snapshot manifest is unexpectedly large.")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != 1 or set(manifest.get("sha256", {})) != EXPECTED - {"manifest.json"}:
            raise ValueError("Snapshot manifest is invalid.")
        if not isinstance(manifest.get("captured_utc"), str) or not manifest["captured_utc"].endswith("+00:00"):
            raise ValueError("Snapshot UTC capture timestamp is invalid.")
        if extract_to:
            extract_to.mkdir(mode=0o700, parents=True, exist_ok=False)
            restrict(extract_to, directory=True)
        for name in EXPECTED - {"manifest.json"}:
            digest = hashlib.sha256()
            destination = extract_to / name if extract_to else None
            output = None
            if destination:
                fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                output = os.fdopen(fd, "wb")
            with archive.open(name) as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    if output:
                        output.write(chunk)
            if output:
                output.close()
                restrict(destination)
            if digest.hexdigest() != manifest["sha256"].get(name):
                raise ValueError("Snapshot checksum verification failed.")
        if extract_to:
            validate_collector_tar(extract_to / "collector-state.tar.gz")
            validate_collector_tar(extract_to / "device-checkpoints.tar.gz")
        else:
            for member in ("collector-state.tar.gz", "device-checkpoints.tar.gz"):
                with archive.open(member) as stream:
                    validate_collector_stream(stream)
    if extract_to:
        hash_bytes = (extract_to / "dashboard_password_hash").read_bytes()
        env_text = (extract_to / ".env").read_text(encoding="utf-8")
    else:
        with zipfile.ZipFile(snapshot, "r") as archive:
            hash_bytes = archive.read("dashboard_password_hash")
            env_text = archive.read(".env").decode("utf-8")
    if not hash_bytes.startswith(b"$argon2id$"):
        raise ValueError("Snapshot dashboard authentication hash is invalid.")
    validate_env(env_text)
    return manifest


def read_zip(snapshot: Path):
    """Small-fixture helper used by unit tests; production flows stream to disk."""
    manifest = verify_snapshot(snapshot)
    with zipfile.ZipFile(snapshot, "r") as archive:
        payloads = {name: archive.read(name) for name in EXPECTED - {"manifest.json"}}
    return payloads, manifest


def validate_env(text):
    required = {"DB_PASSWORD", "SESSION_KEY", "INGEST_KEY", "COLLECTOR_PASSWORD", "COLLECTOR_KEY"}
    values = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or "\n" in value or "\r" in value:
            raise ValueError("Snapshot environment configuration is malformed.")
        values[key] = value
    if not required.issubset(values) or any(not values[key] for key in required):
        raise ValueError("Snapshot is missing required application configuration.")
    return values


def acquire_lock(directory: Path):
    path = directory / ".backup.lock"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(path, flags, 0o600)
    os.write(fd, f"pid={os.getpid()}\n".encode())
    os.close(fd)
    return path


def backup(directory: Path):
    env_path = ROOT / ".env"
    if not env_path.is_file():
        raise FileNotFoundError("Local .env is missing; complete first-run setup first.")
    directory.mkdir(parents=True, exist_ok=True)
    restrict(directory, directory=True)
    lock = acquire_lock(directory)
    work = None
    try:
        work = Path(tempfile.mkdtemp(prefix=".findhub-backup-", dir=directory))
        restrict(work, directory=True)
        dump = work / "tracker.dump"
        collector_state = work / "collector-state.tar.gz"
        checkpoints = work / "device-checkpoints.tar.gz"
        auth_hash = work / "dashboard_password_hash"
        snapshot_name = datetime.now(timezone.utc).strftime("findhub-%Y%m%dT%H%M%S%fZ.zip")
        compose("exec", "-T", "db", "pg_dump", "-U", "tracker", "-d", "tracker", "--format=custom", output=dump)
        compose("exec", "-T", "collector", "tar", "-czf", "-", "-C", "/data", ".", output=collector_state)
        compose("run", "--rm", "-T", "--no-deps", "--entrypoint", "tar", "device-discovery",
                "-czf", "-", "-C", "/device-deletion-backups", ".", output=checkpoints)
        compose("exec", "-T", "app", "cat", "/run/secrets/dashboard_password_hash", output=auth_hash)
        if (dump.stat().st_size == 0 or collector_state.stat().st_size == 0
                or checkpoints.stat().st_size == 0 or auth_hash.stat().st_size > 4096):
            raise ValueError("A required backup component is empty or invalid.")
        validate_collector_tar(collector_state)
        validate_collector_tar(checkpoints)
        (work / ".env").write_bytes(env_path.read_bytes())
        restrict(work / ".env")
        payload_names = EXPECTED - {"manifest.json"}
        manifest = {"format": 1,
                    "captured_utc": datetime.now(timezone.utc).isoformat(),
                    "sha256": {name: sha256(work / name) for name in sorted(payload_names)}}
        manifest_path = work / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        target = directory / snapshot_name
        pending = directory / ("." + snapshot_name + ".pending")
        try:
            with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_STORED) as archive:
                for name in sorted(EXPECTED):
                    archive.write(work / name, name)
            restrict(pending)
            verify_snapshot(pending)
            os.replace(pending, target)
            restrict(target)
        finally:
            pending.unlink(missing_ok=True)
        prune_verified(directory)
        print(f"Backup created and verified: {target.name}")
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)
        lock.unlink(missing_ok=True)


def prune_verified(directory):
    verified = []
    for item in directory.iterdir():
        if item.is_file() and NAME.fullmatch(item.name):
            try:
                verify_snapshot(item)
                verified.append(item)
            except Exception:
                continue
    verified.sort(key=lambda item: item.name, reverse=True)
    for old in verified[RETENTION:]:
        old.unlink()


def verify_command(snapshot):
    manifest = verify_snapshot(snapshot)
    print("PASS: archive inventory, ZIP CRC, SHA-256 checksums, Argon2id marker and collector archive paths.")
    print("Snapshot capture UTC: " + manifest["captured_utc"])


def docker_volume_exists(project, volume):
    name = f"{project}_{volume}"
    result = subprocess.run(["docker", "volume", "inspect", name],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


def project_has_containers(project):
    result = subprocess.run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    if result.returncode != 0:
        raise RuntimeError("Could not check whether the recovery project name is unused.")
    return bool(result.stdout.strip())


def start_restored_dashboard(run):
    """Start only the restored dashboard; collector and status reader stay idle."""
    run(["up", "-d", "--wait", "app"])


def restore(snapshot: Path, target: Path, project: str, dashboard_port: int, collector_port: int):
    if not PROJECT.fullmatch(project):
        raise ValueError("Choose a Docker project name containing lowercase letters, digits, hyphens or underscores.")
    if not (target / "compose.yaml").is_file() or not (target / "Dockerfile.portable").is_file():
        raise ValueError("Target must be a separate extracted copy of the portable package.")
    if (target / ".env").exists():
        raise FileExistsError("Restore target already has .env; no existing installation will be overwritten.")
    if project_has_containers(project):
        raise FileExistsError("The chosen project already has containers; choose a new project name.")
    if any(docker_volume_exists(project, name) for name in
           ("database", "collector", "collector_status", "dashboard_auth", "device_checkpoints")):
        raise FileExistsError("The chosen project already has persistent volumes; choose a new project name.")
    staging_root = Path(tempfile.mkdtemp(prefix=".findhub-restore-"))
    restrict(staging_root, directory=True)
    staging = staging_root / "payload"
    try:
        verify_snapshot(snapshot, staging)
        env_path = staging / ".env"
        env_text = env_path.read_text(encoding="utf-8")
        replacements = {"COMPOSE_PROJECT_NAME": project, "DASHBOARD_PORT": str(dashboard_port),
                        "COLLECTOR_PORT": str(collector_port), "SECURE_COOKIE": "0"}
        for key, value in replacements.items():
            line = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
            if line.search(env_text):
                env_text = line.sub(lambda _: f"{key}={value}", env_text)
            else:
                env_text += f"\n{key}={value}\n"
        env_path = target / ".env"
        fd = os.open(env_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(env_text)
        restrict(env_path)
        compose_args = ["-p", project]

        def run(args, input_data=None):
            subprocess.run(["docker", "compose", "-f", "compose.yaml", *compose_args, *args],
                           cwd=target, stdin=input_data, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=True)

        run(["build", "app", "collector", "collector-status", "device-discovery", "auth-setup"])
        run(["up", "-d", "--wait", "db"])
        database_dump = "/tmp/findhub-restore.dump"
        with (staging / "tracker.dump").open("rb") as dump:
            run(["exec", "-T", "db", "sh", "-c",
                 "umask 077 && cat > /tmp/findhub-restore.dump"], dump)
        try:
            run(["exec", "-T", "db", "pg_restore", "--no-owner", "--role=tracker",
                 "--exit-on-error", "--dbname=tracker", "-U", "tracker", database_dump])
        finally:
            run(["exec", "-T", "db", "rm", "-f", database_dump])
        collector_archive = staging / "collector-state.tar.gz"
        validate_collector_tar(collector_archive)
        with collector_archive.open("rb") as archive:
            run(["run", "--rm", "-T", "--no-deps", "--entrypoint", "tar", "collector",
                 "-xzf", "-", "-C", "/data"], archive)
        checkpoint_archive = staging / "device-checkpoints.tar.gz"
        with checkpoint_archive.open("rb") as archive:
            run(["run", "--rm", "-T", "--no-deps", "--entrypoint", "tar", "device-discovery",
                 "-xzf", "-", "-C", "/device-deletion-backups"], archive)
        with (staging / "dashboard_password_hash").open("rb") as auth_hash:
            run(["run", "--rm", "-T", "--no-deps", "auth-setup", "--restore-from-stdin"], auth_hash)
        # The status reader intentionally reports unhealthy while the collector is
        # stopped. Starting it with --wait would turn a safe restore into a false
        # failure, so start only the dashboard after the database is verified.
        start_restored_dashboard(run)
        counts = subprocess.run(["docker", "compose", "-f", "compose.yaml", *compose_args,
                                 "exec", "-T", "db", "psql", "-U", "tracker", "-d", "tracker",
                                 "-tAc", "SELECT count(*) FROM detections"], cwd=target,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True).stdout.strip()
        print(f"Restore verified in isolated project {project}; stored detection count: {counts}.")
        print("Collector and discovery remain stopped. Review the restored dashboard before starting collection.")
    except Exception:
        print("Restore did not finish. The isolated target project may contain partial restore state; preserve it for diagnosis.", file=sys.stderr)
        raise
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def validate_tar_bytes(content):
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk() or member.isdev():
                raise ValueError("Collector archive contains an unsafe path or entry type.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    make = sub.add_parser("backup")
    make.add_argument("--directory", type=Path, default=ROOT / "backups")
    check = sub.add_parser("verify")
    check.add_argument("snapshot", type=Path)
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("snapshot", type=Path)
    restore_parser.add_argument("--target", type=Path, required=True,
                                help="A separate extracted package directory with no .env")
    restore_parser.add_argument("--project", required=True)
    restore_parser.add_argument("--dashboard-port", type=int, default=18080)
    restore_parser.add_argument("--collector-port", type=int, default=14321)
    args = parser.parse_args()
    try:
        if args.action == "backup":
            backup(args.directory.resolve())
        elif args.action == "verify":
            verify_command(args.snapshot.resolve())
        else:
            if not 1024 <= args.dashboard_port <= 65535 or not 1024 <= args.collector_port <= 65535:
                raise ValueError("Restore ports must be in range 1024..65535.")
            restore(args.snapshot.resolve(), args.target.resolve(), args.project,
                    args.dashboard_port, args.collector_port)
        return 0
    except Exception as error:
        print(f"{args.action.capitalize()} failed ({type(error).__name__}); see the safe error details above.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
