"""Recognize accounts that can authenticate with their own ECDSA key."""


def is_key_account(code: bytes) -> bool:
    # EIP-7702 EOAs retain their key and may originate transactions. Only the
    # exact delegation indicator is accepted; arbitrary contract code is not.
    # https://eips.ethereum.org/EIPS/eip-7702#delegation-indicator
    return not code or (len(code) == 23 and code[:3] == b'\xef\x01\x00')
