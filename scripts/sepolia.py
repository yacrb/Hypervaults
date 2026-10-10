"""Loopback-only, resumable Sepolia deployment signed by the browser wallet."""

import json
import re
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from web3 import Web3
from web3.exceptions import TransactionNotFound

from webapp.config import ROOT
from webapp.networks import SEPOLIA_CHAIN_ID, SEPOLIA_GENESIS, chain_identity

DEFAULT_RPC = 'https://ethereum-sepolia-rpc.publicnode.com'
STAGES = ['Deploy HVT', 'Deploy vault', 'Create 1-day offer', 'Create 7-day offer',
          'Create 30-day offer', 'Approve reward funding', 'Fund 10,000 HVT rewards']


class Deployment:
    def __init__(self, owner, rpc_url, compiled, private_write, manifest_builder):
        parsed = urlparse(rpc_url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Sepolia requires an HTTPS RPC URL without embedded login credentials')
        self.owner = Web3.to_checksum_address(owner)
        self.rpc_url, self.compiled = rpc_url, compiled
        self.web3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={'timeout': 10}))
        self.identity = chain_identity(self.web3, SEPOLIA_CHAIN_ID)
        if self.web3.eth.get_code(self.owner):
            raise ValueError('Use a dedicated EOA test wallet for deployment')
        self.write, self.manifest_builder = private_write, manifest_builder
        self.path = ROOT / '.local/sepolia-progress.json'
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {
            'owner': self.owner, 'genesis_hash': self.identity, 'transactions': [], 'complete': False,
        }
        if self.state['owner'].lower() != self.owner.lower() or self.state['genesis_hash'] != self.identity:
            raise ValueError('Existing Sepolia progress belongs to a different wallet/network. Preserve and inspect it before starting another deployment.')
        self.save()

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        self.write(temporary, json.dumps(self.state, indent=2) + '\n')
        temporary.replace(self.path)

    def contract(self, kind, address=None):
        artifact = self.compiled[kind]
        return self.web3.eth.contract(address=address, abi=artifact['abi'], bytecode=artifact['bytecode']['object'])

    def expected(self, index):
        tx = {'from': self.owner, 'value': '0x0', 'chainId': hex(SEPOLIA_CHAIN_ID)}
        if index == 0:
            tx['data'] = self.contract('token').constructor(self.owner).data_in_transaction
        elif index == 1:
            tx['data'] = self.contract('vault').constructor(self.state['token'], self.owner).data_in_transaction
        else:
            vault, token = self.contract('vault', self.state['vault']), self.contract('token', self.state['token'])
            if index < 5:
                days, rate = [(1, 300), (7, 500), (30, 800)][index - 2]
                call = vault.functions.createOffer(days * 86400, rate, 10 * 10**18, 1000 * 10**18, 10_000 * 10**18)
            elif index == 5:
                call = token.functions.approve(vault.address, 10_000 * 10**18)
            else:
                call = vault.functions.fundRewards(10_000 * 10**18)
            tx['to'] = call.address
            tx['data'] = call._encode_transaction_data()
        return tx

    def validate_transaction(self, index, tx):
        expected = self.expected(index)
        actual_to = (tx.get('to') or '').lower()
        if (tx['from'].lower() != self.owner.lower()
                or actual_to != expected.get('to', '').lower()
                or Web3.to_hex(tx['input']).lower() != expected['data'].lower()
                or tx.get('value', 0) != 0 or tx.get('chainId') != SEPOLIA_CHAIN_ID):
            raise ValueError('Transaction does not match the owner, network, or deployment step. No migration was activated.')

    def status(self):
        chain_identity(self.web3, SEPOLIA_CHAIN_ID)
        steps = []
        latest = self.web3.eth.block_number
        for index, tx_hash in enumerate(self.state['transactions']):
            try:
                receipt = self.web3.eth.get_transaction_receipt(tx_hash)
            except TransactionNotFound:
                return {'steps': steps, 'pending': tx_hash, 'index': index, 'label': STAGES[index], 'confirmations': 0}
            if receipt.status != 1:
                raise ValueError('A deployment transaction reverted. Inspect its receipt before continuing; it will not be resent automatically.')
            self.validate_transaction(index, self.web3.eth.get_transaction(tx_hash))
            if self.web3.eth.get_block(receipt.blockNumber).hash != receipt.blockHash:
                raise ValueError('Deployment receipt is no longer canonical. Inspect the transaction before continuing.')
            depth = latest - receipt.blockNumber + 1
            if depth < 6:
                return {'steps': steps, 'pending': tx_hash, 'index': index, 'label': STAGES[index], 'confirmations': max(depth, 0)}
            if index < 2:
                if not receipt.contractAddress:
                    raise ValueError('Contract creation did not return an address')
                kind = 'token' if index == 0 else 'vault'
                self.state[kind] = receipt.contractAddress
                if index == 1:
                    self.state['deployment_block'] = receipt.blockNumber
            steps.append({'label': STAGES[index], 'hash': tx_hash})
        self.save()
        index = len(steps)
        result = {'owner': self.owner, 'steps': steps, 'index': index, 'complete': self.state['complete'],
                  'ready': index == len(STAGES), 'chain_id': SEPOLIA_CHAIN_ID, 'genesis_hash': SEPOLIA_GENESIS}
        if index < len(STAGES):
            tx = self.expected(index)
            gas = self.web3.eth.estimate_gas(tx)
            result.update(label=STAGES[index], transaction=tx, gas=gas,
                          estimated_fee_eth=str(self.web3.from_wei(gas * self.web3.eth.gas_price, 'ether')))
        return result

    def record(self, index, tx_hash):
        if not isinstance(tx_hash, str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}', tx_hash):
            raise ValueError('Invalid transaction hash')
        transactions = self.state['transactions']
        if index < len(transactions) and transactions[index].lower() == tx_hash.lower():
            return  # Browser retries after a lost acknowledgement are idempotent.
        status = self.status()
        if status.get('pending') or status.get('ready') or status['index'] != index:
            raise ValueError('Wait for the current transaction before sending another')
        self.validate_transaction(index, self.web3.eth.get_transaction(tx_hash))
        transactions.append(tx_hash)
        self.save()

    def activate(self):
        if not self.status().get('ready'):
            raise ValueError('All deployment steps need six confirmations before activation')
        manifest = self.manifest_builder(self.web3, self.state['vault'], self.state['token'], self.state['deployment_block'])
        vault = self.contract('vault', self.state['vault'])
        if vault.functions.owner().call().lower() != self.owner.lower():
            raise ValueError('The contract owner does not match the selected wallet')
        self.write(ROOT / '.local/sepolia-deployment.json', json.dumps(manifest, indent=2) + '\n')
        self.write(ROOT / '.local/sepolia-rpc.txt', self.rpc_url + '\n')
        self.state['complete'] = True
        self.save()


