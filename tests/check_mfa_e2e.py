"""Real browser + HTTP + Mongo MFA flow in a disposable database, with chain offline.

HV_AUTH_TEST_MONGO_URI=mongodb://127.0.0.1:27018 \
  uv run --with playwright python -m tests.check_mfa_e2e
"""

import asyncio
import base64
import json
import os
import secrets
import socket
import threading
import uuid
from datetime import UTC, datetime, timedelta

import uvicorn
from playwright.async_api import async_playwright
from pymongo import MongoClient

from auth.mfa import totp
from webapp.blockchain import ChainUnavailable
from webapp.config import Settings
from webapp.database import create_indexes
from webapp.main import create_app


class OfflineChain:
    def configuration(self):
        raise ChainUnavailable("Wallet configuration unavailable in this isolated test.")


async def check_flow(origin, clock):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 375, "height": 812})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            password = "isolated-browser-fixture-password"
            await page.goto(origin + "/register")
            await page.locator("#auth-submit:enabled").wait_for()
            await page.locator('input[name="email"]').fill("browser-fixture@example.com")
            await page.locator('input[name="name"]').fill("Browser fixture")
            await page.locator('input[name="password"]').fill(password)
            await page.locator('input[name="confirm"]').fill(password)
            await page.locator("#auth-submit").click()
            await page.wait_for_url(origin + "/overview")
            await page.goto(origin + "/security")
            await page.locator("#mfa-start:enabled").wait_for()
            await page.locator("#mfa-start").click()
            await page.locator('dialog input[name="password"]').fill(password)
            await page.locator('dialog button[type="submit"]').click()
            await page.locator("#mfa-setup-form").wait_for()
            secret = await page.locator("#mfa-secret").inner_text()
            code = totp(secret, int(clock[0].timestamp()) // 30)
            await page.locator('#mfa-setup-form input[name="code"]').fill(code)
            await page.locator("#mfa-enable").click()
            await page.locator("#mfa-recovery").wait_for()
            codes = (await page.locator("#mfa-codes").inner_text()).splitlines()
            assert len(codes) == 8
            await page.locator("#mfa-dismiss-codes").click()
            await page.locator("#logout").click()
            await page.wait_for_url(origin + "/login")
            await page.locator("#auth-submit:enabled").wait_for()
            await page.locator('input[name="email"]').fill("browser-fixture@example.com")
            await page.locator('input[name="password"]').fill(password)
            await page.locator("#auth-submit").click()
            await page.locator("#mfa-form").wait_for()
            # This check uses the browser's real pending cookie against the real API.
            status = await page.evaluate(
                "fetch('/api/security/sessions').then(response => response.status)"
            )
            assert status == 401
            clock[0] += timedelta(seconds=30)
            code = totp(secret, int(clock[0].timestamp()) // 30)
            await page.locator('#mfa-form input[name="code"]').fill(code)
            await page.locator("#mfa-submit").click()
            await page.wait_for_url(origin + "/overview")
            await page.goto(origin + "/security")
            await page.locator("#mfa-status").filter(has_text="Enabled").wait_for()
            await page.locator("#mfa-disable").click()
            await page.locator('dialog input[name="password"]').fill(password)
            await page.locator('dialog input[name="code"]').fill(codes[0])
            await page.locator('dialog button[type="submit"]').click()
            await page.locator("#mfa-status").filter(has_text="Off").wait_for()
            assert await page.locator("#mfa-codes").inner_text() == ""
            assert not errors, errors
        finally:
            await browser.close()


async def main():
    uri = os.environ.get("HV_AUTH_TEST_MONGO_URI")
    if not uri:
        raise SystemExit("Set HV_AUTH_TEST_MONGO_URI to run this isolated database check.")
    mongo = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    db = mongo[f"hypervaults_test_browser_{uuid.uuid4().hex}"]
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    keyring = json.dumps({"v1": base64.b64encode(secrets.token_bytes(32)).decode()})
    settings = Settings(_env_file=None, mode="test", origin=origin, note_keys=keyring)
    clock = [datetime.now(UTC)]
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings, db=db, chain=OfflineChain(), clock=lambda: clock[0]),
            log_level="error",
            access_log=False,
        )
    )
    worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    try:
        create_indexes(db)
        worker.start()
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        if not server.started:
            raise RuntimeError("Isolated application failed to start.")
        await check_flow(origin, clock)
        print(
            "PASS: real registration, MFA enrollment, gated login and action-bound disable; chain offline. Existing accounts untouched."
        )
    finally:
        server.should_exit = True
        if worker.is_alive():
            worker.join(timeout=5)
        listener.close()
        mongo.drop_database(db.name)
        mongo.close()


if __name__ == "__main__":
    asyncio.run(main())
