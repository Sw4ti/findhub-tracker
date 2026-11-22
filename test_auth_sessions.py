"""Isolated tracker-only auth tests; credentials and hashes are runtime-generated."""
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Waleed Khan

import os
import secrets
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient
from test_support import TEST_PASSWORD, _TEST_HASH_FILE

os.environ.setdefault("DEMO_MODE", "0")
os.environ.setdefault("POLL_SECONDS", "600")
os.environ.setdefault("DISPLAY_TZ", "Australia/Sydney")
os.environ.setdefault("DB_PASSWORD", "synthetic-only")
os.environ.setdefault("DASHBOARD_PASSWORD_HASH_FILE", str(_TEST_HASH_FILE))
os.environ.setdefault("SESSION_KEY", secrets.token_urlsafe(32))
os.environ.setdefault("SECURE_COOKIE", "1")
os.environ.setdefault("INGEST_KEY", secrets.token_urlsafe(32))
os.environ.setdefault("DISCOVERY_URL", "http://discovery.invalid")

import tracker


class Result:
    def __init__(self, row=None, rows=None, rowcount=0):
        self.row = row
        self.rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class MemoryDB:
    def __init__(self):
        self.sessions = {}
        self.attempts = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=()):
        normalized = " ".join(query.split())
        if "SELECT count(*) AS total FROM login_attempts WHERE client_key=%s" in normalized:
            return Result({"total": sum(key == params[0] for key in self.attempts)})
        if "SELECT count(*) AS total FROM login_attempts" in normalized:
            return Result({"total": len(self.attempts)})
        if "INSERT INTO login_attempts" in normalized:
            self.attempts.append(params[0])
            return Result(rowcount=1)
        if "INSERT INTO sessions" in normalized:
            session_digest, lifetime, csrf_digest, actor = params
            self.sessions[session_digest] = {
                "csrf_digest": csrf_digest, "actor": actor, "lifetime": lifetime,
                "expires_at": datetime.now(UTC) + timedelta(seconds=lifetime),
            }
            return Result(rowcount=1)
        if "SELECT actor FROM sessions" in normalized:
            session = self.sessions.get(params[0])
            valid = session and session["expires_at"] > datetime.now(UTC)
            return Result({"actor": session["actor"]} if valid else None)
        if "SELECT csrf_digest FROM sessions" in normalized:
            session = self.sessions.get(params[0])
            valid = session and session["expires_at"] > datetime.now(UTC)
            return Result({"csrf_digest": session["csrf_digest"]} if valid else None)
        if "DELETE FROM sessions WHERE digest=%s" in normalized:
            return Result(rowcount=int(self.sessions.pop(params[0], None) is not None))
        if "SELECT device, label, reported, collected" in normalized:
            return Result(rows=[])
        if "SELECT device, max(label)" in normalized:
            return Result(rows=[])
        if "SELECT device,label,account_status" in normalized:
            return Result(rows=[])
        if "SELECT status,attempted,succeeded,error FROM discovery_state" in normalized:
            return Result(None)
        if "SELECT * FROM poll_state" in normalized:
            return Result(None)
        return Result()


