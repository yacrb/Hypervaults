# Security notes

This educational project uses test tokens and has not been independently audited.
Public website hosting and production SMTP are unverified. Public Sepolia contract
deployment requires browser-wallet approval and verified transaction receipts.

## Boundaries

- The application never holds wallet private keys or signs transactions.
  Contract calls require the selected browser wallet and a review step.
- Connecting, website authentication and wallet linking are separate operations.
  SIWE proofs are short-lived, browser-bound, single-use and verified on the server.
- Passwords use Argon2id. Sessions use hashed opaque identifiers, HttpOnly cookies,
  CSRF protection, explicit trusted origins and current account/role checks.
- Administrator roles control website accounts. Contract ownership controls future
  offers and deposit pause. Neither grants access to another wallet's principal.
- Private notes use server-side AES-256-GCM with fresh nonces and account/position
  binding. This is not end-to-end encryption. Keys stay outside MongoDB in `.env`.
- Financial data comes from verified contract events and confirmed chain reads.
  A wallet's chain and contract code must match the deployment before signing.
  Anvil additionally checks its live genesis; Sepolia checks the recorded deployment block.
  Failed or unavailable verification does not count as a successful transaction.

## Contract behavior

The vault is non-upgradeable and accepts only its configured HVT token. It reserves
an entire fixed reward at deposit from sponsor funding. Locking itself does not
generate yield. Amounts use integer token base units and reward calculations round
down. Original position terms cannot be changed. There is no early withdrawal,
token sweep, position transfer or administrator seizure. Deposit pause does not
restrict owner withdrawals after maturity. Sponsor funding cannot be recovered.

A user can withdraw directly if the website is unavailable or their website account
is disabled. With the position-owner test wallet imported into a local Foundry
keystore, use the actual vault address from `.local/deployment.json`:

```sh
.tools/bin/cast send VAULT_ADDRESS 'withdraw(uint256)' POSITION_ID \
  --rpc-url http://127.0.0.1:8545 --account YOUR_LOCAL_TEST_KEYSTORE
```

Enter the keystore password interactively; never paste a private key into the app,
source code or shell command. Direct withdrawal still requires maturity and gas.

## Local operations

`manage.py up` creates the public demo login `admin@hypervaults.com` / `admin`.
Seeding requires development mode, a loopback MongoDB and a verified local Anvil
RPC on chain 31337. It refuses existing unrelated accounts and preserves later
password changes. This preset is public; keep the demo stack on localhost.
The earlier synthetic seed is migrated once and its old sessions are revoked.

Only chain 31337 on a loopback Anvil RPC permits time travel and unlocked-account
setup. Keep Anvil private. `manage.py` refuses a changed deployment rather than
silently resetting state. Back up `.local`, note keys and Docker volumes before
replacing a chain. Old-chain positions are not transferable to a new chain.

HTTPS enables Secure `__Host-` cookies and HSTS. Local HTTP cookies are restricted
to loopback development. Production configuration requires HTTPS, SMTP over TLS
and six confirmations. Sepolia requires six confirmations in every mode and uses
the browser-wallet setup described in [Sepolia setup](docs/SEPOLIA.md). The selected
network is retained across restarts. Mainnet is rejected.

## Supported wallets

Injected EOAs, including EIP-7702 delegated accounts, are supported with 65-byte
ECDSA signatures from the account's own key. Only empty account code or the exact
23-byte `0xef0100 || address` delegation indicator is accepted. The app does not
request delegations, invoke delegated signature validation, or accept ERC-1271
contract signatures. The picker uses local artwork and treats
wallet metadata as self-reported text; it never executes provider-supplied SVG/HTML.
Account, network or provider changes invalidate the transaction context. Contract
wallet signatures and WalletConnect QR sessions are not supported. Explicit
connection/sign-in requests a network switch in the selected wallet. Missing local
Anvil networks can be added with wallet approval. The app rechecks the chain and
account before continuing; rejection or an account change stops sign-in.
Transactions never switch networks automatically. Review the address and network
shown by your wallet when signing.
