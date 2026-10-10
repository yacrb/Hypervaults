import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from webapp.blockchain import ChainService, ChainUnavailable
from webapp.wallets import is_key_account

DELEGATION = b'\xef\x01\x00' + b'\x11' * 20
ADDRESS = '0x1111111111111111111111111111111111111111'


class WalletTests(unittest.TestCase):
    def test_only_empty_code_and_exact_delegation_indicators_are_supported(self):
        self.assertTrue(is_key_account(b''))
        self.assertTrue(is_key_account(DELEGATION))
        for code in (b'\x60\x00', DELEGATION[:-1], DELEGATION + b'\x00',
                     b'\xef\x01\x01' + b'\x11' * 20):
            with self.subTest(code=code):
                self.assertFalse(is_key_account(code))

    def test_wallet_type_is_rechecked_against_live_code(self):
        chain = object.__new__(ChainService)
        chain.validated_block = Mock(return_value={'number': 100})
        chain.ensure_canonical = Mock()
        code = Mock(return_value=DELEGATION)
        chain.web3 = SimpleNamespace(eth=SimpleNamespace(get_code=code))
        self.assertTrue(chain.is_eoa(ADDRESS))
        code.assert_called_with(ADDRESS, block_identifier='latest')
        code.return_value = b'\x60\x00'
        self.assertFalse(chain.is_eoa(ADDRESS))
        chain.ensure_canonical.assert_called_with({'number': 100})
        chain.ensure_canonical.side_effect = ChainUnavailable('reorg')
        with self.assertRaises(ChainUnavailable):
            chain.is_eoa(ADDRESS)
