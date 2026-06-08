from __future__ import annotations

import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

import bcrypt

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def get_session_secret() -> str:
    """Return a persistent session signing secret, creating it on first call."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    secret_file = _DATA_DIR / ".session_secret"
    if secret_file.exists():
        return secret_file.read_text().strip()
    secret = secrets.token_hex(32)
    secret_file.write_text(secret)
    secret_file.chmod(0o600)
    return secret


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def get_password_hash(conn: sqlite3.Connection) -> Optional[str]:
    try:
        row = conn.execute("SELECT password_hash FROM auth_config WHERE id=1").fetchone()
        return row["password_hash"] if row else None
    except sqlite3.OperationalError:
        return None


def set_password_hash(conn: sqlite3.Connection, hashed: str) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO auth_config (id, password_hash) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET password_hash=excluded.password_hash
            """,
            (hashed,),
        )


def macos_authenticate(reason: str = "access Salt Stocks") -> bool:
    """Prompt Touch ID (with Mac password fallback) via macOS LocalAuthentication.

    Returns True if authenticated, False if cancelled or framework unavailable.
    """
    try:
        from LocalAuthentication import LAContext, LAPolicyDeviceOwnerAuthentication
        from Foundation import NSRunLoop, NSDate, NSDefaultRunLoopMode
    except ImportError:
        return False

    context = LAContext.alloc().init()
    can_auth, _ = context.canEvaluatePolicy_error_(LAPolicyDeviceOwnerAuthentication, None)
    if not can_auth:
        return False

    result: list[Optional[bool]] = [None]
    done = threading.Event()

    def _reply(success: bool, error: object) -> None:
        result[0] = bool(success)
        done.set()

    context.evaluatePolicy_localizedReason_reply_(
        LAPolicyDeviceOwnerAuthentication,
        reason,
        _reply,
    )

    # Pump the NSRunLoop so macOS can deliver the LA callback on the main thread.
    deadline = time.monotonic() + 60.0
    while not done.is_set() and time.monotonic() < deadline:
        NSRunLoop.mainRunLoop().runMode_beforeDate_(
            NSDefaultRunLoopMode,
            NSDate.dateWithTimeIntervalSinceNow_(0.05),
        )

    return bool(result[0])
