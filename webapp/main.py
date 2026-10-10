"""One FastAPI process serving both accounts and the native-module interface."""

import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pymongo.errors import PyMongoError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from auth.crypto import NoteCipher, NoteTampered
from auth.mfa_routes import router as mfa_router
from auth.routes import router as auth_router
from auth.services import AuthService, utcnow
from webapp.blockchain import ChainUnavailable, UnverifiedTransaction
from webapp.config import ROOT, Settings
from webapp.database import connect
from webapp.routes import router as vault_router

log = logging.getLogger("hypervaults")


class SecurityMiddleware:
    """Bound actual request bodies, not just caller-supplied Content-Length."""

    def __init__(self, app, secure: bool):
        self.app, self.secure = app, secure

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def secured_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (
                            b"content-security-policy",
                            b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'",
                        ),
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"no-referrer"),
                        (
                            b"permissions-policy",
                            b"camera=(), microphone=(), geolocation=()",
                        ),
                        (
                            b"cache-control",
                            b"no-cache" if scope["path"].startswith("/static/") else b"no-store",
                        ),
                    ]
                )
                if self.secure:
                    headers.append((b"strict-transport-security", b"max-age=31536000"))
                message["headers"] = headers
            await send(message)

        if scope["path"].startswith("/api/") and scope["method"] in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }:
            headers = dict(scope["headers"])
            try:
                declared = int(headers.get(b"content-length", b"0"))
            except ValueError:
                return await JSONResponse({"detail": "Invalid request length."}, 400)(
                    scope, receive, secured_send
                )
            body = bytearray()
            if declared > 32768:
                return await JSONResponse({"detail": "Request body exceeds 32 KiB."}, 413)(
                    scope, receive, secured_send
                )
            while True:
                chunk = await receive()
                if chunk["type"] == "http.disconnect":
                    return
                body.extend(chunk.get("body", b""))
                if len(body) > 32768:
                    return await JSONResponse({"detail": "Request body exceeds 32 KiB."}, 413)(
                        scope, receive, secured_send
                    )
                if not chunk.get("more_body", False):
                    break
            if body and headers.get(b"content-type", b"").split(b";")[0] != b"application/json":
                return await JSONResponse({"detail": "Use application/json."}, 415)(
                    scope, receive, secured_send
                )
            delivered = False

            async def buffered_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {
                        "type": "http.request",
                        "body": bytes(body),
                        "more_body": False,
                    }
                return await receive()

            return await self.app(scope, buffered_receive, secured_send)
        return await self.app(scope, receive, secured_send)


def create_app(settings: Settings | None = None, db=None, chain=None, clock=utcnow) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        client = None
        app.state.now = clock
        if db is None:
            client, app.state.db = connect(settings)
        else:
            app.state.db = db
        app.state.auth = AuthService(app.state.db, settings, lambda: app.state.now())
        app.state.cipher = NoteCipher(settings.note_keys, settings.note_active_key, app.state.db)
        if chain is None:
            from webapp.blockchain import ChainService

            app.state.chain = ChainService(settings, app.state.db)
        else:
            app.state.chain = chain
        task = None
        if settings.mode != "test":

            async def index_loop():
                while True:
                    try:
                        await asyncio.to_thread(app.state.chain.sync)
                    except Exception:
                        # No raw RPC/request content, credentials or recovery tokens in logs.
                        log.warning(
                            "Blockchain index unavailable; reads will report unavailable status"
                        )
                    await asyncio.sleep(settings.index_interval)

            task = asyncio.create_task(index_loop())
        try:
            yield
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if client is not None:
                client.close()

    app = FastAPI(
        title="HyperVaults",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.mode != "production" else None,
    )
    app.state.settings = settings
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlparse(settings.origin).hostname])
    app.add_middleware(SecurityMiddleware, secure=settings.secure_cookie)
    app.include_router(auth_router)
    app.include_router(mfa_router)
    app.include_router(vault_router)

    @app.exception_handler(ChainUnavailable)
    async def chain_unavailable(request: Request, exc: ChainUnavailable):
        return JSONResponse({"detail": str(exc), "state": "unavailable"}, 503)

    @app.exception_handler(UnverifiedTransaction)
    async def unverified_transaction(request: Request, exc: UnverifiedTransaction):
        return JSONResponse({"detail": str(exc), "state": "unverified"}, 400)

    @app.exception_handler(NoteTampered)
    async def note_tampered(request: Request, exc: NoteTampered):
        return JSONResponse(
            {"detail": "Encrypted data integrity check failed. Decryption was refused."},
            409,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Pydantic's usual "input" field can echo submitted passwords/tokens.
        return JSONResponse(
            {
                "detail": "Invalid request data.",
                "fields": [list(e["loc"]) for e in exc.errors()],
            },
            422,
        )

    @app.exception_handler(PyMongoError)
    async def database_error(request: Request, exc: PyMongoError):
        return JSONResponse({"detail": "Database unavailable. Try again shortly."}, 503)

    @app.get("/healthz")
    def health(request: Request):
        request.app.state.db.command("ping")
        return {"ok": True}

    @app.get("/")
    def root():
        return RedirectResponse("/overview", status_code=303)

    for path in ["/login", "/register", "/recover", "/reset"]:
        app.add_api_route(
            path,
            lambda: FileResponse(ROOT / "webapp/templates/auth.html"),
            methods=["GET"],
            include_in_schema=False,
        )
    for path in ["/overview", "/vaults", "/positions", "/security", "/admin"]:
        app.add_api_route(
            path,
            lambda: FileResponse(ROOT / "webapp/templates/index.html"),
            methods=["GET"],
            include_in_schema=False,
        )
    app.mount("/static", StaticFiles(directory=ROOT / "webapp/static"), name="static")
    return app


app = create_app()
