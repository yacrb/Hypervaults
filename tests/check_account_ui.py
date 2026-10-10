"""Run with uv run --with playwright python tests/check_account_ui.py.

Browser checks use isolated account/API/wallet fixtures and local source assets.
No real accounts, credentials, signatures or chain transactions are changed.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8088"
ADDRESS = "0x1111111111111111111111111111111111111111"
WALLET = """
import {api} from './api.js?v=2';
window.walletActions = [];
export const walletError = e => e.message;
export const tokens = v => String(Number(v) / 1e18), rate = v => String(v / 100), amount = v => BigInt(v);
export class Wallet extends EventTarget {
  constructor() { super(); this.address = null; this.connecting = false; this.selected = {name: 'Fixture'}; }
  async resume() { return false; }
  async connect() { window.walletActions.push('connect'); this.address = 'ADDRESS'; this.dispatchEvent(new Event('change')); }
  async authenticate(action) {
    window.walletActions.push(action);
    await new Promise((resolve, reject) => { window.finishProof = resolve; window.cancelProof = () => reject(new Error('Signature cancelled.')); });
    return api('/auth/verify', {method: 'POST', body: {action}});
  }
}
""".replace("ADDRESS", ADDRESS)


async def fixture(browser, has_password=True):
    context = await browser.new_context(viewport={"width": 375, "height": 812})
    user = {"id": "fixture", "name": "Member", "role": "user", "email": "fixture@example.com" if has_password else None,
            "has_password": has_password, "wallets": [] if has_password else [ADDRESS]}
    requests = []

    async def respond(route):
        request = route.request
        path = urlparse(request.url).path
        if path in {"/overview", "/security"}:
            return await route.fulfill(path=ROOT / "webapp/templates/index.html", content_type="text/html")
        if path.startswith("/static/"):
            source = (ROOT / "webapp" / path.lstrip("/")).resolve()
            assert source.is_relative_to(ROOT / "webapp/static")
            if source.name == "wallet.js":
                return await route.fulfill(body=WALLET, content_type="text/javascript")
            mime = "text/javascript" if source.suffix == ".js" else "text/css" if source.suffix == ".css" else "image/svg+xml"
            return await route.fulfill(path=source, content_type=mime)
        requests.append((path, request.post_data_json if request.post_data else None))
        payload = {
            "/api/session": {"user": user, "csrf": "fixture-csrf"},
            "/api/config": {"chain_id": 11155111, "network": "Sepolia"},
            "/api/security/sessions": {"sessions": []},
            "/api/security/activity": {"events": []},
            "/api/snapshot": {"block": {"number": 100, "observed_at": datetime.now(timezone.utc).isoformat()},
                              "positions": [], "activity": [], "totals": {"principal": "0", "rewards": "0", "at_maturity": "0"}},
        }
        if path == "/api/auth/verify":
            assert request.post_data_json["action"] in {"link", "reauth"}
            if request.post_data_json["action"] == "link":
                user["wallets"] = [ADDRESS]
            return await route.fulfill(json={"user": user, "csrf": "rotated-fixture-csrf"})
        if path == "/api/auth/password":
            assert request.headers["x-csrf-token"] == "rotated-fixture-csrf"
            user.update(has_password=True, email=request.post_data_json.get("email", user["email"]))
            return await route.fulfill(json={"user": user, "csrf": "password-fixture-csrf"})
        assert path in payload, path
        await route.fulfill(json=payload[path])

    await context.route("**/*", respond)
    return context, await context.new_page(), requests


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context, page, requests = await fixture(browser)
        await page.goto(BASE + "/overview")
        await page.locator("#wallet-setup-action:enabled").wait_for()
        assert await page.evaluate("walletActions") == []
        await page.locator("#wallet-setup-action").click()
        assert await page.locator("#wallet-setup-action").inner_text() == "Link wallet"
        assert await page.evaluate("walletActions") == ["connect"]
        assert not any(path == "/api/auth/verify" for path, _ in requests)
        await page.locator("#wallet-setup-action").click()
        await page.wait_for_function("walletActions.includes('link')")
        await page.locator("#link-wallet").dispatch_event("click")
        await page.evaluate("cancelProof()")
        await page.locator("#wallet-setup-action:enabled").wait_for()
        assert await page.locator("#wallet-setup").is_visible()
        assert await page.evaluate("walletActions") == ["connect", "link"]
        assert not any(path == "/api/auth/verify" for path, _ in requests)
        await page.locator("#wallet-setup-action").click()
        await page.wait_for_function("walletActions.length === 3")
        await page.evaluate("finishProof()")
        await page.locator("#wallet-setup").wait_for(state="hidden")
        assert sum(path == "/api/auth/verify" for path, _ in requests) == 1
        assert await page.locator("#link-wallet").is_disabled()
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await context.close()
        await browser.close()
        print("PASS: connect/link are separate, cancellation preserves setup, duplicate proofs are blocked, successful linking completes the guide.")


if __name__ == "__main__":
    asyncio.run(main())
