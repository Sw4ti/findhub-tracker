"""Internal device discovery bridge for the pinned Google Find Hub collector."""
import asyncio
import hashlib
import hmac
import html.parser
import json
import logging
import os
import re
import time
import uuid
from collections import Counter
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from pathlib import Path

import httpx
import psycopg
import yaml
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from psycopg.rows import dict_row
from device_deletion import (DeletionRejected, deletion_eligibility,
                             tombstone_return_is_safe, verify_checkpoint_manifest,
                             write_device_checkpoint)

COLLECTOR = os.environ.get("COLLECTOR_BASE_URL", "http://collector:4321").rstrip("/")
INTERVAL = max(300, int(os.environ.get("DISCOVERY_INTERVAL", "1800")))
TRACKER_URL = "http://app:8080/api/ingest"
API_TOKEN = os.environ.get("INGEST_KEY", "")
_run_lock = asyncio.Lock()
_poll_wake = asyncio.Event()
_logger = logging.getLogger("findhub.device_discovery")
DELETION_BACKUP_DIR = Path(os.environ.get("DELETION_BACKUP_DIR", "/device-deletion-backups"))


def connect():
    return psycopg.connect(host="db", user="tracker", dbname="tracker",
                           password=os.environ["DB_PASSWORD"], row_factory=dict_row,
                           options="-c timezone=UTC", connect_timeout=5)


