"""Integration tests for deletion against an isolated PostgreSQL and collector."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import threading
import unittest
from urllib.parse import quote
from unittest.mock import patch

import httpx
import yaml
from fastapi import HTTPException

import device_discovery as discovery
import tracker
from device_deletion import DeletionRejected, verify_checkpoint_manifest
from restore_device_checkpoint import restore_checkpoint


TARGET_ID = os.environ["TEST_DEVICE_ID"]
OTHER_ID = os.environ["TEST_OTHER_ID"]


class DeletionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tracker.connect() as db:
            db.execute(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
        discovery.ensure_schema()

    def _unique(self, suffix):
        return f"{TARGET_ID}-{suffix}"

    def _collector_config(self, device, label):
        fixture_path = os.environ.get("COLLECTOR_TEST_DEVICES_FILE")
        if fixture_path:
            # Isolated Linux integration QA has no Google account. Seed only
            # the synthetic collector volume, never a production devices.yaml.
            path = Path(fixture_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            document = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
            document = document if isinstance(document, dict) else {}
            devices = document.setdefault("devices", {})
            devices[device] = {
                "config": {"display_name": label, "endpoints": [{
                    "url": "http://127.0.0.1:9/synthetic-test-sink", "cron": "*/10 * * * *"}]},
                "location": {"locations": [{"latitude": 12.34, "longitude": 45.67,
                                               "timestamp": "2026-11-21T00:00:00Z"}]},
                "endpoint_state": {"synthetic": {"last_forward_status": "ok"}},
            }
            temp = path.with_suffix(path.suffix + ".test-tmp")
            temp.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
            temp.chmod(0o600)
            temp.replace(path)
            return
        url = "http://127.0.0.1:9/synthetic-test-sink"
        document = yaml.safe_dump({
            "display_name": label,
            "endpoints": [{"url": url, "cron": "*/10 * * * *"}],
        }, sort_keys=False)
        response = discovery.collector_request(
            "POST", f"/settings/devices/{quote(device, safe='')}/yaml",
            data={"yaml_text": document}, timeout=20)
        if response.status_code != 200:
            raise AssertionError(f"Synthetic collector fixture setup failed: HTTP {response.status_code}")

    def _database_rows(self, device, label, status="removed", misses=3, count=2):
        now = datetime.now(UTC)
        with tracker.connect() as db:
            db.execute("DELETE FROM detections WHERE device=%s", (device,))
            db.execute("DELETE FROM discovered_devices WHERE device=%s", (device,))
            db.execute("DELETE FROM deleted_devices WHERE device=%s", (device,))
            db.execute("DELETE FROM device_deletion_jobs WHERE device=%s", (device,))
            db.execute("DELETE FROM device_deletion_events WHERE device=%s", (device,))
            db.execute("""INSERT INTO discovered_devices
                (device,label,account_status,first_seen,last_seen,missing_successes)
                VALUES (%s,%s,%s,%s,%s,%s)""",
                (device, label, status, now-timedelta(days=2), now-timedelta(minutes=5), misses))
            for index in range(count):
                reported = now - timedelta(minutes=10-index)
                db.execute("""INSERT INTO detections
                    (fingerprint,device,label,source,report_id,reported,collected,latitude,longitude,accuracy,status)
                    VALUES (%s,%s,%s,'find-hub',%s,%s,%s,%s,%s,%s,'Synthetic')""",
                    (f"synthetic:{device}:{index}", device, label, f"synthetic-report-{index}",
                     reported, now, -33.8-index/10000, 151.2+index/10000, 10.0))
            db.execute("""INSERT INTO discovery_state(id,status,attempted,succeeded,error)
                VALUES (1,'success',now(),now(),NULL)
                ON CONFLICT (id) DO UPDATE SET status='success',attempted=now(),succeeded=now(),error=NULL""")

    def _count(self, query, params=()):
        with tracker.connect() as db:
            return db.execute(query, params).fetchone()["n"]

    def _call_delete(self, device, label):
        return discovery._delete_device_once(device, label, "owner")

    def test_success_checkpoint_restore_and_unrelated_device_preservation(self):
        self._database_rows(TARGET_ID, "Synthetic Target")
        self._collector_config(TARGET_ID, "Synthetic Target")
        self._database_rows(OTHER_ID, "Synthetic Other", status="active", misses=0, count=3)
        self._collector_config(OTHER_ID, "Synthetic Unrelated Device")
        result = self._call_delete(TARGET_ID, "Synthetic Target")
        self.assertTrue(result["deleted"])
        self.assertEqual(result["detections_deleted"], 2)
        self.assertEqual(self._collector_yaml_name(TARGET_ID), "")
        self.assertEqual(self._collector_yaml_name(OTHER_ID), "Synthetic Unrelated Device")
        self.assertEqual(self._count("SELECT count(*) AS n FROM detections WHERE device=%s", (TARGET_ID,)), 0)
        self.assertEqual(self._count("SELECT count(*) AS n FROM discovered_devices WHERE device=%s", (TARGET_ID,)), 0)
        self.assertEqual(self._count("SELECT count(*) AS n FROM detections WHERE device=%s", (OTHER_ID,)), 3)
        self.assertEqual(self._count("SELECT count(*) AS n FROM discovered_devices WHERE device=%s", (OTHER_ID,)), 1)
        with tracker.connect() as db:
            tombstone = db.execute("SELECT * FROM deleted_devices WHERE device=%s", (TARGET_ID,)).fetchone()
            event = db.execute("SELECT * FROM device_deletion_events WHERE device=%s ORDER BY id DESC LIMIT 1",
                               (TARGET_ID,)).fetchone()
        self.assertIsNotNone(tombstone)
        self.assertEqual(event["detections_deleted"], 2)
        checkpoint = verify_checkpoint_manifest(
            Path(discovery.DELETION_BACKUP_DIR) / tombstone["checkpoint_file"],
            tombstone["checkpoint_sha256"], TARGET_ID, 2)
        self.assertEqual(checkpoint["collector_entry"]["device_state"]["config"]["display_name"], "Synthetic Target")
        self.assertIn("location", checkpoint["collector_entry"]["device_state"])
        self.assertIn("endpoint_state", checkpoint["collector_entry"]["device_state"])
        restored = restore_checkpoint(tombstone["checkpoint_file"])
        self.assertEqual(restored["detections_restored"], 2)
        self.assertEqual(self._count("SELECT count(*) AS n FROM detections WHERE device=%s", (TARGET_ID,)), 2)
        self.assertEqual(self._count("SELECT count(*) AS n FROM detections WHERE device=%s", (OTHER_ID,)), 3)
        self.assertEqual(self._count("SELECT count(*) AS n FROM deleted_devices WHERE device=%s", (TARGET_ID,)), 0)
        self.assertEqual(self._collector_yaml_name(TARGET_ID), "Synthetic Target")
        self.assertEqual(self._collector_yaml_name(OTHER_ID), "Synthetic Unrelated Device")

    def test_active_and_stale_discovery_are_rejected(self):
        active = self._unique("active")
        self._database_rows(active, "Synthetic Active", status="active", misses=0)
        self._collector_config(active, "Synthetic Active")
        with self.assertRaises(DeletionRejected):
            self._call_delete(active, "Synthetic Active")
        stale = self._unique("stale")
        self._database_rows(stale, "Synthetic Stale")
        with tracker.connect() as db:
            db.execute("UPDATE discovery_state SET succeeded=now()-interval '36 minutes' WHERE id=1")
        with self.assertRaises(DeletionRejected):
            self._call_delete(stale, "Synthetic Stale")
        self.assertEqual(self._count("SELECT count(*) AS n FROM detections WHERE device=%s", (stale,)), 2)

    def test_checkpoint_failure_restores_collector_and_preserves_database(self):
        device = self._unique("backup-failure")
        label = "Synthetic Backup Failure"
        self._database_rows(device, label)
        self._collector_config(device, label)
        with patch.object(discovery, "write_device_checkpoint", side_effect=OSError("synthetic backup fault")):
            with self.assertRaises(DeletionRejected):
                self._call_delete(device, label)
        self.assertEqual(self._count("SELECT count(*) AS n FROM detections WHERE device=%s", (device,)), 2)
        self.assertEqual(self._count("SELECT count(*) AS n FROM device_deletion_jobs WHERE device=%s", (device,)), 0)
        self.assertEqual(self._collector_yaml_name(device), label)

    def test_collector_cleanup_failure_is_compensated(self):
        device = self._unique("collector-failure")
        label = "Synthetic Collector Failure"
        self._database_rows(device, label)
        self._collector_config(device, label)
        original = discovery.collector_request

        def fail_after_cleanup(method, path, **kwargs):
            response = original(method, path, **kwargs)
            if path.endswith("/delete"):
                return httpx.Response(503)
            return response

        with patch.object(discovery, "collector_request", side_effect=fail_after_cleanup):
            with self.assertRaises(DeletionRejected):
                self._call_delete(device, label)
        self.assertEqual(self._count("SELECT count(*) AS n FROM detections WHERE device=%s", (device,)), 2)
        self.assertEqual(self._count("SELECT count(*) AS n FROM device_deletion_jobs WHERE device=%s", (device,)), 0)
        self.assertEqual(self._collector_yaml_name(device), label)

    def test_collector_prepare_failure_preserves_database_and_state(self):
        device = self._unique("prepare-failure")
        label = "Synthetic Prepare Failure"
        self._database_rows(device, label)
        self._collector_config(device, label)
        original = discovery.collector_request

        def fail_prepare(method, path, **kwargs):
            if path.endswith("/prepare"):
                return httpx.Response(503)
            return original(method, path, **kwargs)

        with patch.object(discovery, "collector_request", side_effect=fail_prepare):
            with self.assertRaises(DeletionRejected):
                self._call_delete(device, label)
        self.assertEqual(self._count("SELECT count(*) AS n FROM detections WHERE device=%s", (device,)), 2)
        self.assertEqual(self._count("SELECT count(*) AS n FROM discovered_devices WHERE device=%s", (device,)), 1)
        with tracker.connect() as db:
            job = db.execute("SELECT state FROM device_deletion_jobs WHERE device=%s", (device,)).fetchone()
        # A failed prepare response is ambiguous: the collector may have
        # committed its fence before the response was lost. Keep tracker-side
        # collection blocked until operator recovery rather than assuming it
        # had no side effects.
        self.assertEqual(job["state"], "recovery_required")
        self.assertEqual(self._collector_yaml_name(device), label)

    def test_database_delete_failure_is_compensated(self):
        device = self._unique("database-failure")
        label = "Synthetic Database Failure"
        self._database_rows(device, label)
        self._collector_config(device, label)
        real_connect = discovery.connect
        fault = {"armed": True}

        class ConnectionProxy:
            def __init__(self):
                self.connection = real_connect()
            def execute(self, sql, params=()):
                if fault["armed"] and sql.lstrip().startswith("DELETE FROM detections"):
                    fault["armed"] = False
                    raise RuntimeError("synthetic database-delete fault")
                return self.connection.execute(sql, params)
            def transaction(self, *args, **kwargs):
                return self.connection.transaction(*args, **kwargs)
            def commit(self):
                return self.connection.commit()
            def close(self):
                return self.connection.close()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return self.connection.__exit__(*args)

        with patch.object(discovery, "connect", side_effect=lambda: ConnectionProxy()):
            with self.assertRaises(DeletionRejected):
                self._call_delete(device, label)
        self.assertEqual(self._count("SELECT count(*) AS n FROM detections WHERE device=%s", (device,)), 2)
        self.assertEqual(self._count("SELECT count(*) AS n FROM discovered_devices WHERE device=%s", (device,)), 1)
        self.assertEqual(self._collector_yaml_name(device), label)

    def test_finalization_failure_is_retryable_and_idempotent(self):
        device = self._unique("finalize-failure")
        label = "Synthetic Finalize Failure"
        self._database_rows(device, label)
        self._collector_config(device, label)
        original = discovery.collector_request
        failed = {"once": True}

        def fail_commit_once(method, path, **kwargs):
            if path.endswith("/commit") and failed["once"]:
                failed["once"] = False
                return httpx.Response(503)
            return original(method, path, **kwargs)

        with patch.object(discovery, "collector_request", side_effect=fail_commit_once):
            with self.assertRaises(DeletionRejected):
                self._call_delete(device, label)
        self.assertEqual(self._count("SELECT count(*) AS n FROM detections WHERE device=%s", (device,)), 0)
        self.assertEqual(self._count("SELECT count(*) AS n FROM device_deletion_jobs WHERE device=%s", (device,)), 1)
        result = self._call_delete(device, label)
        self.assertTrue(result["deleted"])
        self.assertTrue(result["idempotent"])
        self.assertEqual(self._count("SELECT count(*) AS n FROM deleted_devices WHERE device=%s", (device,)), 1)

    def test_same_id_repair_requires_new_synthetic_successful_inventory_and_starts_empty(self):
        device = self._unique("repair")
        label = "Synthetic Repaired Tracker"
        self._database_rows(device, label)
        self._collector_config(device, label)
        self._call_delete(device, label)
        self.assertEqual(self._count("SELECT count(*) AS n FROM deleted_devices WHERE device=%s", (device,)), 1)
        response = discovery.collector_request(
            "POST", f"/internal/tracker/devices/{quote(device, safe='')}/revive")
        self.assertEqual(response.status_code, 200)
        discovery.reconcile({device: label})  # synthetic payload stands in for a fresh successful Google inventory
        self.assertEqual(self._count("SELECT count(*) AS n FROM deleted_devices WHERE device=%s", (device,)), 0)
        self.assertEqual(self._count("SELECT count(*) AS n FROM discovered_devices WHERE device=%s", (device,)), 1)
        self.assertEqual(self._count("SELECT count(*) AS n FROM detections WHERE device=%s", (device,)), 0)

    def test_concurrent_ingest_and_discovery_wait_for_delete_fence(self):
        device = self._unique("concurrent")
        label = "Synthetic Concurrent Tracker"
        self._database_rows(device, label)
        self._collector_config(device, label)
        original = discovery.collector_request
        prepared = threading.Event()
        release = threading.Event()

        def pause_after_prepare(method, path, **kwargs):
            response = original(method, path, **kwargs)
            if path.endswith("/prepare"):
                prepared.set()
                if not release.wait(20):
                    raise TimeoutError("synthetic test release timed out")
            return response

        def try_ingest():
            fix = tracker.Fix(device=device, label=label, reported=datetime.now(UTC),
                              latitude=-33.8, longitude=151.2, status="Synthetic")
            try:
                tracker.ingest(fix, authorization="Bearer " + os.environ["INGEST_KEY"])
            except HTTPException as error:
                return error.status_code
            return 204

        with ThreadPoolExecutor(max_workers=3) as pool, \
             patch.object(discovery, "collector_request", side_effect=pause_after_prepare):
            delete_future = pool.submit(self._call_delete, device, label)
            self.assertTrue(prepared.wait(10), "collector preparation was not reached")
            with tracker.connect() as db:
                self.assertFalse(db.execute("SELECT pg_try_advisory_xact_lock(118903) AS ok").fetchone()["ok"])
                self.assertFalse(db.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS ok",
                                            (device,)).fetchone()["ok"])
            ingest_future = pool.submit(try_ingest)
            discovery_future = pool.submit(discovery.reconcile, {
                OTHER_ID: "Synthetic Other",
            })
            self.assertFalse(ingest_future.done(), "ingest did not wait for the deletion lock")
            self.assertFalse(discovery_future.done(), "discovery did not wait for the deletion lock")
            release.set()
            delete_future.result(timeout=30)
            self.assertEqual(ingest_future.result(timeout=10), 410)
            discovery_future.result(timeout=10)

    def _collector_yaml_name(self, device):
        fixture_path = os.environ.get("COLLECTOR_TEST_DEVICES_FILE")
        if fixture_path:
            path = Path(fixture_path)
            if not path.exists():
                return ""
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return document.get("devices", {}).get(device, {}).get("config", {}).get("display_name", "")
        response = discovery.collector_request(
            "GET", f"/settings/devices/{quote(device, safe='')}/yaml", timeout=15)
        self.assertEqual(response.status_code, 200)
        from device_discovery import TextArea
        parser = TextArea("yaml_text")
        parser.feed(response.text)
        return (yaml.safe_load(parser.value) or {}).get("display_name")


if __name__ == "__main__":
    unittest.main()
