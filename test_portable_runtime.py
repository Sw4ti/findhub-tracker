"""Runtime smoke tests enabled only in the isolated release QA Compose project."""
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Waleed Khan

import os
import unittest
from datetime import UTC, datetime

import httpx


@unittest.skipUnless(os.environ.get("PORTABLE_QA_PASSWORD"), "isolated release QA environment only")
class PortableRuntimeTests(unittest.TestCase):
    def test_login_protection_synthetic_discovery_ingestion_dedup_and_logout(self):
        from device_discovery import reconcile

        base = os.environ.get("PORTABLE_QA_BASE", "http://app:8080")
        password = os.environ["PORTABLE_QA_PASSWORD"]
        ingest_key = os.environ["INGEST_KEY"]
        device_id = "synthetic-portable-qa-device"
        with httpx.Client(base_url=base, timeout=10) as client:
            dashboard = client.get("/")
            self.assertEqual(dashboard.status_code, 200)
            self.assertIn("Find Hub Tracker", dashboard.text)
            self.assertEqual(client.get("/api/history").status_code, 401)
            self.assertEqual(client.get("/api/export/json").status_code, 401)
            login = client.post("/api/login", json={"password": password, "remember_me": True})
            self.assertEqual(login.status_code, 204)
            self.assertIn("HttpOnly", login.headers.get("set-cookie", ""))
            self.assertEqual(client.post("/api/devices/refresh").status_code, 403)
            self.assertEqual(client.post("/api/refresh").status_code, 403)

            reconcile({device_id: "Synthetic Portable QA Tracker"})
            payload = {"device": device_id, "label": "Synthetic Portable QA Tracker",
                       "report_id": "synthetic-report-001", "reported": "2026-11-21T20:00:00Z",
                       "latitude": -31.25, "longitude": 150.75, "accuracy": 12.5,
                       "status": "Synthetic test"}
            self.assertEqual(client.post("/api/ingest", json=payload).status_code, 401)
            headers = {"Authorization": "Bearer " + ingest_key}
            self.assertEqual(client.post("/api/ingest", json=payload, headers=headers).status_code, 204)
            self.assertEqual(client.post("/api/ingest", json=payload, headers=headers).status_code, 204)
            history = client.get("/api/history").json()
            points = [point for point in history["points"] if point["device"] == device_id]
            self.assertEqual(len(points), 1)
            self.assertEqual(datetime.fromisoformat(points[0]["reported"]),
                             datetime(2026, 11, 21, 20, 0, tzinfo=UTC))
            self.assertEqual(points[0]["latitude"], -31.25)
            self.assertEqual(points[0]["longitude"], 150.75)
            row = next(item for item in history["devices"] if item["device"] == device_id)
            self.assertEqual(row["label"], "Synthetic Portable QA Tracker")
            self.assertEqual(row["total"], 1)
            self.assertEqual(client.get("/api/export/json").status_code, 200)
            csrf = client.cookies.get("tracker_csrf")
            logout = client.post("/api/logout", headers={"X-CSRF-Token": csrf or ""})
            self.assertEqual(logout.status_code, 204)
            self.assertEqual(client.get("/api/history").status_code, 401)

    def test_collector_basic_auth_is_independent_of_dashboard_login(self):
        password = os.environ["COLLECTOR_PASSWORD"]
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            response = client.get("http://collector:4321/auth")
            self.assertEqual(response.status_code, 401)
            authenticated = client.get("http://collector:4321/auth", auth=("tracker", password))
            self.assertIn(authenticated.status_code, (200, 302, 303))


if __name__ == "__main__":
    unittest.main()
