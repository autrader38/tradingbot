"""Independent statistics with exact dollars and confirmed equity endpoints."""

import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D, localcontext
from fractions import Fraction as F
import json

from tradingbot_backtest.config import FROZEN_V1
from tradingbot_backtest.historical_results import EquityPoint, manifest, summarize
from tradingbot_backtest.historical_runner import HistoricalBacktest
from tests.historical_fixtures import DAY, dataset, with_setup


class ResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=dataset()
        cls.win=HistoricalBacktest(with_setup(cls.data)).run(DAY,DAY)
        cls.loss=HistoricalBacktest(with_setup(cls.data,outcome='loss')).run(DAY,DAY)

    def test_r_uses_original_total_risk(self):
        self.assertEqual(self.win.trades[0].realized_r,F(1))
        self.assertEqual(self.loss.trades[0].realized_r,F(-1))

    def test_one_winner_exact_metrics(self):
        s=self.win.summary
        self.assertEqual(s.total_net_pnl,D(60))
        self.assertEqual(s.total_return,F(3,500))
        self.assertEqual(s.win_rate,F(1))
        self.assertEqual(s.average_win,F(60))
        self.assertIsNone(s.average_loss)
        self.assertIsNone(s.profit_factor)

    def test_one_loss_exact_metrics(self):
        s=self.loss.summary
        self.assertEqual(s.win_rate,F(0)); self.assertEqual(s.average_loss,F(-60))
        self.assertEqual(s.profit_factor,F(0))
        self.assertEqual(s.maximum_drawdown_confirmed_path,F(3,500))

    def test_mixed_profit_factor_and_average(self):
        result=summarize(self.win.sessions+self.loss.sessions,self.win.trades+self.loss.trades,(),FROZEN_V1,False)
        self.assertEqual(result.win_rate,F(1,2)); self.assertEqual(result.profit_factor,F(1))
        self.assertEqual(result.average_trade_net,F(0))
        self.assertEqual(result.largest_win,D(60)); self.assertEqual(result.largest_loss,D(-60))

    def test_drawdown_uses_confirmed_chronology(self):
        at=self.data.calendar.session_for(DAY).open
        points=tuple(EquityPoint(at+timedelta(minutes=i),D(value),'CONFIRMED')
                     for i,value in enumerate(('10000','11000','9900','10500','12000','11800')))
        result=summarize((),(),points,FROZEN_V1,False)
        self.assertEqual(result.maximum_drawdown_confirmed_path,F(1,10))

    def test_no_trade_undefined_statistics_not_infinity(self):
        s=summarize((),(),(),FROZEN_V1,False)
        self.assertEqual(s.ending_confirmed_equity,D(10000)); self.assertEqual(s.total_net_pnl,D(0))
        self.assertIsNone(s.win_rate); self.assertIsNone(s.profit_factor); self.assertIsNone(s.average_trade_net)

    def test_incomplete_omits_fabricated_final_return(self):
        s=summarize(self.win.sessions,self.win.trades,self.win.equity_chronology,FROZEN_V1,True)
        self.assertEqual(s.status,'BACKTEST INCOMPLETE')
        self.assertIsNone(s.ending_confirmed_equity); self.assertIsNone(s.total_return)
        self.assertEqual(s.completed_trades,1)

    def test_json_includes_required_research_disclosures(self):
        output=json.loads(self.win.to_json())
        self.assertIn('ZERO-FRICTION BASELINE',output['disclosures'])
        self.assertIn('FULL-FILL RESEARCH ASSUMPTION — MARKET DEPTH AND PARTIAL FILLS NOT MODELED',output['disclosures'])

    def test_manifest_preserves_version_and_security_scope(self):
        m=manifest(self.data,DAY,DAY,FROZEN_V1,'reviewed-code')
        self.assertEqual(m.strategy_version,'1.0')
        self.assertEqual(m.code_version,'reviewed-code')
        self.assertEqual(m.security_ids,('id-AAA',))
        self.assertEqual(m.contract_version,'historical-contract-1')
        self.assertEqual(len(m.dataset_sha256),64)

    def test_manifest_records_future_input_without_using_it_for_decisions(self):
        m=manifest(self.data,DAY,DAY,FROZEN_V1,None)
        changed=replace(self.data,source_version='corrected-source-v2')
        self.assertNotEqual(m.dataset_sha256,manifest(changed,DAY,DAY,FROZEN_V1,None).dataset_sha256)

    def test_serialization_preserves_exact_nonterminating_ratios(self):
        from tradingbot_backtest.historical_results import serialized
        self.assertEqual(serialized(F(1,3)),'{"denominator":3,"numerator":1}')

    def test_summary_does_not_depend_on_decimal_context(self):
        with localcontext() as ctx:
            ctx.prec=2
            summary=summarize(self.win.sessions,self.win.trades,self.win.equity_chronology,FROZEN_V1,False)
        self.assertEqual(summary,self.win.summary)