class DeviceCards(html.parser.HTMLParser):
    """Read only the stable ID and visible name from the collector's device cards."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.devices = {}
        self.card = None
        self.depth = 0
        self.capture_name = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set((attrs.get("class") or "").split())
        if tag == "div" and self.card is None and "device-card" in classes:
            device = attrs.get("data-canonic-id", "").strip()
            if device and len(device) <= 200 and not any(ord(c) < 32 for c in device):
                self.card = {"device": device, "label": device}
                self.depth = 1
        elif self.card is not None:
            if tag == "div":
                self.depth += 1
            if tag == "b" and "device-card-name" in classes:
                self.capture_name = True

    def handle_endtag(self, tag):
        if self.card is None:
            return
        if tag == "b" and self.capture_name:
            self.capture_name = False
        elif tag == "div":
            self.depth -= 1
            if self.depth == 0:
                self.devices[self.card["device"]] = self.card["label"]
                self.card = None

    def handle_data(self, data):
        if self.card is not None and self.capture_name:
            self.card["label"] = (self.card["label"] if self.card["label"] != self.card["device"] else "") + data.strip()


class TextArea(html.parser.HTMLParser):
    def __init__(self, field):
        super().__init__(convert_charrefs=True)
        self.field = field
        self.reading = False
        self.value = None

    def handle_starttag(self, tag, attrs):
        if tag == "textarea" and dict(attrs).get("name") == self.field:
            self.reading = True

    def handle_endtag(self, tag):
        if tag == "textarea" and self.reading:
            self.reading = False

    def handle_data(self, data):
        if self.reading:
            self.value = (self.value or "") + data


class DiscoveryError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class DeviceDeletionRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=200)
    confirm_name: str = Field(min_length=1, max_length=200)
    actor: str = Field(min_length=1, max_length=100)


def parse_device_cards(document):
    parser = DeviceCards()
    parser.feed(document)
    return parser.devices


def minute_for_device(device, loads):
    """Choose the least-loaded ten-minute cron phase, with stable tie-breaking."""
    phase = int(hashlib.sha256(device.encode()).hexdigest()[:8], 16) % 10
    choice = min(range(10), key=lambda minute: (loads[minute], (minute - phase) % 10))
    loads[choice] += 1
    return choice


def account_status_after_missing(successful_misses):
    return "removed" if successful_misses >= 3 else "unavailable"


def reconcile_inventory_state(existing, discovered):
    """Pure inventory transition used by the DB writer and synthetic tests."""
    result = {device: dict(row) for device, row in existing.items()}
    for device, label in discovered.items():
        previous = result.get(device, {})
        result[device] = {**previous, "device": device, "label": label or device,
                          "account_status": "active", "missing_successes": 0}
    for device, row in result.items():
        if device not in discovered:
            misses = int(row.get("missing_successes", 0)) + 1
            row["missing_successes"] = misses
            row["account_status"] = account_status_after_missing(misses)
    return result


def endpoint_config(ingest_key, minute):
    cron = "*/10 * * * *" if minute == 0 else f"{minute}-59/10 * * * *"
    return {
        "alias": "Find Hub Tracker (automatic)", "method": "POST", "url": TRACKER_URL,
        "headers": {"Authorization": "Bearer " + ingest_key}, "body_type": "json",
        "body": ('{"device":"{{tracker_id}}","label":"{{tracker_id}}",'
                 '"reported":"{{google_timestamp}}","latitude":"{{latitude}}",'
                 '"longitude":"{{longitude}}","accuracy":"{{accuracy_m}}",'
                 '"status":"{{status}}"}'),
        "cron": cron, "skip_if_close": False, "skip_if_stale": False,
        "skip_if_inaccurate": False, "filter_by_status": False,
        "skip_if_not_own_report": False, "skip_if_already_seen": False,
        "only_most_recent": False,
    }


def collector_client(transport=None):
    return httpx.Client(base_url=COLLECTOR, auth=(os.environ.get("COLLECTOR_USERNAME", "tracker"),
                         os.environ["COLLECTOR_PASSWORD"]), timeout=35, transport=transport)


def fetch_inventory(client):
    try:
        response = client.post("/devices/refresh")
    except httpx.TimeoutException as error:
        raise DiscoveryError("unavailable", "The collector did not finish device discovery in time.") from error
    except httpx.HTTPError as error:
        raise DiscoveryError("unavailable", "The collector could not be reached.") from error
    if response.status_code == 401:
        raise DiscoveryError("authentication_error", "Collector authentication was rejected.")
    if response.status_code == 429:
        raise DiscoveryError("rate_limited", "Google or the collector rate-limited device discovery.")
    if response.status_code >= 500:
        if re.search(r"429|rate.?limit|quota|resource_exhausted|too many requests", response.text, re.I):
            raise DiscoveryError("rate_limited", "Google or the collector rate-limited device discovery.")
        raise DiscoveryError("unavailable", "The collector could not complete device discovery.")
    if response.status_code != 200:
        raise DiscoveryError("error", "The collector rejected device discovery.")
    devices = parse_device_cards(response.text)
    if not devices and re.search(r"sign.?in with google|not signed in|google account.*sign", response.text, re.I):
        raise DiscoveryError("authentication_error", "Google account authentication is unavailable in the collector.")
    return devices


def read_device_yaml(client, device):
    response = client.get(f"/settings/devices/{quote(device, safe='')}/yaml")
    if response.status_code == 401:
        raise DiscoveryError("authentication_error", "Collector authentication was rejected.")
    if response.status_code == 429:
        raise DiscoveryError("rate_limited", "The collector rate-limited a device settings request.")
    if response.status_code != 200:
        raise DiscoveryError("error", "The collector could not read a device configuration.")
    parser = TextArea("yaml_text")
    parser.feed(response.text)
    if parser.value is None:
        raise DiscoveryError("error", "The collector returned an unsupported device configuration page.")
    try:
        doc = yaml.safe_load(parser.value) or {}
    except yaml.YAMLError as error:
        raise DiscoveryError("error", "A device configuration could not be read safely.") from error
    if not isinstance(doc, dict) or not isinstance(doc.get("endpoints", []), list):
        raise DiscoveryError("error", "A device configuration has an unexpected structure.")
    return doc


def save_device_yaml(client, device, doc):
    response = client.post(f"/settings/devices/{quote(device, safe='')}/yaml",
                           data={"yaml_text": yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)})
    if response.status_code == 401:
        raise DiscoveryError("authentication_error", "Collector authentication was rejected.")
    if response.status_code == 429:
        raise DiscoveryError("rate_limited", "The collector rate-limited a device configuration update.")
    if response.status_code != 200 or "form-error" in response.text:
        raise DiscoveryError("error", "The collector rejected an automatic device configuration update.")


def ensure_devices_configured(client, devices, loads, configs=None):
    added = []
    for device in sorted(devices):
        doc = configs[device] if configs is not None else read_device_yaml(client, device)
        endpoints = doc.get("endpoints", [])
        if any(endpoint.get("url") == TRACKER_URL for endpoint in endpoints if isinstance(endpoint, dict)):
            continue
        minute = minute_for_device(device, loads)
        doc["endpoints"] = [*endpoints, endpoint_config(API_TOKEN, minute)]
        save_device_yaml(client, device, doc)
        added.append(device)
    return added


def schedule_loads(client, devices, configs=None):
    loads = Counter()
    for device in devices:
        doc = configs[device] if configs is not None else read_device_yaml(client, device)
        for endpoint in doc.get("endpoints", []):
            cron = (endpoint.get("cron") or "").strip()
            match = re.fullmatch(r"(\d+)-59/10 \* \* \* \*", cron)
            if match:
                minute = int(match[1])
                if 0 <= minute < 10:
                    loads[minute] += 1
            elif cron == "*/10 * * * *":
                loads[0] += 1
    return loads


def ensure_schema():
    with connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS discovery_state (
                id integer PRIMARY KEY CHECK (id=1), status text NOT NULL DEFAULT 'never',
                attempted timestamptz, succeeded timestamptz, error text
            );
            CREATE TABLE IF NOT EXISTS discovered_devices (
                device text PRIMARY KEY, label text NOT NULL,
                account_status text NOT NULL CHECK (account_status IN ('active','unavailable','removed')),
                first_seen timestamptz NOT NULL DEFAULT now(), last_seen timestamptz NOT NULL DEFAULT now(),
                missing_successes integer NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS deleted_devices (
                device text PRIMARY KEY, label text NOT NULL, deleted_at timestamptz NOT NULL DEFAULT now(),
                actor text NOT NULL, operation_id text NOT NULL, checkpoint_file text NOT NULL,
                checkpoint_sha256 text NOT NULL, detections_deleted integer NOT NULL CHECK (detections_deleted >= 0),
                collector_log_rows_deleted integer NOT NULL DEFAULT 0 CHECK (collector_log_rows_deleted >= 0)
            );
            CREATE TABLE IF NOT EXISTS device_deletion_jobs (
                device text PRIMARY KEY, operation_id text NOT NULL UNIQUE, actor text NOT NULL,
                state text NOT NULL CHECK (state IN ('preparing','checkpointed','collector_removed','database_deleted','recovery_required')),
                checkpoint_file text, checkpoint_sha256 text, detections_expected integer,
                updated_at timestamptz NOT NULL DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS device_deletion_events (
                id bigserial PRIMARY KEY, occurred_at timestamptz NOT NULL DEFAULT now(),
                device text NOT NULL, actor text NOT NULL,
                detections_deleted integer NOT NULL CHECK (detections_deleted >= 0),
                registration_deleted integer NOT NULL CHECK (registration_deleted IN (0,1)),
                collector_log_rows_deleted integer NOT NULL DEFAULT 0 CHECK (collector_log_rows_deleted >= 0),
                checkpoint_file text NOT NULL,
                outcome text NOT NULL CHECK (outcome IN ('complete','recovery_required','restored'))
            );
            ALTER TABLE deleted_devices ADD COLUMN IF NOT EXISTS collector_log_rows_deleted integer NOT NULL DEFAULT 0;
            ALTER TABLE device_deletion_events ADD COLUMN IF NOT EXISTS collector_log_rows_deleted integer NOT NULL DEFAULT 0;
            ALTER TABLE device_deletion_jobs DROP CONSTRAINT IF EXISTS device_deletion_jobs_state_check;
            ALTER TABLE device_deletion_jobs ADD CONSTRAINT device_deletion_jobs_state_check
                CHECK (state IN ('preparing','checkpointed','collector_removed','database_deleted','recovery_required'));
            INSERT INTO discovery_state(id,status) VALUES (1,'never') ON CONFLICT (id) DO NOTHING;
        """)


