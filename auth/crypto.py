"""Server-side note encryption, with authorization supplied by the route."""

import base64
import json
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pymongo.errors import DuplicateKeyError


class NoteTampered(Exception):
    """Ciphertext or its authenticated context is invalid."""


class NoteCipher:
    def __init__(self, encoded_keys: str, active_key: str, db, purpose="hypervaults-note-v1"):
        try:
            self.keys = {
                key_id: base64.b64decode(value, validate=True)
                for key_id, value in json.loads(encoded_keys).items()
            }
            if any(len(key) != 32 for key in self.keys.values()):
                raise ValueError("AES-256 keys must be 32 bytes")
            if len(set(self.keys.values())) != len(self.keys):
                raise ValueError("Each key identifier must have distinct key material")
            if active_key not in self.keys:
                raise ValueError("Active note key is unavailable")
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError(
                "Configure HV_NOTE_KEYS with a JSON key ring outside the repository"
            ) from exc
        self.active_key = active_key
        self.db = db
        self.purpose = purpose

    def aad(self, user_id: str, record: str) -> bytes:
        return json.dumps([self.purpose, user_id, record], separators=(",", ":")).encode()

    def encrypt(self, plaintext: str, user_id: str, record: str) -> dict:
        # Persist a nonce reservation so a nonce cannot be reused, even after edits.
        for _ in range(5):
            nonce = secrets.token_bytes(12)
            encoded_nonce = base64.b64encode(nonce).decode()
            try:
                self.db.note_nonces.insert_one({"_id": f"{self.active_key}:{encoded_nonce}"})
                break
            except DuplicateKeyError:
                continue
        else:
            raise RuntimeError("Could not reserve a fresh encryption nonce")
        ciphertext = AESGCM(self.keys[self.active_key]).encrypt(
            nonce, plaintext.encode(), self.aad(user_id, record)
        )
        return {
            "key_id": self.active_key,
            "nonce": encoded_nonce,
            "ciphertext": base64.b64encode(ciphertext).decode(),
        }

    def decrypt(self, encrypted: dict, user_id: str, record: str) -> str:
        try:
            nonce = base64.b64decode(encrypted["nonce"], validate=True)
            ciphertext = base64.b64decode(encrypted["ciphertext"], validate=True)
            return (
                AESGCM(self.keys[encrypted["key_id"]])
                .decrypt(nonce, ciphertext, self.aad(user_id, record))
                .decode()
            )
        except (InvalidTag, KeyError, ValueError, UnicodeDecodeError, TypeError) as exc:
            raise NoteTampered("The encrypted note cannot be authenticated") from exc
