# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Waleed Khan

import asyncio
import csv
import hashlib
import hmac
import httpx
import ipaddress
import io
import json
import logging
import os
import secrets
import time
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from pydantic import AwareDatetime, BaseModel, Field, field_validator
from device_deletion import deletion_eligibility

DEMO = os.environ["DEMO_MODE"] == "1"
SOURCE = "demo" if DEMO else "find-hub"
INTERVAL = max(300, min(3600, int(os.environ["POLL_SECONDS"])))
TIMEZONE = ZoneInfo(os.environ["DISPLAY_TZ"]).key
DISCOVERY_URL = os.environ.get("DISCOVERY_URL", "").rstrip("/")
COOKIE = "tracker_session"
CSRF_COOKIE = "tracker_csrf"
SESSION_SECONDS = 12 * 60 * 60
REMEMBER_SESSION_SECONDS = 30 * 24 * 60 * 60
LOGIN_WINDOW_SECONDS = 60
LOGIN_CLIENT_LIMIT = 10
LOGIN_GLOBAL_LIMIT = 100
OWNER_ACTOR = os.environ.get("OWNER_ACTOR", "owner")
PASSWORD_HASHER = PasswordHasher()


def connect():
    return psycopg.connect(host="db", user="tracker", dbname="tracker",
                           password=os.environ["DB_PASSWORD"], row_factory=dict_row,
                           options="-c timezone=UTC", connect_timeout=5)


class Fix(BaseModel):
    device: str = Field(min_length=1, max_length=200)
    label: str | None = Field(default=None, max_length=200)
    reported: AwareDatetime
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    status: str = Field(default="", max_length=100)
    report_id: str | None = Field(default=None, max_length=200)

    @field_validator("accuracy", mode="before")
    @classmethod
    def optional_accuracy(cls, value):
        return None if value == "" else value


class Login(BaseModel):
    password: str = Field(max_length=512)
    remember_me: bool = False


class DeviceDeletion(BaseModel):
    device_id: str = Field(min_length=1, max_length=200)
    confirm_name: str = Field(min_length=1, max_length=200)


def insert(db, source, fix, label):
    entry = fix.model_dump()
    entry["reported"] = fix.reported.astimezone(UTC)
    identity = [source, fix.device, fix.report_id] if fix.report_id else [
        source, fix.device, entry["reported"].isoformat(), fix.latitude, fix.longitude
    ]
    entry.update(source=source, label=label,
                 fingerprint=hashlib.sha256(json.dumps(identity).encode()).hexdigest())
    return db.execute("""
        INSERT INTO detections (fingerprint, device, label, source, report_id, reported,
                                latitude, longitude, accuracy, status)
        VALUES (%(fingerprint)s, %(device)s, %(label)s, %(source)s, %(report_id)s,
                %(reported)s, %(latitude)s, %(longitude)s, %(accuracy)s, %(status)s)
        ON CONFLICT DO NOTHING
    """, entry).rowcount


def demo_poll():
    with connect() as db:
        db.execute("SELECT pg_advisory_xact_lock(118903)")
        state = db.execute("SELECT succeeded FROM poll_state WHERE source='demo'").fetchone()
        if not state or not state["succeeded"]:
            start = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(hours=2)
            devices = [
                ("demo-keys", "House Keys", [(12.3405, 45.674), (12.343, 45.6764),
                                            (12.3459, 45.678), (12.3496, 45.6807)]),
                ("demo-bag", "Travel Bag", [(12.3279, 45.677), (12.332, 45.680),
                                           (12.336, 45.684), (12.340, 45.687)]),
                ("demo-bike", "Bike Tag", [(12.348, 45.668), (12.352, 45.670),
                                           (12.356, 45.674), (12.359, 45.677)]),
            ]
            for device, label, points in devices:
                for index, (lat, lon) in enumerate(points):
                    insert(db, "demo", Fix(device=device, reported=start + timedelta(minutes=25*index),
                                           latitude=lat, longitude=lon, accuracy=12+index*4,
                                           status="Synthetic"), label)
        record_success(db, "demo")


