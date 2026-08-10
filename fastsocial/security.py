from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any

import bcrypt
from cryptography.fernet import Fernet, InvalidToken

from fastsocial.config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except (ValueError, TypeError):
        return False


def _fernet() -> Fernet:
    configured = settings().token_encryption_key.strip()
    if configured:
        key = configured.encode()
    else:
        digest = hashlib.sha256(settings().app_secret.encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_text(value: str | None) -> bytes | None:
    return _fernet().encrypt(value.encode()) if value else None


def decrypt_text(value: bytes | None) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value).decode()
    except InvalidToken as exc:
        raise RuntimeError("Stored credential cannot be decrypted with the configured key") from exc


def encrypt_json(value: dict[str, Any]) -> bytes:
    return _fernet().encrypt(json.dumps(value, separators=(",", ":")).encode())


def decrypt_json(value: bytes | None) -> dict[str, Any]:
    plain = decrypt_text(value)
    return json.loads(plain) if plain else {}


def csrf_token(sess: dict) -> str:
    token = sess.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        sess["csrf_token"] = token
    return token


def verify_csrf(sess: dict, submitted: str | None) -> bool:
    expected = sess.get("csrf_token", "")
    return bool(expected and submitted and secrets.compare_digest(expected, submitted))