def known_count():
    with connect() as db:
        return db.execute("SELECT count(*) AS n FROM discovered_devices").fetchone()["n"]


def current_devices():
    with connect() as db:
        return db.execute("SELECT device,label,account_status,missing_successes FROM discovered_devices").fetchall()


def pending_deletion_ids():
    with connect() as db:
        return {row["device"] for row in db.execute(
            "SELECT device FROM device_deletion_jobs WHERE state<>'complete'").fetchall()}


def tombstoned_device_ids():
    with connect() as db:
        return {row["device"] for row in db.execute("SELECT device FROM deleted_devices").fetchall()}


def update_discovery_state(status, error=None, success=False):
    with connect() as db:
        db.execute("""
            INSERT INTO discovery_state(id,status,attempted,succeeded,error) VALUES (1,%s,now(),
                CASE WHEN %s THEN now() ELSE NULL END,%s)
            ON CONFLICT (id) DO UPDATE SET status=excluded.status, attempted=now(),
                succeeded=CASE WHEN %s THEN now() ELSE discovery_state.succeeded END, error=excluded.error
        """, (status, success, error, success))


def collector_request(method, path, **kwargs):
    with collector_client() as client:
        response = client.request(method, path, headers={
            "X-FindHub-Deletion-Token": API_TOKEN,
        }, **kwargs)
    return response


