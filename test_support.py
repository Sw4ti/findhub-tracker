"""Runtime-generated synthetic auth material shared by isolated tests."""
import os
import secrets
import tempfile
from pathlib import Path

from argon2 import PasswordHasher

TEST_PASSWORD = os.environ.get("PORTABLE_QA_PASSWORD") or secrets.token_urlsafe(32)
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="findhub-test-auth-"))
_TEST_HASH_FILE = _TEST_ROOT / "password_hash"
_TEST_HASH_FILE.write_text(
    PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(TEST_PASSWORD),
    encoding="ascii",
)
try:
    _TEST_HASH_FILE.chmod(0o600)
except OSError:
    pass
os.environ.setdefault("DASHBOARD_PASSWORD_HASH_FILE", str(_TEST_HASH_FILE))
