"""Database-backed authentication and account administration REST endpoints."""

import secrets
import smtplib
import uuid
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from web3 import Web3

from auth.dependencies import administrator, context, current_user, protect_mutations
from auth.services import digest, public_user

router = APIRouter(prefix="/api", dependencies=[Depends(protect_mutations)])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class Credentials(Input):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)


class Registration(Credentials):
    password: str = Field(min_length=12, max_length=128)
    name: str = Field(min_length=1, max_length=60)


class Reauthentication(Input):
    password: str = Field(min_length=1, max_length=128)
    mfa_code: str = Field(default="", max_length=64)
    mfa_action: Literal["disable", "recovery-codes"] | None = None


class PasswordChange(Input):
    current_password: str = Field(default="", max_length=128)
    new_password: str = Field(min_length=12, max_length=128)
    email: EmailStr | None = None
    mfa_code: str = Field(default="", max_length=64)


class Recovery(Input):
    email: EmailStr = Field(max_length=254)


class Reset(Input):
    token: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    password: str = Field(min_length=12, max_length=128)
    mfa_code: str = Field(default="", max_length=64)


class Challenge(Input):
    address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    action: Literal["login", "link", "reauth"]


class Proof(Input):
    id: str = Field(pattern=r"^[0-9a-f]{64}$")
    message: str = Field(min_length=80, max_length=2048)
    signature: str = Field(max_length=256)
    mfa_code: str = Field(default="", max_length=64)
    mfa_action: Literal["disable", "recovery-codes"] | None = None


class UserChange(Input):
    role: Literal["user", "admin"] | None = None
    disabled: bool | None = None


def signed_in_payload(user, session):
    return {"user": public_user(user), "csrf": session["csrf"]}


@router.get("/session")
def session_bootstrap(request: Request, response: Response):
    service = request.app.state.auth
    service.throttle(request, "bootstrap", limit=60)
    session, user = service.resolve(request, required=False)
    if session is None:
        session = service.create_session(response, request)
    payload = signed_in_payload(user, session)
    pending = session.get("pending_mfa")
    payload["mfa_required"] = bool(not user and pending and pending["expires_at"] > service.now())
    return payload


@router.post("/auth/register", status_code=201)
def register(body: Registration, request: Request, response: Response):
    service = request.app.state.auth
    service.throttle(request, "registration", str(body.email), limit=5)
    _, existing = context(request)
    if existing:
        raise HTTPException(409, "Sign out before creating another account.")
    user = {
        "_id": str(uuid.uuid4()),
        "email": str(body.email).casefold(),
        "name": body.name.strip() or "Member",
        "password_hash": service.passwords.hash(body.password),
        "wallets": [],
        "role": "user",
        "disabled": False,
        "auth_version": 1,
        "created_at": service.now(),
    }
    try:
        service.db.users.insert_one(user)
    except DuplicateKeyError as exc:
        raise HTTPException(
            409,
            "Registration could not be completed. Try signing in or recovering access.",
        ) from exc
    session = service.rotate(response, request, user, "password")
    service.audit(user["_id"], "account_registered")
    return signed_in_payload(user, session)


@router.post("/auth/login")
def login(body: Credentials, request: Request, response: Response):
    service = request.app.state.auth
    email = str(body.email).casefold()
    service.throttle(request, "login", email, limit=5)
    user = service.db.users.find_one({"email": email})
    valid = service.verify_password(body.password, user)
    if not valid or user["disabled"]:
        service.audit(user["_id"] if user else None, "login_failed")
        raise HTTPException(401, "Invalid credentials or unavailable account.")
    return service.sign_in(response, request, user, "password")


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    session, user = context(request)
    service = request.app.state.auth
    service.db.sessions.delete_one({"_id": session["_id"]})
    service.db.challenges.delete_many({"session_id": session["_id"]})
    service.db.mfa_setups.delete_one({"_id": session["_id"]})
    response.delete_cookie(
        service.settings.cookie_name,
        path="/",
        secure=service.settings.secure_cookie,
        httponly=True,
        samesite="strict",
    )
    if user:
        service.audit(user["_id"], "session_logged_out")
    return {"ok": True}