def _delete_device_once(device_id, confirm_name, actor):
    """Coordinate device cleanup. Database advisory locks fence discovery and ingestion."""
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", device_id):
        raise DeletionRejected("invalid_id", "The stable device ID has an unsupported format.")
    if len(confirm_name) > 200 or not confirm_name.strip():
        raise DeletionRejected("invalid_confirmation", "The device-name confirmation is invalid.")
    if not isinstance(actor, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,100}", actor):
        raise DeletionRejected("unauthorized_actor", "The authenticated owner could not be verified.")

    conn = connect()
    operation_id = str(uuid.uuid4())
    removed_state = None
    checkpoint = None
    job_started = False
    database_deleted = False
    recovered_retry = False
    recovery_blocked = False
    try:
        # Session locks span the collector filesystem operation as well as DB
        # commits. Ingest and discovery use matching transaction locks.
        conn.execute("SELECT pg_advisory_lock(118903)")
        conn.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (device_id,))
        conn.commit()
        with conn.transaction():
            existing = conn.execute("SELECT state,operation_id,checkpoint_file,checkpoint_sha256 "
                                    "FROM device_deletion_jobs WHERE device=%s", (device_id,)).fetchone()
            prior = conn.execute("SELECT actor,label,detections_deleted,collector_log_rows_deleted,checkpoint_file,checkpoint_sha256 "
                                 "FROM deleted_devices WHERE device=%s", (device_id,)).fetchone()
            if prior and not existing:
                if not hmac.compare_digest(prior["label"], confirm_name):
                    raise DeletionRejected("name_mismatch", "The typed device name does not match.")
                return {"device_id": device_id, "deleted": True, "detections_deleted": prior["detections_deleted"],
                        "registration_deleted": True, "checkpoint": prior["checkpoint_file"],
                        "checkpoint_sha256": prior["checkpoint_sha256"], "idempotent": True}
            if existing:
                if prior:
                    if not hmac.compare_digest(prior["label"], confirm_name):
                        raise DeletionRejected("name_mismatch", "The typed device name does not match.")
                    try:
                        finalized = collector_request(
                            "POST", f"/internal/tracker/devices/{quote(device_id, safe='')}/commit",
                            json={"operation_id": existing["operation_id"]}, timeout=20)
                    except httpx.HTTPError:
                        finalized = None
                    if finalized is not None and finalized.status_code == 200:
                        conn.execute("UPDATE device_deletion_events SET outcome='complete' WHERE device=%s "
                                     "AND outcome='recovery_required'", (device_id,))
                        if not conn.execute("SELECT 1 FROM device_deletion_events WHERE device=%s", (device_id,)).fetchone():
                            conn.execute("INSERT INTO device_deletion_events(device,actor,detections_deleted,registration_deleted,"
                                         "collector_log_rows_deleted,checkpoint_file,outcome) VALUES (%s,%s,%s,1,%s,%s,'complete')",
                                         (device_id, prior["actor"], prior["detections_deleted"],
                                          prior["collector_log_rows_deleted"], prior["checkpoint_file"]))
                        conn.execute("DELETE FROM device_deletion_jobs WHERE device=%s", (device_id,))
                        return {"device_id": device_id, "deleted": True,
                                "detections_deleted": prior["detections_deleted"], "registration_deleted": True,
                                "checkpoint": prior["checkpoint_file"], "checkpoint_sha256": prior["checkpoint_sha256"],
                                "idempotent": True}
                else:
                    try:
                        restored = collector_request(
                            "POST", f"/internal/tracker/devices/{quote(device_id, safe='')}/restore",
                            json={"operation_id": existing["operation_id"]}, timeout=30)
                    except httpx.HTTPError:
                        restored = None
                    if restored is not None and restored.status_code == 200:
                        conn.execute("DELETE FROM device_deletion_jobs WHERE device=%s", (device_id,))
                        recovered_retry = True
                if not recovered_retry:
                    conn.execute("UPDATE device_deletion_jobs SET state='recovery_required',updated_at=now() WHERE device=%s",
                                 (device_id,))
                    recovery_blocked = True
            if not recovered_retry and not recovery_blocked:
                device = conn.execute("SELECT device,label,account_status,missing_successes "
                                      "FROM discovered_devices WHERE device=%s FOR UPDATE", (device_id,)).fetchone()
                state = conn.execute("SELECT status,succeeded FROM discovery_state WHERE id=1 FOR UPDATE").fetchone()
                allowed, reason = deletion_eligibility(device, state, datetime.now(UTC))
                if not allowed:
                    raise DeletionRejected("not_confirmed_removed", reason)
                if not hmac.compare_digest(device["label"], confirm_name):
                    raise DeletionRejected("name_mismatch", "The typed device name does not match.")
                conn.execute("INSERT INTO device_deletion_jobs(device,operation_id,actor,state) "
                             "VALUES (%s,%s,%s,'preparing')", (device_id, operation_id, actor))
                job_started = True

        if recovered_retry:
            raise DeletionRejected("recovered_retry", "A prior partial attempt was rolled back; retry deletion now.")
        if recovery_blocked:
            raise DeletionRejected("recovery_required", "A previous deletion is blocked pending recovery.")

        response = collector_request("POST", f"/internal/tracker/devices/{quote(device_id, safe='')}/prepare",
                                     json={"operation_id": operation_id}, timeout=180)
        if response.status_code != 200:
            raise DeletionRejected("collector_unavailable", "The collector could not safely stop and remove this device.")
        result = response.json()
        if result.get("operation_id") != operation_id or result.get("device_id") != device_id \
                or not isinstance(result.get("entry"), dict):
            raise DeletionRejected("collector_invalid_response", "The collector returned an incomplete deletion checkpoint.")
        removed_state = result["entry"]

        with conn.transaction():
            # Ingest is blocked by the durable job row and session lock. Capture
            # the exact affected rows after the collector has drained its work.
            device = conn.execute("SELECT device,label,account_status,first_seen,last_seen,missing_successes "
                                  "FROM discovered_devices WHERE device=%s FOR UPDATE", (device_id,)).fetchone()
            rows = conn.execute("SELECT fingerprint,device,label,source,report_id,reported,collected,latitude,longitude,accuracy,status "
                                "FROM detections WHERE device=%s AND source='find-hub' ORDER BY reported,fingerprint "
                                "FOR UPDATE", (device_id,)).fetchall()
            if not device:
                raise DeletionRejected("registration_changed", "The device registration changed during deletion.")
            other_source = conn.execute(
                "SELECT 1 FROM detections WHERE device=%s AND source<>'find-hub' LIMIT 1 FOR UPDATE",
                (device_id,)).fetchone()
            if other_source:
                raise DeletionRejected("ambiguous_history", "Other local data uses this device identifier; deletion was stopped.")
            checkpoint = write_device_checkpoint(
                DELETION_BACKUP_DIR, device_id, device["label"], dict(device), rows,
                removed_state, datetime.now(UTC))
            archive_path = DELETION_BACKUP_DIR / checkpoint["file"]
            checkpoint_payload = verify_checkpoint_manifest(archive_path, checkpoint["sha256"], device_id, len(rows))
            log_state = removed_state.get("forward_logs", {})
            collector_logs = sum(len(log_state.get(key, [])) for key in ("active", "legacy"))
            conn.execute("UPDATE device_deletion_jobs SET state='checkpointed',checkpoint_file=%s,"
                         "checkpoint_sha256=%s,detections_expected=%s,updated_at=now() WHERE device=%s",
                         (checkpoint["file"], checkpoint["sha256"], len(rows), device_id))

        # The recovery archive is now durable and verified. Only now remove
        # collector YAML/log state; the prepare endpoint has fenced the device.
        response = collector_request("POST", f"/internal/tracker/devices/{quote(device_id, safe='')}/delete",
                                     json={"operation_id": operation_id}, timeout=45)
        if response.status_code != 200 or response.json().get("operation_id") != operation_id:
            raise DeletionRejected("collector_unavailable", "The collector could not safely remove this device.")
        with conn.transaction():
            conn.execute("UPDATE device_deletion_jobs SET state='collector_removed',updated_at=now() WHERE device=%s",
                         (device_id,))

        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(118903)")
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (device_id,))
            removed = conn.execute("DELETE FROM detections WHERE device=%s AND source='find-hub'", (device_id,)).rowcount
            registration = conn.execute("DELETE FROM discovered_devices WHERE device=%s", (device_id,)).rowcount
            if removed != checkpoint["detections"] or registration != 1:
                raise DeletionRejected("database_scope_changed", "The database rows changed during deletion; no partial result was accepted.")
            conn.execute("INSERT INTO deleted_devices(device,label,actor,operation_id,checkpoint_file,checkpoint_sha256,"
                         "detections_deleted,collector_log_rows_deleted) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                         (device_id, checkpoint_payload["label"], actor, operation_id,
                          checkpoint["file"], checkpoint["sha256"], removed, collector_logs))
            conn.execute("UPDATE device_deletion_jobs SET state='database_deleted',updated_at=now() WHERE device=%s",
                         (device_id,))
        database_deleted = True

        finalized = collector_request("POST", f"/internal/tracker/devices/{quote(device_id, safe='')}/commit",
                                      json={"operation_id": operation_id}, timeout=20)
        if finalized.status_code != 200:
            with conn.transaction():
                conn.execute("UPDATE device_deletion_jobs SET state='recovery_required',updated_at=now() WHERE device=%s",
                             (device_id,))
                conn.execute("INSERT INTO device_deletion_events(device,actor,detections_deleted,registration_deleted,"
                             "collector_log_rows_deleted,checkpoint_file,outcome) VALUES (%s,%s,%s,1,%s,%s,'recovery_required')",
                             (device_id, actor, checkpoint["detections"], collector_logs, checkpoint["file"]))
            raise DeletionRejected("recovery_required", "Database deletion completed, but collector cleanup needs recovery. The device remains blocked.")
        with conn.transaction():
            conn.execute("INSERT INTO device_deletion_events(device,actor,detections_deleted,registration_deleted,"
                         "collector_log_rows_deleted,checkpoint_file,outcome) VALUES (%s,%s,%s,1,%s,%s,'complete')",
                         (device_id, actor, checkpoint["detections"], collector_logs, checkpoint["file"]))
            conn.execute("DELETE FROM device_deletion_jobs WHERE device=%s", (device_id,))
        return {"device_id": device_id, "deleted": True, "detections_deleted": checkpoint["detections"],
                "registration_deleted": True, "checkpoint": checkpoint["file"],
                "checkpoint_sha256": checkpoint["sha256"], "idempotent": False}
    except Exception as error:
        if job_started and not database_deleted:
            # A connection error during COMMIT makes the outcome uncertain.
            # Consult durable state before compensation; never restore the
            # collector blindly if PostgreSQL may already have deleted rows.
            try:
                with connect() as check:
                    database_deleted = bool(check.execute(
                        "SELECT 1 FROM deleted_devices WHERE device=%s", (device_id,)).fetchone())
            except psycopg.Error:
                database_deleted = True  # conservative: leave both sides fenced for retry/recovery
        if job_started and not database_deleted:
            restored = False
            try:
                restored = collector_request(
                    "POST", f"/internal/tracker/devices/{quote(device_id, safe='')}/restore",
                    json={"operation_id": operation_id, "entry": removed_state}, timeout=30).status_code == 200
            except httpx.HTTPError:
                restored = False
            if restored:
                with conn.transaction():
                    conn.execute("DELETE FROM device_deletion_jobs WHERE device=%s", (device_id,))
            else:
                with conn.transaction():
                    conn.execute("UPDATE device_deletion_jobs SET state='recovery_required',updated_at=now() WHERE device=%s",
                                 (device_id,))
        if isinstance(error, DeletionRejected):
            raise
        _logger.error("Device deletion failed (%s)", type(error).__name__)
        raise DeletionRejected("deletion_failed", "Device deletion did not complete; the device may be blocked pending recovery.") from error
    finally:
        try:
            conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (device_id,))
            conn.execute("SELECT pg_advisory_unlock(118903)")
            conn.commit()
        except psycopg.Error:
            pass
        conn.close()