class PersistentLoginTests(unittest.TestCase):
    def setUp(self):
        self.db = MemoryDB()
        self.env = patch.dict(os.environ, {
            "DEMO_MODE": "0", "DASHBOARD_PASSWORD_HASH_FILE": str(_TEST_HASH_FILE),
            "SESSION_KEY": secrets.token_urlsafe(32), "SECURE_COOKIE": "1",
            "TRUSTED_PROXY_IPS": "",
        })
        self.env.start()
        self.connect_patch = patch.object(tracker, "connect", return_value=self.db)
        self.connect_patch.start()
        self.client = TestClient(tracker.app, base_url="https://tracker.test")

    def tearDown(self):
        self.client.close()
        self.connect_patch.stop()
        self.env.stop()

    def test_argon2id_hash_accepts_only_the_configured_password(self):
        self.assertTrue(tracker.dashboard_password_matches(TEST_PASSWORD))
        self.assertFalse(tracker.dashboard_password_matches(TEST_PASSWORD + "x"))
        self.assertTrue(_TEST_HASH_FILE.read_text(encoding="ascii").startswith("$argon2id$"))

    def test_missing_hash_file_fails_closed_without_creating_a_session(self):
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD_HASH_FILE": str(_TEST_HASH_FILE.with_name("missing"))}):
            response = self.client.post("/api/login", json={"password": TEST_PASSWORD})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.db.sessions, {})

    def test_ordinary_login_keeps_short_default_and_secure_cookie_flags(self):
        response = self.client.post("/api/login", json={"password": TEST_PASSWORD})
        self.assertEqual(response.status_code, 204)
        session = next(iter(self.db.sessions.values()))
        self.assertEqual(session["lifetime"], tracker.SESSION_SECONDS)
        self.assertEqual(session["actor"], tracker.OWNER_ACTOR)
        cookies = "\n".join(response.headers.get_list("set-cookie")).lower()
        self.assertIn("max-age=43200", cookies)
        self.assertIn("httponly", cookies)
        self.assertIn("secure", cookies)
        self.assertIn("samesite=strict", cookies)
        self.assertIn("path=/", cookies)
        self.assertEqual(self.client.get("/api/history").status_code, 200)

    def test_remember_choice_sets_finite_30_day_db_and_cookie_expiry(self):
        response = self.client.post("/api/login", json={"password": TEST_PASSWORD, "remember_me": True})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(next(iter(self.db.sessions.values()))["lifetime"], 30 * 24 * 60 * 60)
        cookies = "\n".join(response.headers.get_list("set-cookie")).lower()
        self.assertIn("max-age=2592000", cookies)
        self.assertIn("httponly", cookies)
        self.assertIn("secure", cookies)
        self.assertIn("samesite=strict", cookies)
        self.assertNotIn("localstorage", Path("ui/main.tsx").read_text(encoding="utf-8").lower())

    def test_wrong_password_does_not_create_a_session(self):
        response = self.client.post("/api/login", json={"password": TEST_PASSWORD + "wrong"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.db.sessions, {})

    def test_login_rate_limit_rejects_attempts_after_ten_client_failures(self):
        for _ in range(tracker.LOGIN_CLIENT_LIMIT):
            self.assertEqual(self.client.post("/api/login", json={"password": TEST_PASSWORD + "x"}).status_code, 401)
        limited = self.client.post("/api/login", json={"password": TEST_PASSWORD})
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.headers.get("retry-after"), "60")
        self.assertEqual(self.db.sessions, {})

    def test_global_login_rate_limit_is_retained(self):
        self.db.attempts = [f"synthetic-{index}" for index in range(tracker.LOGIN_GLOBAL_LIMIT)]
        response = self.client.post("/api/login", json={"password": TEST_PASSWORD})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.db.sessions, {})

    def test_spoofed_forwarding_header_is_ignored_for_untrusted_peer(self):
        with patch.dict(os.environ, {"TRUSTED_PROXY_IPS": "172.20.0.2"}):
            client = TestClient(tracker.app, base_url="https://tracker.test", client=("198.51.100.10", 5000))
            try:
                for address in ("203.0.113.7", "203.0.113.8"):
                    response = client.post("/api/login", json={"password": TEST_PASSWORD + "x"},
                                           headers={"X-Tracker-Client-IP": address})
                    self.assertEqual(response.status_code, 401)
            finally:
                client.close()
        self.assertEqual(len(self.db.attempts), 2)
        self.assertEqual(len(set(self.db.attempts)), 1)
        self.assertNotIn("198.51.100.10", self.db.attempts)

    def test_forwarded_client_ip_is_used_only_from_configured_proxy_peer(self):
        with patch.dict(os.environ, {"TRUSTED_PROXY_IPS": "172.20.0.2"}):
            client = TestClient(tracker.app, base_url="https://tracker.test", client=("172.20.0.2", 5000))
            try:
                for address in ("198.51.100.7", "198.51.100.8"):
                    self.assertEqual(client.post("/api/login", json={"password": TEST_PASSWORD + "x"},
                                                 headers={"X-Tracker-Client-IP": address}).status_code, 401)
            finally:
                client.close()
        self.assertEqual(len(self.db.attempts), 2)
        self.assertEqual(len(set(self.db.attempts)), 2)

    def test_successful_reauthentication_rotates_and_revokes_prior_session(self):
        self.client.post("/api/login", json={"password": TEST_PASSWORD})
        prior = self.client.cookies.get(tracker.COOKIE)
        prior_digest = tracker.digest(prior)
        self.client.post("/api/login", json={"password": TEST_PASSWORD})
        current = self.client.cookies.get(tracker.COOKIE)
        self.assertNotEqual(prior, current)
        self.assertNotIn(prior_digest, self.db.sessions)
        self.assertEqual(len(self.db.sessions), 1)

    def test_spoofed_identity_header_cannot_change_owner_session(self):
        response = self.client.post("/api/login", json={"password": TEST_PASSWORD},
                                    headers={"Remote-User": "attacker"})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(next(iter(self.db.sessions.values()))["actor"], tracker.OWNER_ACTOR)

    def test_expired_remembered_session_rejects_protected_api(self):
        self.client.post("/api/login", json={"password": TEST_PASSWORD, "remember_me": True})
        session = next(iter(self.db.sessions.values()))
        session["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
        self.assertEqual(self.client.get("/api/history").status_code, 401)

    def test_logout_requires_csrf_revokes_session_and_clears_cookies(self):
        login = self.client.post("/api/login", json={"password": TEST_PASSWORD, "remember_me": True})
        self.assertEqual(login.status_code, 204)
        stale_session = self.client.cookies.get(tracker.COOKIE)
        session_digest = tracker.digest(stale_session)
        self.assertEqual(self.client.post("/api/logout").status_code, 403)
        csrf = self.client.cookies.get(tracker.CSRF_COOKIE)
        logout = self.client.post("/api/logout", headers={"X-CSRF-Token": csrf})
        self.assertEqual(logout.status_code, 204)
        self.assertNotIn(session_digest, self.db.sessions)
        self.assertIn("max-age=0", "\n".join(logout.headers.get_list("set-cookie")).lower())
        self.client.cookies.set(tracker.COOKIE, stale_session)
        self.assertEqual(self.client.get("/api/history").status_code, 401)

    def test_all_private_api_routes_require_tracker_session(self):
        requests = (
            ("GET", "/api/history", None), ("GET", "/api/export/csv", None),
            ("POST", "/api/refresh", None), ("POST", "/api/devices/refresh", None),
            ("POST", "/api/devices/delete", {"device_id": "synthetic", "confirm_name": "Synthetic"}),
        )
        for method, path, body in requests:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.client.request(method, path, json=body).status_code, 401)

    def test_manual_refresh_and_discovery_require_csrf(self):
        self.client.post("/api/login", json={"password": TEST_PASSWORD})
        for path in ("/api/refresh", "/api/devices/refresh"):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path).status_code, 403)
                response = self.client.post(path, headers={
                    "X-CSRF-Token": self.client.cookies.get(tracker.CSRF_COOKIE, "")
                })
                self.assertEqual(response.status_code, 409 if path == "/api/refresh" else 503)

    def test_collector_ingest_requires_bearer_token(self):
        payload = {"device": "synthetic-device", "reported": "2026-01-01T00:00:00Z",
                   "latitude": 12.34, "longitude": 45.67}
        self.assertEqual(self.client.post("/api/ingest", json=payload).status_code, 401)

    def test_mobile_login_form_and_logout_have_no_authelia_or_localstorage_dependency(self):
        source = Path("ui/main.tsx").read_text(encoding="utf-8")
        self.assertNotIn("/authelia/logout", source)
        self.assertNotIn("localStorage", source)
        self.assertIn("rememberMe", source)
        self.assertIn("setSignedOut(true)", source)
        self.assertIn("X-CSRF-Token", source)
        stylesheet = Path("ui/style.css").read_text(encoding="utf-8")
        self.assertIn(".signin", stylesheet)
        self.assertIn("@media (max-width: 430px)", stylesheet)


if __name__ == "__main__":
    unittest.main()
