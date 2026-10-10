"""Read-only web3 integration and a canonical, rebuildable MongoDB event index.

There is intentionally no signing, private-key handling, transact(), RPC proxy,
faucet endpoint or time-travel endpoint in the web application.
"""

import functools
import json
import secrets
import threading
from datetime import timedelta

from eth_utils import event_abi_to_log_topic
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from web3 import HTTPProvider, Web3
from web3._utils.events import get_event_data
from web3.exceptions import TransactionNotFound

from auth.services import utcnow
from webapp.config import ROOT, Settings
from webapp.networks import chain_identity
from webapp.wallets import is_key_account


class ChainUnavailable(Exception):
    pass


class UnverifiedTransaction(Exception):
    pass


def rpc_read(function):
    @functools.wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ChainUnavailable, UnverifiedTransaction):
            raise
        except Exception as exc:
            raise ChainUnavailable(
                "Blockchain data unavailable. No balances or confirmations can be verified."
            ) from exc

    return guarded


def string_values(values: dict) -> dict:
    return {
        key: str(value) if isinstance(value, int) and not isinstance(value, bool) else value
        for key, value in values.items()
    }


class ChainService:
    def __init__(self, settings: Settings, db, provider=None):
        self.settings, self.db = settings, db
        self.web3 = Web3(provider or HTTPProvider(settings.rpc_url, request_kwargs={"timeout": 3}))
        self.manifest = None
        self.mutex = threading.RLock()
        self.lease_owner = secrets.token_hex(16)
        self.vault = self.token = None
        self.event_abis = {}
        try:
            self.manifest = json.loads(settings.deployment_file.read_text())
            abis = json.loads((ROOT / "webapp/contracts/abi.json").read_text())
            self.vault = self.web3.eth.contract(
                address=self.manifest["vault_address"], abi=abis["vault"]
            )
            self.token = self.web3.eth.contract(
                address=self.manifest["token_address"], abi=abis["token"]
            )
            for kind, contract in [("vault", self.vault), ("token", self.token)]:
                self.event_abis[kind] = {
                    Web3.to_hex(event_abi_to_log_topic(abi)): abi
                    for abi in contract.abi
                    if abi["type"] == "event"
                }
            self.deployment = f"{settings.chain_id}:{self.vault.address.lower()}:{self.manifest['deployment_block_hash']}"
        except (OSError, ValueError, KeyError):
            self.manifest = None

    def configuration(self):
        if self.manifest is None:
            raise ChainUnavailable(
                "Local contracts have not been deployed. Run the documented setup."
            )
        return {
            "chain_id": self.settings.chain_id,
            "network": self.manifest["network"],
            "token_address": self.token.address,
            "vault_address": self.vault.address,
            "token_symbol": "HVT",
            "token_decimals": 18,
            "confirmations": self.settings.confirmations,
            "explorer_url": "https://sepolia.etherscan.io"
            if self.settings.chain_id == 11155111
            else None,
            "vault_abi": self.vault.abi,
            "token_abi": self.token.abi,
            "mode": self.settings.mode,
            "origin": self.settings.origin,
            "genesis_hash": self.manifest["genesis_hash"],
            "deployment_block": self.manifest["deployment_block"],
            "deployment_block_hash": self.manifest["deployment_block_hash"],
            "vault_code_hash": self.manifest["vault_code_hash"],
            "token_code_hash": self.manifest["token_code_hash"],
        }

    def validated_block(self):
        if self.manifest is None:
            raise ChainUnavailable("Local deployment unavailable. Run setup before using a wallet.")
        if (
            self.web3.eth.chain_id != self.settings.chain_id
            or self.manifest["chain_id"] != self.settings.chain_id
        ):
            raise ChainUnavailable("RPC network does not match the configured test network.")
        if chain_identity(self.web3, self.settings.chain_id) != self.manifest["genesis_hash"]:
            raise ChainUnavailable(
                "The local chain was reset. Deployment and index must be explicitly rebuilt."
            )
        latest = self.web3.eth.block_number
        number = latest - self.settings.confirmations + 1
        if number < self.manifest["deployment_block"]:
            raise ChainUnavailable("Deployment is awaiting the configured confirmation depth.")
        deployment_block = self.web3.eth.get_block(self.manifest["deployment_block"])
        if Web3.to_hex(deployment_block.hash) != self.manifest["deployment_block_hash"]:
            raise ChainUnavailable(
                "Deployment block changed. Inspect the chain before rebuilding configuration."
            )
        block = self.web3.eth.get_block(number)
        for contract, field in [(self.vault, "vault_code_hash"), (self.token, "token_code_hash")]:
            code = self.web3.eth.get_code(contract.address, block_identifier=number)
            if not code or Web3.to_hex(Web3.keccak(code)) != self.manifest[field]:
                raise ChainUnavailable(
                    "Configured contract bytecode does not match the deployment export."
                )
        if (
            self.vault.functions.token().call(block_identifier=number).lower()
            != self.token.address.lower()
        ):
            raise ChainUnavailable("Vault token does not match the configured test token.")
        return {
            "number": number,
            "hash": Web3.to_hex(block.hash),
            "timestamp": block.timestamp,
            "latest": latest,
            "observed_at": utcnow().isoformat(),
        }

    def ensure_canonical(self, block):
        if Web3.to_hex(self.web3.eth.get_block(block["number"]).hash) != block["hash"]:
            raise ChainUnavailable(
                "Chain changed during this read. Refresh to verify a canonical snapshot."
            )

    @rpc_read
    def is_eoa(self, address: str) -> bool:
        """Allow key-controlled EOAs, including exact EIP-7702 delegations."""
        block = self.validated_block()
        result = is_key_account(self.web3.eth.get_code(
            Web3.to_checksum_address(address), block_identifier="latest"
        ))
        self.ensure_canonical(block)
        return result

    def decode_log(self, log, kind="vault"):
        contract = self.vault if kind == "vault" else self.token
        if log["address"].lower() != contract.address.lower() or not log["topics"]:
            return None
        abi = self.event_abis[kind].get(Web3.to_hex(log["topics"][0]))
        if abi is None:
            return None
        decoded = get_event_data(self.web3.codec, abi, log)
        args = string_values(dict(decoded["args"]))
        return {
            "deployment": self.deployment,
            "kind": abi["name"],
            "args": args,
            "owner": str(args.get("owner", args.get("sponsor", ""))).lower(),
            "tx_hash": Web3.to_hex(log["transactionHash"]),
            "block_hash": Web3.to_hex(log["blockHash"]),
            "block_number": log["blockNumber"],
            "log_index": log["logIndex"],
        }

    def save_event(self, event):
        event_id = (
            f"{event['deployment']}:{event['tx_hash']}:{event['log_index']}:{event['block_hash']}"
        )
        self.db.chain_events.update_one({"_id": event_id}, {"$setOnInsert": event}, upsert=True)

    @rpc_read
    def sync(self, rebuild=False):
        with self.mutex:
            block = self.validated_block()
            now = utcnow()
            try:
                lease = self.db.index_leases.find_one_and_update(
                    {
                        "_id": self.deployment,
                        "$or": [{"expires_at": {"$lte": now}}, {"owner": self.lease_owner}],
                    },
                    {
                        "$set": {
                            "owner": self.lease_owner,
                            "expires_at": now + timedelta(seconds=30),
                        }
                    },
                    upsert=True,
                    return_document=ReturnDocument.AFTER,
                )
            except DuplicateKeyError:
                checkpoint = self.db.chain_checkpoints.find_one({"_id": self.deployment})
                if (
                    checkpoint
                    and checkpoint["block_number"] == block["number"]
                    and checkpoint["block_hash"] == block["hash"]
                ):
                    return block
                raise ChainUnavailable(
                    "The blockchain index is catching up. Refresh shortly."
                ) from None
            try:
                checkpoint = self.db.chain_checkpoints.find_one({"_id": self.deployment})
                if checkpoint and (
                    checkpoint["block_number"] > block["number"]
                    or Web3.to_hex(self.web3.eth.get_block(checkpoint["block_number"]).hash)
                    != checkpoint["block_hash"]
                ):
                    rebuild = True
                if rebuild:
                    self.db.chain_events.delete_many({"deployment": self.deployment})
                    self.db.chain_checkpoints.delete_one({"_id": self.deployment})
                    checkpoint = None
                start = (
                    checkpoint["block_number"] + 1
                    if checkpoint
                    else self.manifest["deployment_block"]
                )
                # Remove any partial batch left by a crash or interrupted canonicality check.
                self.db.chain_events.delete_many(
                    {"deployment": self.deployment, "block_number": {"$gte": start}}
                )
                for first in range(start, block["number"] + 1, 2000):
                    last = min(first + 1999, block["number"])
                    logs = self.web3.eth.get_logs(
                        {"address": self.vault.address, "fromBlock": first, "toBlock": last}
                    )
                    for entry in logs:
                        event = self.decode_log(entry)
                        if event:
                            self.save_event(event)
                    renewed = self.db.index_leases.update_one(
                        {
                            "_id": self.deployment,
                            "owner": lease["owner"],
                            "expires_at": {"$gt": utcnow()},
                        },
                        {"$set": {"expires_at": utcnow() + timedelta(seconds=30)}},
                    )
                    if not renewed.matched_count:
                        raise ChainUnavailable(
                            "Indexer lease expired. Retry the canonical rebuild."
                        )
                self.ensure_canonical(block)
                self.db.chain_checkpoints.replace_one(
                    {"_id": self.deployment},
                    {
                        "_id": self.deployment,
                        "block_number": block["number"],
                        "block_hash": block["hash"],
                        "updated_at": utcnow(),
                    },
                    upsert=True,
                )
                return block
            finally:
                self.db.index_leases.delete_one({"_id": self.deployment, "owner": self.lease_owner})

    def position_at(self, position_id: int, block: dict):
        values = self.vault.functions.positions(position_id).call(block_identifier=block["number"])
        if values[0] == "0x0000000000000000000000000000000000000000":
            return None
        owner, principal, reward, offer, duration, apr, deposited, maturity, settled = values
        return {
            "id": str(position_id),
            "owner": owner.lower(),
            "principal": str(principal),
            "reward": str(reward),
            "total": str(principal + reward),
            "offer_id": str(offer),
            "duration": duration,
            "apr_bps": apr,
            "deposited_at": deposited,
            "matures_at": maturity,
            "settled": settled,
            "mature": block["timestamp"] >= maturity,
        }

    @rpc_read
    def position(self, position_id: int):
        block = self.validated_block()
        result = self.position_at(position_id, block)
        self.ensure_canonical(block)
        return result

    def offers_at(self, block):
        count = self.vault.functions.offerCount().call(block_identifier=block["number"])
        if count > 1000:
            raise ChainUnavailable("Offer count exceeds this application's supported bounds.")
        result = []
        for id_ in range(1, count + 1):
            duration, apr, minimum, maximum, capacity, used, opened = self.vault.functions.offers(
                id_
            ).call(block_identifier=block["number"])
            result.append(
                {
                    "id": str(id_),
                    "duration": duration,
                    "apr_bps": apr,
                    "minimum": str(minimum),
                    "maximum": str(maximum),
                    "capacity": str(capacity),
                    "used_capacity": str(used),
                    "remaining_capacity": str(capacity - used),
                    "open": opened,
                }
            )
        return result

    @rpc_read
    def snapshot(self, wallets: list[str], administration=False):
        block = self.sync()
        bn = block["number"]
        offers = self.offers_at(block)
        positions, balances = [], {}
        total_count = 0
        for wallet in wallets:
            address = Web3.to_checksum_address(wallet)
            balances[wallet] = str(
                self.token.functions.balanceOf(address).call(block_identifier=bn)
            )
            count = self.vault.functions.ownerPositionCount(address).call(block_identifier=bn)
            total_count += count
            if total_count > 1000:
                raise ChainUnavailable(
                    "This account exceeds the documented 1000-position read limit. Use direct contract pagination."
                )
            for offset in range(0, count, 100):
                ids = self.vault.functions.ownerPositionIds(address, offset, 100).call(
                    block_identifier=bn
                )
                positions.extend(self.position_at(id_, block) for id_ in ids)
        positions.sort(key=lambda p: int(p["id"]), reverse=True)
        principal = sum(int(p["principal"]) for p in positions if not p["settled"])
        reward = sum(int(p["reward"]) for p in positions if not p["settled"])
        outstanding = self.vault.functions.outstandingPrincipal().call(block_identifier=bn)
        committed = self.vault.functions.committedRewards().call(block_identifier=bn)
        balance = self.token.functions.balanceOf(self.vault.address).call(block_identifier=bn)
        if balance < outstanding + committed:
            raise ChainUnavailable(
                "Contract balance does not cover liabilities. Financial actions are unavailable."
            )
        reserve = balance - outstanding - committed
        result = {
            "block": block,
            "offers": offers,
            "positions": positions,
            "wallet_balances": balances,
            "totals": {
                "principal": str(principal),
                "rewards": str(reward),
                "at_maturity": str(principal + reward),
            },
            "reserves": {
                "balance": str(balance),
                "principal": str(outstanding),
                "committed_rewards": str(committed),
                "liabilities": str(outstanding + committed),
                "uncommitted": str(reserve),
                "covered": True,
            },
            "paused": self.vault.functions.depositsPaused().call(block_identifier=bn),
            "contract_owner": self.vault.functions.owner().call(block_identifier=bn).lower(),
            "pending_owner": self.vault.functions.pendingOwner().call(block_identifier=bn).lower(),
        }
        event_query = {
            "deployment": self.deployment,
            "owner": {"$in": wallets},
            "block_number": {"$lte": bn},
        }
        result["activity"] = list(
            self.db.chain_events.find(event_query, {"_id": 0})
            .sort([("block_number", -1), ("log_index", -1)])
            .limit(20)
        )
        if administration:
            count = self.vault.functions.nextPositionId().call(block_identifier=bn) - 1
            if count > 2000:
                raise ChainUnavailable(
                    "Administrative liability reads exceed the documented 2000-position project limit."
                )
            all_positions = [self.position_at(id_, block) for id_ in range(1, count + 1)]
            result["liabilities"] = sorted(
                [p for p in all_positions if not p["settled"]], key=lambda p: p["matures_at"]
            )
            result["contract_events"] = list(
                self.db.chain_events.find(
                    {"deployment": self.deployment, "block_number": {"$lte": bn}}, {"_id": 0}
                )
                .sort([("block_number", -1), ("log_index", -1)])
                .limit(50)
            )
        self.ensure_canonical(block)
        return result

    @rpc_read
    def quote(self, offer_id: int, amount: int):
        block = self.validated_block()
        offers = self.offers_at(block)
        offer = next((o for o in offers if int(o["id"]) == offer_id), None)
        if offer is None:
            raise UnverifiedTransaction("Offer unavailable.")
        if not 0 < amount <= 2**128 - 1:
            raise UnverifiedTransaction("Amount exceeds the supported bounds.")
        reward = amount * offer["apr_bps"] * offer["duration"] // (10_000 * 365 * 86400)
        reserve = self.vault.functions.availableRewardReserve().call(
            block_identifier=block["number"]
        )
        paused = self.vault.functions.depositsPaused().call(block_identifier=block["number"])
        reason = None
        if paused:
            reason = "New deposits are paused. Matured withdrawals remain available."
        elif not offer["open"]:
            reason = "This offer is closed."
        elif not int(offer["minimum"]) <= amount <= int(offer["maximum"]):
            reason = "Amount is outside the per-position limits."
        elif amount > int(offer["remaining_capacity"]):
            reason = "This offer has insufficient remaining capacity."
        elif reward > reserve:
            reason = "The sponsor reserve cannot fund this reward."
        self.ensure_canonical(block)
        return {
            "offer": offer,
            "amount": str(amount),
            "reward": str(reward),
            "total": str(amount + reward),
            "estimated_maturity": block["timestamp"] + offer["duration"],
            "block": block,
            "can_deposit": reason is None,
            "reason": reason,
        }

    @rpc_read
    def verify_transaction(self, tx_hash: str, kind: str, wallets: list[str]):
        self.validated_block()
        try:
            receipt = self.web3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            return {"state": "pending", "tx_hash": tx_hash, "confirmations": 0}
        if Web3.to_hex(receipt["transactionHash"]).lower() != tx_hash.lower():
            raise UnverifiedTransaction("Receipt hash does not match the submitted hash.")
        transaction = self.web3.eth.get_transaction(tx_hash)
        sender = transaction["from"].lower()
        if sender not in wallets or transaction.get("chainId") != self.settings.chain_id:
            raise UnverifiedTransaction(
                "Transaction signer or chain is outside this account's verified context."
            )
        source = "token" if kind in {"approve", "faucet"} else "vault"
        contract = self.token if source == "token" else self.vault
        if not transaction.get("to") or transaction["to"].lower() != contract.address.lower():
            raise UnverifiedTransaction("Transaction target does not match the expected contract.")
        canonical = self.web3.eth.get_block(receipt["blockNumber"])
        if canonical.hash != receipt["blockHash"]:
            raise UnverifiedTransaction("Receipt is no longer in the canonical chain.")
        if receipt["status"] != 1:
            return {"state": "reverted", "tx_hash": tx_hash}
        confirmations = self.web3.eth.block_number - receipt["blockNumber"] + 1
        if confirmations < self.settings.confirmations:
            return {"state": "pending", "tx_hash": tx_hash, "confirmations": confirmations}
        expected = {
            "deposit": {"PositionOpened"},
            "withdraw": {"PositionSettled"},
            "fund": {"RewardsFunded"},
            "offer": {"OfferCreated", "OfferUpdated"},
            "availability": {"OfferAvailabilityChanged"},
            "pause": {"DepositPauseChanged"},
            "approve": {"Approval"},
            "faucet": {"FaucetClaimed"},
            "ownership": {"OwnershipTransferred", "OwnershipTransferStarted"},
        }[kind]
        events = [self.decode_log(entry, source) for entry in receipt["logs"]]
        events = [event for event in events if event and event["kind"] in expected]
        if not events:
            raise UnverifiedTransaction(
                "No matching event from the expected contract was verified."
            )
        for event in events:
            args = event["args"]
            if kind in {"deposit", "withdraw", "approve"} and args["owner"].lower() != sender:
                raise UnverifiedTransaction("Event owner does not match the transaction signer.")
            if kind == "approve" and args["spender"].lower() != self.vault.address.lower():
                raise UnverifiedTransaction("Approval does not name the vault as spender.")
            if kind == "faucet" and args["account"].lower() != sender:
                raise UnverifiedTransaction("Faucet recipient does not match the signer.")
            if kind == "fund" and args["sponsor"].lower() != sender:
                raise UnverifiedTransaction("Funding sponsor does not match the signer.")
        # Re-check after decoding. A browser hash alone never creates a financial record.
        if self.web3.eth.get_block(receipt["blockNumber"]).hash != receipt["blockHash"]:
            raise UnverifiedTransaction("Chain changed during receipt verification.")
        if source == "vault":
            self.sync()
        return {
            "state": "confirmed",
            "tx_hash": tx_hash,
            "confirmations": confirmations,
            "block_number": receipt["blockNumber"],
            "events": events,
        }