@router.post("/auth/reauth")
def reauthenticate(body: Reauthentication, request: Request, user=Depends(current_user)):
    service = request.app.state.auth
    session, _ = context(request)
    service.throttle(request, "reauthentication", user["_id"], limit=5)
    if not service.verify_password(body.password, user):
        raise HTTPException(401, "Authentication failed.")
    service.mfa.verify(user, body.mfa_code)
    confirmation = service.mfa.confirmation(body.mfa_action, user)
    service.db.sessions.update_one(
        {"_id": session["_id"]},
        {"$set": {"authenticated_at": service.now(), "method": "password", "mfa_confirmation": confirmation}},
    )
    service.audit(user["_id"], "recent_password_authentication")
    return {"ok": True}


def security_change(service, request, response, user, update: dict, kind: str):
    changed = service.db.users.find_one_and_update(
        {"_id": user["_id"], "auth_version": user["auth_version"], "disabled": False},
        {"$set": update, "$inc": {"auth_version": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if changed is None:
        raise HTTPException(409, "Account changed. Sign in again.")
    service.db.sessions.delete_many({"user_id": user["_id"]})
    service.db.reset_tokens.delete_many({"user_id": user["_id"]})
    service.db.mfa_setups.delete_many({"user_id": user["_id"]})
    previous, _ = context(request)
    session = service.create_session(
        response,
        request,
        changed,
        "password" if kind == "password_changed" else previous["method"],
    )
    service.audit(user["_id"], kind)
    return signed_in_payload(changed, session)


@router.post("/auth/password")
def change_password(
    body: PasswordChange,
    request: Request,
    response: Response,
    user=Depends(current_user),
):
    service = request.app.state.auth
    session, _ = context(request)
    service.throttle(request, "password_change", user["_id"], limit=5)
    update = {"password_hash": service.passwords.hash(body.new_password)}
    if user.get("password_hash"):
        if not service.verify_password(body.current_password, user):
            raise HTTPException(401, "Authentication failed.")
        if body.email is not None and str(body.email).casefold() != user["email"]:
            raise HTTPException(400, "This operation does not change your email identity.")
    else:
        service.recent(session)
        if session["method"] != "wallet" or body.email is None:
            raise HTTPException(
                400,
                "Verify a linked wallet and provide an email to add password sign-in.",
            )
        update["email"] = str(body.email).casefold()
    # Wallet-only setup has just completed full wallet+MFA reauthentication.
    if user.get("password_hash"):
        service.mfa.verify(user, body.mfa_code)
    try:
        return security_change(service, request, response, user, update, "password_changed")
    except DuplicateKeyError as exc:
        raise HTTPException(
            409,
            "Email identity is unavailable; accounts are never merged automatically.",
        ) from exc


@router.post("/auth/recover", status_code=202)
def recover(body: Recovery, request: Request):
    service = request.app.state.auth
    email = str(body.email).casefold()
    service.throttle(request, "recovery", email, limit=3)
    user = service.db.users.find_one({"email": email, "disabled": False})
    if user and user.get("password_hash"):
        token = secrets.token_urlsafe(32)
        service.db.reset_tokens.delete_many({"user_id": user["_id"]})
        service.db.reset_tokens.insert_one(
            {
                "_id": digest(token),
                "user_id": user["_id"],
                "version": user["auth_version"],
                "expires_at": service.now() + timedelta(seconds=service.settings.reset_seconds),
            }
        )
        try:
            service.deliver_recovery(email, token)
            service.audit(user["_id"], "password_recovery_requested")
        except (OSError, smtplib.SMTPException):
            service.audit(user["_id"], "recovery_mail_delivery_failed")
    return {"message": "If this account can recover access, a reset link has been sent."}


@router.post("/auth/reset")
def reset(body: Reset, request: Request):
    service = request.app.state.auth
    service.throttle(request, "reset", limit=5)
    row = service.db.reset_tokens.find_one(
        {
            "_id": digest(body.token),
            "expires_at": {"$gt": service.now()},
        }
    )
    if row is None:
        raise HTTPException(400, "Reset link is invalid, used or expired.")
    user = service.db.users.find_one(
        {"_id": row["user_id"], "auth_version": row["version"], "disabled": False}
    )
    if user is None:
        raise HTTPException(400, "Reset link is invalid, used or expired.")
    service.throttle(request, "reset_mfa", user["_id"], limit=5)
    service.mfa.verify(user, body.mfa_code)
    # Invalid factors leave the link usable; successful resets claim it once.
    if (
        service.db.reset_tokens.find_one_and_delete({"_id": row["_id"], "version": row["version"]})
        is None
    ):
        raise HTTPException(400, "Reset link is invalid, used or expired.")
    changed = service.db.users.find_one_and_update(
        {"_id": row["user_id"], "auth_version": row["version"], "disabled": False},
        {
            "$set": {"password_hash": service.passwords.hash(body.password)},
            "$inc": {"auth_version": 1},
        },
    )
    if changed is None:
        raise HTTPException(400, "Reset link is invalid, used or expired.")
    service.db.sessions.delete_many({"user_id": row["user_id"]})
    service.db.reset_tokens.delete_many({"user_id": row["user_id"]})
    service.audit(row["user_id"], "password_reset")
    return {"message": "Password reset. Sign in with your new password."}


@router.post("/auth/challenge")
def wallet_challenge(body: Challenge, request: Request):
    service = request.app.state.auth
    session, user = context(request)
    service.throttle(request, "wallet_challenge", session["_id"], limit=10)
    if body.action == "login" and user:
        raise HTTPException(409, "Sign out before wallet sign-in; use linking to add a wallet.")
    if body.action != "login":
        user = current_user(request)
        if body.action == "link":
            service.recent(session)
        if body.action == "reauth" and body.address.lower() not in user["wallets"]:
            raise HTTPException(403, "Authenticate with a wallet already linked to this account.")
    address = Web3.to_checksum_address(body.address)
    if not request.app.state.chain.is_eoa(address):
        raise HTTPException(
            400,
            "Use a key-controlled wallet. Contract-wallet signatures are not supported.",
        )
    return service.challenge(session, user, address, body.action)


@router.post("/auth/verify")
def wallet_verify(body: Proof, request: Request, response: Response):
    service = request.app.state.auth
    session, user = context(request)
    service.throttle(request, "wallet_verify", session["_id"], limit=10)
    # Reject unsupported wallets again in case code was deployed after issuance.
    candidate = service.db.challenges.find_one({"_id": body.id, "session_id": session["_id"]})
    if candidate and not request.app.state.chain.is_eoa(candidate["address"]):
        raise HTTPException(400, "Only EOA wallet signatures are supported.")
    proof = service.consume_challenge(session, user, body.id, body.message, body.signature)
    if body.mfa_action and proof["action"] != "reauth":
        raise HTTPException(400, "Account confirmation requires reauthentication.")
    address = proof["address"]
    if proof["action"] == "link":
        service.recent(session)
        try:
            changed = service.db.users.find_one_and_update(
                {
                    "_id": user["_id"],
                    "auth_version": user["auth_version"],
                    "disabled": False,
                    "wallets": {"$ne": address},
                    "$expr": {"$lt": [{"$size": "$wallets"}, 8]},
                },
                {"$addToSet": {"wallets": address}, "$inc": {"auth_version": 1}},
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as exc:
            raise HTTPException(409, "Wallet is already linked to another account.") from exc
        if changed is None:
            raise HTTPException(
                409,
                "Wallet already linked, eight-wallet limit reached, or account changed.",
            )
        service.db.sessions.delete_many({"user_id": user["_id"]})
        new_session = service.create_session(response, request, changed, session["method"])
        service.audit(user["_id"], "wallet_linked", address=address)
        return signed_in_payload(changed, new_session)
    if proof["action"] == "reauth":
        if address not in user["wallets"]:
            raise HTTPException(403, "Linked wallet ownership required.")
        service.throttle(request, "reauthentication", user["_id"], limit=5)
        service.mfa.verify(user, body.mfa_code)
        confirmation = service.mfa.confirmation(body.mfa_action, user)
        service.db.sessions.update_one(
            {"_id": session["_id"]},
            {"$set": {"authenticated_at": service.now(), "method": "wallet", "mfa_confirmation": confirmation}},
        )
        service.audit(user["_id"], "recent_wallet_authentication", address=address)
        return signed_in_payload(user, session)
    user = service.db.users.find_one({"wallets": address})
    if user is None:
        user = {
            "_id": str(uuid.uuid4()),
            "name": f"Wallet {address[-6:]}",
            "wallets": [address],
            "role": "user",
            "disabled": False,
            "auth_version": 1,
            "created_at": service.now(),
        }
        try:
            service.db.users.insert_one(user)
        except DuplicateKeyError:
            user = service.db.users.find_one({"wallets": address})
    if user is None or user["disabled"]:
        raise HTTPException(401, "Invalid credentials or unavailable account.")
    return service.sign_in(response, request, user, "wallet")


@router.get("/security/sessions")
def sessions(request: Request, user=Depends(current_user)):
    service = request.app.state.auth
    current, _ = context(request)
    now = service.now()
    rows = (
        service.db.sessions.find(
            {
                "user_id": user["_id"],
                "version": user["auth_version"],
                "expires_at": {"$gt": now},
                "last_seen": {
                    "$gt": now - timedelta(seconds=service.settings.session_idle_seconds)
                },
            }
        )
        .sort("last_seen", -1)
        .limit(100)
    )
    return {
        "sessions": [
            {
                "id": row["_id"],
                "current": row["_id"] == current["_id"],
                "agent": row["agent"],
                "created_at": row["created_at"],
                "last_seen": row["last_seen"],
                "expires_at": row["expires_at"],
            }
            for row in rows
        ]
    }


@router.delete("/security/sessions/{session_id}")
def revoke_session(
    session_id: str, request: Request, response: Response, user=Depends(current_user)
):
    service = request.app.state.auth
    deleted = service.db.sessions.delete_one({"_id": session_id, "user_id": user["_id"]})
    if deleted.deleted_count == 0:
        raise HTTPException(404, "Session not found.")
    current, _ = context(request)
    if session_id == current["_id"]:
        response.delete_cookie(
            service.settings.cookie_name,
            path="/",
            secure=service.settings.secure_cookie,
            httponly=True,
            samesite="strict",
        )
    service.audit(user["_id"], "session_revoked")
    return {"ok": True}


@router.post("/security/sessions/revoke-others")
def revoke_others(request: Request, user=Depends(current_user)):
    service = request.app.state.auth
    session, _ = context(request)
    service.recent(session)
    result = service.db.sessions.delete_many(
        {"user_id": user["_id"], "_id": {"$ne": session["_id"]}}
    )
    service.audit(user["_id"], "other_sessions_revoked", count=result.deleted_count)
    return {"ok": True, "count": result.deleted_count}


@router.delete("/security/wallets/{address}")
def unlink_wallet(address: str, request: Request, response: Response, user=Depends(current_user)):
    if not Web3.is_address(address):
        raise HTTPException(400, "Invalid address.")
    service = request.app.state.auth
    session, _ = context(request)
    service.recent(session)
    changed = service.db.users.find_one_and_update(
        {
            "_id": user["_id"],
            "auth_version": user["auth_version"],
            "disabled": False,
            "wallets": address.lower(),
            "$or": [
                {"password_hash": {"$type": "string"}, "email": {"$type": "string"}},
                {"$expr": {"$gt": [{"$size": "$wallets"}, 1]}},
            ],
        },
        {"$pull": {"wallets": address.lower()}, "$inc": {"auth_version": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if changed is None:
        raise HTTPException(
            409, "Cannot remove the final sign-in method, or the wallet is unavailable."
        )
    service.db.sessions.delete_many({"user_id": user["_id"]})
    new_session = service.create_session(response, request, changed, session["method"])
    service.audit(user["_id"], "wallet_unlinked", address=address.lower())
    return signed_in_payload(changed, new_session)


@router.get("/security/activity")
def security_activity(request: Request, user=Depends(current_user)):
    return {
        "events": list(
            request.app.state.db.audit.find({"user_id": user["_id"]}, {"_id": 0})
            .sort("created_at", -1)
            .limit(100)
        )
    }


@router.get("/admin/users")
def admin_users(request: Request, user=Depends(administrator)):
    return {
        "total": request.app.state.db.users.count_documents({}),
        "users": [
            public_user(row)
            for row in request.app.state.db.users.find().sort("created_at", -1).limit(200)
        ],
    }


@router.patch("/admin/users/{user_id}")
def admin_change_user(
    user_id: str, body: UserChange, request: Request, actor=Depends(administrator)
):
    service = request.app.state.auth
    session, _ = context(request)
    service.recent(session)
    if user_id == actor["_id"]:
        raise HTTPException(
            400, "Administrators cannot change their own role or disable themselves."
        )
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(400, "No account changes supplied.")
    # A short exclusive lease serializes last-admin checks on standalone MongoDB.
    now, lock_token = service.now(), secrets.token_hex(16)
    try:
        service.db.admin_locks.find_one_and_update(
            {"_id": "account-administration", "expires_at": {"$lte": now}},
            {"$set": {"token": lock_token, "expires_at": now + timedelta(seconds=30)}},
            upsert=True,
        )
    except DuplicateKeyError as exc:
        raise HTTPException(409, "Another administrator is changing accounts. Try again.") from exc
    try:
        target = service.db.users.find_one({"_id": user_id})
        if target is None:
            raise HTTPException(404, "User not found.")
        removes_admin = (
            changes.get("disabled", target["disabled"])
            or changes.get("role", target["role"]) != "admin"
        )
        if target["role"] == "admin" and not target["disabled"] and removes_admin:
            if service.db.users.count_documents({"role": "admin", "disabled": False}) <= 1:
                raise HTTPException(
                    409, "At least one active application administrator must remain."
                )
        service.db.users.update_one(
            {"_id": user_id}, {"$set": changes, "$inc": {"auth_version": 1}}
        )
        service.db.sessions.delete_many({"user_id": user_id})
        service.db.reset_tokens.delete_many({"user_id": user_id})
        service.audit(actor["_id"], "admin_account_changed", target=user_id, changes=changes)
    finally:
        service.db.admin_locks.delete_one({"_id": "account-administration", "token": lock_token})
    return {"ok": True}


@router.post("/admin/users/{user_id}/revoke-sessions")
def admin_revoke_sessions(user_id: str, request: Request, actor=Depends(administrator)):
    service = request.app.state.auth
    session, _ = context(request)
    service.recent(session)
    if (
        service.db.users.update_one({"_id": user_id}, {"$inc": {"auth_version": 1}}).matched_count
        == 0
    ):
        raise HTTPException(404, "User not found.")
    result = service.db.sessions.delete_many({"user_id": user_id})
    service.audit(
        actor["_id"],
        "admin_sessions_revoked",
        target=user_id,
        count=result.deleted_count,
    )
    return {"ok": True}


@router.get("/admin/audit")
def admin_audit(request: Request, user=Depends(administrator)):
    rows = (
        request.app.state.db.audit.find({"kind": {"$regex": "^admin_"}}, {"_id": 0})
        .sort("created_at", -1)
        .limit(100)
    )
    return {"events": list(rows)}
