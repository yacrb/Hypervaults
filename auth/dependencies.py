"""Request-scoped dependencies; application modules never import the app instance."""

import secrets

from fastapi import HTTPException, Request


def context(request: Request):
    return request.app.state.auth.resolve(request)


def current_user(request: Request) -> dict:
    _, user = context(request)
    if user is None:
        raise HTTPException(401, "Sign in to continue.")
    return user


def administrator(request: Request) -> dict:
    user = current_user(request)
    if user["role"] != "admin":
        raise HTTPException(403, "Application administrator permission required.")
    return user


def protect_mutations(request: Request) -> None:
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        settings = request.app.state.settings
        if request.headers.get("origin") != settings.origin:
            raise HTTPException(403, "Request origin is not allowed.")
        session, _ = context(request)
        token = request.headers.get("x-csrf-token", "")
        if len(token) > 128 or not secrets.compare_digest(token, session["csrf"]):
            raise HTTPException(403, "CSRF validation failed. Refresh the page and try again.")
