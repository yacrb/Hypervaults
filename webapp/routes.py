"""Authorized read workflows, receipt verification and encrypted position notes."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from auth.dependencies import administrator, current_user, protect_mutations
from webapp.blockchain import ChainUnavailable

router = APIRouter(prefix="/api", dependencies=[Depends(protect_mutations)])


class QuoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offer_id: str = Field(pattern=r"^[1-9][0-9]{0,3}$")
    amount: str = Field(pattern=r"^[1-9][0-9]{0,38}$")


class TransactionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tx_hash: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    kind: Literal[
        "deposit",
        "withdraw",
        "fund",
        "offer",
        "availability",
        "pause",
        "approve",
        "faucet",
        "ownership",
    ]


class NoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(max_length=2000)


@router.get("/config")
def configuration(request: Request):
    return request.app.state.chain.configuration()


@router.get("/snapshot")
def snapshot(request: Request, user=Depends(current_user)):
    return request.app.state.chain.snapshot(user["wallets"])


@router.get("/admin/snapshot")
def admin_snapshot(request: Request, user=Depends(administrator)):
    return request.app.state.chain.snapshot(user["wallets"], administration=True)


@router.post("/quote")
def quote(body: QuoteInput, request: Request, user=Depends(current_user)):
    return request.app.state.chain.quote(int(body.offer_id), int(body.amount))


@router.post("/transactions/verify")
def transaction(body: TransactionInput, request: Request, user=Depends(current_user)):
    request.app.state.auth.throttle(request, "receipt_verification", user["_id"], limit=90)
    return request.app.state.chain.verify_transaction(body.tx_hash, body.kind, user["wallets"])


def owned_record(request, user, position_id):
    if not position_id.isdecimal() or len(position_id) > 78 or int(position_id) <= 0:
        raise HTTPException(400, "Invalid position identifier.")
    chain = request.app.state.chain
    position = chain.position(int(position_id))
    if position is None or position["owner"] not in user["wallets"]:
        raise HTTPException(404, "Position not available to this account.")
    # The RPC call may be slow. Recheck revocation and linkage before decryption.
    current = request.app.state.db.users.find_one(
        {"_id": user["_id"], "disabled": False, "auth_version": user["auth_version"]}
    )
    if current is None or position["owner"] not in current["wallets"]:
        raise HTTPException(401, "Account access changed. Sign in again.")
    if not chain.manifest:
        raise ChainUnavailable("Deployment unavailable.")
    record = f"{chain.deployment}:{int(position_id)}"
    return record


@router.get("/positions/{position_id}/note")
def note_read(position_id: str, request: Request, user=Depends(current_user)):
    record = owned_record(request, user, position_id)
    row = request.app.state.db.notes.find_one({"user_id": user["_id"], "record": record})
    if row is None:
        return {"note": None}
    plaintext = request.app.state.cipher.decrypt(row, user["_id"], record)
    return {"note": plaintext, "key_id": row["key_id"]}


@router.put("/positions/{position_id}/note")
def note_write(position_id: str, body: NoteInput, request: Request, user=Depends(current_user)):
    service = request.app.state.auth
    service.throttle(request, "note_write", user["_id"], limit=30)
    record = owned_record(request, user, position_id)
    encrypted = request.app.state.cipher.encrypt(body.note, user["_id"], record)
    service.db.notes.update_one(
        {"user_id": user["_id"], "record": record},
        {
            "$set": {**encrypted, "updated_at": service.now()},
            "$setOnInsert": {"user_id": user["_id"], "record": record},
        },
        upsert=True,
    )
    service.audit(user["_id"], "position_note_saved", position_id=position_id)
    return {"ok": True}
