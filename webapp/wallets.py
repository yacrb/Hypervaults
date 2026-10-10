"""Recognize accounts that can authenticate with their own ECDSA key."""


from eth_abi import decode, encode
from web3 import Web3


def is_key_account(code: bytes) -> bool:
    # EIP-7702 EOAs retain their key and may originate transactions. Only the
    # exact delegation indicator is accepted; arbitrary contract code is not.
    # https://eips.ethereum.org/EIPS/eip-7702#delegation-indicator
    return not code or (len(code) == 23 and code[:3] == b'\xef\x01\x00')


# MetaMask Delegation Framework v1.3.0 deployment on Sepolia:
# https://github.com/MetaMask/delegation-framework/blob/main/documents/Deployments.md
DELEGATION_MANAGER = '0xdb9b1e94b5b69df7e401ddbede43491141047db3'
REDEEM_SELECTOR = Web3.keccak(text='redeemDelegations(bytes[],bytes32[],bytes[])')[:4]
REDEEM_TYPES = ['bytes[]', 'bytes32[]', 'bytes[]']
DELEGATION_TYPE = '(address,address,bytes32,(address,bytes,bytes)[],uint256,bytes)[]'
BALANCE_ENFORCER = '0xbd7b277507723490cd50b12eaafe87c616be6880'


def wallet_call(tx, owner):
    """Unwrap only a single owner-to-self MetaMask execution; reject batches."""
    destination, payload = (tx.get('to') or '').lower(), bytes(tx['input'])
    if destination != DELEGATION_MANAGER:
        return destination, payload
    if (tx.get('chainId') != 11155111 or tx['from'].lower() != owner.lower()
            or tx.get('value', 0) != 0 or payload[:4] != REDEEM_SELECTOR):
        raise ValueError('Unsupported wallet execution')
    try:
        permissions, modes, executions = decode(REDEEM_TYPES, payload[4:])
        if (encode(REDEEM_TYPES, [permissions, modes, executions]) != payload[4:]
                or len(permissions) != 1 or len(modes) != 1 or len(executions) != 1
                or modes[0] != bytes(32)):
            raise ValueError('Only one standard execution is supported')
        delegations = decode([DELEGATION_TYPE], permissions[0])[0]
        if (encode([DELEGATION_TYPE], [delegations]) != permissions[0]
                or len(delegations) != 1 or delegations[0][0].lower() != owner.lower()
                or delegations[0][1].lower() != owner.lower()
                or delegations[0][2] != b'\xff' * 32 or len(delegations[0][5]) != 65):
            raise ValueError('Execution must use the owner self-delegation')
        # Accept only MetaMask's observed zero native-balance-change caveat.
        # Arbitrary enforcers may execute hooks outside the requested call.
        caveats = delegations[0][3]
        if (len(caveats) != 1 or caveats[0][0].lower() != BALANCE_ENFORCER
                or caveats[0][1] != b'\x01' + bytes.fromhex(owner[2:]) + bytes(32)
                or caveats[0][2] != b''):
            raise ValueError('Unsupported wallet execution caveat')
        execution = executions[0]
        if len(execution) < 56 or execution[20:52] != bytes(32):
            raise ValueError('Execution must send no ETH')
        return '0x' + execution[:20].hex(), execution[52:]
    except Exception as error:
        raise ValueError('Unsupported wallet execution') from error
