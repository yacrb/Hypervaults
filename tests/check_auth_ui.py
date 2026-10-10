"""Isolated browser checks: uv run --with playwright python tests/check_auth_ui.py.

Install Chromium once with: uv run --with playwright playwright install chromium.
All requests use local source files or fixtures; no accounts or wallets are changed.
"""

import asyncio
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8088"


async def fixture(browser, *, session_gate=None, config_gate=None, login_gate=None,
                  session_failed=False, config_failed=False, script_failed=False,
                  mfa=False, pending=False, mfa_gate=None):
    context = await browser.new_context(viewport={"width": 375, "height": 812})
    await context.add_init_script("""
        window.walletRequests = 0;
        window.ethereum = {isMetaMask: true, request: async ({method}) => {
            if (method !== 'eth_requestAccounts') throw new Error('Unexpected wallet request');
            window.walletRequests++;
            return new Promise((resolve, reject) => {
                window.rejectWallet = () => reject({code: 4001});
            });
        }};
    """)
    requests, started = [], asyncio.Event()

    async def route_request(route):
        nonlocal pending
        request = route.request
        path = urlparse(request.url).path
        requests.append((request.method, request.url))
        if path == "/api/session":
            started.set()
            if session_gate:
                await session_gate.wait()
            await route.fulfill(status=503 if session_failed else 200, json={
                "detail": "Session unavailable.", "user": None, "csrf": "fixture-csrf", "mfa_required": pending,
            })
        elif path == "/api/config":
            if config_gate:
                await config_gate.wait()
            await route.fulfill(status=503 if config_failed else 200, json={
                "detail": "Wallet configuration unavailable.", "chain_id": 11155111,
                "network": "Sepolia", "genesis_hash": "fixture-genesis",
            })
        elif path == "/api/auth/login":
            assert request.method == "POST"
            assert request.headers.get("x-csrf-token") == "fixture-csrf"
            if login_gate:
                await login_gate.wait()
            if mfa:
                pending = True
                await route.fulfill(json={"user": None, "csrf": "fixture-csrf", "mfa_required": True})
            else:
                await route.fulfill(status=401, json={"detail": "Invalid credentials or unavailable account."})
        elif path == "/api/auth/mfa/verify":
            if mfa_gate:
                await mfa_gate.wait()
            if request.post_data_json["code"] not in {"123456", "a" * 32}:
                return await route.fulfill(status=400, json={"detail": "Code is invalid or already used. Try the next code."})
            pending = False
            await route.fulfill(json={"user": {"id": "fixture"}, "csrf": "fixture-csrf"})
        elif path == "/api/auth/logout":
            pending = False
            await route.fulfill(json={"ok": True})
        elif path == "/api/auth/reset":
            data = request.post_data_json
            if not data.get("mfa_code"):
                return await route.fulfill(status=403, headers={"X-MFA-Required": "true"}, json={"detail": "Enter an authenticator or recovery code."})
            await route.fulfill(json={"message": "Password reset. Sign in with your new password."})
        elif path == "/overview":
            await route.fulfill(body="<!doctype html><h1>Fixture dashboard</h1>", content_type="text/html")
        elif path.startswith("/static/"):
            source = (ROOT / "webapp" / path.lstrip("/")).resolve()
            assert source.is_relative_to(ROOT / "webapp/static")
            if script_failed and source.name == "auth.js":
                await route.abort()
            else:
                mime = "text/javascript" if source.suffix == ".js" else "text/css" if source.suffix == ".css" else "image/svg+xml"
                await route.fulfill(path=source, content_type=mime)
        elif path in {"/login", "/register", "/recover", "/reset"}:
            await route.fulfill(path=ROOT / "webapp/templates/auth.html", content_type="text/html")
        else:
            raise AssertionError(f"Unexpected request: {request.method} {path}")

    await context.route("**/*", route_request)
    return context, await context.new_page(), requests, started


