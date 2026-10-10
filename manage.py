"""Start and manage the private local HyperVaults development stack."""

import argparse
import base64
import hashlib
import http.client
import io
import ipaddress
import json
import os
import platform
import secrets
import ssl
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pwdlib import PasswordHash
from pymongo.errors import DuplicateKeyError
from web3 import Web3

from auth.services import utcnow
from webapp.config import ROOT, Settings
from webapp.database import connect
from webapp.networks import chain_identity


def run(*args):
    subprocess.run(args, cwd=ROOT, check=True)  # noqa: S603 - fixed local commands, no shell


def private_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Keep both newly created and replaced secrets private before writing content.
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        os.chmod(path, 0o600)
        stream.write(content)


def install(name: str, spec: dict):
    destination = ROOT / spec["destination"]
    marker = destination / f".hypervaults-{name}.json"
    if marker.exists() and json.loads(marker.read_text()).get("sha256") == spec["sha256"]:
        return
    parsed = urlparse(spec["url"])
    if parsed.scheme != "https" or parsed.hostname not in {
        "github.com",
        "codeload.github.com",
        "registry.npmjs.org",
    }:
        raise RuntimeError("Dependency downloads must use pinned official HTTPS sources")
    request = urllib.request.Request(  # noqa: S310 - pinned HTTPS host validated above
        spec["url"], headers={"User-Agent": "HyperVaults-setup"}
    )  # noqa: S310 - validated above
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - pinned official HTTPS URLs
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != spec["sha256"]:
        raise RuntimeError(f"Checksum verification failed for {name}")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        if name.startswith("foundry"):
            for member in tar.getmembers():
                if Path(member.name).name in {"forge", "cast"}:
                    file = destination / Path(member.name).name
                    file.write_bytes(tar.extractfile(member).read())
                    file.chmod(0o755)
        else:
            prefix = tar.getmembers()[0].name.split("/")[0] + "/"
            for member in tar.getmembers():
                if member.name.startswith(prefix):
                    member.name = member.name[len(prefix) :]
                    if member.name:
                        tar.extract(member, destination, filter="data")
    marker.write_text(json.dumps({"sha256": spec["sha256"]}))
    print(f"Verified {name}")


def setup_dependencies():
    lock = json.loads((ROOT / "smartcontract/dependencies.json").read_text())
    system = platform.system().lower()
    machine = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64"}.get(platform.machine())
    platform_key = f"foundry-{system}-{machine}"
    if platform_key not in lock:
        raise SystemExit(
            "Setup supports macOS/Linux arm64/amd64. Use official Foundry installation on other platforms."
        )
    for name in ["openzeppelin", platform_key]:
        install(name, lock[name])


def artifacts():
    return {
        kind: json.loads((ROOT / f"smartcontract/out/{name}.sol/{name}.json").read_text())
        for kind, name in [("vault", "HyperVaults"), ("token", "TestToken")]
    }


def export():
    compiled = artifacts()
    output = ROOT / "webapp/contracts/abi.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "vault": compiled["vault"]["abi"],
                "token": compiled["token"]["abi"],
                "compiler": "0.8.30",
                "version": "0.1.0",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return compiled


def verify_runtime(web3, compiled, vault_address, token_address):
    """Verify source/compiler outputs, including the vault's immutable token slot."""
    for kind, address in [("vault", vault_address), ("token", token_address)]:
        artifact = compiled[kind]["deployedBytecode"]
        expected = bytearray(Web3.to_bytes(hexstr=artifact["object"]))
        references = artifact.get("immutableReferences", {})
        if kind == "vault":
            if len(references) != 1:
                raise RuntimeError("Review immutable verification after changing the contract")
            encoded_token = bytes.fromhex(token_address[2:]).rjust(32, b"\0")
            for reference in next(iter(references.values())):
                if reference["length"] != 32:
                    raise RuntimeError("Unexpected immutable token encoding")
                start = reference["start"]
                expected[start : start + 32] = encoded_token
        elif references:
            raise RuntimeError("Unexpected test-token immutable fields")
        if bytes(web3.eth.get_code(address)) != bytes(expected):
            raise RuntimeError(
                f"Deployed {kind} code does not match the pinned compiler/source output"
            )


