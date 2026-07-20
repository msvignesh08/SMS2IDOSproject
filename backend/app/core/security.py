"""
SMS-2 IDOS — SECURITY CORE (Section 11)
Intranet-first. Zero cloud/internet dependency.

Password security: PBKDF2-HMAC-SHA256 with per-user salt and key stretching.
This honors the constitutional "SHA-256 Password Security" clause (11.4)
while meeting its companion clauses "Strong Password Policies" and
"Future Encryption Standards" — raw single-round SHA-256 is not stored,
plain text is never stored.
"""
from __future__ import annotations
import hashlib, hmac, os, secrets

PBKDF2_ITERATIONS = 240_000
_TOKEN_BYTES = 32


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def password_policy_errors(password: str) -> list[str]:
    errs = []
    if len(password) < 8:
        errs.append("Password must be at least 8 characters.")
    if not any(c.isdigit() for c in password):
        errs.append("Password must contain at least one digit.")
    if not any(c.isalpha() for c in password):
        errs.append("Password must contain at least one letter.")
    return errs