def serve(deployment, port=8090):
    token = secrets.token_hex(32)
    origin = f'http://127.0.0.1:{port}'
    activated = False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not log setup tokens or RPC credentials.

        def reply(self, status, content, content_type='application/json'):
            payload = content if isinstance(content, bytes) else json.dumps(content).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; object-src 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def allowed(self, mutation=False):
            if self.headers.get('Host') != f'127.0.0.1:{port}':
                self.reply(403, {'detail': 'Host not allowed'})
                return False
            supplied = self.headers.get('X-Setup-Token', '')
            if not secrets.compare_digest(supplied, token) or (mutation and self.headers.get('Origin') != origin):
                self.reply(403, {'detail': 'Setup request not allowed'})
                return False
            return True

        def do_GET(self):
            if self.headers.get('Host') != f'127.0.0.1:{port}':
                return self.reply(403, {'detail': 'Host not allowed'})
            if self.path == '/':
                page = (ROOT / 'scripts/sepolia.html').read_text().replace('SETUP_TOKEN', token)
                return self.reply(200, page.encode(), 'text/html; charset=utf-8')
            if self.path == '/status':
                if not self.allowed():
                    return
                try:
                    return self.reply(200, deployment.status())
                except ValueError as error:
                    return self.reply(400, {'detail': str(error)})
                except Exception:
                    return self.reply(503, {'detail': 'Sepolia RPC unavailable. Keep this page open and retry; no transaction was sent by the server.'})
            files = {'/setup.js': ROOT / 'scripts/sepolia.js'}
            for name in ['js/wallet.js', 'js/api.js', 'vendor/ethers-6.17.0.min.js', 'style.css', 'icons.svg', 'favicon.svg', 'wallets/metamask.svg', 'wallets/rabby.svg']:
                files['/static/' + name] = ROOT / 'webapp/static' / name
            path = files.get(self.path.split('?')[0])
            if path:
                mime = 'text/javascript' if path.suffix == '.js' else 'text/css' if path.suffix == '.css' else 'image/svg+xml'
                return self.reply(200, path.read_bytes(), mime)
            self.reply(404, {'detail': 'Not found'})

        def do_POST(self):
            nonlocal activated
            if not self.allowed(mutation=True):
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 512 or self.headers.get('Content-Type') != 'application/json':
                    return self.reply(400, {'detail': 'Invalid setup request'})
                data = json.loads(self.rfile.read(length))
                if self.path == '/transaction':
                    deployment.record(data['index'], data['hash'])
                elif self.path == '/activate':
                    deployment.activate()
                    activated = True
                else:
                    return self.reply(404, {'detail': 'Not found'})
                self.reply(200, {'ok': True})
            except (ValueError, KeyError, TypeError):
                self.reply(400, {'detail': 'Setup step could not be verified. Refresh and inspect the recorded transaction.'})
            except Exception:
                self.reply(503, {'detail': 'Sepolia RPC unavailable. Retry this step; recorded transactions will not be resent.'})

    server = HTTPServer(('127.0.0.1', port), Handler)
    server.timeout = 1
    print(f'Sepolia setup ready: {origin}/', flush=True)
    print(f'Contract owner: {deployment.owner}. Approve each transaction in your browser wallet.', flush=True)
    try:
        while not activated:
            server.handle_request()
    finally:
        server.server_close()
    return activated