async def enabled(page, selector):
    await page.locator(selector + ":enabled").wait_for()


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        session_gate, config_gate, login_gate = [asyncio.Event() for _ in range(3)]
        context, page, requests, started = await fixture(
            browser, session_gate=session_gate, config_gate=config_gate, login_gate=login_gate,
        )
        await page.goto(BASE + "/login")
        await asyncio.wait_for(started.wait(), 5)
        assert await page.locator("#auth-submit").is_disabled()
        await page.locator("#auth-form").dispatch_event("submit")
        assert not any("/auth/login" in url for _, url in requests)
        session_gate.set()
        await enabled(page, "#auth-submit")
        assert await page.locator("#siwe-login").is_disabled()
        await page.locator("input[name=email]").fill("fixture@example.com")
        await page.locator("input[name=password]").fill("fixture-password")
        await page.locator("input[name=password]").press("Enter")
        await page.locator('#auth-submit[aria-busy="true"]').wait_for()
        assert await page.locator("#auth-submit").inner_text() == "Signing in…"
        config_gate.set()
        await page.wait_for_function("document.querySelector('#siwe-login').disabled")
        assert await page.locator("#siwe-login").is_disabled()
        await page.locator("#auth-form").dispatch_event("submit")
        await page.locator("#siwe-login").dispatch_event("click")
        assert await page.evaluate("walletRequests") == 0
        login_gate.set()
        await enabled(page, "#siwe-login")
        assert sum("/auth/login" in url for _, url in requests) == 1
        assert await page.locator("input[name=password]").input_value() == "fixture-password"
        await page.locator("#siwe-login").click()
        await page.wait_for_function("walletRequests === 1")
        assert await page.locator("#auth-submit").is_disabled()
        assert await page.locator("#wallet-login-label").inner_text() == "Waiting for wallet…"
        await page.locator("#auth-form").dispatch_event("submit")
        await page.evaluate("rejectWallet()")
        await enabled(page, "#auth-submit")
        await enabled(page, "#siwe-login")
        assert sum("/auth/login" in url for _, url in requests) == 1
        assert "cancelled" in await page.locator("#auth-message").inner_text()
        assert await page.locator("#auth-submit").inner_text() == "Sign in"
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await context.close()

        for options in ({"session_failed": True}, {"config_failed": True}, {"script_failed": True}):
            context, page, requests, _ = await fixture(browser, **options)
            await page.goto(BASE + "/login")
            if options.get("script_failed"):
                await page.locator("input[name=password]").fill("fixture-password")
                async with page.expect_navigation():
                    await page.locator("#auth-form").evaluate("form => form.submit()")
                assert any(method == "POST" and url == BASE + "/login" for method, url in requests)
            else:
                await page.locator("#auth-message" if options.get("session_failed") else "#auth-submit:enabled").wait_for()
                if options.get("session_failed"):
                    await page.locator("#auth-message").filter(has_text="Session unavailable").wait_for()
                    assert await page.locator("#auth-submit").is_disabled()
                else:
                    await page.locator('#siwe-login[title="Wallet configuration unavailable."]').wait_for()
                assert await page.locator("#siwe-login").is_disabled()
            assert all("fixture-password" not in url for _, url in requests)
            await context.close()

        for path, label in (("/register", "Create account"), ("/recover", "Send recovery link"),
                            ("/reset#token=fixture", "Reset password")):
            context, page, _, _ = await fixture(browser)
            await page.goto(BASE + path)
            await enabled(page, "#auth-submit")
            assert await page.locator("#auth-submit").inner_text() == label
            if path != "/register":
                assert await page.locator("#wallet-auth").is_hidden()
            await context.close()
        context, page, _, _ = await fixture(browser)
        await page.goto(BASE + "/reset")
        await page.locator("#auth-message").filter(has_text="valid recovery link").wait_for()
        assert await page.locator("#auth-submit").is_disabled()
        await context.close()

        mfa_gate = asyncio.Event()
        context, page, requests, _ = await fixture(browser, mfa=True, mfa_gate=mfa_gate)
        await page.goto(BASE + "/login")
        await enabled(page, "#auth-submit")
        await page.locator("input[name=email]").fill("fixture@example.com")
        await page.locator("input[name=password]").fill("fixture-password")
        await page.locator("#auth-submit").click()
        await page.locator("#mfa-form").wait_for()
        assert await page.locator("#auth-form").is_hidden()
        assert await page.locator("input[name=password]").input_value() == ""
        await page.locator("#auth-form").dispatch_event("submit")
        assert sum("/auth/login" in url for _, url in requests) == 1
        assert page.url.endswith("/login")
        await page.locator("#mfa-form input").fill("000000")
        await page.locator("#mfa-submit").click()
        await page.locator('#mfa-submit[aria-busy="true"]').wait_for()
        await page.locator("#mfa-form").dispatch_event("submit")
        assert await page.locator("#mfa-cancel").is_disabled()
        mfa_gate.set()
        await page.locator("#auth-message").filter(has_text="invalid or already used").wait_for()
        assert sum("/auth/mfa/verify" in url for _, url in requests) == 1
        await page.locator("#mfa-use-recovery").click()
        await page.locator("#mfa-form input").fill("a" * 32)
        await page.locator("#mfa-submit").click()
        await page.wait_for_url(BASE + "/overview")
        await context.close()

        context, page, requests, _ = await fixture(browser, pending=True)
        await page.goto(BASE + "/login")
        await page.locator("#mfa-form").wait_for()
        assert not any("/api/config" in url for _, url in requests)
        await page.locator("#mfa-cancel").click()
        await page.locator("#auth-form").wait_for()
        assert any("/auth/logout" in url for _, url in requests)
        await context.close()

        context, page, requests, _ = await fixture(browser)
        await page.goto(BASE + "/reset#token=" + "a" * 32)
        await enabled(page, "#auth-submit")
        assert page.url.endswith("/reset")
        await page.locator("input[name=password]").fill("fixture-new-password")
        await page.locator("input[name=confirm]").fill("fixture-new-password")
        await page.locator("#auth-submit").click()
        await page.locator("#mfa-form").wait_for()
        assert await page.locator("#mfa-submit").inner_text() == "Verify & reset password"
        await page.locator("#mfa-form input").fill("123456")
        await page.locator("#mfa-submit").click()
        await page.locator("#auth-message").filter(has_text="Password reset.").wait_for()
        assert await page.locator("#mfa-form").is_hidden()
        assert await page.locator("#auth-links").is_visible()
        assert all("fixture-new-password" not in url for _, url in requests)
        await context.close()

        await browser.close()
        print("PASS: delayed startup, one sign-in at a time, wallet cancellation, MFA retry/recovery/duplicate guards/cancellation, protected reset and POST fallback.")


if __name__ == "__main__":
    asyncio.run(main())