def local_web3(settings=None):
    settings = settings or Settings()
    if settings.mode not in {"development", "test"} or settings.chain_id != 31337:
        raise RuntimeError(
            "Local development helpers are disabled outside Anvil development/testing"
        )
    if urlparse(settings.rpc_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Local helpers require an explicit loopback RPC")
    web3 = Web3(Web3.HTTPProvider(settings.rpc_url, request_kwargs={"timeout": 5}))
    if web3.eth.chain_id != 31337 or "anvil" not in web3.client_version.lower():
        raise RuntimeError("Refusing development operations on a non-Anvil chain")
    return web3


def successful_receipt(web3, tx_hash):
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
    if receipt.status != 1:
        raise RuntimeError("Local transaction reverted")
    return receipt


def deployment_manifest(web3, vault, token, block_number):
    compiled = artifacts()
    verify_runtime(web3, compiled, vault, token)
    block = web3.eth.get_block(block_number)
    return {
        "chain_id": web3.eth.chain_id,
        "network": "Anvil local" if web3.eth.chain_id == 31337 else "Sepolia testnet",
        "vault_address": vault,
        "token_address": token,
        "deployment_block": block_number,
        "deployment_block_hash": web3.to_hex(block.hash),
        "genesis_hash": chain_identity(web3, web3.eth.chain_id),
        "vault_code_hash": web3.to_hex(web3.keccak(web3.eth.get_code(vault))),
        "token_code_hash": web3.to_hex(web3.keccak(web3.eth.get_code(token))),
        "build_hashes": {
            kind: web3.to_hex(web3.keccak(hexstr=artifact["bytecode"]["object"]))
            for kind, artifact in compiled.items()
        },
    }


def deploy_local():
    web3 = local_web3()
    compiled = export()
    path = ROOT / ".local/deployment.json"
    if path.exists():
        existing = json.loads(path.read_text())
        verify_runtime(web3, compiled, existing["vault_address"], existing["token_address"])
        valid = (
            web3.to_hex(web3.eth.get_block(0).hash) == existing["genesis_hash"]
            and web3.to_hex(web3.keccak(web3.eth.get_code(existing["vault_address"])))
            == existing["vault_code_hash"]
            and web3.to_hex(web3.keccak(web3.eth.get_code(existing["token_address"])))
            == existing["token_code_hash"]
            and web3.to_hex(web3.eth.get_block(existing["deployment_block"]).hash)
            == existing["deployment_block_hash"]
        )
        if not valid:
            raise SystemExit(
                "Existing export does not match this chain. Preserve .local data, then explicitly rebuild deployment configuration."
            )
        print("Existing local deployment verified; no duplicate deployment or funding")
        return
    sponsor = web3.eth.accounts[0]
    token_factory = web3.eth.contract(
        abi=compiled["token"]["abi"], bytecode=compiled["token"]["bytecode"]["object"]
    )
    token_receipt = successful_receipt(
        web3, token_factory.constructor(sponsor).transact({"from": sponsor})
    )
    token = web3.eth.contract(address=token_receipt.contractAddress, abi=compiled["token"]["abi"])
    vault_factory = web3.eth.contract(
        abi=compiled["vault"]["abi"], bytecode=compiled["vault"]["bytecode"]["object"]
    )
    vault_receipt = successful_receipt(
        web3,
        vault_factory.constructor(token.address, sponsor).transact({"from": sponsor}),
    )
    vault = web3.eth.contract(address=vault_receipt.contractAddress, abi=compiled["vault"]["abi"])
    for days, rate in [(7, 300), (30, 500), (90, 800)]:
        successful_receipt(
            web3,
            vault.functions.createOffer(
                days * 86400, rate, 10 * 10**18, 1000 * 10**18, 10_000 * 10**18
            ).transact({"from": sponsor}),
        )
    successful_receipt(
        web3,
        token.functions.approve(vault.address, 10_000 * 10**18).transact({"from": sponsor}),
    )
    successful_receipt(
        web3, vault.functions.fundRewards(10_000 * 10**18).transact({"from": sponsor})
    )
    manifest = deployment_manifest(web3, vault.address, token.address, vault_receipt.blockNumber)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x") as stream:
        path.chmod(0o600)
        stream.write(json.dumps(manifest, indent=2) + "\n")
    print(f"Deployed local vault {vault.address}; three offers and 10,000 HVT sponsor reserve")


def seed_local():
    settings = Settings()
    if settings.mode != "development" or settings.chain_id != 31337:
        raise SystemExit("Public demo administrator is restricted to local Anvil development")
    if urlparse(settings.mongo_uri).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit("Public demo administrator requires an explicit loopback MongoDB")
    local_web3(settings)
    client, db = connect(settings)
    try:
        seed_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "hypervaults-local-administrator-v1"))
        email = "admin@hypervaults.com"
        existing = db.users.find_one({"_id": seed_id})
        if existing and existing.get("seed_marker") == "local-demo-v2":
            print("Local demo administrator already exists; account and sessions preserved")
            return
        if existing and (
            existing.get("seed_marker") != "local-v1"
            or existing.get("email") != "admin@example.com"
            or existing.get("role") != "admin"
            or existing.get("disabled")
        ):
            raise SystemExit("Existing seed account was customized; it will not be overwritten")
        if db.users.find_one({"email": email, "_id": {"$ne": seed_id}}):
            raise SystemExit(
                "Seed email already belongs to another account. It will not be promoted or merged."
            )
        # Public demo preset, permitted only by the local-development guards above.
        password = "admin"  # noqa: S105 - public, loopback-only development preset
        hashed = PasswordHash.recommended().hash(password)
        if existing:
            changed = db.users.update_one(
                {"_id": seed_id, "seed_marker": "local-v1", "auth_version": existing["auth_version"]},
                {
                    "$set": {"email": email, "password_hash": hashed, "seed_marker": "local-demo-v2"},
                    "$inc": {"auth_version": 1},
                },
            )
            if changed.modified_count != 1:
                raise SystemExit("Administrator changed during setup; rerun after reviewing it")
            for collection in ["sessions", "reset_tokens", "challenges"]:
                db[collection].delete_many({"user_id": seed_id})
        else:
            db.users.insert_one(
                {
                    "_id": seed_id,
                    "email": email,
                    "name": "Local administrator",
                    "password_hash": hashed,
                    "wallets": [],
                    "role": "admin",
                    "disabled": False,
                    "auth_version": 1,
                    "seed_marker": "local-demo-v2",
                    "created_at": utcnow(),
                }
            )
        file = ROOT / ".local/admin-credentials.txt"
        private_write(file, f"PUBLIC LOCAL DEMO ONLY\nEmail: {email}\nPassword: {password}\n")
        print(
            "Public local demo administrator ready; see the README or .local/admin-credentials.txt."
        )
    except DuplicateKeyError as exc:
        raise SystemExit("Seed identity already exists; no account will be promoted or merged") from exc
    finally:
        client.close()


