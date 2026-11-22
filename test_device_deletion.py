"""Synthetic-only tests for fail-closed permanent-device deletion primitives."""
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Waleed Khan

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from device_deletion import (deletion_eligibility, tombstone_return_is_safe,
                             verify_checkpoint_manifest, verify_device_checkpoint,
                             write_device_checkpoint)


class DeletionGateTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 20, 12, tzinfo=UTC)
        self.device = {"device": "synthetic-id", "label": "Synthetic Tag",
                       "account_status": "removed", "missing_successes": 3}
        self.discovery = {"status": "success", "succeeded": self.now - timedelta(minutes=5)}

    def test_only_recent_successful_removed_device_is_eligible(self):
        self.assertEqual(deletion_eligibility(self.device, self.discovery, self.now), (True, ""))
        for state, discovery, message in (
            ({**self.device, "account_status": "active"}, self.discovery, "not confirmed"),
            ({**self.device, "account_status": "unavailable"}, self.discovery, "not confirmed"),
            (self.device, {**self.discovery, "status": "unavailable"}, "did not succeed"),
            (self.device, {**self.discovery, "succeeded": self.now-timedelta(minutes=36)}, "stale"),
            (self.device, {**self.discovery, "succeeded": self.now+timedelta(seconds=1)}, "stale"),
        ):
            allowed, reason = deletion_eligibility(state, discovery, self.now)
            self.assertFalse(allowed)
            self.assertIn(message, reason)

    def test_checkpoint_is_atomic_private_and_restorable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "private"
            result = write_device_checkpoint(
                root, "synthetic-id", "Synthetic Tag",
                {"account_status": "removed", "missing_successes": 3},
                [{"reported": self.now, "latitude": -33.8, "longitude": 151.2}],
                {"device_state": {"location": {"locations": [{"time": 1}]},
                                   "config": {"endpoints": []}},
                 "forward_logs": {"active": [], "legacy": []}}, self.now,
            )
            archive = root / result["file"]
            if os.name == "posix":
                self.assertEqual(os.stat(root).st_mode & 0o777, 0o700)
                self.assertEqual(os.stat(archive).st_mode & 0o777, 0o600)
            self.assertEqual(result["detections"], 1)
            payload = verify_device_checkpoint(archive, "synthetic-id")
            self.assertEqual(payload["device"], "synthetic-id")
            self.assertEqual(len(payload["detections"]), 1)
            self.assertEqual(payload["captured_utc"], self.now.isoformat())
            with self.assertRaises(ValueError):
                verify_device_checkpoint(archive, "other-id")
            with self.assertRaises(FileExistsError):
                write_device_checkpoint(
                    root, "synthetic-id", "Synthetic Tag", {}, [], {}, self.now,
                )
            self.assertEqual(verify_checkpoint_manifest(archive, result["sha256"], "synthetic-id", 1)["device"],
                             "synthetic-id")
            archive.write_bytes(archive.read_bytes() + b"tamper")
            with self.assertRaises(ValueError):
                verify_checkpoint_manifest(archive, result["sha256"], "synthetic-id", 1)

    def test_missing_or_corrupt_checkpoint_fails_closed(self):
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.tar.gz"
            with self.assertRaises(FileNotFoundError):
                verify_checkpoint_manifest(missing, "0" * 64, "synthetic-id", 0)

    def test_same_id_repair_requires_clean_deleted_state(self):
        self.assertTrue(tombstone_return_is_safe(True, False, False, False))
        self.assertFalse(tombstone_return_is_safe(True, True, False, False))
        self.assertFalse(tombstone_return_is_safe(True, False, True, False))
        self.assertFalse(tombstone_return_is_safe(True, False, False, True))
        self.assertTrue(tombstone_return_is_safe(False, False, True, True))


if __name__ == "__main__":
    unittest.main()
