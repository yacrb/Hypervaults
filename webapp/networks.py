"""Supported test networks; public RPCs may prune the genesis block."""

from web3 import Web3

SEPOLIA_CHAIN_ID = 11155111
# https://github.com/eth-clients/sepolia#meta-data-sepolia
SEPOLIA_GENESIS = "0x25a5cc106eea7138acab33231d7160d69cb777ee0c2c553fcddf5138993e6dd9"


def chain_identity(web3, chain_id):
    if chain_id not in {31337, SEPOLIA_CHAIN_ID} or web3.eth.chain_id != chain_id:
        raise ValueError("RPC must match the configured Anvil or Sepolia test network")
    if chain_id == SEPOLIA_CHAIN_ID:
        # Deployment-block and bytecode checks supply the live RPC binding.
        return SEPOLIA_GENESIS
    return Web3.to_hex(web3.eth.get_block(0).hash)
