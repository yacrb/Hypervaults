"""Authenticator codes, encrypted enrollment and atomic one-use factor verification."""

import base64
import hashlib
import hmac
import re
import secrets
import struct
import uuid
from datetime import timedelta
from urllib.parse import quote, urlencode

from fastapi import HTTPException
from pymongo import ReturnDocument

from auth.crypto import NoteCipher


def totp(secret: str, step: int, digits: int = 6) -> str:
    """RFC 6238, SHA-1 and a 30-second counter; digits=8 supports RFC test vectors."""
    key = base64.b32decode(secret)
    mac = hmac.digest(key, struct.pack(">Q", step), "sha1")
    offset = mac[-1] & 15
    number = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10**digits)).zfill(digits)


def matching_step(secret: str, code: str, timestamp: float) -> int | None:
    if not re.fullmatch(r"[0-9]{6}", code):
        return None
    current = int(timestamp) // 30
    for step in (current, current - 1, current + 1):
        if step >= 0 and secrets.compare_digest(totp(secret, step), code):
            return step
    return None


def recovery_hash(code: str) -> str:
    # Codes contain 128 random bits, so a fast hash does not permit feasible guessing.
    return hashlib.sha256(code.replace("-", "").strip().lower().encode()).hexdigest()


def recovery_codes() -> tuple[list[str], list[str]]:
    raw = [secrets.token_hex(16) for _ in range(8)]
    codes = ["-".join(code[i : i + 8] for i in range(0, 32, 8)) for code in raw]
    return codes, [recovery_hash(code) for code in codes]


class MFAService:
    def __init__(self, auth):
        self.auth, self.db = auth, auth.db
        settings = auth.settings
        # Separate authenticated context prevents MFA ciphertext becoming a note.
        # Both purposes share the same persistent nonce reservations and key ring.
        self.cipher = NoteCipher(
            settings.note_keys, settings.note_active_key, self.db, "hypervaults-mfa-v1"
        )

    def verify(self, user: dict, code: str) -> dict:
        """Consume a factor only once, even when two requests arrive together."""
        mfa = user.get("mfa")
        if not mfa:
            return user
        if not code:
            raise HTTPException(
                403,
                "Enter an authenticator or recovery code.",
                headers={"X-MFA-Required": "true"},
            )
        selector = {
            "_id": user["_id"],
            "auth_version": user["auth_version"],
            "disabled": False,
        }
        if re.fullmatch(r"[0-9]{6}", code):
            secret = self.cipher.decrypt(mfa["secret"], user["_id"], "authenticator")
            step = matching_step(secret, code, self.auth.now().timestamp())
            if step is None:
                raise HTTPException(400, "Code is invalid or already used. Try the next code.")
            selector["mfa.last_step"] = {"$lt": step}
            update = {"$set": {"mfa.last_step": step}}
        else:
            hashed = recovery_hash(code)
            selector["mfa.recovery_hashes"] = hashed
            update = {"$pull": {"mfa.recovery_hashes": hashed}}
        changed = self.db.users.find_one_and_update(
            selector, update, return_document=ReturnDocument.AFTER
        )
        if changed is None:
            raise HTTPException(400, "Code is invalid or already used. Try the next code.")
        self.auth.audit(user["_id"], "mfa_verified", recovery=not code.isdecimal())
        return changed

    def begin(self, session: dict, user: dict) -> dict:
        if user.get("mfa"):
            raise HTTPException(409, "Two-factor authentication is already enabled.")
        secret = base64.b32encode(secrets.token_bytes(20)).decode()
        enrollment = str(uuid.uuid4())
        self.db.mfa_setups.replace_one(
            {"_id": session["_id"]},
            {
                "_id": session["_id"],
                "enrollment": enrollment,
                "user_id": user["_id"],
                "version": user["auth_version"],
                "secret": self.cipher.encrypt(secret, user["_id"], enrollment),
                "expires_at": self.auth.now() + timedelta(minutes=5),
            },
            upsert=True,
        )
        label = quote(f"HyperVaults:{user.get('email') or user['name']}", safe="")
        parameters = urlencode(
            {
                "secret": secret,
                "issuer": "HyperVaults",
                "algorithm": "SHA1",
                "digits": 6,
                "period": 30,
            }
        )
        uri = f"otpauth://totp/{label}?{parameters}"
        return {"secret": secret, "uri": uri}

    def confirmation(self, action: str | None, user: dict) -> dict | None:
        if not action:
            return None
        if not user.get("mfa"):
            raise HTTPException(409, "Enable two-factor authentication first.")
        return {"action": action, "expires_at": self.auth.now() + timedelta(seconds=60)}

    def consume_confirmation(self, session: dict, action: str) -> None:
        # One primary+MFA proof authorizes one named settings change, not arbitrary work.
        claimed = self.db.sessions.update_one(
            {
                "_id": session["_id"],
                "mfa_confirmation.action": action,
                "mfa_confirmation.expires_at": {"$gt": self.auth.now()},
            },
            {"$unset": {"mfa_confirmation": ""}},
        )
        if not claimed.modified_count:
            raise HTTPException(403, "Confirm your identity for this two-factor setting.")

    def enrollment(self, session: dict, user: dict, code: str) -> dict:
        row = self.db.mfa_setups.find_one(
            {
                "_id": session["_id"],
                "user_id": user["_id"],
                "version": user["auth_version"],
                "expires_at": {"$gt": self.auth.now()},
            }
        )
        if row is None:
            raise HTTPException(400, "Setup expired. Start again.")
        secret = self.cipher.decrypt(row["secret"], user["_id"], row["enrollment"])
        step = matching_step(secret, code, self.auth.now().timestamp())
        if step is None:
            raise HTTPException(400, "Enter the six-digit code from your authenticator.")
        if not self.db.mfa_setups.delete_one(
            {"_id": row["_id"], "enrollment": row["enrollment"]}
        ).deleted_count:
            raise HTTPException(409, "Setup changed. Start again.")
        return {
            "secret": self.cipher.encrypt(secret, user["_id"], "authenticator"),
            "last_step": step,
        }
