"""RFC 6238 interoperability, drift and recovery-code format checks."""

import base64
import secrets
import unittest

from auth.mfa import matching_step, recovery_codes, recovery_hash, totp


class AlgorithmTests(unittest.TestCase):
    def test_rfc6238_sha1_vectors(self):
        secret = base64.b32encode(b"12345678901234567890").decode()
        for seconds, expected in [
            (59, "94287082"),
            (1111111109, "07081804"),
            (1111111111, "14050471"),
            (1234567890, "89005924"),
            (2000000000, "69279037"),
            (20000000000, "65353130"),
        ]:
            self.assertEqual(totp(secret, seconds // 30, digits=8), expected)

    def test_drift_and_code_format(self):
        secret = base64.b32encode(secrets.token_bytes(20)).decode()
        now = 1234567890
        self.assertEqual(matching_step(secret, totp(secret, now // 30 - 1), now), now // 30 - 1)
        self.assertIsNone(matching_step(secret, "12345", now))
        self.assertIsNone(matching_step(secret, "１２３４５６", now))

    def test_recovery_entropy_and_normalization(self):
        codes, hashes = recovery_codes()
        self.assertEqual(len(set(hashes)), 8)
        for code, hashed in zip(codes, hashes):
            self.assertEqual(len(code.replace("-", "")), 32)
            self.assertEqual(recovery_hash(code.upper()), hashed)



if __name__ == "__main__":
    unittest.main()