def configure(https):
    local = ROOT / ".local"
    local.mkdir(exist_ok=True, mode=0o700)
    local.chmod(0o700)
    env = ROOT / ".env"
    if not env.exists():
        key_ring = json.dumps(
            {"v1": base64.b64encode(secrets.token_bytes(32)).decode()},
            separators=(",", ":"),
        )
        content = (
            (ROOT / ".env.example")
            .read_text()
            .replace('{"v1":"REPLACE_WITH_RANDOM_32_BYTE_BASE64_KEY"}', key_ring)
        )
        private_write(env, content + f"\nHV_UID={os.getuid()}\nHV_GID={os.getgid()}\n")
    settings = Settings()
    if settings.mode != "development" or settings.chain_id != 31337:
        raise SystemExit("manage.py is restricted to the local development configuration")
    lines = env.read_text().splitlines()
    origin = "https://localhost:8443" if https else "http://127.0.0.1:8088"
    lines = [f"HV_ORIGIN={origin}" if line.startswith("HV_ORIGIN=") else line for line in lines]
    if not any(line.startswith("HV_ORIGIN=") for line in lines):
        lines.append(f"HV_ORIGIN={origin}")
    private_write(env, "\n".join(lines) + "\n")
    return origin


def make_tls():
    folder = ROOT / ".local/tls"
    if (folder / "server.crt").exists():
        return
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    server_key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    ca_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "HyperVaults local development CA")]
    )
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=90))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.IPAddress(ipaddress.ip_address("::1")),
                ]
            ),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    for file, key in [("ca.key", ca_key), ("server.key", server_key)]:
        private_write(
            folder / file,
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode(),
        )
    for file, cert in [("ca.pem", ca), ("server.crt", certificate)]:
        private_write(folder / file, cert.public_bytes(serialization.Encoding.PEM).decode())
    print(
        "Local TLS certificates created. Trust .local/tls/ca.pem manually in your browser/OS before browser HTTPS testing."
    )