def record_success(db, source):
    db.execute("""
        INSERT INTO poll_state VALUES (%s, now(), now(), NULL)
        ON CONFLICT (source) DO UPDATE SET attempted=now(), succeeded=now(), error=NULL
    """, (source,))


def retry_delay(failures):
    return min(INTERVAL, 15 * 2 ** min(failures - 1, 8))


async def poll_demo():
    failures = 0
    while True:
        try:
            await asyncio.to_thread(demo_poll)
            failures = 0
            delay = INTERVAL
        except Exception as error:
            failures += 1
            delay = retry_delay(failures)
            logging.warning("Demo polling failed (%s). Retrying in %ss.", type(error).__name__, delay)
        await asyncio.sleep(delay)


@asynccontextmanager
async def lifespan(_):
    with connect() as db:
        db.execute(Path(__file__).with_name("schema.sql").read_text())
    task = asyncio.create_task(poll_demo()) if DEMO else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, error):
    # Validation errors can contain submitted fields. Return a generic response.
    return Response('{"detail":"Invalid request data"}', status_code=422, media_type="application/json")


@app.middleware("http")
async def security_headers(request, call_next):
    result = await call_next(request)
    result.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                           "Referrer-Policy": "strict-origin", "X-Frame-Options": "DENY",
                           "Content-Security-Policy": "default-src 'self'; img-src 'self' data: https://tile.openstreetmap.org; style-src 'self' 'unsafe-inline'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
    return result


def digest(token):
    return hmac.new(os.environ["SESSION_KEY"].encode(), token.encode(), "sha256").hexdigest()


def client_key(request):
    """Return an HMAC of the client IP, trusting the custom header only from configured proxies."""
    peer = request.client.host if request.client else "unknown"
    address = peer
    trusted = set(filter(None, (part.strip() for part in os.environ.get("TRUSTED_PROXY_IPS", "").split(","))))
    if peer in trusted:
        forwarded = request.headers.get("X-Tracker-Client-IP", "")
        try:
            address = str(ipaddress.ip_address(forwarded))
        except ValueError:
            address = peer
    try:
        address = str(ipaddress.ip_address(address))
    except ValueError:
        address = "unknown"
    return hmac.new(os.environ["SESSION_KEY"].encode(), ("login-ip:" + address).encode(),
                    "sha256").hexdigest()


def dashboard_password_matches(candidate):
    """Verify a password against an Argon2id PHC string held in a mounted secret file."""
    try:
        encoded = Path(os.environ["DASHBOARD_PASSWORD_HASH_FILE"]).read_text(encoding="ascii").strip()
    except (KeyError, OSError, UnicodeError):
        raise HTTPException(503, "Dashboard login is not configured") from None
    try:
        return PASSWORD_HASHER.verify(encoded, candidate)
    except (VerifyMismatchError, InvalidHashError, VerificationError, ValueError):
        return False


def authorize(request, db):
    token = request.cookies.get(COOKIE, "")
    session = db.execute("SELECT actor FROM sessions WHERE digest=%s AND expires > now()",
                         (digest(token),)).fetchone() if len(token) == 43 else None
    if not session:
        raise HTTPException(401, "Sign in required")
    return session.get("actor", "")


def authorize_csrf(request, db):
    """Require a same-session double-submit token for destructive mutations."""
    token = request.cookies.get(CSRF_COOKIE, "")
    submitted = request.headers.get("X-CSRF-Token", "")
    if not token or not hmac.compare_digest(token, submitted):
        raise HTTPException(403, "CSRF validation failed")
    session = db.execute("SELECT csrf_digest FROM sessions WHERE digest=%s AND expires > now()",
                         (digest(request.cookies.get(COOKIE, "")),)).fetchone()
    if not session or not session["csrf_digest"] or not hmac.compare_digest(
            session["csrf_digest"], digest(token)):
        raise HTTPException(403, "CSRF validation failed")


@app.get("/health")
def health():
    with connect() as db:
        db.execute("SELECT 1 FROM detections LIMIT 1")
    return {"status": "ok"}


