"""Account, access-control and encryption checks in disposable Mongo databases."""

import base64
import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient
from pydantic import ValidationError

import tests.test_mfa as fixtures
from auth.crypto import NoteCipher, NoteTampered
from auth.services import digest
from webapp.config import Settings
from webapp.main import create_app


@unittest.skipUnless(os.environ.get("HV_AUTH_TEST_MONGO_URI"), "Select isolated Mongo checks")
class SecurityTests(unittest.TestCase):
    setUp = fixtures.AuthenticationTests.setUp
    register = fixtures.AuthenticationTests.register
    post = fixtures.AuthenticationTests.post
    wallet_proof = fixtures.AuthenticationTests.wallet_proof

    def another_browser(self, email="other@example.com"):
        client = self.enterContext(TestClient(self.app, base_url=self.settings.origin))
        csrf = client.get("/api/session").json()["csrf"]
        result = client.post(
            "/api/auth/register",
            json={"email": email, "password": fixtures.PASSWORD, "name": "Other member"},
            headers={"origin": self.settings.origin, "x-csrf-token": csrf},
        )
        self.assertEqual(result.status_code, 201)
        return client, result.json()

    def test_password_hash_and_registration_policy(self):
        row = self.db.users.find_one({"_id": self.user_id})
        self.assertTrue(row["password_hash"].startswith("$argon2id$"))
        self.assertNotIn(fixtures.PASSWORD, json.dumps(row, default=str))
        self.post("/auth/logout")
        self.csrf = self.client.get("/api/session").json()["csrf"]
        for changes in ({"password": "short"}, {"role": "admin"}, {"email": {"$ne": None}}):
            body = {"email": "new@example.com", "password": fixtures.PASSWORD, "name": "New"}
            self.assertEqual(self.post("/auth/register", body | changes).status_code, 422)
        self.assertEqual(
            self.post(
                "/auth/register",
                {
                    "email": "FIXTURE@example.com",
                    "password": fixtures.PASSWORD,
                    "name": "Duplicate",
                },
            ).status_code,
            409,
        )

    def test_login_errors_are_generic_and_rate_limited(self):
        self.post("/auth/logout")
        self.csrf = self.client.get("/api/session").json()["csrf"]
        missing = self.post("/auth/login", {"email": "missing@example.com", "password": "bad"})
        wrong = self.post("/auth/login", {"email": "fixture@example.com", "password": "bad"})
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.json(), wrong.json())
        for _ in range(4):
            self.assertEqual(
                self.post(
                    "/auth/login",
                    {
                        "email": "fixture@example.com",
                        "password": "bad",
                    },
                ).status_code,
                401,
            )
        self.assertEqual(
            self.post(
                "/auth/login",
                {
                    "email": "fixture@example.com",
                    "password": "bad",
                },
            ).status_code,
            429,
        )

    def test_session_rotation_hashing_and_logout(self):
        raw = self.client.cookies.get(self.settings.cookie_name)
        self.assertIsNone(self.db.sessions.find_one({"_id": raw}))
        self.assertIsNotNone(self.db.sessions.find_one({"_id": digest(raw)}))
        result = self.post(
            "/auth/login", {"email": "fixture@example.com", "password": fixtures.PASSWORD}
        )
        self.assertEqual(result.status_code, 200)
        self.assertNotEqual(raw, self.client.cookies.get(self.settings.cookie_name))
        self.assertIsNone(self.db.sessions.find_one({"_id": digest(raw)}))
        old = self.enterContext(TestClient(self.app, base_url=self.settings.origin))
        old.cookies.set(self.settings.cookie_name, raw)
        self.assertEqual(old.get("/api/security/sessions").status_code, 401)
        self.post("/auth/logout")
        self.assertEqual(self.client.get("/api/security/sessions").status_code, 401)

    def test_idle_and_absolute_expiry(self):
        raw = self.client.cookies.get(self.settings.cookie_name)
        self.now += timedelta(seconds=self.settings.session_idle_seconds + 1)
        self.assertEqual(self.client.get("/api/security/sessions").status_code, 401)
        # Recent activity cannot extend the fixed absolute lifetime.
        self.db.sessions.update_one(
            {"_id": digest(raw)},
            {
                "$set": {
                    "last_seen": self.now,
                    "expires_at": self.now - timedelta(seconds=1),
                }
            },
        )
        self.assertEqual(self.client.get("/api/security/sessions").status_code, 401)

    def test_https_cookie_flags_and_headers(self):
        settings = self.settings.model_copy(update={"origin": "https://localhost:8443"})
        app = create_app(settings, db=self.db, chain=self.chain, clock=lambda: self.now)
        client = self.enterContext(TestClient(app, base_url=settings.origin))
        result = client.get("/api/session")
        cookie = result.headers["set-cookie"]
        for flag in ("__Host-hv_session=", "HttpOnly", "Secure", "SameSite=strict", "Path=/"):
            self.assertIn(flag, cookie)
        self.assertNotIn("Domain=", cookie)
        self.assertEqual(result.headers["strict-transport-security"], "max-age=31536000")
        self.assertEqual(result.headers["x-frame-options"], "DENY")
        self.assertEqual(result.headers["x-content-type-options"], "nosniff")
        self.assertEqual(result.headers["referrer-policy"], "no-referrer")
        self.assertIn("frame-ancestors 'none'", result.headers["content-security-policy"])
        self.assertEqual(result.headers["cache-control"], "no-store")

    def test_origin_csrf_body_size_and_media_type(self):
        base = {"origin": self.settings.origin, "x-csrf-token": self.csrf}
        for headers in (
            {},
            base | {"origin": "https://attacker.invalid"},
            base | {"x-csrf-token": "wrong"},
        ):
            self.assertEqual(
                self.client.post(
                    "/api/auth/reauth", json={"password": fixtures.PASSWORD}, headers=headers
                ).status_code,
                403,
            )
        self.assertEqual(
            self.client.post(
                "/api/auth/reauth",
                content="x" * 32769,
                headers=base | {"content-type": "application/json"},
            ).status_code,
            413,
        )
        self.assertEqual(
            self.client.post(
                "/api/auth/reauth",
                content="password=bad",
                headers=base | {"content-type": "application/x-www-form-urlencoded"},
            ).status_code,
            415,
        )

    def test_current_role_and_admin_account_controls(self):
        for path in ("/api/admin/users", "/api/admin/audit", "/api/admin/snapshot"):
            self.assertEqual(self.client.get(path).status_code, 403)
        other, payload = self.another_browser()
        self.db.users.update_one({"_id": self.user_id}, {"$set": {"role": "admin"}})
        self.assertEqual(self.client.get("/api/admin/users").status_code, 200)
        self.assertEqual(
            self.post(f"/admin/users/{self.user_id}", {"disabled": True}, "PATCH").status_code, 400
        )
        self.assertEqual(
            self.post(
                f"/admin/users/{payload['user']['id']}", {"disabled": True}, "PATCH"
            ).status_code,
            200,
        )
        self.assertEqual(other.get("/api/security/sessions").status_code, 401)
        self.db.users.update_one({"_id": self.user_id}, {"$set": {"role": "user"}})
        self.assertEqual(self.client.get("/api/admin/users").status_code, 403)

    def test_session_ids_do_not_grant_cross_account_revocation(self):
        other, payload = self.another_browser()
        other_id = other.get("/api/security/sessions").json()["sessions"][0]["id"]
        self.assertEqual(
            self.post(f"/security/sessions/{other_id}", method="DELETE").status_code, 404
        )
        self.assertEqual(other.get("/api/security/sessions").status_code, 200)
        self.assertEqual(payload["user"]["role"], "user")

    def test_wallet_proofs_reject_tampering_wrong_signer_expiry_and_replay(self):
        owner, outsider = Account.create(), Account.create()
        challenge = self.post(
            "/auth/challenge", {"address": owner.address, "action": "link"}
        ).json()
        signature = (
            "0x" + owner.sign_message(encode_defunct(text=challenge["message"])).signature.hex()
        )
        self.now += timedelta(seconds=1)
        body = {"id": challenge["id"], "message": challenge["message"], "signature": signature}
        self.assertEqual(
            self.post(
                "/auth/verify",
                body | {"message": body["message"].replace("HyperVaults", "Attacker")},
            ).status_code,
            400,
        )
        wrong = "0x" + outsider.sign_message(encode_defunct(text=body["message"])).signature.hex()
        self.assertEqual(self.post("/auth/verify", body | {"signature": wrong}).status_code, 400)
        self.assertEqual(self.post("/auth/verify", body).status_code, 200)
        self.assertEqual(self.post("/auth/verify", body).status_code, 400)
        challenge = self.post(
            "/auth/challenge", {"address": owner.address, "action": "reauth"}
        ).json()
        signature = (
            "0x" + owner.sign_message(encode_defunct(text=challenge["message"])).signature.hex()
        )
        self.now += timedelta(seconds=self.settings.challenge_seconds + 1)
        self.assertEqual(
            self.post(
                "/auth/verify",
                {"id": challenge["id"], "message": challenge["message"], "signature": signature},
            ).status_code,
            400,
        )

    def test_wallet_identity_is_unique_and_final_login_cannot_be_removed(self):
        wallet = Account.create()
        self.assertEqual(self.wallet_proof(wallet, "link").status_code, 200)
        other, payload = self.another_browser()
        previous, previous_csrf = self.client, self.csrf
        self.client, self.csrf = other, payload["csrf"]
        self.assertEqual(self.wallet_proof(wallet, "link").status_code, 409)
        self.client, self.csrf = previous, previous_csrf
        self.post("/auth/logout")
        self.csrf = self.client.get("/api/session").json()["csrf"]
        wallet_only = Account.create()
        self.assertEqual(self.wallet_proof(wallet_only).status_code, 200)
        self.assertEqual(
            self.post(f"/security/wallets/{wallet_only.address}", method="DELETE").status_code, 409
        )

    def note_context(self):
        wallet = Account.create()
        self.assertEqual(self.wallet_proof(wallet, "link").status_code, 200)
        self.chain.manifest, self.chain.deployment = {"network": "isolated"}, "isolated:deployment"
        self.chain.position = lambda _: {"owner": wallet.address.lower()}

    def test_notes_are_encrypted_and_unauthorized_accounts_get_no_plaintext(self):
        self.note_context()
        plaintext = "Private plan <script>alert('x')</script>"
        self.assertEqual(
            self.post("/positions/1/note", {"note": plaintext}, "PUT").status_code, 200
        )
        stored = self.db.notes.find_one()
        self.assertNotIn(plaintext, json.dumps(stored, default=str))
        self.assertEqual(self.client.get("/api/positions/1/note").json()["note"], plaintext)
        other, _ = self.another_browser()
        self.assertEqual(other.get("/api/positions/1/note").status_code, 404)
        self.assertEqual(
            other.put(
                "/api/positions/1/note",
                json={"note": "overwrite"},
                headers={
                    "origin": self.settings.origin,
                    "x-csrf-token": other.get("/api/session").json()["csrf"],
                },
            ).status_code,
            404,
        )

    def test_ciphertext_tampering_and_context_swaps_fail_closed(self):
        self.note_context()
        self.post("/positions/1/note", {"note": "Bound note"}, "PUT")
        row = self.db.notes.find_one()
        for user, record in (
            ("other-user", row["record"]),
            (self.user_id, "isolated:deployment:2"),
        ):
            with self.assertRaises(NoteTampered):
                self.app.state.cipher.decrypt(row, user, record)
        blob = bytearray(base64.b64decode(row["ciphertext"]))
        blob[0] ^= 1
        self.db.notes.update_one(
            {"_id": row["_id"]}, {"$set": {"ciphertext": base64.b64encode(blob).decode()}}
        )
        self.assertEqual(self.client.get("/api/positions/1/note").status_code, 409)

    def test_note_authorization_is_rechecked_after_a_slow_chain_read(self):
        self.note_context()
        original = self.chain.position

        def revoke_during_read(position):
            self.db.users.update_one({"_id": self.user_id}, {"$inc": {"auth_version": 1}})
            return original(position)

        self.chain.position = revoke_during_read
        self.assertEqual(self.client.get("/api/positions/1/note").status_code, 401)

    def test_nonce_collision_fails_and_key_rotation_retains_old_records(self):
        cipher = self.app.state.cipher
        with patch("auth.crypto.secrets.token_bytes", return_value=b"x" * 12):
            cipher.encrypt("first", self.user_id, "1")
            with self.assertRaises(RuntimeError):
                cipher.encrypt("second", self.user_id, "2")
        old = cipher.encrypt("retain me", self.user_id, "3")
        keys = json.loads(self.settings.note_keys)
        keys["v2"] = base64.b64encode(b"y" * 32).decode()
        rotated = NoteCipher(json.dumps(keys), "v2", self.db)
        self.assertEqual(rotated.decrypt(old, self.user_id, "3"), "retain me")
        self.assertEqual(rotated.encrypt("new", self.user_id, "4")["key_id"], "v2")

    def test_password_recovery_is_single_use_and_local_files_are_private(self):
        with tempfile.TemporaryDirectory() as folder:
            self.settings.outbox_path = Path(folder) / "outbox"
            missing = self.post("/auth/recover", {"email": "missing@example.com"})
            valid = self.post("/auth/recover", {"email": "fixture@example.com"})
            self.assertEqual(missing.json(), valid.json())
            file = next(self.settings.outbox_path.glob("*.eml"))
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(file.parent.stat().st_mode & 0o777, 0o700)
            text = file.read_text()
            token = text.split("#token=", 1)[1].split()[0]
            self.assertIsNotNone(self.db.reset_tokens.find_one({"_id": digest(token)}))
            self.assertIsNone(self.db.reset_tokens.find_one({"_id": token}))
            body = {"token": token, "password": "new-isolated-fixture-password"}
            self.assertEqual(self.post("/auth/reset", body).status_code, 200)
            self.csrf = self.client.get("/api/session").json()["csrf"]
            self.assertEqual(self.post("/auth/reset", body).status_code, 400)
            self.assertEqual(self.client.get("/api/security/sessions").status_code, 401)

    def test_input_bounds_and_production_configuration_fail_closed(self):
        for body in (
            {"offer_id": "-1", "amount": "1"},
            {"offer_id": "1", "amount": "0"},
            {"offer_id": "1", "amount": {"$gt": 0}},
        ):
            self.assertEqual(self.post("/quote", body).status_code, 422)
        self.assertEqual(
            self.post("/positions/1/note", {"note": "x" * 2001}, "PUT").status_code, 422
        )
        for changes in (
            {"origin": "http://remote.example.com"},
            {"chain_id": 1},
            {"chain_id": 11155111, "confirmations": 1},
            {"mode": "production", "origin": "http://localhost"},
        ):
            values = self.settings.model_dump() | changes
            with self.assertRaises(ValidationError):
                Settings(_env_file=None, **values)
