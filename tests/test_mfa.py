"""RFC vectors and real Mongo/API checks in a disposable, uniquely named database.

Run integration checks with HV_AUTH_TEST_MONGO_URI=mongodb://127.0.0.1:27018.
No existing account or database is modified.
"""

import base64
import json
import os
import secrets
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pymongo import MongoClient

from auth.crypto import NoteCipher, NoteTampered
from auth.mfa import totp
from auth.services import digest
from webapp.config import Settings
from webapp.database import create_indexes
from webapp.main import create_app

PASSWORD = "isolated-fixture-password"


@unittest.skipUnless(
    os.environ.get("HV_AUTH_TEST_MONGO_URI"),
    "Set HV_AUTH_TEST_MONGO_URI for isolated Mongo/API checks",
)
class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.mongo = MongoClient(
            os.environ["HV_AUTH_TEST_MONGO_URI"],
            tz_aware=True,
            serverSelectionTimeoutMS=3000,
        )
        self.db = self.mongo[f"hypervaults_test_mfa_{uuid.uuid4().hex}"]
        self.addCleanup(self.mongo.close)
        self.addCleanup(lambda: self.mongo.drop_database(self.db.name))
        create_indexes(self.db)
        self.now = datetime(2026, 10, 10, 12, tzinfo=UTC)
        keyring = json.dumps({"v1": base64.b64encode(secrets.token_bytes(32)).decode()})
        self.settings = Settings(
            _env_file=None,
            mode="test",
            origin="http://127.0.0.1:8088",
            note_keys=keyring,
        )
        self.chain = type("Chain", (), {"is_eoa": lambda _, address: True})()
        self.app = create_app(self.settings, db=self.db, chain=self.chain, clock=lambda: self.now)
        self.client = self.enterContext(TestClient(self.app, base_url=self.settings.origin))
        self.csrf = self.client.get("/api/session").json()["csrf"]
        self.register()

    def register(self):
        result = self.post(
            "/auth/register",
            {"email": "fixture@example.com", "password": PASSWORD, "name": "Fixture"},
        )
        self.assertEqual(result.status_code, 201)
        self.user_id = result.json()["user"]["id"]

    def post(self, path, body=None, method="POST"):
        result = self.client.request(
            method,
            "/api" + path,
            json=body or {},
            headers={"origin": self.settings.origin, "x-csrf-token": self.csrf},
        )
        if result.is_success and "csrf" in result.json():
            self.csrf = result.json()["csrf"]
        return result

    def next_code(self):
        self.now += timedelta(seconds=30)
        return totp(self.secret, int(self.now.timestamp()) // 30)

    def enable(self):
        setup = self.post("/security/mfa/setup")
        self.assertEqual(setup.status_code, 200)
        self.secret = setup.json()["secret"]
        row = self.db.mfa_setups.find_one()
        self.assertNotIn(self.secret, json.dumps(row, default=str))
        result = self.post("/security/mfa/enable", {"code": self.next_code()})
        self.assertEqual(result.status_code, 200, result.text)
        self.codes = result.json()["recovery_codes"]
        self.assertTrue(result.json()["user"]["mfa_enabled"])
        stored = self.db.users.find_one({"_id": self.user_id})
        self.assertNotIn(self.secret, json.dumps(stored, default=str))
        self.assertNotIn(self.codes[0], json.dumps(stored, default=str))

    def login(self):
        self.post("/auth/logout")
        self.csrf = self.client.get("/api/session").json()["csrf"]
        return self.post("/auth/login", {"email": "fixture@example.com", "password": PASSWORD})

    def wallet_proof(self, account, action="login", code=""):
        challenge = self.post("/auth/challenge", {"address": account.address, "action": action})
        self.assertEqual(challenge.status_code, 200, challenge.text)
        body = challenge.json()
        self.now += timedelta(seconds=1)
        signature = account.sign_message(encode_defunct(text=body["message"])).signature.hex()
        return self.post(
            "/auth/verify",
            {
                "id": body["id"],
                "message": body["message"],
                "signature": "0x" + signature,
                "mfa_code": code,
            },
        )

    def test_password_pending_session_cannot_access_account_or_bypass_mfa(self):
        self.enable()
        pending = self.login()
        self.assertTrue(pending.json()["mfa_required"])
        self.assertIsNone(pending.json()["user"])
        self.assertEqual(self.client.get("/api/security/sessions").status_code, 401)
        self.assertEqual(self.post("/auth/mfa/verify", {"code": "wrong"}).status_code, 400)
        self.assertTrue(self.client.get("/api/session").json()["mfa_required"])
        finished = self.post("/auth/mfa/verify", {"code": self.next_code()})
        self.assertEqual(finished.status_code, 200, finished.text)
        self.assertEqual(self.client.get("/api/security/sessions").status_code, 200)
        self.assertEqual(self.post("/auth/mfa/verify", {"code": self.next_code()}).status_code, 401)

    def test_wallet_login_and_wallet_reauthentication_require_mfa(self):
        account = Account.create()
        self.db.users.update_one(
            {"_id": self.user_id}, {"$set": {"wallets": [account.address.lower()]}}
        )
        self.enable()
        self.post("/auth/logout")
        self.csrf = self.client.get("/api/session").json()["csrf"]
        pending = self.wallet_proof(account)
        self.assertEqual(pending.status_code, 200, pending.text)
        self.assertTrue(pending.json()["mfa_required"])
        self.assertEqual(self.client.get("/api/security/sessions").status_code, 401)
        self.assertEqual(self.post("/auth/mfa/verify", {"code": self.next_code()}).status_code, 200)
        self.assertEqual(self.wallet_proof(account, "reauth").status_code, 403)
        self.assertEqual(self.wallet_proof(account, "reauth", self.next_code()).status_code, 200)

    def test_replay_and_recovery_codes_are_atomic(self):
        self.enable()
        user = self.db.users.find_one({"_id": self.user_id})
        code = self.next_code()

        def verify(value):
            try:
                self.app.state.auth.mfa.verify(user, value)
                return True
            except HTTPException:
                return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sum(executor.map(verify, [code, code])), 1)
            self.assertEqual(sum(executor.map(verify, [self.codes[0], self.codes[0]])), 1)

    def test_reset_and_password_reauthentication_do_not_bypass_mfa(self):
        self.enable()
        self.assertEqual(
            self.post(
                "/auth/password",
                {"current_password": PASSWORD, "new_password": PASSWORD + "new"},
            ).status_code,
            403,
        )
        self.assertEqual(self.post("/auth/reauth", {"password": PASSWORD}).status_code, 403)
        token = secrets.token_urlsafe(32)
        user = self.db.users.find_one({"_id": self.user_id})
        self.db.reset_tokens.insert_one(
            {
                "_id": digest(token),
                "user_id": self.user_id,
                "version": user["auth_version"],
                "expires_at": self.now + timedelta(minutes=5),
            }
        )
        body = {"token": token, "password": PASSWORD + "new"}
        self.assertEqual(self.post("/auth/reset", body).status_code, 403)
        self.assertIsNotNone(self.db.reset_tokens.find_one())
        self.assertEqual(
            self.post("/auth/reset", {**body, "mfa_code": self.codes[0]}).status_code,
            200,
        )
        self.assertTrue(self.db.users.find_one({"_id": self.user_id})["mfa"])
        self.assertEqual(self.client.get("/api/security/sessions").status_code, 401)

    def test_setup_expiry_ciphertext_context_and_session_revocation(self):
        self.post("/security/mfa/setup")
        self.now += timedelta(minutes=6)
        self.assertEqual(self.post("/security/mfa/enable", {"code": "123456"}).status_code, 403)
        self.post("/auth/reauth", {"password": PASSWORD})
        self.assertEqual(self.post("/security/mfa/enable", {"code": "123456"}).status_code, 400)
        old_user = self.db.users.find_one({"_id": self.user_id})
        self.db.sessions.insert_one(
            {
                "_id": "other-browser",
                "user_id": self.user_id,
                "version": old_user["auth_version"],
            }
        )
        self.enable()
        self.assertIsNone(self.db.sessions.find_one({"_id": "other-browser"}))
        encrypted = self.db.users.find_one({"_id": self.user_id})["mfa"]["secret"]
        note_cipher = NoteCipher(self.settings.note_keys, "v1", self.db)
        with self.assertRaises(NoteTampered):
            note_cipher.decrypt(encrypted, self.user_id, "authenticator")
        with self.assertRaises(NoteTampered):
            self.app.state.auth.mfa.cipher.decrypt(encrypted, "other-user", "authenticator")

    def test_expired_pending_or_disabled_account_cannot_finish(self):
        self.enable()
        self.login()
        self.now += timedelta(minutes=6)
        self.assertEqual(self.post("/auth/mfa/verify", {"code": self.next_code()}).status_code, 401)
        self.login()
        self.db.users.update_one(
            {"_id": self.user_id},
            {"$set": {"disabled": True}, "$inc": {"auth_version": 1}},
        )
        self.assertEqual(self.post("/auth/mfa/verify", {"code": self.next_code()}).status_code, 401)

    def test_disable_replace_codes_and_csrf(self):
        self.enable()
        result = self.client.post(
            "/api/security/mfa/setup", json={}, headers={"origin": self.settings.origin}
        )
        self.assertEqual(result.status_code, 403)
        self.assertEqual(self.post("/security/mfa", {"code": "wrong"}, "DELETE").status_code, 400)
        replaced = self.post("/security/mfa/recovery-codes", {"code": self.next_code()})
        self.assertEqual(replaced.status_code, 200, replaced.text)
        self.assertEqual(
            self.post("/security/mfa", {"code": self.codes[0]}, "DELETE").status_code,
            400,
        )
        disabled = self.post(
            "/security/mfa", {"code": replaced.json()["recovery_codes"][0]}, "DELETE"
        )
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertFalse(disabled.json()["user"]["mfa_enabled"])
        self.assertEqual(self.login().json()["user"]["id"], self.user_id)

    def test_factor_attempts_are_rate_limited(self):
        self.enable()
        self.login()
        for _ in range(5):
            self.assertEqual(self.post("/auth/mfa/verify", {"code": "wrong"}).status_code, 400)
        self.assertEqual(self.post("/auth/mfa/verify", {"code": self.codes[0]}).status_code, 429)


if __name__ == "__main__":
    unittest.main()
