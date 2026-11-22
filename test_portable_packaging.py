import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup = load("portable_setup_test_module", "portable_setup.py")
auth = load("portable_auth_test_module", "portable_auth_setup.py")
backup = load("portable_backup_test_module", "portable_backup.py")
privacy = load("portable_privacy_test_module", "release_privacy_scan.py")


def valid_env():
    names = ("DB_PASSWORD", "SESSION_KEY", "INGEST_KEY", "COLLECTOR_PASSWORD", "COLLECTOR_KEY")
    return "COMPOSE_PROJECT_NAME=synthetic-qa\n" + "OWNER_ACTOR=owner\n" + "".join(
        f"{name}=" + ("synthetic-" + name.lower()) + "\n" for name in names)


def synthetic_collector_tar():
    import tarfile
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        payload = b"synthetic collector state"
        info = tarfile.TarInfo("synthetic-state.yaml")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return stream.getvalue()


def make_snapshot(path, collector=None, env=None, auth_hash=None):
    from argon2 import PasswordHasher
    env = env or valid_env().encode()
    auth_hash = auth_hash or (PasswordHasher().hash("synthetic-only-password") + "\n").encode()
    collector = collector or synthetic_collector_tar()
    payload = {"tracker.dump": b"synthetic PostgreSQL dump",
               "collector-state.tar.gz": collector,
               "device-checkpoints.tar.gz": synthetic_collector_tar(),
               "dashboard_password_hash": auth_hash,
               ".env": env}
    manifest = {"format": 1, "captured_utc": "2026-01-01T00:00:00+00:00",
                "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in payload.items():
            archive.writestr(name, data)
        archive.writestr("manifest.json", json.dumps(manifest))


class PortablePackageTests(unittest.TestCase):
    def test_setup_accepts_only_validated_linux_engine_architecture(self):
        setup.validate_architecture("linux/amd64")
        setup.validate_architecture("linux/x86_64")
        with self.assertRaises(RuntimeError):
            setup.validate_architecture("linux/arm64")

    def test_fresh_environment_secrets_are_unique_and_private(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            setup.ROOT = root
            with patch.object(setup, "docker_ready"), patch.object(setup, "restrict"):
                setup.initialize()
            first = setup.ROOT.joinpath(".env").read_text()
            self.assertIn("COMPOSE_PROJECT_NAME=findhub-tracker-friend", first)
            self.assertIn("DEMO_MODE=0", first)
            values = dict(line.split("=", 1) for line in first.splitlines())
            self.assertTrue(all(values[key] for key in
                                ("DB_PASSWORD", "SESSION_KEY", "INGEST_KEY", "COLLECTOR_PASSWORD", "COLLECTOR_KEY")))
            self.assertEqual(len({values[key] for key in
                                  ("DB_PASSWORD", "SESSION_KEY", "INGEST_KEY", "COLLECTOR_PASSWORD", "COLLECTOR_KEY")}), 5)
            with patch.object(setup, "docker_ready"), patch.object(setup, "restrict"):
                setup.initialize()
            self.assertEqual(first, setup.ROOT.joinpath(".env").read_text())

    def test_argon2id_hash_setup_and_restore_are_silent_about_password(self):
        from argon2 import PasswordHasher
        with tempfile.TemporaryDirectory() as temp:
            auth.AUTH_DIR = Path(temp) / "auth"
            auth.AUTH_FILE = auth.AUTH_DIR / "dashboard_password_hash"
            with patch.object(auth.os, "chown", create=True), patch.object(auth.os, "fchmod", create=True):
                encoded = (PasswordHasher().hash("synthetic-only-password") + "\n").encode()
                auth.install(encoded)
            stored = auth.AUTH_FILE.read_text().strip()
            self.assertTrue(stored.startswith("$argon2id$"))
            self.assertTrue(PasswordHasher().verify(stored, "synthetic-only-password"))
            with self.assertRaises(FileExistsError):
                auth.install(encoded)

    def test_verified_synthetic_backup_round_trips_and_checksums(self):
        import tarfile
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tar_path = root / "state.tar.gz"
            with tarfile.open(tar_path, "w:gz") as archive:
                payload = b"synthetic collector state"
                info = tarfile.TarInfo("synthetic.yaml")
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            snapshot = root / "verified.zip"
            make_snapshot(snapshot, tar_path.read_bytes())
            payloads, _ = backup.read_zip(snapshot)
            self.assertEqual(payloads["tracker.dump"], b"synthetic PostgreSQL dump")
            backup.validate_tar_bytes(payloads["collector-state.tar.gz"])
            backup.validate_tar_bytes(payloads["device-checkpoints.tar.gz"])
            corrupted = root / "corrupted.zip"
            with zipfile.ZipFile(snapshot, "r") as source, zipfile.ZipFile(corrupted, "w") as target:
                for name in source.namelist():
                    target.writestr(name, b"changed" if name == "tracker.dump" else source.read(name))
            with self.assertRaises(ValueError):
                backup.read_zip(corrupted)

    def test_snapshot_extraction_requires_and_uses_a_fresh_staging_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = root / "snapshot.zip"
            make_snapshot(snapshot)
            target = root / "fresh-payload"
            manifest = backup.verify_snapshot(snapshot, target)
            self.assertTrue(target.is_dir())
            self.assertEqual(set(path.name for path in target.iterdir()),
                             backup.EXPECTED - {"manifest.json"})
            self.assertTrue(manifest["captured_utc"].endswith("+00:00"))
            with self.assertRaises(FileExistsError):
                backup.verify_snapshot(snapshot, target)

    def test_restore_rejects_path_traversal_in_collector_archive(self):
        import tarfile
        with tempfile.TemporaryDirectory() as temp:
            malicious = Path(temp) / "unsafe.tar.gz"
            with tarfile.open(malicious, "w:gz") as archive:
                info = tarfile.TarInfo("../../escape")
                info.size = 1
                archive.addfile(info, io.BytesIO(b"x"))
            with self.assertRaises(ValueError):
                backup.validate_collector_tar(malicious)

    def test_unique_project_name_validation_and_backup_retention_scope(self):
        self.assertIsNotNone(backup.PROJECT.fullmatch("friend-tracker-02"))
        self.assertIsNotNone(backup.PROJECT.fullmatch("friend_tracker_02"))
        self.assertIsNone(backup.NAME.fullmatch("unrelated-backup.zip"))

    def test_backup_lock_prevents_overlap_and_retains_fourteen_verified_archives(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = backup.acquire_lock(root)
            with self.assertRaises(FileExistsError):
                backup.acquire_lock(root)
            lock.unlink()
            collector_state = synthetic_collector_tar()
            for index in range(15):
                path = root / f"findhub-20260101T0000{index:08d}Z.zip"
                make_snapshot(path, collector_state)
            corrupt = root / "findhub-20250101T000000000000Z.zip"
            corrupt.write_bytes(b"not a verified archive")
            backup.prune_verified(root)
            self.assertEqual(sum(1 for path in root.iterdir() if backup.NAME.fullmatch(path.name)), 15)
            self.assertTrue(corrupt.exists())
            self.assertEqual(sum(1 for path in root.iterdir() if backup.NAME.fullmatch(path.name)
                                 and path.name != corrupt.name), 14)

    def test_backup_uses_explicit_complete_compose_file_set(self):
        command = {}
        def capture(args, **kwargs):
            command["args"] = args
            return type("Result", (), {"returncode": 0})()
        separator = os.pathsep
        with patch.dict(os.environ, {"PORTABLE_COMPOSE_FILES":
                                     separator.join(("compose.yaml", "compose.https.yaml"))}):
            with patch.object(backup.subprocess, "run", side_effect=capture):
                backup.compose("ps")
        self.assertEqual(command["args"], ["docker", "compose", "-f", "compose.yaml",
                                            "-f", "compose.https.yaml", "ps"])

    def test_restore_starts_dashboard_without_starting_collector_or_status_reader(self):
        calls = []
        backup.start_restored_dashboard(lambda args: calls.append(args))
        self.assertEqual(calls, [["up", "-d", "--wait", "app"]])

    def test_release_privacy_scanner_rejects_generic_private_markers(self):
        samples = [
            b"account.example." + b"duckdns.org",
            ".".join(("8", "8", "8", "8")).encode("ascii"),
            ("C:" + chr(92) + "Users" + chr(92) + "friend" + chr(92) + "private.txt").encode("ascii"),
            ("/" + "opt" + "/friend-tracker").encode("ascii"),
            str(uuid.uuid4()).encode("ascii"),
        ]
        for sample in samples:
            self.assertIsNotNone(privacy.check_content("sample.txt", sample))
        self.assertIsNone(privacy.check_content("example.txt", b"127.0.0.1 and 10.0.0.2"))


if __name__ == "__main__":
    unittest.main()