@app.post("/api/login")
def login(request: Request, body: Login):
    lifetime = REMEMBER_SESSION_SECONDS if body.remember_me else SESSION_SECONDS
    remote_key = client_key(request)
    with connect() as db:
        db.execute("SELECT pg_advisory_xact_lock(118904)")
        db.execute("DELETE FROM login_attempts WHERE attempted < now() - interval '1 minute'")
        attempts = db.execute("SELECT count(*) AS total FROM login_attempts").fetchone()["total"]
        client_attempts = db.execute("SELECT count(*) AS total FROM login_attempts WHERE client_key=%s",
                                     (remote_key,)).fetchone()["total"]
        if attempts >= LOGIN_GLOBAL_LIMIT or client_attempts >= LOGIN_CLIENT_LIMIT:
            return Response(status_code=429, headers={"Retry-After": "60"})
        if not dashboard_password_matches(body.password):
            db.execute("INSERT INTO login_attempts (client_key) VALUES (%s)", (remote_key,))
            return Response(status_code=401)
        previous_token = request.cookies.get(COOKIE, "")
        if len(previous_token) == 43:
            db.execute("DELETE FROM sessions WHERE digest=%s", (digest(previous_token),))
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        # The tracker has one owner account. Never derive owner authorization from
        # caller-controlled identity headers (Authelia is not the identity source).
        actor = OWNER_ACTOR
        db.execute("DELETE FROM sessions WHERE expires <= now()")
        db.execute("INSERT INTO sessions (digest,expires,csrf_digest,actor) "
                   "VALUES (%s, now() + (%s * interval '1 second'),%s,%s)",
                   (digest(token), lifetime, digest(csrf_token), actor))
    response = Response(status_code=204)
    response.set_cookie(COOKIE, token, max_age=lifetime, path="/", httponly=True, samesite="strict",
                        secure=os.environ["SECURE_COOKIE"] == "1")
    response.set_cookie(CSRF_COOKIE, csrf_token, max_age=lifetime, path="/", httponly=False, samesite="strict",
                        secure=os.environ["SECURE_COOKIE"] == "1")
    return response


@app.post("/api/logout")
def logout(request: Request):
    with connect() as db:
        authorize(request, db)
        authorize_csrf(request, db)
        db.execute("DELETE FROM sessions WHERE digest=%s", (digest(request.cookies[COOKIE]),))
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path="/", httponly=True,
                           secure=os.environ["SECURE_COOKIE"] == "1", samesite="strict")
    response.delete_cookie(CSRF_COOKIE, path="/", httponly=False,
                           secure=os.environ["SECURE_COOKIE"] == "1", samesite="strict")
    return response


@app.post("/api/ingest", status_code=204)
def ingest(fix: Fix, authorization: Annotated[str, Header()] = ""):
    if not hmac.compare_digest(authorization.encode(), ("Bearer " + os.environ["INGEST_KEY"]).encode()):
        raise HTTPException(401, "Invalid collector credentials")
    if DEMO:
        raise HTTPException(409, "Live ingestion is disabled in demo mode")
    with connect() as db:
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (fix.device,))
        if (db.execute("SELECT 1 FROM deleted_devices WHERE device=%s", (fix.device,)).fetchone()
                or db.execute("SELECT 1 FROM device_deletion_jobs WHERE device=%s", (fix.device,)).fetchone()):
            raise HTTPException(410, "Device was permanently deleted; awaiting successful rediscovery")
        registered = db.execute("SELECT label FROM discovered_devices WHERE device=%s", (fix.device,)).fetchone()
        insert(db, "find-hub", fix, (registered or {}).get("label") or fix.label or fix.device)
        record_success(db, "find-hub")


@app.post("/api/refresh", status_code=204)
def refresh(request: Request):
    with connect() as db:
        authorize(request, db)
        authorize_csrf(request, db)
    if not DEMO:
        raise HTTPException(409, "Use Locate or Send now in the collector")
    demo_poll()


