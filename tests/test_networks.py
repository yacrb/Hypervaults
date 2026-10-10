import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from webapp.config import Settings
from webapp.networks import SEPOLIA_GENESIS, chain_identity


class NetworkTests(unittest.TestCase):
    def test_sepolia_does_not_require_pruned_genesis(self):
        eth = SimpleNamespace(chain_id=11155111, get_block=Mock(side_effect=AssertionError))
        self.assertEqual(chain_identity(SimpleNamespace(eth=eth), 11155111), SEPOLIA_GENESIS)
        eth.get_block.assert_not_called()

    def test_local_identity_changes_after_reset(self):
        eth = SimpleNamespace(chain_id=31337, get_block=Mock(return_value=SimpleNamespace(hash=b'a' * 32)))
        first = chain_identity(SimpleNamespace(eth=eth), 31337)
        eth.get_block.return_value.hash = b'b' * 32
        self.assertNotEqual(first, chain_identity(SimpleNamespace(eth=eth), 31337))

    def test_wrong_network_and_mainnet_are_rejected(self):
        for actual, requested in [(1, 11155111), (1, 1), (31337, 11155111)]:
            with self.subTest(actual=actual, requested=requested), self.assertRaises(ValueError):
                chain_identity(SimpleNamespace(eth=SimpleNamespace(chain_id=actual)), requested)

    def test_sepolia_confirmation_policy(self):
        for depth in (1, 5):
            with self.subTest(depth=depth), self.assertRaises(ValueError):
                Settings(_env_file=None, chain_id=11155111, confirmations=depth)
        self.assertEqual(Settings(_env_file=None, chain_id=11155111, confirmations=6).confirmations, 6)


if __name__ == '__main__':
    unittest.main()
