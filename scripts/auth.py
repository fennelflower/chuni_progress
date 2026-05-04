from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from db import cursor, row_to_dict


GROUP_NORMAL = "normal_users"
GROUP_HONORED = "honored_users"
GROUP_ADMIN = "admin"
GROUPS = {GROUP_NORMAL, GROUP_HONORED, GROUP_ADMIN}


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    iterations = 120_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations_text))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def ensure_default_admin() -> None:
    password_hash = hash_password("admin", bytes.fromhex("00112233445566778899aabbccddeeff"))
    with cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO users (id, account, password_hash, user_group)
            VALUES (1, %s, %s, %s)
            ON CONFLICT (id)
            DO UPDATE SET
                account = EXCLUDED.account,
                password_hash = EXCLUDED.password_hash,
                user_group = EXCLUDED.user_group
            """,
            ("admin", password_hash, GROUP_ADMIN),
        )
        cur.execute("SELECT setval(pg_get_serial_sequence('users', 'id'), GREATEST((SELECT MAX(id) FROM users), 1))")
        cur.execute("UPDATE scores SET user_id = 1 WHERE user_id IS NULL")


def create_user(account: str, password: str, qq_user_id: int | None = None) -> dict[str, Any]:
    with cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO users (account, password_hash, user_group, qq_user_id)
            VALUES (%s, %s, %s, %s)
            RETURNING id, account, user_group, qq_user_id
            """,
            (account, hash_password(password), GROUP_NORMAL, str(qq_user_id) if qq_user_id else None),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("failed to create user")
        return row_to_dict(row)


def bind_login(account: str, password: str, qq_user_id: int) -> dict[str, Any] | None:
    with cursor(commit=True) as cur:
        cur.execute("SELECT id, account, password_hash, user_group, qq_user_id FROM users WHERE account = %s", (account,))
        row = cur.fetchone()
        if not row:
            return None
        user = row_to_dict(row)
        if not verify_password(password, user["password_hash"]):
            return None
        cur.execute(
            """
            UPDATE users
            SET qq_user_id = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING id, account, user_group, qq_user_id
            """,
            (str(qq_user_id), user["id"]),
        )
        updated_row = cur.fetchone()
        if updated_row is None:
            raise RuntimeError("failed to bind login")
        return row_to_dict(updated_row)


def get_user_by_qq(qq_user_id: int | None) -> dict[str, Any] | None:
    if not qq_user_id:
        return None
    with cursor() as cur:
        cur.execute(
            "SELECT id, account, user_group, qq_user_id FROM users WHERE qq_user_id = %s",
            (str(qq_user_id),),
        )
        row = cur.fetchone()
        return row_to_dict(row) if row else None


def logout_by_qq(qq_user_id: int | None) -> dict[str, Any] | None:
    if not qq_user_id:
        return None
    with cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE users
            SET qq_user_id = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE qq_user_id = %s
            RETURNING id, account, user_group
            """,
            (str(qq_user_id),),
        )
        row = cur.fetchone()
        return row_to_dict(row) if row else None


def set_user_group(account: str, user_group: str) -> dict[str, Any] | None:
    if user_group not in GROUPS:
        raise ValueError(f"unknown user group: {user_group}")
    with cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE users
            SET user_group = %s, updated_at = CURRENT_TIMESTAMP
            WHERE account = %s
            RETURNING id, account, user_group, qq_user_id
            """,
            (user_group, account),
        )
        row = cur.fetchone()
        return row_to_dict(row) if row else None
