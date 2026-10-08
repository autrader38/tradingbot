from dataclasses import FrozenInstanceError, fields, replace
from datetime import time, timedelta
from decimal import Decimal
from pathlib import Path
import re
import unittest

from tradingbot_backtest.config import FROZEN_V1, StrategyConfig
from tradingbot_backtest.codes import (
    ContextDataClassification, HistoryClassification, ReasonCode, RunStatus, SetupTermination,
)
from tradingbot_backtest.market import IntervalClassification
from tradingbot_backtest.states import StrategyState

ROOT = Path(__file__).resolve().parents[1]


class ConfigAndCodeTests(unittest.TestCase):
    def test_every_default_matches_canonical_document(self):
        rows = re.findall(r'^\| `([^`]+)` \| ([^|]+) \|',
                          (ROOT/'docs/strategy-parameters-v1.0.md').read_text(), re.M)
        self.assertEqual(len(rows), 64)
        self.assertEqual({key for key, _ in rows}, {f.name for f in fields(StrategyConfig)} | {"max_total_breakout_attempts"})
        for key, raw in rows:
            raw = raw.strip()
            if raw.endswith('%'):
                expected = Decimal(raw[:-1].replace('+', ''))
                if not key.endswith('_pct'):
                    expected /= 100
            elif raw.startswith('$'):
                expected = Decimal(raw[1:].replace(',', ''))
            elif raw.endswith(('x', 'R')):
                expected = Decimal(raw[:-1])
            elif raw == '04:00':
                expected = time(4, 0)
            elif raw.endswith((' minute', ' minutes')):
                expected = timedelta(minutes=int(raw.split()[0]))
            elif key == 'slippage_baseline':
                expected = Decimal(raw)
            else:
                expected = int(raw.replace('At least ', '').replace(',', ''))
            with self.subTest(parameter=key):
                self.assertEqual(getattr(FROZEN_V1, key), expected)
                self.assertIs(type(getattr(FROZEN_V1, key)), type(expected))

    def test_research_config_does_not_mutate_frozen_defaults(self):
        alternate = replace(FROZEN_V1, max_price_usd=Decimal('4'), max_failed_breakout_attempts=1)
        self.assertEqual(FROZEN_V1.max_price_usd, Decimal('5'))
        self.assertEqual(FROZEN_V1.max_total_breakout_attempts, 3)
        self.assertEqual(alternate.max_total_breakout_attempts, 2)
        with self.assertRaises(FrozenInstanceError):
            FROZEN_V1.max_price_usd = Decimal('4')
        with self.assertRaises(TypeError):
            replace(FROZEN_V1, risk_fraction_bod=0.01)
        with self.assertRaises(ValueError):
            replace(FROZEN_V1, max_price_usd=Decimal('NaN'))
        with self.assertRaises(TypeError):
            replace(FROZEN_V1, max_daily_entries=True)

    def test_all_requested_strategy_states(self):
        expected = 'INELIGIBLE ELIGIBLE A_CANDIDATE AB_IMPULSE PROVISIONAL_B B_CONFIRMED C_DEVELOPING C_LOCKED BREAKOUT_ATTEMPT D_CONFIRMED PENDING_ENTRY OPEN_POSITION PARTIAL_2R RUNNER CLOSED TERMINATED'.split()
        self.assertEqual([state.value for state in StrategyState], expected)

    def test_exact_approved_codes_no_invented_statuses(self):
        section = (ROOT/'docs/strategy-spec-v1.0.md').read_text().split('## 21. Reason codes and run outcomes')[1].split('## 22.')[0]
        approved = set(re.findall(r'`([A-Z][A-Z0-9_]+)`', section))
        represented = set().union(*({x.value for x in group} for group in (
            ReasonCode, RunStatus, IntervalClassification,
            ContextDataClassification, HistoryClassification,
        )))
        self.assertEqual(represented, approved)
        self.assertEqual({x.value for x in SetupTermination}, {'INVALIDATED', 'EXPIRED', 'CONSUMED'})
        self.assertEqual({x.value for x in RunStatus}, {'INCOMPLETE_UNRESOLVED_EOD_LIQUIDITY', 'INCOMPLETE_TICK_SIZE_UNAVAILABLE_OPEN_POSITION'})
