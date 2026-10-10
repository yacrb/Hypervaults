"""Run with uv run --with playwright python tests/check_account_ui.py.

Browser checks use isolated account/API/wallet fixtures and local source assets.
No real accounts, credentials, signatures or chain transactions are changed.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import segno
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8088"
ADDRESS = "0x1111111111111111111111111111111111111111"
QR = segno.make_qr("otpauth://totp/HyperVaults:fixture?secret=JBSWY3DPEHPK3PXP&issuer=HyperVaults").png_data_uri(scale=5)
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


async def fixture(browser, has_password=True, config_failed=False, qr=QR):
    context = await browser.new_context(viewport={"width": 375, "height": 812})
    user = {"id": "fixture", "name": "Member", "role": "user", "email": "fixture@example.com" if has_password else None,
            "has_password": has_password, "mfa_enabled": False, "recovery_codes_remaining": 0, "wallets": [] if has_password else [ADDRESS]}
    requests = []

    async def respond(route):
        request = route.request
        path = urlparse(request.url).path
        if path in {"/overview", "/security"}:
            return await route.fulfill(path=ROOT / ("webapp/templates/account.html" if path == "/security" else "webapp/templates/index.html"), content_type="text/html")
        if path.startswith("/static/"):
            source = (ROOT / "webapp" / path.lstrip("/")).resolve()
            assert source.is_relative_to(ROOT / "webapp/static")
            if source.name == "wallet.js":
                return await route.fulfill(body=WALLET, content_type="text/javascript")
            mime = "text/javascript" if source.suffix == ".js" else "text/css" if source.suffix == ".css" else "image/svg+xml"
            return await route.fulfill(path=source, content_type=mime)
        requests.append((path, request.post_data_json if request.post_data else None))
        if path == "/api/config" and config_failed:
            return await route.fulfill(status=503, json={"detail": "Wallet configuration unavailable."})
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
        if path == "/api/auth/reauth":
            return await route.fulfill(json={"ok": True})
        if path == "/api/security/mfa/setup":
            return await route.fulfill(json={"ok": True} if request.method == "DELETE" else {"secret": "JBSWY3DPEHPK3PXP", "qr_data_url": qr})
        if path == "/api/security/mfa/enable":
            if request.post_data_json["code"] != "123456":
                return await route.fulfill(status=400, json={"detail": "Enter the six-digit code from your authenticator."})
            user.update(mfa_enabled=True, recovery_codes_remaining=8)
            return await route.fulfill(json={"user": user, "csrf": "fixture-csrf", "recovery_codes": [f"{i:032x}" for i in range(8)]})
        if path == "/api/security/mfa":
            user.update(mfa_enabled=False, recovery_codes_remaining=0)
            return await route.fulfill(json={"user": user, "csrf": "fixture-csrf"})
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
        await page.locator("#wallet-setup-action").dispatch_event("click")
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
        assert await page.locator("#link-wallet").count() == 0
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await context.close()

        context, page, requests = await fixture(browser, has_password=False)
        await page.goto(BASE + "/security")
        await page.locator("#connect-wallet:enabled").wait_for()
        assert await page.locator("#password-title").inner_text() == "Set up email & password"
        assert await page.locator("#current-password-label").is_hidden()
        assert await page.locator("#reauth").is_hidden()
        assert await page.locator("#save-password").is_disabled()
        await page.locator("#connect-wallet").click()
        await page.locator("#save-password:enabled").wait_for()
        await page.locator('#password-form input[name="email"]').fill("new-access@example.com")
        await page.locator('input[name="new_password"]').fill("fixture-new-password")
        await page.locator('input[name="confirm_password"]').fill("fixture-other-password")
        await page.locator("#save-password").click()
        assert await page.locator('input[name="confirm_password"]').get_attribute("aria-invalid") == "true"
        assert await page.evaluate("walletActions") == ["connect"]
        await page.locator('input[name="confirm_password"]').fill("fixture-new-password")
        await page.locator("#security-show-password").click()
        assert await page.locator('input[name="confirm_password"]').get_attribute("type") == "text"
        await page.locator("#save-password").click()
        await page.wait_for_function("walletActions.includes('reauth')")
        assert await page.locator("#connect-wallet").is_disabled()
        await page.locator("#password-form").dispatch_event("submit")
        await page.evaluate("cancelProof()")
        await page.locator("#save-password:enabled").wait_for()
        assert not any(path == "/api/auth/password" for path, _ in requests)
        assert await page.locator('input[name="new_password"]').input_value() == "fixture-new-password"
        await page.locator("#save-password").click()
        await page.wait_for_function("walletActions.length === 3")
        await page.evaluate("finishProof()")
        await page.locator("#password-title").filter(has_text="Email & password").wait_for()
        await page.locator("#password-email").filter(has_text="new-access@example.com").wait_for()
        assert await page.locator("#current-password-label").is_visible()
        assert await page.locator('input[name="new_password"]').input_value() == ""
        assert await page.locator('input[name="new_password"]').get_attribute("type") == "password"
        payloads = [body for path, body in requests if path == "/api/auth/password"]
        assert payloads == [{"email": "new-access@example.com", "current_password": "", "new_password": "fixture-new-password"}]
        await context.close()

        context, page, _ = await fixture(browser)
        await page.goto(BASE + "/security")
        await page.locator("#save-password:enabled").wait_for()
        assert await page.locator("#password-email-label").is_hidden()
        assert await page.locator("#current-password-label").is_visible()
        assert await page.locator('input[name="current_password"]').get_attribute("required") is not None
        await context.close()
        context, page, requests = await fixture(browser)
        await page.goto(BASE + "/security")
        await page.locator("#mfa-start:enabled").wait_for()
        assert await page.locator(".app-shell").count() == 0
        assert not any(path == "/api/snapshot" for path, _ in requests)
        await page.locator("#mfa-start").click()
        await page.locator("dialog input[name=password]").fill("fixture-password")
        await page.locator("dialog button[type=submit]").click()
        await page.locator("#mfa-setup-form").wait_for()
        await page.wait_for_function("document.getElementById('mfa-qr').naturalWidth > 0")
        assert await page.locator("#mfa-secret").is_hidden()
        await page.locator("#mfa-manual summary").click()
        assert await page.locator("#mfa-secret").inner_text() == "JBSWY3DPEHPK3PXP"
        await page.locator("#mfa-setup-form input").fill("000000")
        await page.locator("#mfa-enable").click()
        await page.locator("#mfa-setup-form .form-message").filter(has_text="six-digit").wait_for()
        await page.locator("#mfa-setup-form input").fill("123456")
        await page.locator("#mfa-enable").click()
        await page.locator("#mfa-recovery").wait_for()
        assert await page.locator("#mfa-secret").inner_text() == ""
        assert await page.locator("#mfa-qr").get_attribute("src") is None
        assert not await page.locator("#mfa-manual").evaluate("node => node.open")
        assert len((await page.locator("#mfa-codes").inner_text()).splitlines()) == 8
        assert await page.locator("#mfa-status").inner_text() == "Enabled"
        await page.locator("#mfa-dismiss-codes").click()
        assert await page.locator("#mfa-codes").inner_text() == ""
        await page.locator("#mfa-disable").click()
        await page.locator("dialog button").filter(has_text="Cancel").click()
        assert not any(path == "/api/security/mfa" for path, _ in requests)
        await page.locator("#mfa-disable").click()
        await page.locator("dialog input[name=password]").fill("fixture-password")
        await page.locator("dialog input[name=code]").fill("123456")
        await page.locator("dialog button[type=submit]").click()
        await page.locator("#mfa-status").filter(has_text="Off").wait_for()
        proof = [body for path, body in requests if path == "/api/auth/reauth"][-1]
        assert proof["mfa_action"] == "disable"
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        for width in (375, 320):
            await page.set_viewport_size({"width": width, "height": 812})
            back = await page.locator('.account-header a[aria-label="Back to dashboard"]').bounding_box()
            logout = await page.locator("#logout").bounding_box()
            assert back["x"] + back["width"] <= logout["x"]
        await context.close()

        # Missing/failed images retain a usable manual fallback; cancel clears both.
        for qr in (None, "data:image/png;base64,invalid"):
            context, page, requests = await fixture(browser, qr=qr)
            await page.goto(BASE + "/security")
            await page.locator("#mfa-start:enabled").click()
            await page.locator("dialog input[name=password]").fill("fixture-password")
            await page.locator("dialog button[type=submit]").click()
            await page.locator("#mfa-secret").wait_for(state="visible")
            assert await page.locator("#mfa-qr").is_hidden()
            await page.locator("#mfa-cancel-setup").click()
            await page.locator("#mfa-setup-form").wait_for(state="hidden")
            assert await page.locator("#mfa-secret").inner_text() == ""
            assert await page.locator("#mfa-qr").get_attribute("src") is None
            assert not await page.locator("#mfa-manual").evaluate("node => node.open")
            await context.close()

        context, page, requests = await fixture(browser, config_failed=True)
        await page.goto(BASE + "/security")
        await page.locator("#connected-address").filter(has_text="unavailable").wait_for()
        assert await page.locator("#connect-wallet").is_disabled()
        assert await page.locator("#save-password").is_enabled()
        assert await page.locator("#mfa-start").is_enabled()
        assert not any(path == "/api/snapshot" for path, _ in requests)
        await context.close()
        await browser.close()
        print("PASS: separate connect/link, proof cancellation/retry, duplicate guards, wallet-authorized email setup, password confirmation, separate account layout and MFA enrollment/cancellation.")


if __name__ == "__main__":
    asyncio.run(main())