@app.post("/api/devices/refresh")
def refresh_devices(request: Request):
    with connect() as db:
        authorize(request, db)
        authorize_csrf(request, db)
    if DEMO:
        raise HTTPException(409, "Device discovery is available in live mode")
    try:
        response = httpx.post(DISCOVERY_URL + "/refresh",
                              headers={"Authorization": "Bearer " + os.environ["INGEST_KEY"]},
                              timeout=90)
    except httpx.TimeoutException as error:
        raise HTTPException(504, "Device refresh is still running. Check its status shortly.") from error
    except httpx.HTTPError as error:
        raise HTTPException(503, "Device discovery service is temporarily unavailable") from error
    if response.status_code != 200:
        raise HTTPException(503, "Device discovery service is temporarily unavailable")
    return response.json()


@app.post("/api/devices/delete")
def delete_device(body: DeviceDeletion, request: Request):
    """Permanently delete one confirmed-removed device through the coordinator."""
    with connect() as db:
        actor = authorize(request, db)
        authorize_csrf(request, db)
        if actor != OWNER_ACTOR:
            raise HTTPException(403, "Only the authenticated owner may permanently delete a device")
        device = db.execute("SELECT label FROM discovered_devices WHERE device=%s", (body.device_id,)).fetchone()
        if not device:
            prior = db.execute("SELECT label,detections_deleted FROM deleted_devices WHERE device=%s",
                               (body.device_id,)).fetchone()
            if not prior:
                raise HTTPException(404, "Device registration was not found")
            if not hmac.compare_digest(str(prior["label"]), body.confirm_name):
                raise HTTPException(409, "The typed device name does not match")
            if not db.execute("SELECT 1 FROM device_deletion_jobs WHERE device=%s", (body.device_id,)).fetchone():
                return {"device_id": body.device_id, "deleted": True,
                        "detections_deleted": prior["detections_deleted"], "registration_deleted": True,
                        "idempotent": True}
        elif not hmac.compare_digest(str(device["label"]), body.confirm_name):
            raise HTTPException(409, "The typed device name does not match")
    try:
        response = httpx.post(DISCOVERY_URL + "/devices/delete",
                              headers={"Authorization": "Bearer " + os.environ["INGEST_KEY"]},
                              json={"device_id": body.device_id, "confirm_name": body.confirm_name,
                                    "actor": actor}, timeout=180)
    except httpx.TimeoutException as error:
        raise HTTPException(504, "Deletion outcome is being reconciled; check device status before retrying") from error
    except httpx.HTTPError as error:
        raise HTTPException(503, "Device deletion coordinator is unavailable") from error
    if response.status_code != 200:
        try:
            detail = response.json().get("detail", "Device deletion was rejected")
        except (ValueError, AttributeError):
            detail = "Device deletion was rejected"
        raise HTTPException(response.status_code if response.status_code in (400, 403, 404, 409, 423, 503) else 503,
                            detail)
    return response.json()


def history(db, device, start, end):
    conditions = ["source = %s"]
    values = [SOURCE]
    for condition, value in [("device = %s", device), ("reported >= %s", start), ("reported <= %s", end)]:
        if value is not None:
            conditions.append(condition)
            values.append(value)
    return db.execute(
        "SELECT device, label, reported, collected, latitude, longitude, accuracy, status, source, report_id "
        "FROM detections WHERE " + " AND ".join(conditions) + " ORDER BY reported, fingerprint", values
    )


