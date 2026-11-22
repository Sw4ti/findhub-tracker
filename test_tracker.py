# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Waleed Khan

import os
import unittest
from datetime import UTC
from unittest.mock import patch
from xml.etree import ElementTree

from fastapi.testclient import TestClient
from pydantic import ValidationError
from collector_status import ENDPOINT, snapshot
from test_support import TEST_PASSWORD, _TEST_HASH_FILE

os.environ["DASHBOARD_PASSWORD_HASH_FILE"] = str(_TEST_HASH_FILE)

from tracker import COOKIE, DEMO, Fix, app, connect, demo_poll, device_status, digest, insert, retry_delay


class TrackerTests(unittest.TestCase):
    def test_collector_status_distinguishes_empty_poll_and_error(self):
        def record(name, status, time=120):
            return {
                "config": {"display_name": name, "endpoints": [{"url": ENDPOINT, "headers": {"secret": "fixture"}}]},
                "endpoint_state": {ENDPOINT: {"last_forward_status": status, "last_forward_time": time}},
            }
        data = {"devices": {
            "empty": record("Empty", "no location"),
            "failed": record("Failed", "no location"),
            "delivered": record("Delivered", "ok"),
            "pending": record("Pending", "", None),
        }}
        logs = (
            "119\tINFO\twebui.scheduler\tLocate failed for Failed: fixture (1/3 before alerting)\n"
            "40\tINFO\twebui.scheduler\tLocate failed for Empty: fixture (1/3 before alerting)"
        )
        result = {item["device"]: item for item in snapshot(data, logs)}
        self.assertEqual({key: item["status"] for key, item in result.items()},
                         {"empty": "no-report", "failed": "error", "delivered": "reported", "pending": "pending"})
        self.assertEqual(set(result["failed"]), {"device", "label", "status", "attempted"})

    def test_timestamp_and_accuracy(self):
        values = dict(device="fixture", latitude=-33.8, longitude=151.2, accuracy="")
        a = Fix(**values, reported="2026-01-01T11:00:00+11:00")
        b = Fix(**values, reported="1767225600")
        self.assertEqual(a.reported.astimezone(UTC), b.reported.astimezone(UTC))
        self.assertIsNone(a.accuracy)
        with self.assertRaises(ValidationError):
            Fix(**values, reported="2026-01-01T00:00:00")

    def test_dedup_and_persistence(self):
        a = Fix(device="fixture", latitude=-33.8, longitude=151.2, reported="2026-01-01T00:00:00Z")
        b = a.model_copy(update={"accuracy": 42})
        try:
            with connect() as db:
                self.assertEqual(insert(db, "test", a, "Fixture"), 1)
                self.assertEqual(insert(db, "test", b, "Fixture"), 0)
            with connect() as db:
                saved = db.execute("SELECT * FROM detections WHERE source='test'").fetchone()
                self.assertEqual(saved["reported"], a.reported)
                self.assertIsNotNone(saved["collected"])
        finally:
            with connect() as db:
                db.execute("DELETE FROM detections WHERE source='test'")

    def test_history_auth_filters_and_exports(self):
        if DEMO:
            demo_poll()
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/history").status_code, 401)
            self.assertEqual(client.get("/api/export/gpx").status_code, 401)
            self.assertEqual(client.post("/api/login", json={"password": "wrong"}).status_code, 401)
            response = client.post("/api/login", json={"password": TEST_PASSWORD})
            self.assertEqual(response.status_code, 204)
            self.assertIn("HttpOnly", response.headers["set-cookie"])
            history = client.get("/api/history").json()
            self.assertIn("discovery", history)
            self.assertIn("discovery_enabled", history)
            if DEMO:
                self.assertEqual(len(history["points"]), 12)
                self.assertEqual(len(client.get("/api/history?device=demo-keys").json()["points"]), 4)
                self.assertEqual(client.get("/api/history?end=2020-01-01T00:00:00Z").json()["points"], [])
                self.assertEqual(len(client.get("/api/export/json").json()), 12)
                self.assertEqual(len(client.get("/api/export/csv").text.splitlines()), 13)
                document = ElementTree.fromstring(client.get("/api/export/gpx").content)
                self.assertEqual(len(document), 12)
            logout = client.post("/api/logout", headers={
                "X-CSRF-Token": client.cookies.get("tracker_csrf", "")
            })
            self.assertEqual(logout.status_code, 204)
            self.assertIn("Max-Age=0", logout.headers["set-cookie"])
            self.assertEqual(client.get("/api/history").status_code, 401)

    def test_logout_revokes_server_session_for_all_private_routes(self):
        with TestClient(app, base_url="https://testserver") as client:
            response = client.post("/api/login", json={"password": TEST_PASSWORD})
            self.assertEqual(response.status_code, 204)
            token = client.cookies.get(COOKIE)
            self.assertTrue(token)
            session_digest = digest(token)
            try:
                self.assertEqual(client.get("/api/history").status_code, 200)
                logout = client.post("/api/logout", headers={
                    "X-CSRF-Token": client.cookies.get("tracker_csrf", "")
                })
                self.assertEqual(logout.status_code, 204)
                self.assertIn("Max-Age=0", logout.headers["set-cookie"])
                with connect() as db:
                    self.assertIsNone(db.execute("SELECT 1 FROM sessions WHERE digest=%s",
                                                 (session_digest,)).fetchone())
                # Re-send the revoked token to simulate a stolen/stale browser cookie.
                for method, path in (("get", "/api/history"), ("get", "/api/export/json"),
                                     ("get", "/api/export/csv"), ("get", "/api/export/gpx"),
                                     ("post", "/api/devices/refresh"), ("post", "/api/refresh"),
                                     ("post", "/api/logout")):
                    self.assertEqual(getattr(client, method)(path, cookies={COOKIE: token}).status_code, 401, path)
            finally:
                with connect() as db:
                    db.execute("DELETE FROM sessions WHERE digest=%s", (session_digest,))

    def test_expired_session_cannot_access_protected_routes(self):
        with TestClient(app) as client:
            response = client.post("/api/login", json={"password": TEST_PASSWORD})
            self.assertEqual(response.status_code, 204)
            token = client.cookies.get(COOKIE)
            session_digest = digest(token)
            try:
                with connect() as db:
                    db.execute("UPDATE sessions SET expires=now()-interval '1 second' WHERE digest=%s",
                               (session_digest,))
                self.assertEqual(client.get("/api/history").status_code, 401)
                self.assertEqual(client.get("/api/export/json").status_code, 401)
                self.assertEqual(client.post("/api/devices/refresh").status_code, 401)
                self.assertEqual(client.post("/api/logout").status_code, 401)
            finally:
                with connect() as db:
                    db.execute("DELETE FROM sessions WHERE digest=%s", (session_digest,))

    def test_exponential_backoff_is_bounded(self):
        self.assertEqual([retry_delay(i) for i in range(1, 5)], [15, 30, 60, 120])
        self.assertLessEqual(retry_delay(1000), 3600)

    def test_discovered_devices_are_visible_before_the_first_location(self):
        with patch("tracker.Path.read_text", side_effect=OSError("synthetic status file unavailable")):
            devices = device_status([], None, [{"device": "fixture-new", "label": "Synthetic renamed tag",
                                                 "account_status": "active"}])
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["device"], "fixture-new")
        self.assertEqual(devices[0]["label"], "Synthetic renamed tag")
        self.assertEqual(devices[0]["total"], 0)
        self.assertEqual(devices[0]["account_status"], "active")


if __name__ == "__main__":
    unittest.main()