def reconcile(devices):
    with connect() as db:
        # Serialize successful inventory application with ingest and deletion.
        db.execute("SELECT pg_advisory_xact_lock(118903)")
        old = {row["device"]: row for row in db.execute(
            "SELECT device,label,account_status,missing_successes FROM discovered_devices").fetchall()}
        if not devices and old:
            raise DiscoveryError("unavailable", "Google returned an empty inventory; existing devices were kept unchanged.")
        transitions = reconcile_inventory_state(old, devices)
        pending = {row["device"] for row in db.execute(
            "SELECT device FROM device_deletion_jobs WHERE state<>'complete'").fetchall()}
        for device, label in devices.items():
            if device in pending:
                continue
            # A durable tombstone is cleared only by a successful fresh Google
            # inventory that actually contains this exact stable identifier.
            unresolved = db.execute("SELECT 1 FROM device_deletion_jobs WHERE device=%s", (device,)).fetchone()
            if not unresolved:
                tombstone = db.execute("SELECT 1 FROM deleted_devices WHERE device=%s", (device,)).fetchone()
                registration_left = db.execute("SELECT 1 FROM discovered_devices WHERE device=%s", (device,)).fetchone()
                history_left = db.execute("SELECT 1 FROM detections WHERE device=%s LIMIT 1", (device,)).fetchone()
                if not tombstone_return_is_safe(bool(tombstone), bool(unresolved),
                                                bool(registration_left), bool(history_left)):
                    raise DiscoveryError("error", "A deleted device still has local records; rediscovery was blocked.")
                db.execute("DELETE FROM deleted_devices WHERE device=%s", (device,))
            db.execute("""
                INSERT INTO discovered_devices(device,label,account_status,first_seen,last_seen,missing_successes)
                VALUES (%s,%s,'active',now(),now(),0)
                ON CONFLICT (device) DO UPDATE SET label=excluded.label, account_status='active',
                    last_seen=now(), missing_successes=0
            """, (device, label or device))
        for device, row in transitions.items():
            if device in devices or device in pending:
                continue
            db.execute("UPDATE discovered_devices SET account_status=%s,missing_successes=%s WHERE device=%s",
                       (row["account_status"], row["missing_successes"], device))