@app.get("/api/history")
def read_history(request: Request, device: str | None = None,
                 start: AwareDatetime | None = None, end: AwareDatetime | None = None):
    with connect() as db:
        authorize(request, db)
        devices = db.execute("""
            SELECT device, max(label) AS label, count(*) AS total, max(reported) AS latest
            FROM detections WHERE source=%s GROUP BY device ORDER BY label, device
        """, (SOURCE,)).fetchall()
        state = db.execute("SELECT * FROM poll_state WHERE source=%s", (SOURCE,)).fetchone()
        registered = db.execute("SELECT device,label,account_status,first_seen,last_seen,missing_successes "
                                "FROM discovered_devices ORDER BY label,device").fetchall()
        discovery = db.execute("SELECT status,attempted,succeeded,error FROM discovery_state WHERE id=1").fetchone()
        for item in registered:
            allowed, reason = deletion_eligibility(item, discovery, datetime.now(UTC))
            item["deletion_allowed"] = allowed and not DEMO
            item["deletion_reason"] = reason if not DEMO else "Permanent deletion is unavailable in demo mode."
            item["deletion_history_count"] = db.execute(
                "SELECT count(*) AS total FROM detections WHERE source='find-hub' AND device=%s",
                (item["device"],)).fetchone()["total"]
        return {"points": history(db, device, start, end).fetchall(),
                "devices": device_status(devices, state, registered), "state": state,
                "discovery": discovery, "discovery_enabled": bool(DISCOVERY_URL),
                "timezone": TIMEZONE, "demo": DEMO}


def device_status(detections, poll_state, registered=()):
    entries = {row["device"]: {**row, "status": "unknown", "attempted": None}
               for row in detections}
    for item in registered:
        row = entries.setdefault(item["device"], {"device": item["device"], "latest": None, "total": 0,
                                                    "status": "unknown", "attempted": None})
        row.update(label=item["label"], account_status=item["account_status"])
        row.update(deletion_allowed=item.get("deletion_allowed", False),
                   deletion_reason=item.get("deletion_reason", "Device deletion status unavailable."),
                   deletion_history_count=item.get("deletion_history_count", row.get("total", 0)))
    if DEMO:
        for row in entries.values():
            row.update(status="reported", attempted=(poll_state or {}).get("attempted"))
    else:
        try:
            snapshot = json.loads(Path("/status/devices.json").read_text(encoding="utf-8"))
            fresh = 0 <= time.time() - snapshot["updated"] < 60
            for item in snapshot["devices"]:
                # Ignore stale collector-status cache rows for registrations
                # removed from PostgreSQL, so a successful delete disappears
                # from cards and selectors on the very next response.
                if item["device"] not in entries:
                    continue
                row = entries.setdefault(item["device"], {"device": item["device"], "latest": None, "total": 0})
                row.update(label=item["label"], attempted=item["attempted"],
                           status=item["status"] if fresh else "unknown")
        except (OSError, ValueError, KeyError, TypeError):
            pass
    for row in entries.values():
        row.setdefault("account_status", "unknown")
    return sorted(entries.values(), key=lambda row: (row["label"].casefold(), row["device"]))


@app.get("/api/export/{format}")
def export(request: Request, format: Literal["csv", "json", "gpx"], device: str | None = None,
           start: AwareDatetime | None = None, end: AwareDatetime | None = None):
    with connect() as db:
        authorize(request, db)
        records = history(db, device, start, end)
        if format == "json":
            content = json.dumps(records.fetchall(), default=lambda instant: instant.isoformat())
            mime = "application/json"
        elif format == "csv":
            out = io.StringIO(newline="")
            writer = csv.writer(out)
            writer.writerow([col.name for col in records.description])
            for row in records:
                writer.writerow([
                    "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@"))
                    else value for value in row.values()
                ])
            content, mime = out.getvalue(), "text/csv"
        else:
            root = ET.Element("gpx", xmlns="http://www.topografix.com/GPX/1/1",
                              version="1.1", creator="Find Hub Tracker")
            for row in records:
                point = ET.SubElement(root, "wpt", lat=str(row["latitude"]), lon=str(row["longitude"]))
                ET.SubElement(point, "time").text = row["reported"].isoformat()
                ET.SubElement(point, "name").text = row["label"]
            content, mime = ET.tostring(root, encoding="utf-8", xml_declaration=True), "application/gpx+xml"
    return Response(content, media_type=mime, headers={
        "Content-Disposition": f'attachment; filename="find-hub-{SOURCE}.{format}"'
    })


app.mount("/", StaticFiles(directory=Path(__file__).with_name("dist"), html=True))
