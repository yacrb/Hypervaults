# Sepolia setup

HyperVaults supports local Anvil (31337) and public Sepolia (11155111).
Ethereum mainnet is rejected by configuration and both contracts. HVT remains an
unpriced test token; Sepolia ETH pays transaction gas.

## Deploy and switch

Keep Docker running and use a dedicated browser wallet with Sepolia test ETH.
The wallet address below becomes both the deployment sponsor and contract owner.
Website administrator roles remain separate.

```sh
uv sync --frozen
uv run python manage.py sepolia --owner YOUR_PUBLIC_TEST_WALLET_ADDRESS
```

Open `http://127.0.0.1:8090/` in the browser containing your wallet. Connect the
specified account, approve switching to Sepolia, and review each transaction:

1. Deploy the HVT token (1,000,000 HVT minted to the sponsor).
2. Deploy the vault owned by the same wallet.
3. Create a one-day offer (300 annualized basis points).
4. Create a seven-day offer (500 annualized basis points).
5. Create a thirty-day offer (800 annualized basis points).
6. Approve exactly 10,000 HVT for the vault.
7. Fund the shared reward reserve with that 10,000 HVT.

Each offer has a 10 HVT minimum, a 1,000 HVT maximum per position, and a 10,000 HVT
capacity. Funding is test-token funding and cannot be withdrawn by the sponsor.
Wait for six confirmations per step, then choose **Activate Sepolia**. The setup
command verifies deployed runtime bytecode, the vault owner, and the deployment
block before starting the application. Website accounts and encryption keys are
reused. Original Anvil manifests, chain volumes, indexed events, and private notes
are preserved; local positions are not migrated to Sepolia.

The application stays loopback-only. Publicly hosting the website is a separate
operation requiring production HTTPS and recovery-mail configuration.

## Resume and restart

Transaction hashes are saved in `.local/sepolia-progress.json`; the browser also
retains a submitted hash until the setup server acknowledges it. Reloading or
repeating the setup command with the same owner resumes confirmed progress.
The helper never automatically resends a recorded transaction. A failed attempt
awaiting acknowledgement can be cleared only after its reverted receipt has six
canonical confirmations and matches the owner/network. Its hash is retained in
`failed_transactions`; it does not advance setup. Replaced, cancelled, dropped,
or already-recorded reverted transactions still require receipt inspection;
do not erase progress to retry.

Use standard wallet transactions for vault operations. Setup can also acknowledge
MetaMask v1.3.0 single-call owner self-delegations with the zero native-balance-change
caveat: it decodes the execution and still requires the exact destination, value,
and payload. Multiple calls, other delegates/enforcers/modes and relay senders are
rejected. If a wallet execution reverts, switch **Account details → Smart account →
Sepolia** off in MetaMask, review its wallet transaction, then resume setup.
This changes only that network's wallet mode; the address stays the same. See
[MetaMask's account-mode guide](https://support.metamask.io/configure/accounts/switch-to-or-revert-from-a-smart-account/).
EIP-7702 delegated accounts can authenticate with their own ECDSA key, but this
does not enable general batched or relayed transaction verification.

Restart the selected network with:

```sh
uv run python manage.py up
uv run python manage.py status
uv run python manage.py down
```

Selection is stored privately in `.local/network`. Explicit overrides are
`manage.py up --network sepolia` and `manage.py up --network local`. Anvil startup
continues to reject a mismatched old deployment instead of resetting it.
Use `--https` to serve the app on `https://localhost:8443`; trust the local CA
manually before using it in your test browser. The temporary wallet setup helper
itself binds only to `127.0.0.1:8090` and closes after activation.

## RPC and verification

The default is `https://ethereum-sepolia-rpc.publicnode.com`. Pass a different
HTTPS endpoint with `--rpc-url` if needed; avoid putting private API credentials
in shell history. The chosen endpoint is saved privately in `.local/sepolia-rpc.txt`
and never returned to the browser. Public RPCs can be rate-limited; RPC failures
stop verification and activation rather than inventing successful results.

Sepolia's published genesis identifier is pinned from the
[network configuration](https://github.com/eth-clients/sepolia#meta-data-sepolia).
Public RPCs may prune block zero, so live binding uses chain ID, the canonical
deployment-block hash, runtime bytecode hashes, and the vault's token address.
The browser independently checks the deployment block and bytecode through its
wallet RPC before transaction signing. Anvil retains its live genesis comparison.
Use an RPC with sufficient historical access if it can no longer serve the
recorded deployment block. The event index is scoped to each deployment; switching
networks does not delete another deployment's records.

Private keys are never requested or handled by the helper or backend. Setup writes
require a random setup token and the exact same origin; the helper also checks the
Host header and binds only to loopback. All assets are served locally.

Sepolia lock periods use actual elapsed time. Local time travel and unlocked
account gas-funding helpers apply only to Anvil.

## Checks

```sh
uv run python -m unittest discover -s tests -v
```

The tests cover supported networks, confirmation depth, transaction identity and
payload, durable/idempotent progress, pending/reverted/reorganized receipts, and
activation gates. These checks complement a live browser-wallet deployment; they
do not claim that an unsigned or simulated transaction was deployed.
