"""Real browser/API/contract journey on a disposable Anvil node and Mongo database.

HV_AUTH_TEST_MONGO_URI=mongodb://127.0.0.1:27018 \
  uv run --with playwright python -m tests.check_app_e2e
Optional HV_REPORT_EVIDENCE_DIR saves screenshots and public result metadata.
The injected demo wallet uses the disposable node's unlocked accounts, never a
user's wallet or private key. Existing chains, accounts and manifests are untouched.
"""

import asyncio
import base64
import json
import os
import secrets
import socket
import subprocess
import tempfile
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from playwright.async_api import async_playwright
from pymongo import MongoClient
from web3 import HTTPProvider, Web3

from auth.mfa import totp
from manage import deployment_manifest
from webapp.blockchain import ChainService
from webapp.config import ROOT, Settings
from webapp.database import create_indexes
from webapp.main import create_app

IMAGE = "ghcr.io/foundry-rs/foundry:v1.8.4@sha256:f69e1b8d1dda17d89227a4d0513b1d86f93ece430dea8d87aaea643da3607d3e"
PASSWORD = "disposable-demo-passphrase"
WALLET = """
const provider = {request: payload => window.demoWalletRequest(payload), on() {}, removeListener() {}};
window.ethereum = provider;
window.addEventListener('eip6963:requestProvider', () => window.dispatchEvent(new CustomEvent('eip6963:announceProvider', {
  detail: {provider, info: {uuid: 'report-demo-wallet', name: 'Isolated demo wallet', rdns: 'org.hypervaults.demo'}}
})));
"""


def receipt(web3, tx):
    result = web3.eth.wait_for_transaction_receipt(tx)
    assert result.status == 1
    return result


def deploy(web3, folder):
    owner = web3.eth.accounts[0]
    contracts = {}
    for name, args in (("TestToken", [owner]), ("HyperVaults", None)):
        artifact = json.loads((ROOT / f"smartcontract/out/{name}.sol/{name}.json").read_text())
        factory = web3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"]["object"])
        args = args if args is not None else [contracts["TestToken"].address, owner]
        deployed = receipt(web3, factory.constructor(*args).transact({"from": owner}))
        contracts[name] = web3.eth.contract(address=deployed.contractAddress, abi=artifact["abi"])
    token, vault = contracts["TestToken"], contracts["HyperVaults"]
    for days, rate in ((1, 300), (7, 500), (30, 800)):
        receipt(
            web3,
            vault.functions.createOffer(
                days * 86400, rate, 10 * 10**18, 1000 * 10**18, 10_000 * 10**18
            ).transact({"from": owner}),
        )
    receipt(web3, token.functions.approve(vault.address, 10_000 * 10**18).transact({"from": owner}))
    receipt(web3, vault.functions.fundRewards(10_000 * 10**18).transact({"from": owner}))
    manifest = deployment_manifest(web3, vault.address, token.address, deployed.blockNumber)
    path = folder / "deployment.json"
    path.write_text(json.dumps(manifest))
    return path, token, vault


