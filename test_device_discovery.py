"""Synthetic-only tests for Find Hub device discovery and registration."""
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Waleed Khan

import os
import secrets
import unittest
from collections import Counter
from urllib.parse import parse_qs

import httpx
import yaml

os.environ.setdefault("INGEST_KEY", secrets.token_urlsafe(32))

from device_discovery import (
    TRACKER_URL, account_status_after_missing, endpoint_config, ensure_devices_configured,
    delay_until_next_attempt, fetch_inventory, minute_for_device, parse_device_cards, reconcile_inventory_state,
)


class DiscoveryTests(unittest.TestCase):
    def test_synthetic_device_cards_use_stable_ids_and_latest_names(self):
        first = '<div class="device-card" data-canonic-id="fixture-001"><b class="device-card-name">Synthetic Tag</b></div>'
        renamed = '<div class="device-card" data-canonic-id="fixture-001"><b class="device-card-name">Renamed Tag</b></div>'
        self.assertEqual(parse_device_cards(first), {"fixture-001": "Synthetic Tag"})
        self.assertEqual(parse_device_cards(first + renamed), {"fixture-001": "Renamed Tag"})

    def test_synthetic_refresh_success_and_failure_classification(self):
        def handler(request):
            if request.url.path == "/devices/refresh":
                return httpx.Response(200, text='<div class="device-card" data-canonic-id="fixture-7"><b class="device-card-name">Bag</b></div>')
            return httpx.Response(429)
        with httpx.Client(base_url="http://collector", transport=httpx.MockTransport(handler)) as client:
            self.assertEqual(fetch_inventory(client), {"fixture-7": "Bag"})
        for status, expected, text in ((401, "authentication_error", ""),
                                       (429, "rate_limited", ""),
                                       (503, "rate_limited", "Google quota rate limit")):
            with self.subTest(status=status):
                with httpx.Client(base_url="http://collector", transport=httpx.MockTransport(
                        lambda request, status=status, text=text: httpx.Response(status, text=text))) as client:
                    with self.assertRaises(Exception) as caught:
                        fetch_inventory(client)
                    self.assertEqual(caught.exception.status, expected)
        with httpx.Client(base_url="http://collector", transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text="Not signed in yet. Sign in with Google first."))) as client:
            with self.assertRaises(Exception) as caught:
                fetch_inventory(client)
            self.assertEqual(caught.exception.status, "authentication_error")

    def test_new_synthetic_device_gets_one_staggered_tracker_endpoint(self):
        configs = {"fixture-new": {"display_name": "", "endpoints": []}}
        post_count = 0

        def handler(request):
            nonlocal post_count
            device = request.url.path.rstrip("/").split("/")[-2]
            if request.method == "GET":
                contents = yaml.safe_dump(configs[device], sort_keys=False)
                return httpx.Response(200, text=f'<textarea name="yaml_text">{contents}</textarea>')
            post_count += 1
            values = parse_qs(request.content.decode())
            configs[device] = yaml.safe_load(values["yaml_text"][0])
            return httpx.Response(200, text="saved")

        with httpx.Client(base_url="http://collector", transport=httpx.MockTransport(handler)) as client:
            loads = Counter({0: 1, 1: 1, 2: 1, 3: 1, 7: 1, 8: 1, 9: 1})
            added = ensure_devices_configured(client, {"fixture-new": "Synthetic Bag"}, loads)
            self.assertEqual(added, ["fixture-new"])
            self.assertEqual(ensure_devices_configured(client, {"fixture-new": "Renamed Synthetic Bag"}, loads), [])

        self.assertEqual(post_count, 1)
        endpoints = configs["fixture-new"]["endpoints"]
        self.assertEqual(len(endpoints), 1)
        self.assertEqual(endpoints[0]["url"], TRACKER_URL)
        self.assertTrue(endpoints[0]["cron"].endswith(" * * * *"))
        self.assertIn('"device":"{{tracker_id}}"', endpoints[0]["body"])
        self.assertIn('"label":"{{tracker_id}}"', endpoints[0]["body"])
        self.assertEqual(endpoints[0]["headers"]["Authorization"], "Bearer " + __import__("device_discovery").API_TOKEN)

    def test_stagger_phases_and_missing_device_retention(self):
        loads = Counter()
        phases = {minute_for_device(f"fixture-{index}", loads) for index in range(10)}
        self.assertEqual(phases, set(range(10)))
        self.assertEqual([account_status_after_missing(count) for count in (1, 2, 3, 10)],
                         ["unavailable", "unavailable", "removed", "removed"])
        old = {"fixture-001": {"label": "Old name", "account_status": "active", "missing_successes": 0}}
        renamed = reconcile_inventory_state(old, {"fixture-001": "New name"})
        self.assertEqual(renamed["fixture-001"]["label"], "New name")
        one_miss = reconcile_inventory_state(renamed, {})
        self.assertEqual(one_miss["fixture-001"]["account_status"], "unavailable")
        two_misses = reconcile_inventory_state(one_miss, {})
        self.assertEqual(two_misses["fixture-001"]["account_status"], "unavailable")
        returned = reconcile_inventory_state(two_misses, {"fixture-001": "Returned name"})
        self.assertEqual(returned["fixture-001"]["account_status"], "active")
        self.assertEqual(returned["fixture-001"]["missing_successes"], 0)
        self.assertIn("fixture-001", reconcile_inventory_state(two_misses, {}).keys())

    def test_endpoint_body_matches_existing_live_payload_shape(self):
        endpoint = endpoint_config(secrets.token_urlsafe(32), 4)
        self.assertEqual(endpoint["cron"], "4-59/10 * * * *")
        self.assertIn('"reported":"{{google_timestamp}}"', endpoint["body"])
        self.assertFalse(endpoint["only_most_recent"])
        self.assertFalse(endpoint["skip_if_already_seen"])

    def test_restart_respects_persisted_thirty_minute_schedule(self):
        from datetime import UTC, datetime, timedelta
        now = datetime.now(UTC)
        self.assertEqual(delay_until_next_attempt(None, now), 0)
        self.assertEqual(delay_until_next_attempt(now, now), 1800)
        self.assertEqual(delay_until_next_attempt(now - timedelta(minutes=29), now), 60)
        self.assertEqual(delay_until_next_attempt(now - timedelta(minutes=31), now), 0)


if __name__ == "__main__":
    unittest.main()
