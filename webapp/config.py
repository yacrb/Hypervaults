"""Trusted configuration; no wallet private keys belong in this application."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HV_", env_file=ROOT / ".env", extra="ignore", hide_input_in_errors=True
    )

    mode: Literal["development", "test", "production"] = "development"
    origin: str = "http://127.0.0.1:8088"
    mongo_uri: str = "mongodb://127.0.0.1:27018"
    mongo_database: str = "hypervaults"
    rpc_url: str = "http://127.0.0.1:8545"
    chain_id: int = 31337
    deployment_file: Path = ROOT / ".local/deployment.json"
    confirmations: int = Field(default=1, ge=1, le=64)
    index_interval: float = Field(default=3, ge=0.1, le=60)
    session_idle_seconds: int = Field(default=1800, ge=60)
    session_absolute_seconds: int = Field(default=28800, ge=60)
    recent_auth_seconds: int = Field(default=300, ge=30, le=900)
    challenge_seconds: int = Field(default=300, ge=30, le=600)
    reset_seconds: int = Field(default=900, ge=60, le=1800)
    note_keys: str = ""
    note_active_key: str = "v1"
    outbox_path: Path = ROOT / ".local/outbox"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = ""

    @model_validator(mode="after")
    def security_configuration(self):
        parsed = urlparse(self.origin)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("HV_ORIGIN must be an explicit HTTP(S) origin")
        if parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("HV_ORIGIN must contain only scheme, host and optional port")
        if self.mode in {"development", "test"}:
            if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("Development/test origins must be loopback")
        elif parsed.scheme != "https":
            raise ValueError("Production requires an HTTPS origin")
        if parsed.scheme == "http" and parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("Insecure cookies are limited to explicit localhost development")
        if self.chain_id not in {31337, 11155111}:
            raise ValueError("Only local Anvil or the documented Sepolia testnet is supported")
        if self.mode == "production" and not (self.smtp_host and self.mail_from):
            raise ValueError("Production password recovery requires SMTP and a sender")
        if self.mode == "production" and self.confirmations < 6:
            raise ValueError("Production/testnet mode requires at least six confirmations")
        if self.session_idle_seconds > self.session_absolute_seconds:
            raise ValueError("Idle lifetime cannot exceed absolute lifetime")
        return self

    @property
    def secure_cookie(self) -> bool:
        return urlparse(self.origin).scheme == "https" or self.mode == "production"

    @property
    def cookie_name(self) -> str:
        return "__Host-hv_session" if self.secure_cookie else "hv_session"

    @property
    def siwe_domain(self) -> str:
        return urlparse(self.origin).netloc
