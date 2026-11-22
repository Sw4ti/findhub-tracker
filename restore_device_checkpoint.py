"""Operator-only recovery for a verified device deletion checkpoint.

Run inside the isolated Find Hub Tracker discovery service. The command takes
only a private checkpoint basename under DELETION_BACKUP_DIR; it accepts no
browser-supplied path or SQL.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

from device_deletion import verify_checkpoint_manifest
from device_discovery import (DELETION_BACKUP_DIR, collector_request, connect)


def _same_data(left, right) -> bool:
    return json.dumps(left, sort_keys=True, separators=(",", ":"), default=lambda value: value.isoformat()) == \
        json.dumps(right, sort_keys=True, separators=(",", ":"), default=lambda value: value.isoformat())


def restore_checkpoint(filename: str) -> dict:
    if not re.fullmatch(r"device-[a-f0-9]{16}-\d{8}T\d{6}Z\.tar\.gz", filename):
        raise ValueError("Checkpoint name is invalid.")
    root = DELETION_BACKUP_DIR.resolve(strict=True)
    archive = root / filename
    if archive.is_symlink() or archive.resolve(strict=True).parent != root:
        raise ValueError("Checkpoint location is unsafe.")

    conn = connect()
    device = None
    locks_acquired = False
    try:
        tombstone = conn.execute(
            "SELECT device,actor,operation_id,checkpoint_file,checkpoint_sha256,detections_deleted "
            "FROM deleted_devices WHERE checkpoint_file=%s", (filename,)
        ).fetchone()
        if not tombstone:
            raise ValueError("No matching deleted-device checkpoint is registered.")
        device = tombstone["device"]
        payload = verify_checkpoint_manifest(
            archive, tombstone["checkpoint_sha256"], device, tombstone["detections_deleted"])
        conn.execute("SELECT pg_advisory_lock(118903)")
        conn.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (device,))
        conn.commit()
        locks_acquired = True
        registration = payload["registration"]
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(118903)")
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (device,))
            existing_registration = conn.execute(
                "SELECT device,label,account_status,first_seen,last_seen,missing_successes "
                "FROM discovered_devices WHERE device=%s FOR UPDATE", (device,)).fetchone()
            existing_rows = conn.execute(
                "SELECT fingerprint,device,label,source,report_id,reported,collected,latitude,longitude,accuracy,status "
                "FROM detections WHERE device=%s ORDER BY fingerprint", (device,)).fetchall()
            expected_rows = sorted(payload["detections"], key=lambda row: row["fingerprint"])
            if existing_registration is None and not existing_rows:
                conn.execute("""INSERT INTO discovered_devices
                    (device,label,account_status,first_seen,last_seen,missing_successes)
                    VALUES (%s,%s,%s,%s,%s,%s)""",
                    (registration["device"], registration["label"], registration["account_status"],
                     registration["first_seen"], registration["last_seen"], registration["missing_successes"]))
                for row in payload["detections"]:
                    conn.execute("""INSERT INTO detections
                        (fingerprint,device,label,source,report_id,reported,collected,latitude,longitude,accuracy,status)
                        VALUES (%(fingerprint)s,%(device)s,%(label)s,%(source)s,%(report_id)s,%(reported)s,
                                %(collected)s,%(latitude)s,%(longitude)s,%(accuracy)s,%(status)s)""", row)
            elif (not existing_registration or not _same_data(dict(existing_registration), registration)
                  or not _same_data([dict(row) for row in existing_rows], expected_rows)):
                raise ValueError("Existing local records do not match this checkpoint; restore stopped safely.")

        # Keep the tombstone until collector state has been restored and verified.
        # If this call or the final DB transaction fails, rerunning this command
        # is idempotent: exact checkpoint rows are recognized above.
        response = collector_request(
            "POST", f"/internal/tracker/devices/{quote(device, safe='')}/restore",
            json={"operation_id": tombstone["operation_id"], "entry": payload["collector_entry"]}, timeout=45)
        if response.status_code != 200:
            raise RuntimeError("Collector state could not be restored; the tombstone remains active for retry.")
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(118903)")
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (device,))
            conn.execute("DELETE FROM deleted_devices WHERE device=%s AND checkpoint_sha256=%s",
                         (device, tombstone["checkpoint_sha256"]))
            conn.execute("INSERT INTO device_deletion_events(device,actor,detections_deleted,registration_deleted,"
                         "collector_log_rows_deleted,checkpoint_file,outcome) "
                         "VALUES (%s,'system-restore',%s,1,%s,%s,'restored')",
                         (device, len(payload["detections"]), len(payload["collector_entry"].get("forward_logs", {}).get("active", []))
                          + len(payload["collector_entry"].get("forward_logs", {}).get("legacy", [])), filename))
        return {"device_id": device, "detections_restored": len(payload["detections"]),
                "registration_restored": True}
    finally:
        try:
            if locks_acquired:
                conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (device,))
                conn.execute("SELECT pg_advisory_unlock(118903)")
                conn.commit()
        except Exception:
            pass
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", help="Verified checkpoint basename from the private deletion backup directory")
    args = parser.parse_args()
    try:
        result = restore_checkpoint(args.checkpoint)
    except Exception as error:
        print(f"Device checkpoint restore failed ({type(error).__name__}).", file=sys.stderr)
        return 1
    print(f"Restored {result['detections_restored']} history rows and one registration for the checkpointed device.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
