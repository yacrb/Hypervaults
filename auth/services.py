"""Focused password, opaque session, throttling, SIWE and recovery services."""

import hashlib
import secrets
import smtplib
import ssl
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException, Request, Response
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pymongo import ReturnDocument
from siwe import SiweMessage

from webapp.config import Settings


def utcnow() -> datetime:
    return datetime.now(UTC)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def public_user(user: dict | None) -> dict | None:
    if user is None:
        return None
    return {
        "id": user["_id"],
        "name": user["name"],
        "email": user.get("email"),
        "wallets": user.get("wallets", []),
        "role": user["role"],
        "disabled": user["disabled"],
        "has_password": bool(user.get("password_hash")),
    }


class AuthService:
    def __init__(self, db, settings: Settings, clock=utcnow):
        self.db, self.settings, self.now = db, settings, clock
        self.passwords = PasswordHash.recommended()
        self.dummy_hash = self.passwords.hash(secrets.token_urlsafe(32))

    def audit(self, user_id: str | None, kind: str, **details) -> None:
        self.db.audit.insert_one(
            {
                "_id": str(uuid.uuid4()),
                "user_id": user_id,
                "kind": kind,
                "details": details,
                "created_at": self.now(),
            }
        )

    def throttle(self, request: Request, action: str, identity: str = "", limit: int = 10) -> None:
        now = self.now()
        window = int(now.timestamp()) // 60
        ip = request.client.host if request.client else "unknown"
        # Separate IP and identity budgets bound both spray and targeted attempts.
        budgets = [(f"ip:{ip}", max(limit * 3, 15))]
        if identity:
            budgets.append((f"identity:{identity.casefold()}", limit))
        for key, maximum in budgets:
            row = self.db.throttles.find_one_and_update(
                {"_id": digest(f"{action}:{key}:{window}")},
                {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": now + timedelta(minutes=3)}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            if row["count"] > maximum:
                raise HTTPException(
                    429, "Too many attempts. Try again shortly.", headers={"Retry-After": "60"}
                )

    def create_session(
        self,
        response: Response,
        request: Request,
        user: dict | None = None,
        method: str | None = None,
    ) -> dict:
        raw = secrets.token_urlsafe(32)
        now = self.now()
        row = {
            "_id": digest(raw),
            "csrf": secrets.token_urlsafe(32),
            "created_at": now,
            "last_seen": now,
            "expires_at": now + timedelta(seconds=self.settings.session_absolute_seconds),
            "user_id": user["_id"] if user else None,
            "version": user["auth_version"] if user else None,
            "authenticated_at": now if user else None,
            "method": method,
            "agent": request.headers.get("user-agent", "Unknown browser")[:120],
        }
        self.db.sessions.insert_one(row)
        response.set_cookie(
            self.settings.cookie_name,
            raw,
            httponly=True,
            secure=self.settings.secure_cookie,
            samesite="strict",
            path="/",
            max_age=self.settings.session_absolute_seconds,
        )
        request.state.auth_context = (row, user)
        return row

    def resolve(self, request: Request, required: bool = True) -> tuple[dict | None, dict | None]:
        if hasattr(request.state, "auth_context"):
            session, user = request.state.auth_context
        else:
            raw = request.cookies.get(self.settings.cookie_name, "")
            now = self.now()
            session = None
            if raw and len(raw) <= 128:
                session = self.db.sessions.find_one_and_update(
                    {
                        "_id": digest(raw),
                        "expires_at": {"$gt": now},
                        "last_seen": {
                            "$gt": now - timedelta(seconds=self.settings.session_idle_seconds)
                        },
                    },
                    {"$set": {"last_seen": now}},
                    return_document=ReturnDocument.AFTER,
                )
            user = (
                self.db.users.find_one({"_id": session["user_id"]})
                if session and session["user_id"]
                else None
            )
            if (
                session
                and session["user_id"]
                and (user is None or user["disabled"] or user["auth_version"] != session["version"])
            ):
                self.db.sessions.delete_one({"_id": session["_id"]})
                session, user = None, None
            request.state.auth_context = (session, user)
        if required and session is None:
            raise HTTPException(401, "Session expired. Sign in again.")
        return session, user

    def rotate(self, response: Response, request: Request, user: dict, method: str) -> dict:
        previous, _ = self.resolve(request)
        self.db.sessions.delete_one({"_id": previous["_id"]})
        self.db.challenges.delete_many({"session_id": previous["_id"]})
        return self.create_session(response, request, user, method)

    def recent(self, session: dict) -> None:
        threshold = self.now() - timedelta(seconds=self.settings.recent_auth_seconds)
        if session.get("authenticated_at") is None or session["authenticated_at"] < threshold:
            raise HTTPException(
                403, "Recent authentication required. Verify your password or linked wallet."
            )

    def verify_password(self, password: str, user: dict | None) -> bool:
        stored = user.get("password_hash", self.dummy_hash) if user else self.dummy_hash
        try:
            valid, replacement = self.passwords.verify_and_update(password, stored)
        except UnknownHashError:
            return False
        if valid and user and replacement:
            self.db.users.update_one(
                {"_id": user["_id"], "password_hash": stored},
                {"$set": {"password_hash": replacement}},
            )
        return bool(valid and user and user.get("password_hash"))

    def challenge(self, session: dict, user: dict | None, address: str, action: str) -> dict:
        current = self.now()
        now = current.replace(microsecond=(current.microsecond // 1000) * 1000)
        expires = now + timedelta(seconds=self.settings.challenge_seconds)
        nonce = secrets.token_hex(16)
        statement = {
            "login": "Sign in to HyperVaults. This does not authorize a token transaction.",
            "link": "Link this wallet to your HyperVaults account. No token transaction is authorized.",
            "reauth": "Confirm recent authentication for your HyperVaults account.",
        }[action]
        message = SiweMessage(
            scheme=urlparse(self.settings.origin).scheme,
            domain=self.settings.siwe_domain,
            address=address,
            statement=statement,
            uri=self.settings.origin,
            version="1",
            chain_id=self.settings.chain_id,
            nonce=nonce,
            issued_at=now.isoformat(),
            expiration_time=expires.isoformat(),
            not_before=now.isoformat(),
            request_id=action,
        ).prepare_message()
        key = digest(f"{session['_id']}:{action}")
        self.db.challenges.replace_one(
            {"_id": key},
            {
                "_id": key,
                "session_id": session["_id"],
                "user_id": user["_id"] if user else None,
                "version": user["auth_version"] if user else None,
                "action": action,
                "address": address.lower(),
                "nonce": nonce,
                "message": message,
                "issued_at": now,
                "expires_at": expires,
            },
            upsert=True,
        )
        return {"id": key, "message": message, "expires_at": expires.isoformat()}

    def consume_challenge(
        self, session: dict, user: dict | None, challenge_id: str, message: str, signature: str
    ) -> dict:
        now = self.now()
        row = self.db.challenges.find_one(
            {
                "_id": challenge_id,
                "session_id": session["_id"],
                "expires_at": {"$gt": now},
            }
        )
        if row is None or not secrets.compare_digest(message, row["message"]):
            raise HTTPException(400, "Invalid or expired wallet challenge.")
        if row["user_id"] != (user["_id"] if user else None) or row["version"] != (
            user["auth_version"] if user else None
        ):
            raise HTTPException(400, "Invalid or expired wallet challenge.")
        try:
            parsed = SiweMessage.from_message(message)
            if len(signature) != 132 or not signature.startswith("0x"):
                raise ValueError("Only 65-byte EOA signatures are supported")
            if (
                parsed.address.lower() != row["address"]
                or parsed.chain_id != self.settings.chain_id
                or str(parsed.uri) != self.settings.origin
                or parsed.request_id != row["action"]
                or parsed.issued_at != row["issued_at"].isoformat()
            ):
                raise ValueError("Unexpected message context")
            parsed.verify(
                signature=signature,
                scheme=urlparse(self.settings.origin).scheme,
                domain=self.settings.siwe_domain,
                nonce=row["nonce"],
                timestamp=now,
            )
        except Exception as exc:
            raise HTTPException(400, "Invalid or expired wallet challenge.") from exc
        consumed = self.db.challenges.find_one_and_delete(
            {
                "_id": row["_id"],
                "session_id": session["_id"],
                "message": message,
                "expires_at": {"$gt": self.now()},
            }
        )
        if consumed is None:
            raise HTTPException(400, "Wallet challenge already used or expired.")
        return consumed

    def deliver_recovery(self, email: str, token: str) -> None:
        # Fragment keeps tokens out of HTTP URLs, server access logs and referrers.
        message = EmailMessage()
        message["To"], message["From"] = (
            email,
            self.settings.mail_from or "local-preview@hypervaults.invalid",
        )
        message["Subject"] = "Reset your HyperVaults password"
        message.set_content(
            f"Use this single-use link within {self.settings.reset_seconds // 60} minutes:\n"
            f"{self.settings.origin}/reset#token={token}\n\n"
            "Password recovery cannot transfer or unlock on-chain positions."
        )
        if self.settings.mode in {"development", "test"}:
            path: Path = self.settings.outbox_path
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.chmod(0o700)
            file = path / f"{uuid.uuid4()}.eml"
            # Exclusively create private files, with no intermediate readable token.
            with file.open("x") as stream:
                file.chmod(0o600)
                stream.write(message.as_string())
        else:
            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=10) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if self.settings.smtp_user:
                    smtp.login(self.settings.smtp_user, self.settings.smtp_password)
                smtp.send_message(message)