def status_document():
    with connect() as db:
        state = db.execute("SELECT status,attempted,succeeded,error FROM discovery_state WHERE id=1").fetchone()
        devices = db.execute("SELECT device,label,account_status,first_seen,last_seen,missing_successes "
                             "FROM discovered_devices ORDER BY label,device").fetchall()
    next_attempt = state["attempted"] + timedelta(seconds=INTERVAL) if state["attempted"] else None
    return {"status": state["status"], "attempted": state["attempted"], "succeeded": state["succeeded"],
            "next_attempt": next_attempt,
            "error": state["error"], "interval_seconds": INTERVAL, "devices": devices}


def discover_once():
    update_discovery_state("running")
    try:
        with collector_client() as client:
            inventory = fetch_inventory(client)
            if not inventory and known_count():
                raise DiscoveryError("unavailable", "Google returned an empty inventory; existing devices were kept unchanged.")
            previously_deleted = tombstoned_device_ids()
            returned_deleted = set(inventory) & previously_deleted
            # Revive is based on this fresh successful Google response. Keep
            # the tombstone in PostgreSQL until the collector confirms revival.
            for device in sorted(returned_deleted):
                response = collector_request("POST", f"/internal/tracker/devices/{quote(device, safe='')}/revive")
                if response.status_code != 200:
                    raise DiscoveryError("error", "A rediscovered device could not be safely re-enabled in the collector.")
            reconcile(inventory)
            blocked = pending_deletion_ids()
            eligible_inventory = {device: label for device, label in inventory.items() if device not in blocked}
            configs = {device: read_device_yaml(client, device) for device in sorted(eligible_inventory)}
            loads = schedule_loads(client, eligible_inventory, configs)
            ensure_devices_configured(client, eligible_inventory, loads, configs)
        update_discovery_state("success", success=True)
        return status_document()
    except DiscoveryError as error:
        update_discovery_state(error.status, error.message)
        _logger.warning("Device discovery %s", error.status)
        return status_document()
    except (httpx.HTTPError, OSError, psycopg.Error, yaml.YAMLError, ValueError, TypeError) as error:
        update_discovery_state("error", "Device discovery could not complete; existing devices were preserved.")
        _logger.warning("Device discovery failed (%s)", type(error).__name__)
        return status_document()


