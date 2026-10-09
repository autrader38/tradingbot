"""Opt-in local inspection only. Run after manually logging into PAPER Gateway."""

import os
import unittest

from tradingbot_broker.ibkr_readonly import ReadOnlyTWSTransport, TWSReadOnlyConfig
from tradingbot_broker.ibkr_readonly_broker import ReadOnlyIBKRBroker
from tradingbot_broker.readonly_models import AccountMode
from tradingbot_broker.models import ConnectionStatus


@unittest.skipUnless(os.environ.get('IBKR_RUN_READ_ONLY_TESTS') == '1', 'Local read-only IB Gateway test is explicitly opt-in')
class RealReadOnlyTest(unittest.TestCase):
    def test_local_read_batch(self):
        broker = ReadOnlyIBKRBroker(ReadOnlyTWSTransport(TWSReadOnlyConfig.from_environment()))
        self.addCleanup(broker.disconnect)
        self.assertEqual(broker.connect(), ConnectionStatus.CONNECTED)
        self.assertEqual(broker.account_mode, AccountMode.UNKNOWN)
        self.assertIsNone(broker.account_summary().mode)
        self.assertIsInstance(broker.working_orders(), tuple)
        self.assertIsInstance(broker.fills(), tuple)
        self.assertFalse(broker.controls.trading_enabled)
        # UNKNOWN is NOT successful independent PAPER qualification. No write test here.
