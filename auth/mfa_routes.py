"""Account MFA endpoints; enrollment and pending sign-in never grant app access."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field

from auth.dependencies import context, current_user, protect_mutations
from auth.mfa import recovery_codes
from auth.routes import Input, security_change, signed_in_payload

router = APIRouter(prefix="/api", dependencies=[Depends(protect_mutations)])


class Factor(Input):
    code: str = Field(min_length=1, max_length=64)


@router.post("/auth/mfa/verify")
def complete_login(body: Factor, request: Request, response: Response):
    service = request.app.state.auth
    session, current = context(request)
    pending = session.get("pending_mfa")
    if current or not pending or pending["expires_at"] <= service.now():
        raise HTTPException(401, "Sign-in expired. Start again.")
    service.throttle(request, "mfa_login", pending["user_id"], limit=5)
    user = service.db.users.find_one(
        {
            "_id": pending["user_id"],
            "auth_version": pending["version"],
            "disabled": False,
            "mfa.secret": {"$exists": True},
        }
    )
    if user is None:
        raise HTTPException(401, "Account changed. Start sign-in again.")
    user = service.mfa.verify(user, body.code)
    claimed = service.db.sessions.update_one(
        {"_id": session["_id"], "pending_mfa": pending}, {"$unset": {"pending_mfa": ""}}
    )
    if not claimed.modified_count:
        raise HTTPException(401, "Sign-in already completed or cancelled. Start again.")
    new_session = service.rotate(response, request, user, pending["method"])
    service.audit(user["_id"], f"{pending['method']}_login", mfa=True)
    return signed_in_payload(user, new_session)


def account_change(request: Request, user: dict):
    service = request.app.state.auth
    session, _ = context(request)
    service.throttle(request, "mfa_settings", user["_id"], limit=5)
    service.recent(session)
    return service, session


@router.post("/security/mfa/setup")
def setup(request: Request, user=Depends(current_user)):
    service, session = account_change(request, user)
    return service.mfa.begin(session, user)


@router.delete("/security/mfa/setup")
def cancel_setup(request: Request, user=Depends(current_user)):
    session, _ = context(request)
    request.app.state.db.mfa_setups.delete_one({"_id": session["_id"], "user_id": user["_id"]})
    return {"ok": True}


@router.post("/security/mfa/enable")
def enable(body: Factor, request: Request, response: Response, user=Depends(current_user)):
    service, session = account_change(request, user)
    if user.get("mfa"):
        raise HTTPException(409, "Two-factor authentication is already enabled.")
    mfa = service.mfa.enrollment(session, user, body.code)
    codes, mfa["recovery_hashes"] = recovery_codes()
    result = security_change(service, request, response, user, {"mfa": mfa}, "mfa_enabled")
    # Plain recovery codes are returned once, never stored or included in audit logs.
    return {**result, "recovery_codes": codes}


@router.post("/security/mfa/recovery-codes")
def replace_codes(body: Input, request: Request, response: Response, user=Depends(current_user)):
    service, session = account_change(request, user)
    if not user.get("mfa"):
        raise HTTPException(409, "Enable two-factor authentication first.")
    service.mfa.consume_confirmation(session, "recovery-codes")
    codes, hashes = recovery_codes()
    result = security_change(
        service,
        request,
        response,
        user,
        {"mfa.recovery_hashes": hashes},
        "mfa_recovery_codes_replaced",
    )
    return {**result, "recovery_codes": codes}


@router.delete("/security/mfa")
def disable(body: Input, request: Request, response: Response, user=Depends(current_user)):
    service, session = account_change(request, user)
    if not user.get("mfa"):
        raise HTTPException(409, "Two-factor authentication is already off.")
    service.mfa.consume_confirmation(session, "disable")
    return security_change(service, request, response, user, {"mfa": {}}, "mfa_disabled")