def compose(*args, https=False):
    command = ["docker", "compose"]
    if https:
        command += ["-f", "compose.yaml", "-f", "compose.https.yaml"]
    run(*command, *args)


def wait_for_application(origin, https):
    context = ssl.create_default_context(cafile=str(ROOT / ".local/tls/ca.pem")) if https else None
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
    )
    for _ in range(40):
        try:
            with opener.open(origin + "/healthz", timeout=2) as response:
                if response.status == 200:
                    return
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.HTTPException,
        ):
            pass
        time.sleep(0.5)
    raise SystemExit("Application did not become healthy. Inspect docker compose logs app.")


def local_operation(args):
    web3 = local_web3()
    if args.command == "advance":
        if not 1 <= args.seconds <= 365 * 86400:
            raise SystemExit("Time advance must be between 1 second and 365 days")
        response = web3.provider.make_request("evm_increaseTime", [args.seconds])
        if "error" in response:
            raise RuntimeError("Local time advance failed")
        web3.provider.make_request("evm_mine", [])
        print("Advanced the local chain only; refresh the dashboard")
    elif args.command == "fund-gas":
        address = Web3.to_checksum_address(args.address)
        successful_receipt(
            web3,
            web3.eth.send_transaction(
                {
                    "from": web3.eth.accounts[0],
                    "to": address,
                    "value": Web3.to_wei(1, "ether"),
                }
            ),
        )
        print("Funded 1 test ETH for local gas; this is not real ETH")
    else:
        manifest = json.loads((ROOT / ".local/deployment.json").read_text())
        abi = json.loads((ROOT / "webapp/contracts/abi.json").read_text())["vault"]
        vault = web3.eth.contract(address=manifest["vault_address"], abi=abi)
        if vault.functions.owner().call().lower() != web3.eth.accounts[0].lower():
            raise RuntimeError(
                "The initial local owner no longer controls the contract. Use the current owner's wallet."
            )
        successful_receipt(
            web3,
            vault.functions.transferOwnership(Web3.to_checksum_address(args.address)).transact(
                {"from": web3.eth.accounts[0]}
            ),
        )
        print(
            "Nominated the wallet. That wallet must sign acceptOwnership in the administrator screen."
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ["up", "down", "status"]:
        command = commands.add_parser(name)
        command.add_argument("--https", action="store_true", help="Use local HTTPS on port 8443")
    commands.add_parser("advance", help="Advance only the local Anvil clock").add_argument(
        "seconds", type=int
    )
    for name in ["fund-gas", "nominate-owner"]:
        commands.add_parser(name).add_argument("--address", required=True)
    args = parser.parse_args()
    if args.command == "up":
        origin = configure(args.https)
        setup_dependencies()
        compose("up", "-d", "--wait", "mongo", "anvil")
        run(str(ROOT / ".tools/bin/forge"), "build", "--root", "smartcontract")
        deploy_local()
        seed_local()
        if args.https:
            make_tls()
        compose("up", "-d", "--build", "--force-recreate", "app", https=args.https)
        wait_for_application(origin, args.https)
        print(f"HyperVaults ready: {origin}/login")
        print("Administrator credentials: .local/admin-credentials.txt")
    elif args.command == "down":
        compose("down", https=args.https)
        print("HyperVaults stopped. Local volumes, keys and credentials were preserved.")
    elif args.command == "status":
        compose("ps", https=args.https)
    else:
        local_operation(args)


if __name__ == "__main__":
    main()