async def refresh_locked(manual=False):
    async with _run_lock:
        result = await asyncio.to_thread(discover_once)
    if manual:
        _poll_wake.set()
    return result


def seconds_until_next_attempt():
    with connect() as db:
        row = db.execute("SELECT attempted FROM discovery_state WHERE id=1").fetchone()
    if not row or not row["attempted"]:
        return 0
    return delay_until_next_attempt(row["attempted"])


def delay_until_next_attempt(attempted, now=None):
    if attempted is None:
        return 0
    return max(0, INTERVAL - ((now or datetime.now(UTC)) - attempted).total_seconds())


async def poll_loop():
    while True:
        remaining = await asyncio.to_thread(seconds_until_next_attempt)
        if remaining > 0:
            try:
                await asyncio.wait_for(_poll_wake.wait(), timeout=remaining)
                _poll_wake.clear()
                continue
            except asyncio.TimeoutError:
                pass
        started = time.monotonic()
        try:
            await refresh_locked()
        except Exception as error:
            _logger.warning("Device discovery worker failed (%s)", type(error).__name__)
        await asyncio.sleep(max(1, INTERVAL - (time.monotonic() - started)))


@asynccontextmanager
async def lifespan(_):
    ensure_schema()
    task = asyncio.create_task(poll_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
def health():
    with connect() as db:
        db.execute("SELECT 1 FROM discovery_state WHERE id=1")
    return {"status": "ok"}


@app.get("/status")
def read_status(authorization: str = Header(default="")):
    if not hmac.compare_digest(authorization.encode(), ("Bearer " + API_TOKEN).encode()):
        raise HTTPException(401, "Unauthorized")
    return status_document()


@app.post("/refresh")
async def refresh(authorization: str = Header(default="")):
    if not hmac.compare_digest(authorization.encode(), ("Bearer " + API_TOKEN).encode()):
        raise HTTPException(401, "Unauthorized")
    return await refresh_locked(manual=True)


@app.post("/devices/delete")
async def delete_device(body: DeviceDeletionRequest, authorization: str = Header(default="")):
    if not hmac.compare_digest(authorization.encode(), ("Bearer " + API_TOKEN).encode()):
        raise HTTPException(401, "Unauthorized")
    async with _run_lock:
        try:
            return await asyncio.to_thread(_delete_device_once, body.device_id,
                                           body.confirm_name, body.actor)
        except DeletionRejected as error:
            status = {"not_confirmed_removed": 409, "name_mismatch": 409,
                      "invalid_id": 422, "invalid_confirmation": 422,
                      "unauthorized_actor": 403, "collector_unavailable": 503,
                      "collector_invalid_response": 503, "recovery_required": 423}.get(error.code, 503)
            raise HTTPException(status, error.message) from error
