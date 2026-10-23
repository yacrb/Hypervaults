<p align="center">
  <img src="docs/assets/hero.svg" width="100%" alt="HyperVaults — account security and wallet-owned test-token vaults">
</p>

HyperVaults is a security-focused application combining email and wallet sign-in,
optional authenticator MFA, encrypted private notes and Solidity token vaults.
**The website controls account access; the wallet signs financial actions.**

[Quickstart](#run-locally) · [Architecture](#architecture) · [Security](SECURITY.md) ·
[Verification](docs/VERIFICATION.md) · [Illustrated report](docs/report/HyperVaults_Report.pdf)

![HyperVaults dashboard captured from the actual local demo](docs/assets/overview.webp)

## Security engineering

| Area | Implemented control |
|---|---|
| Identity | Argon2id passwords and one-use, browser-bound Sign-In with Ethereum proofs. |
| Sessions | Hashed opaque tokens, rotation, idle/absolute expiry and revocation. |
| MFA | Locally generated enrollment QR, replay-resistant TOTP and one-use recovery codes. |
| Browser requests | Exact Origin and CSRF checks, strict cookies, security headers and bounded JSON bodies. |
| Private records | AES-256-GCM with persistent nonce reservations and account/record-bound authenticated data. |
| Authorization | Server-side roles and record ownership; website administrators cannot withdraw another wallet's position. |
| Vaults | Prefunded rewards, immutable position terms, maturity checks and canonical receipt verification. |

## Run locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), Python 3.12+
and Docker. The setup installs and verifies the locked contract toolchain.

```sh
git clone https://github.com/yacrb/Hypervaults.git
cd Hypervaults
uv sync --frozen
uv run python manage.py up
```

Open **http://127.0.0.1:8088/login** and create an account. Startup provisions a
local Anvil chain, MongoDB, three offers and a funded reward reserve. Subsequent
starts preserve accounts, keys and chain state.

<details>
<summary>Local test wallet and useful commands</summary>

Use a fresh test wallet with RPC `http://127.0.0.1:8545` and chain ID `31337`.
The local demo administrator is `admin@hypervaults.com`, password `admin`.
That preset is restricted to loopback development and never overwrites a
customized account.

```sh
uv run python manage.py fund-gas --address YOUR_PUBLIC_TEST_WALLET_ADDRESS
uv run python manage.py advance 604800
uv run python manage.py status
uv run python manage.py down
```

Published app, RPC and database ports bind to localhost. Preserve `.env`, `.local`
and Docker volumes: they contain configuration, encryption keys and saved state.
For local HTTPS use `uv run python manage.py up --https` and manually trust
`.local/tls/ca.pem` before visiting `https://localhost:8443`.

</details>

For wallet-approved Sepolia deployment, follow the [Sepolia guide](docs/SEPOLIA.md).
The backend never requests your wallet's private key.

## Architecture

![Browser, account API, key ring, MongoDB and contract trust boundaries](docs/assets/security/architecture.svg)

FastAPI serves the plain HTML/CSS/JavaScript interface and enforces account
permissions. MongoDB stores identity, encrypted records, audit events and a
rebuildable chain index. Solidity enforces position ownership, maturity and reward
reserves. Encryption keys stay outside the database; signing authority stays in
the wallet.

[Authentication flows](docs/AUTHENTICATION.md) ·
[Editable SVG diagrams](docs/assets/security) · [Security boundaries](SECURITY.md)

## Screenshots

| Sign in | Browse vaults |
|---|---|
| ![Actual email and wallet sign-in screen](docs/assets/login.webp) | ![Actual offers and funding terms](docs/assets/vaults.webp) |

| MFA sign-in | Administration |
|---|---|
| ![Actual invalid-code rejection during MFA sign-in](docs/assets/mfa.webp) | ![Actual contract administration screen](docs/assets/admin.webp) |

These captures come from the real disposable Anvil/MongoDB demo. Account, wallet,
deposit and withdrawal actions used the app and compiled contracts. See the
[original evidence and scope](docs/report/evidence/README.md), plus a
[new mobile MFA status capture](docs/VERIFICATION.md#browser-checks).

## Verification

[GitHub Actions](https://github.com/yacrb/Hypervaults/actions/workflows/verify.yml)
runs the Python suite against real MongoDB, focused browser checks, actual MFA
enrollment/sign-in, and Solidity tests with 512 reward-fuzz inputs.

The [verification guide](docs/VERIFICATION.md) explains how to reproduce each
check and which checks use fixtures. The full transaction browser journey is
separate from CI. The [report evidence](docs/report/evidence/README.md) preserves
historical results; it is not a live status badge.

## Scope and limitations

This is an academic test-token application. HVT has no assigned price. The code
is unaudited and is not ready for real funds. MFA is optional; email ownership
verification is absent. Public HTTPS, real SMTP delivery, backup restoration and
production load still need validation.

## Repository map

| Path | Purpose |
|---|---|
| `auth/` | Identity, sessions, MFA and account permissions. |
| `webapp/` | API, chain verification, browser pages and assets. |
| `smartcontract/` | Vault/token contracts, tests and toolchain lock. |
| `tests/` | API, browser and negative security checks. |
| `docs/` | Architecture, deployment, report and evidence. |
| `manage.py` | Local lifecycle and development helpers. |

Built by **Mohamed Yacine Riabi** and **Yassine ben lazreg**.
Technology and artwork attribution: [asset notices](docs/report/logos/NOTICE.md).
