"""Real-PostgreSQL auth integration tests; run only in isolated QA Compose."""
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Waleed Khan

import os
import secrets
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from test_support import TEST_PASSWORD

if os.environ.get("FHT_ISOLATED_TEST") != "1":
    raise RuntimeError("Refusing PostgreSQL integration tests without FHT_ISOLATED_TEST=1")

os.environ.setdefault("DEMO_MODE", "0")
os.environ.setdefault("POLL_SECONDS", "600")
os.environ.setdefault("DISPLAY_TZ", "Australia/Sydney")
os.environ.setdefault("SESSION_KEY", secrets.token_urlsafe(32))
os.environ.setdefault("SECURE_COOKIE", "1")
os.environ.setdefault("INGEST_KEY", secrets.token_urlsafe(32))
os.environ.setdefault("DISCOVERY_URL", "http://discovery.invalid")

import tracker


class JsonResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload or {"status": "success"}

    def json(self):
        return self.payload


class PostgreSQLAuthIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tracker.connect() as db:
            identity = db.execute("SELECT current_database() AS name").fetchone()
            if identity["name"] != "tracker":
                raise RuntimeError("Unexpected database name for isolated test harness")

    def setUp(self):
        with tracker.connect() as db:
            db.execute("DELETE FROM detections WHERE device LIKE 'qa-auth-%'")
            db.execute("DELETE FROM discovered_devices WHERE device LIKE 'qa-auth-%'")

    def tearDown(self):
        with tracker.connect() as db:
            db.execute("DELETE FROM detections WHERE device LIKE 'qa-auth-%'")
            db.execute("DELETE FROM discovered_devices WHERE device LIKE 'qa-auth-%'")

    def logged_in(self, remember=False):
        client = TestClient(tracker.app, base_url="https://testserver")
        response = client.post("/api/login", json={"password": TEST_PASSWORD, "remember_me": remember})
        self.assertEqual(response.status_code, 204)
        token = client.cookies.get(tracker.COOKIE)
        csrf = client.cookies.get(tracker.CSRF_COOKIE)
        self.assertTrue(token)
        self.assertTrue(csrf)
        return client, token, csrf

    def test_session_persists_in_postgresql_across_clients_and_logout_revokes(self):
        client, token, csrf = self.logged_in(remember=True)
        token_digest = tracker.digest(token)
        try:
            with tracker.connect() as db:
                row = db.execute("SELECT actor,expires,csrf_digest FROM sessions WHERE digest=%s",
                                 (token_digest,)).fetchone()
                remaining = db.execute("SELECT expires-now() AS remaining FROM sessions WHERE digest=%s",
                                       (token_digest,)).fetchone()["remaining"].total_seconds()
            self.assertIsNotNone(row)
            self.assertEqual(row["actor"], tracker.OWNER_ACTOR)
            self.assertGreater(remaining, 29 * 24 * 60 * 60)
            self.assertEqual(row["csrf_digest"], tracker.digest(csrf))

            # A different client with the browser cookies proves that the session
            # is backed by PostgreSQL rather than process-local memory.
            with TestClient(tracker.app, base_url="https://testserver") as resumed:
                cookies = {tracker.COOKIE: token, tracker.CSRF_COOKIE: csrf}
                self.assertEqual(resumed.get("/api/history", cookies=cookies).status_code, 200)
                logout = resumed.post("/api/logout", headers={"X-CSRF-Token": csrf}, cookies=cookies)
                self.assertEqual(logout.status_code, 204)
                self.assertIn("Max-Age=0", logout.headers["set-cookie"])
                self.assertEqual(resumed.get("/api/history", cookies=cookies).status_code, 401)
            with tracker.connect() as db:
                self.assertIsNone(db.execute("SELECT 1 FROM sessions WHERE digest=%s",
                                             (token_digest,)).fetchone())
        finally:
            client.close()
            with tracker.connect() as db:
                db.execute("DELETE FROM sessions WHERE digest=%s", (token_digest,))

    def test_expired_persistent_session_is_rejected_by_database(self):
        client, token, _csrf = self.logged_in(remember=True)
        token_digest = tracker.digest(token)
        try:
            with tracker.connect() as db:
                lifetime = db.execute("SELECT expires - now() AS remaining FROM sessions WHERE digest=%s",
                                      (token_digest,)).fetchone()["remaining"].total_seconds()
                self.assertGreater(lifetime, 29 * 24 * 60 * 60)
                db.execute("UPDATE sessions SET expires=now()-interval '1 second' WHERE digest=%s",
                           (token_digest,))
            self.assertEqual(client.get("/api/history").status_code, 401)
            self.assertEqual(client.get("/api/export/json").status_code, 401)
        finally:
            client.close()
            with tracker.connect() as db:
                db.execute("DELETE FROM sessions WHERE digest=%s", (token_digest,))

    def test_unauthenticated_private_routes_are_rejected(self):
        with TestClient(tracker.app, base_url="https://testserver") as client:
            for method, path, kwargs in (
                ("get", "/api/history", {}),
                ("get", "/api/export/json", {}),
                ("get", "/api/export/csv", {}),
                ("get", "/api/export/gpx", {}),
                ("post", "/api/refresh", {}),
                ("post", "/api/devices/refresh", {}),
                ("post", "/api/devices/delete", {"json": {"device_id": "qa-auth-id", "confirm_name": "QA"}}),
                ("post", "/api/logout", {}),
            ):
                self.assertEqual(getattr(client, method)(path, **kwargs).status_code, 401, path)

    def test_refresh_discovery_and_deletion_enforce_csrf_and_owner_session(self):
        with tracker.connect() as db:
            db.execute("INSERT INTO discovered_devices(device,label,account_status) VALUES (%s,%s,'active')",
                       ("qa-auth-device", "QA synthetic tag"))
        client, token, csrf = self.logged_in()
        try:
            bad = {"X-CSRF-Token": "invalid"}
            self.assertEqual(client.post("/api/refresh", headers=bad).status_code, 403)
            self.assertEqual(client.post("/api/devices/refresh", headers=bad).status_code, 403)
            self.assertEqual(client.post("/api/devices/delete", json={"device_id": "qa-auth-device",
                                "confirm_name": "QA synthetic tag"}, headers=bad).status_code, 403)

            # Live mode makes demo refresh inapplicable after auth/CSRF succeeds.
            self.assertEqual(client.post("/api/refresh", headers={"X-CSRF-Token": csrf}).status_code, 409)
            with patch.object(tracker.httpx, "post", return_value=JsonResponse()):
                response = client.post("/api/devices/refresh", headers={"X-CSRF-Token": csrf})
            self.assertEqual(response.status_code, 200)

            calls = []
            def coordinator(url, **kwargs):
                calls.append((url, kwargs))
                return JsonResponse(409, {"detail": "Synthetic active device rejected"})
            with patch.object(tracker.httpx, "post", side_effect=coordinator):
                denied = client.post("/api/devices/delete", json={"device_id": "qa-auth-device",
                                            "confirm_name": "QA synthetic tag"},
                                     headers={"X-CSRF-Token": csrf, "Remote-User": "attacker"})
            self.assertEqual(denied.status_code, 409)
            self.assertEqual(calls[0][1]["json"]["actor"], tracker.OWNER_ACTOR)
            self.assertEqual(calls[0][1]["json"]["device_id"], "qa-auth-device")
            with tracker.connect() as db:
                self.assertIsNotNone(db.execute("SELECT 1 FROM discovered_devices WHERE device=%s",
                                                ("qa-auth-device",)).fetchone())
        finally:
            client.close()
            with tracker.connect() as db:
                db.execute("DELETE FROM sessions WHERE digest=%s", (tracker.digest(token),))

    def test_collector_bearer_auth_is_independent_of_dashboard_session(self):
        payload = {"device": "qa-auth-ingest", "label": "Synthetic ingest", "reported": "2026-11-20T00:00:00Z",
                   "latitude": 12.34, "longitude": 45.67, "accuracy": 4.2, "report_id": "qa-auth-report"}
        with TestClient(tracker.app, base_url="https://testserver") as client:
            self.assertEqual(client.post("/api/ingest", json=payload).status_code, 401)
            accepted = client.post("/api/ingest", json=payload,
                                   headers={"Authorization": "Bearer " + os.environ["INGEST_KEY"]})
            self.assertEqual(accepted.status_code, 204)
            with tracker.connect() as db:
                row = db.execute("SELECT latitude,longitude,reported FROM detections WHERE device=%s",
                                 ("qa-auth-ingest",)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["latitude"], payload["latitude"])
            self.assertEqual(row["longitude"], payload["longitude"])
        with tracker.connect() as db:
            db.execute("DELETE FROM detections WHERE device=%s", ("qa-auth-ingest",))

if __name__ == "__main__":
    unittest.main()
