"""SQLite-backed user accounts — registration, approval, authentication.

Self-registered accounts (POST /register) land in status='pending' and cannot
authenticate until an admin approves them via POST /api/users/{id}/approve.
Accounts created by an admin from the dashboard (POST /api/users) are
status='approved' immediately — creating the account there *is* the approval.
Self-registration is hardcoded to role='user' (see routes/pages.py), so only
an admin can ever create an admin account, and that endpoint is itself
admin-only (enforced by the AuthMiddleware admin-path gate in main.py).

Passwords are stored as PBKDF2-HMAC-SHA256 hashes (stdlib `hashlib`, no extra
dependency) with a random per-user salt, formatted as "<salt-hex>$<hash-hex>".
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import threading
from datetime import datetime, timezone

from ..config import USERS_DB_PATH

_LOCK = threading.RLock()
_PBKDF2_ITERATIONS = 200_000

_SEED_ADMIN_USERNAME = "PIKORUA"
_SEED_ADMIN_PASSWORD = "Pikorua@123"


def _connect() -> sqlite3.Connection:
    USERS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(USERS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, _ = stored_hash.split("$", 1)
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    # Constant-time compare so login timing can't be used to guess the password.
    return hmac.compare_digest(_hash_password(password, salt), stored_hash)


def _public(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "username": row["username"], "role": row["role"],
        "status": row["status"], "created_at": row["created_at"],
        "approved_at": row["approved_at"],
    }


def init_db() -> None:
    with _LOCK, _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                approved_at TEXT
            )
        """)
        conn.commit()
        row = conn.execute(
            "SELECT id FROM users WHERE username = ?", (_SEED_ADMIN_USERNAME,)
        ).fetchone()
        if row is None:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO users (username, password_hash, role, status, created_at, approved_at) "
                "VALUES (?, ?, 'admin', 'approved', ?, ?)",
                (_SEED_ADMIN_USERNAME, _hash_password(_SEED_ADMIN_PASSWORD), now, now),
            )
            conn.commit()


def create_user(username: str, password: str, role: str = "user", status: str = "pending") -> dict:
    username = username.strip()
    if not username or not password:
        raise ValueError("Username and password are required.")
    now = datetime.now(timezone.utc).isoformat()
    approved_at = now if status == "approved" else None
    with _LOCK, _connect() as conn:
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing is not None:
            raise ValueError(f"Username '{username}' is already taken.")
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, status, created_at, approved_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (username, _hash_password(password), role, status, now, approved_at),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _public(row)


def get_user(user_id: int) -> dict | None:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _public(row) if row else None


def list_users() -> list[dict]:
    with _LOCK, _connect() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    return [_public(row) for row in rows]


def verify_credentials(username: str, password: str) -> dict | None:
    """Returns the user (any status) if the password is correct, else None.

    Status is left to the caller (services/auth.authenticate) so it can give
    a specific "pending approval" / "rejected" message rather than a generic
    incorrect-password error.
    """
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
    if row is None or not _verify_password(password, row["password_hash"]):
        return None
    return _public(row)


def approve_user(user_id: int) -> dict | None:
    now = datetime.now(timezone.utc).isoformat()
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE users SET status = 'approved', approved_at = ? WHERE id = ?", (now, user_id)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _public(row)


def reject_user(user_id: int) -> dict | None:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE users SET status = 'rejected' WHERE id = ?", (user_id,))
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _public(row)


def count_admins() -> int:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'admin'").fetchone()
    return row["n"]


def delete_user(user_id: int) -> bool:
    with _LOCK, _connect() as conn:
        cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    return cur.rowcount > 0


init_db()