async def journey(origin, web3, db, clock, token, vault, evidence):
    member = web3.eth.accounts[1]
    transactions = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context(
                viewport={"width": 1440, "height": 1000}, device_scale_factor=1
            )

            async def wallet_request(_, payload):
                method, params = payload["method"], payload.get("params", [])
                if method in {"eth_requestAccounts", "eth_accounts"}:
                    return [member]
                if method == "eth_sendTransaction":
                    assert params[0]["from"].lower() == member.lower()
                result = await asyncio.to_thread(web3.provider.make_request, method, params)
                if "error" in result:
                    raise RuntimeError(result["error"]["message"])
                if method == "eth_sendTransaction":
                    transactions.append(result["result"])
                return result["result"]

            await context.expose_binding("demoWalletRequest", wallet_request)
            await context.add_init_script(WALLET)
            page = await context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            async def capture(name, locator=None):
                if evidence:
                    await page.evaluate("document.fonts.ready")
                    target = page.locator(locator) if locator else page
                    await target.screenshot(
                        path=str(evidence / f"{name}.png"), animations="disabled"
                    )

            async def ready(path):
                await page.goto(origin + path)
                await page.locator("#freshness").filter(has_text="Confirmed block").wait_for()

            async def finish_transaction():
                await page.locator("#confirm-transaction").click()
                await (
                    page.locator("#transaction-status")
                    .filter(has_text="Confirmed by successful")
                    .wait_for(timeout=30000)
                )
                await page.locator("#cancel-transaction:enabled").click()

            await page.goto(origin + "/login")
            await page.locator("#auth-submit:enabled").wait_for()
            await capture("01-login")
            await page.goto(origin + "/register")
            await page.locator("#auth-submit:enabled").wait_for()
            for name, value in {
                "email": "demo.member@example.com",
                "name": "Demo member",
                "password": PASSWORD,
                "confirm": PASSWORD,
            }.items():
                await page.locator(f'input[name="{name}"]').fill(value)
            await capture("02-registration")
            await page.locator("#auth-submit").click()
            await page.wait_for_url(origin + "/overview")
            await page.locator("#freshness").filter(has_text="Confirmed block").wait_for()
            await capture("03-wallet-onboarding")
            await page.locator("#wallet-setup-action:enabled").click()
            await page.locator("#wallet-setup-action").filter(has_text="Link wallet").wait_for()
            assert not db.users.find_one({"email": "demo.member@example.com"})["wallets"]
            await capture("04-wallet-connected")
            await page.locator("#wallet-setup-action:enabled").click()
            await page.locator("#wallet-setup").wait_for(state="hidden")
            assert db.users.find_one({"email": "demo.member@example.com"})["wallets"] == [
                member.lower()
            ]
            await ready("/vaults")
            await capture("05-vaults")
            await page.get_by_role("button", name="Get HVT", exact=True).click()
            await finish_transaction()
            assert token.functions.balanceOf(member).call() == 1000 * 10**18
            await page.locator(".offer").first.locator("input").fill("100")
            await page.locator(".offer").first.get_by_role("button", name="Preview deposit").click()
            await page.locator("#transaction-dialog").wait_for()
            await capture("06-deposit-review", "#transaction-dialog")
            await finish_transaction()
            assert vault.functions.outstandingPrincipal().call() == 100 * 10**18
            assert token.functions.allowance(member, vault.address).call() == 0
            await ready("/overview")
            await capture("07-overview")
            await ready("/positions")
            assert await page.get_by_role("button", name="Withdraw at maturity").is_disabled()
            await capture("08-locked-position")
            await page.get_by_role("button", name="Private note").click()
            await page.locator('#note-form textarea[name="note"]').fill(
                "Demo savings plan. Only this account can read this note."
            )
            await page.locator('#note-form button[type="submit"]').click()
            await (
                page.locator("#note-form .form-message")
                .filter(has_text="Encrypted note saved")
                .wait_for()
            )
            await capture("09-encrypted-note", "#note-dialog")
            assert "Demo savings plan" not in json.dumps(db.notes.find_one(), default=str)
            await page.locator("#note-dialog .dialog-close").click()
            receipt(
                web3,
                vault.functions.setDepositsPaused(True).transact({"from": web3.eth.accounts[0]}),
            )
            web3.provider.make_request("evm_increaseTime", [86401])
            web3.provider.make_request("evm_mine", [])
            await ready("/positions")
            await page.get_by_role("button", name="Withdraw at maturity").click()
            await capture("10-withdrawal-review", "#transaction-dialog")
            await finish_transaction()
            assert vault.functions.liabilities().call() == 0
            assert token.functions.balanceOf(member).call() > 1000 * 10**18
            await capture("11-settled-position")
            await page.set_viewport_size({"width": 375, "height": 812})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            await capture("12-mobile-positions")
            await page.set_viewport_size({"width": 1440, "height": 1000})
            await page.goto(origin + "/security")
            await page.locator("#mfa-start:enabled").click()
            await page.locator('dialog input[name="password"]').fill(PASSWORD)
            await page.locator('dialog button[type="submit"]').click()
            await page.locator("#mfa-qr").wait_for(state="visible")
            await capture("13-mfa-qr", ".mfa-panel")
            secret = await page.locator("#mfa-secret").text_content()
            await page.locator('#mfa-setup-form input[name="code"]').fill(
                totp(secret, int(clock[0].timestamp()) // 30)
            )
            await page.locator("#mfa-enable").click()
            await page.locator("#mfa-recovery").wait_for()
            codes = (await page.locator("#mfa-codes").inner_text()).splitlines()
            assert len(codes) == 8
            await capture("14-recovery-codes", ".mfa-panel")
            await page.locator("#mfa-dismiss-codes").click()
            await capture("15-account-security", ".mfa-panel")
            await page.locator("#logout").click()
            await page.wait_for_url(origin + "/login")
            await page.locator("#auth-submit:enabled").wait_for()
            await page.locator('input[name="email"]').fill("demo.member@example.com")
            await page.locator('input[name="password"]').fill(PASSWORD)
            await page.locator("#auth-submit").click()
            await page.locator("#mfa-form").wait_for()
            assert await page.evaluate("fetch('/api/security/sessions').then(r => r.status)") == 401
            clock[0] += timedelta(seconds=30)
            # Valid input shape with the wrong code reaches the server's rejection path.
            wrong = (
                "000000" if totp(secret, int(clock[0].timestamp()) // 30) != "000000" else "111111"
            )
            await page.locator('#mfa-form input[name="code"]').fill(wrong)
            await page.locator("#mfa-submit").click()
            await page.locator("#auth-message").filter(has_text="invalid").wait_for()
            await capture("16-mfa-signin")
            await page.locator('#mfa-form input[name="code"]').fill(
                totp(secret, int(clock[0].timestamp()) // 30)
            )
            await page.locator("#mfa-submit").click()
            await page.wait_for_url(origin + "/overview")
            await page.goto(origin + "/security")
            await page.locator("#mfa-status").filter(has_text="Enabled").wait_for()
            await page.locator("#logout").click()
            await page.wait_for_url(origin + "/login")
            await page.locator("#siwe-login:enabled").click()
            await page.locator("#mfa-form").wait_for()
            await page.locator("#mfa-use-recovery").click()
            await page.locator('#mfa-form input[name="code"]').fill(codes[0])
            await page.locator("#mfa-submit").click()
            await page.wait_for_url(origin + "/overview")
            assert db.users.count_documents({"email": "demo.member@example.com"}) == 1
            await page.locator("#logout").click()
            await page.wait_for_url(origin + "/login")
            # Bootstrap a separate disposable administrator; no live roles are edited.
            password_hash = db.users.find_one({"email": "demo.member@example.com"})["password_hash"]
            db.users.insert_one(
                {
                    "_id": str(uuid.uuid4()),
                    "email": "demo.admin@example.com",
                    "name": "Demo administrator",
                    "password_hash": password_hash,
                    "wallets": [],
                    "role": "admin",
                    "disabled": False,
                    "auth_version": 1,
                    "created_at": clock[0],
                }
            )
            await page.locator("#auth-submit:enabled").wait_for()
            await page.locator('input[name="email"]').fill("demo.admin@example.com")
            await page.locator('input[name="password"]').fill(PASSWORD)
            await page.locator("#auth-submit").click()
            await page.wait_for_url(origin + "/overview")
            await ready("/admin")
            await capture("17-admin-contract")
            await page.locator("#tab-accounts").click()
            await page.locator("#user-list .item").first.wait_for()
            await capture("18-admin-accounts")
            for path in ("/overview", "/vaults", "/positions", "/admin", "/security"):
                await page.goto(origin + path)
                selector = "Account data up to date" if path == "/security" else "Confirmed block"
                await page.locator("#freshness").filter(has_text=selector).wait_for()
                for width in (1440, 768, 375, 320):
                    await page.set_viewport_size({"width": width, "height": 900})
                    assert await page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    ), (path, width)
            await page.locator("#logout").click()
            await page.wait_for_url(origin + "/login")
            assert not errors, errors
            assert len(transactions) == 4  # Faucet, exact approval, deposit, withdrawal.
            result = {
                "result": "PASS",
                "environment": "disposable Anvil + Mongo + real browser/API",
                "wallet": "injected test provider; isolated unlocked accounts, no private keys",
                "journeys": [
                    "registration",
                    "connect then link",
                    "faucet",
                    "exact approval",
                    "deposit",
                    "encrypted note",
                    "mature withdrawal while paused",
                    "QR MFA",
                    "email MFA",
                    "wallet MFA recovery code",
                    "admin views",
                    "20 responsive page checks",
                ],
                "transaction_hashes": transactions,
                "browser_errors": errors,
                "existing_state_changed": False,
            }
            if evidence:
                (evidence / "demo-results.json").write_text(json.dumps(result, indent=2))
            print(json.dumps(result))
        finally:
            await browser.close()


async def main():
    uri = os.environ.get("HV_AUTH_TEST_MONGO_URI")
    if not uri:
        raise SystemExit("Select HV_AUTH_TEST_MONGO_URI for disposable database checks.")
    evidence = (
        Path(os.environ["HV_REPORT_EVIDENCE_DIR"]).resolve()
        if os.environ.get("HV_REPORT_EVIDENCE_DIR")
        else None
    )
    if evidence:
        evidence.mkdir(parents=True, exist_ok=True)
    name = f"hypervaults-report-{uuid.uuid4().hex[:12]}"
    mongo = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    db = mongo[f"hypervaults_test_browser_{uuid.uuid4().hex}"]
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    worker = server = None
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-d",
                "--name",
                name,
                "-p",
                "127.0.0.1::8545",
                "--entrypoint",
                "anvil",
                IMAGE,
                "--host",
                "0.0.0.0",
                "--silent",
                "--chain-id",
                "31337",
            ],
            check=True,
            capture_output=True,
        )
        port = (
            subprocess.check_output(["docker", "port", name, "8545"], text=True)
            .strip()
            .split(":")[-1]
        )
        web3 = Web3(HTTPProvider(f"http://127.0.0.1:{port}", request_kwargs={"timeout": 3}))
        for _ in range(60):
            if web3.is_connected():
                break
            await asyncio.sleep(0.2)
        assert web3.is_connected(), "Disposable chain did not start"
        with tempfile.TemporaryDirectory() as folder:
            deployment, token, vault = deploy(web3, Path(folder))
            settings = Settings(
                _env_file=None,
                mode="test",
                origin=origin,
                deployment_file=deployment,
                rpc_url=web3.provider.endpoint_uri,
                note_keys=json.dumps({"v1": base64.b64encode(secrets.token_bytes(32)).decode()}),
            )
            create_indexes(db)
            clock = [datetime.now(UTC)]
            app = create_app(
                settings, db=db, chain=ChainService(settings, db), clock=lambda: clock[0]
            )
            server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
            worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
            worker.start()
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(0.05)
            assert server.started
            await journey(origin, web3, db, clock, token, vault, evidence)
    finally:
        if server:
            server.should_exit = True
        if worker:
            worker.join(timeout=5)
        listener.close()
        mongo.drop_database(db.name)
        mongo.close()
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


if __name__ == "__main__":
    asyncio.run(main())
