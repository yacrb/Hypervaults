import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from hexbytes import HexBytes
from web3.exceptions import TransactionNotFound

from scripts.sepolia import Deployment
from webapp.networks import SEPOLIA_GENESIS

OWNER = '0x1111111111111111111111111111111111111111'
TX_HASH = '0x' + 'a' * 64


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.plan = object.__new__(Deployment)
        self.plan.owner = OWNER
        self.plan.path = Path(self.folder.name) / 'progress.json'
        self.plan.state = {'owner': OWNER, 'genesis_hash': SEPOLIA_GENESIS, 'transactions': [], 'complete': False}
        self.eth = Mock(chain_id=11155111, block_number=106)
        self.plan.web3 = SimpleNamespace(eth=self.eth)
        self.plan.expected = Mock(return_value={'from': OWNER, 'data': '0x6000', 'value': '0x0', 'chainId': '0xaa36a7'})
        self.plan.write = lambda path, content: path.write_text(content)
        self.valid = {'from': OWNER, 'to': None, 'input': HexBytes('0x6000'), 'value': 0, 'chainId': 11155111}

    def test_transaction_must_match_owner_network_payload_and_value(self):
        self.plan.validate_transaction(0, self.valid)
        for key, value in [('from', '0x2222222222222222222222222222222222222222'),
                           ('to', OWNER), ('input', HexBytes('0x6001')), ('value', 1), ('chainId', 1)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.plan.validate_transaction(0, {**self.valid, key: value})

    def test_record_is_durable_and_duplicate_ack_is_idempotent(self):
        self.plan.status = Mock(return_value={'index': 0})
        self.eth.get_transaction.return_value = self.valid
        self.eth.get_transaction_receipt.side_effect = TransactionNotFound('pending')
        self.plan.record(0, TX_HASH)
        self.assertIn(TX_HASH, self.plan.path.read_text())
        self.plan.status.reset_mock()
        self.plan.record(0, TX_HASH)
        self.plan.status.assert_not_called()
        self.assertEqual(self.plan.state['transactions'], [TX_HASH])

    def test_confirmed_reverted_batch_is_retained_without_advancing(self):
        self.plan.status = Mock(return_value={'index': 3})
        self.eth.get_transaction.return_value = {**self.valid, 'to': OWNER, 'input': HexBytes('0x1234')}
        self.eth.get_transaction_receipt.return_value = SimpleNamespace(status=0, blockNumber=100, blockHash=HexBytes('0x1234'))
        self.eth.get_block.return_value = SimpleNamespace(hash=HexBytes('0x1234'))
        self.assertTrue(self.plan.record(3, TX_HASH))
        self.assertEqual(self.plan.state['transactions'], [])
        self.assertEqual(self.plan.state['failed_transactions'], [{'index': 3, 'hash': TX_HASH}])
        self.assertTrue(self.plan.record(3, TX_HASH))
        self.assertEqual(len(self.plan.state['failed_transactions']), 1)

    def test_revert_recovery_rejects_wrong_owner_network_reorg_or_shallow_receipt(self):
        self.plan.status = Mock(return_value={'index': 3})
        receipt = SimpleNamespace(status=0, blockNumber=100, blockHash=HexBytes('0x1234'))
        self.eth.get_transaction_receipt.return_value = receipt
        self.eth.get_block.return_value = SimpleNamespace(hash=HexBytes('0x1234'))
        for tx in ({**self.valid, 'from': '0x2222222222222222222222222222222222222222'},
                   {**self.valid, 'chainId': 1}, {**self.valid, 'value': 1}):
            self.eth.get_transaction.return_value = tx
            with self.assertRaises(ValueError):
                self.plan.record(3, TX_HASH)
        self.eth.get_transaction.return_value = self.valid
        self.eth.block_number = 104
        with self.assertRaises(ValueError):
            self.plan.record(3, TX_HASH)
        self.eth.block_number = 106
        self.eth.get_block.return_value.hash = HexBytes('0x5678')
        with self.assertRaises(ValueError):
            self.plan.record(3, TX_HASH)
        self.assertNotIn('failed_transactions', self.plan.state)

    def test_mismatched_successful_batch_cannot_advance(self):
        self.plan.status = Mock(return_value={'index': 3})
        self.eth.get_transaction.return_value = {**self.valid, 'to': OWNER, 'input': HexBytes('0x1234')}
        self.eth.get_transaction_receipt.return_value = SimpleNamespace(status=1)
        with self.assertRaises(ValueError):
            self.plan.record(3, TX_HASH)
        self.assertEqual(self.plan.state['transactions'], [])

    def test_invalid_step_is_rejected(self):
        for index in (-1, True, '3', 7):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.plan.record(index, TX_HASH)

    def test_pending_step_cannot_send_next_step(self):
        self.plan.status = Mock(return_value={'index': 0, 'pending': TX_HASH})
        with self.assertRaises(ValueError):
            self.plan.record(1, TX_HASH)
        self.assertEqual(self.plan.state['transactions'], [])

    def test_invalid_hash_is_not_saved(self):
        for value in ['abc', 123, '0x' + 'z' * 64]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.plan.record(0, value)
        self.assertFalse(self.plan.path.exists())

    def test_missing_receipt_remains_pending(self):
        self.plan.state['transactions'] = [TX_HASH]
        self.eth.get_transaction_receipt.side_effect = TransactionNotFound('pending')
        self.assertEqual(self.plan.status()['pending'], TX_HASH)
        self.eth.get_block.assert_not_called()

    def test_six_confirmations_are_required(self):
        self.plan.state['transactions'] = [TX_HASH]
        self.eth.block_number = 104
        self.eth.get_transaction_receipt.return_value = SimpleNamespace(status=1, blockNumber=100, blockHash=HexBytes('0x1234'))
        self.eth.get_block.return_value = SimpleNamespace(hash=HexBytes('0x1234'))
        self.eth.get_transaction.return_value = self.valid
        result = self.plan.status()
        self.assertEqual(result['confirmations'], 5)
        self.assertNotIn('token', self.plan.state)

    def test_reverted_or_reorged_receipt_is_rejected(self):
        self.plan.state['transactions'] = [TX_HASH]
        receipt = SimpleNamespace(status=0, blockNumber=100, blockHash=HexBytes('0x1234'))
        self.eth.get_transaction_receipt.return_value = receipt
        with self.assertRaises(ValueError):
            self.plan.status()
        receipt.status = 1
        self.eth.get_transaction.return_value = self.valid
        self.eth.get_block.return_value = SimpleNamespace(hash=HexBytes('0x5678'))
        with self.assertRaises(ValueError):
            self.plan.status()

    def test_confirmed_contract_address_supplies_next_step(self):
        self.plan.state['transactions'] = [TX_HASH]
        self.eth.get_transaction_receipt.return_value = SimpleNamespace(status=1, blockNumber=100, blockHash=HexBytes('0x1234'), contractAddress=OWNER)
        self.eth.get_transaction.return_value = self.valid
        self.eth.get_block.return_value = SimpleNamespace(hash=HexBytes('0x1234'))
        self.eth.estimate_gas.return_value = 21000
        self.eth.gas_price = 1
        self.plan.web3.from_wei = Mock(return_value=0)
        result = self.plan.status()
        self.assertEqual(result['index'], 1)
        self.assertEqual(self.plan.state['token'], OWNER)

    def test_activation_requires_all_confirmed_steps(self):
        self.plan.status = Mock(return_value={'pending': TX_HASH})
        self.plan.manifest_builder = Mock()
        with self.assertRaises(ValueError):
            self.plan.activate()
        self.plan.manifest_builder.assert_not_called()


if __name__ == '__main__':
    unittest.main()
