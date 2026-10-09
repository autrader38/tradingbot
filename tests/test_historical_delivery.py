"""Separate completion ingestion and replay preserve known OPEN economics."""

import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from fractions import Fraction as F

from tradingbot_backtest.entry_validation import EntryValidator
from tradingbot_backtest.market import IntervalClassification as K, MarketInterval
from tradingbot_backtest.portfolio import PortfolioEngine, PortfolioStatus as P
from tradingbot_backtest.position_management import PositionManager, ManagementStatus as M
from tests.entry_fixtures import handoff_fixture
from tests.historical_fixtures import Ticks, Calendar


class DeliveryTests(unittest.TestCase):
    def engine(self):
        handoff,context,calendar,_=handoff_fixture()
        approval=EntryValidator(calendar,run_id='delivery',data_version='verified').evaluate(handoff,context).approval
        engine=PortfolioEngine(calendar,Ticks(),run_id='delivery',data_version='verified')
        # Fixture ticks supply the same independently declared basis as approval.
        class BasisTicks(Ticks):
            def resolve(self,query):
                return replace(super().resolve(query),share_basis_id='fixture-basis')
        engine.ticks=BasisTicks()
        engine.start_session(approval.session.trading_date)
        bar=replace(handoff.interval,high=D('1.12'),low=D('1.105'),close=D('1.11'))
        engine.on_open(bar.timestamp,(bar,),(approval,))
        return engine,bar

    def missing(self,bar,kind=K.MISSING):
        return MarketInterval(bar.security_id,bar.ticker,bar.timestamp,kind,'source-quality',
                              data_quality_reason='late completed payload')

    def test_completion_fields_arrive_separately(self):
        engine,bar=self.engine()
        self.assertEqual(engine.state.cash,D(8890))
        complete=replace(bar,high=D('1.24'),low=D('1.105'))
        result=engine.on_close(bar.end,(complete,))
        self.assertEqual(result.state.cash,D(10060))
        self.assertEqual(len(engine.completed),1)
        self.assertEqual(engine.completed[0].net_pnl,D(60))

    def test_unavailable_completion_preserves_successful_entry(self):
        engine,bar=self.engine()
        result=engine.on_close(bar.end,(self.missing(bar),))
        self.assertTrue(result.requires_shared_pause)
        self.assertEqual(result.state.new_entries,1)
        self.assertEqual(result.state.cash,D(8890))
        self.assertEqual(result.state.positions[0].remaining_quantity,1000)

    def test_completion_replay_does_not_refill_entry(self):
        engine,bar=self.engine()
        engine.on_close(bar.end,(self.missing(bar),))
        result=engine.resume_with_replacements((bar,))
        self.assertEqual(result.state.status,P.ACTIVE)
        self.assertEqual(result.state.new_entries,1)
        self.assertEqual(result.state.cash,D(8890))
        self.assertEqual(result.state.positions[0].remaining_quantity,1000)
        times=[a.recorded_at for a in result.audit]
        self.assertTrue(all(at==bar.end for at in times))

    def target_open(self):
        engine,entry=self.engine()
        engine.on_close(entry.end,(entry,))
        bar=replace(entry,timestamp=entry.end,open=D('1.23'),high=D('1.25'),low=D('1.23'),close=D('1.24'))
        engine.on_open(bar.timestamp,(bar,))
        return engine,bar

    def test_open_partial_proceeds_not_reapplied_during_completion_replay(self):
        engine,bar=self.target_open()
        self.assertEqual(engine.state.cash,D(9505))
        self.assertEqual(engine.state.realized_net,D(60))
        engine.on_close(bar.end,(self.missing(bar),))
        result=engine.resume_with_replacements((bar,))
        self.assertEqual(result.state.cash,D(9505))
        self.assertEqual(result.state.realized_net,D(60))
        self.assertEqual(result.state.positions[0].remaining_quantity,500)

    def test_standalone_completion_replay_preserves_open_partial(self):
        engine,bar=self.target_open()
        manager=engine._managers['security']
        manager.complete_interval(self.missing(bar))
        result=manager.resume_with_replacement(bar)
        self.assertEqual(result.state.remaining_quantity,500)
        self.assertEqual(len(result.state.fills),1)
        self.assertEqual(result.exit_fills,())
        self.assertEqual(result.state.net_realized_pnl,D(60))

    def test_future_intervals_blocked_during_completion_pause(self):
        engine,bar=self.engine()
        engine.on_close(bar.end,(self.missing(bar),))
        with self.assertRaises(ValueError):
            engine.on_open(bar.end,(replace(bar,timestamp=bar.end),))

    def test_completion_replacement_cannot_revise_known_open(self):
        engine,bar=self.engine()
        engine.on_close(bar.end,(self.missing(bar),))
        before=engine.state
        with self.assertRaisesRegex(ValueError,'already-known'):
            engine.resume_with_replacements((replace(bar,open=D('1.115')),))
        self.assertEqual(engine.state,before)

    def test_completion_correction_rejected_before_account_effect(self):
        engine,bar=self.engine()
        before=engine.state
        with self.assertRaisesRegex(ValueError,'already-known'):
            engine.on_close(bar.end,(replace(bar,open=D('1.115')),))
        self.assertEqual(engine.state,before)

    def test_invalid_completion_cause_and_last_stop_retained(self):
        engine,bar=self.engine()
        r=engine.on_close(bar.end,(self.missing(bar,K.INVALID),))
        self.assertEqual(r.state.positions[0].active_stop,D('1.05'))
        self.assertEqual(r.state.positions[0].data_quality_reason,'late completed payload')
        self.assertEqual(r.state.positions[0].failure_reason.value,'INVALID_DATA')

    def test_mark_incomplete_does_not_fabricate_ending_equity(self):
        engine,bar=self.engine()
        engine.on_close(bar.end,(self.missing(bar),))
        r=engine.mark_data_incomplete()
        self.assertIsNone(r.state.ending_equity)
        self.assertEqual(r.state.positions[0].remaining_quantity,1000)

    def test_exact_fraction_split_volume_supported_without_price_fabrication(self):
        _,bar=self.engine()
        normalized=replace(bar,volume=F(1,3))
        self.assertEqual(normalized.volume,F(1,3))
        self.assertEqual((normalized.open,normalized.high,normalized.low,normalized.close),
                         (bar.open,bar.high,bar.low,bar.close))

    def test_negative_fraction_volume_rejected(self):
        _,bar=self.engine()
        with self.assertRaises(ValueError): replace(bar,volume=F(-1,3))

    def test_no_trade_still_has_no_synthetic_ohlc(self):
        _,bar=self.engine()
        with self.assertRaises(ValueError):
            replace(bar,classification=K.NO_TRADE,volume=D(0),no_trade_verified=True)

    def test_verified_no_trade_needs_no_invented_price_or_unit_transformation(self):
        from tradingbot_backtest.historical_data import Adjustment
        from tradingbot_backtest.historical_runner import HistoricalBacktest
        from tests.historical_fixtures import DAY, dataset, minute
        data=dataset()
        at=data.calendar.session_for(DAY).open
        source=minute('id-AAA','AAA',at,kind=K.NO_TRADE)
        source=replace(source,provenance=replace(source.provenance,
                       share_basis_id='unavailable-price-units',adjustment=Adjustment.UNKNOWN))
        bar,_=HistoricalBacktest(data)._minute({at:(source,)},'id-AAA','AAA',at,None,completed=True)
        self.assertEqual(bar.classification,K.NO_TRADE)
        self.assertEqual(bar.volume,D(0))
        self.assertIsNone(bar.open)
