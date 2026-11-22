"""Synthetic API guard tests; no database or production services are used."""
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Waleed Khan

import os
import secrets
import unittest
from unittest.mock import patch

os.environ.setdefault("DEMO_MODE", "0")
os.environ.setdefault("POLL_SECONDS", "600")
os.environ.setdefault("DISPLAY_TZ", "Australia/Sydney")
os.environ.setdefault("DB_PASSWORD", "synthetic-only")
from test_support import _TEST_HASH_FILE

os.environ.setdefault("DASHBOARD_PASSWORD_HASH_FILE", str(_TEST_HASH_FILE))
os.environ.setdefault("SESSION_KEY", secrets.token_urlsafe(32))
os.environ.setdefault("SECURE_COOKIE", "0")
os.environ.setdefault("INGEST_KEY", secrets.token_urlsafe(32))
os.environ.setdefault("DISCOVERY_URL", "http://discovery.invalid")

from fastapi.testclient import TestClient
import tracker


class FakeResult:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeDB:
    def __init__(self, csrf_digest=None, actor="owner"):
        self.csrf_digest = csrf_digest
        self.actor = actor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=()):
        if "SELECT actor FROM sessions" in query:
            return FakeResult({"actor": self.actor})
        if "SELECT csrf_digest FROM sessions" in query:
            return FakeResult({"csrf_digest": self.csrf_digest})
        if "SELECT label FROM discovered_devices" in query:
            return FakeResult({"label": "Synthetic Tag"})
        return FakeResult(None)


class DeviceDeletionApiTests(unittest.TestCase):
    def setUp(self):
        self.session = "s" * 43
        self.csrf = "c" * 43
        self.cookies = {tracker.COOKIE: self.session, tracker.CSRF_COOKIE: self.csrf}
        self.db = FakeDB(tracker.digest(self.csrf))
        self.payload = {"device_id": "synthetic-id", "confirm_name": "Synthetic Tag"}

    def test_unauthorized_request_is_rejected(self):
        with patch.object(tracker, "connect", return_value=self.db):
            response = TestClient(tracker.app).post("/api/devices/delete", json=self.payload)
        self.assertEqual(response.status_code, 401)

    def test_csrf_invalid_request_is_rejected(self):
        with patch.object(tracker, "connect", return_value=self.db):
            response = TestClient(tracker.app).post("/api/devices/delete", json=self.payload,
                                                   cookies=self.cookies,
                                                   headers={"Remote-User": "spoofed-owner", "X-CSRF-Token": "wrong"})
        self.assertEqual(response.status_code, 403)

    def test_active_device_coordinator_rejection_is_not_reported_as_success(self):
        class Response:
            status_code = 409
            def json(self):
                return {"detail": "Google Find Hub has not confirmed this device removed."}

        with patch.object(tracker, "connect", return_value=self.db), \
             patch.object(tracker.httpx, "post", return_value=Response()):
            response = TestClient(tracker.app).post("/api/devices/delete", json=self.payload,
                                                    cookies=self.cookies,
                                                    headers={"Remote-User": "spoofed-owner", "X-CSRF-Token": self.csrf})
        self.assertEqual(response.status_code, 409)
        self.assertIn("not confirmed", response.json()["detail"])

    def test_name_confirmation_mismatch_is_rejected(self):
        payload = {**self.payload, "confirm_name": "Wrong name"}
        with patch.object(tracker, "connect", return_value=self.db):
            response = TestClient(tracker.app).post("/api/devices/delete", json=payload,
                                                    cookies=self.cookies,
                                                    headers={"Remote-User": "spoofed-owner", "X-CSRF-Token": self.csrf})
        self.assertEqual(response.status_code, 409)

    def test_delete_requires_owner_identity_even_with_a_valid_session_and_csrf(self):
        self.db.actor = "other"
        with patch.object(tracker, "connect", return_value=self.db):
            response = TestClient(tracker.app).post("/api/devices/delete", json=self.payload,
                                                    cookies=self.cookies,
                                                    headers={"Remote-User": "spoofed-owner", "X-CSRF-Token": self.csrf})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
