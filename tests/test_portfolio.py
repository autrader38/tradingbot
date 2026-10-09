"""Shared-account expectations calculated independently from supplied fills."""

from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal as D, localcontext
from fractions import Fraction
import unittest

from tradingbot_backtest.codes import RunStatus
from tradingbot_backtest.config import FROZEN_V1
from tradingbot_backtest.entry_validation import EntryValidator
from tradingbot_backtest.market import IntervalClassification as K, MarketInterval
from tradingbot_backtest.portfolio import LockoutReason as L, PortfolioEngine, PortfolioStatus as P, rank_candidates
from tradingbot_backtest.sessions import NEW_YORK
from tradingbot_backtest.trade_construction import TickPurpose, TickRule
from tests.entry_fixtures import Calendar, handoff_fixture


class Ticks:
    def __init__(self, at):
        self.at, self.calls, self.unavailable = at, [], set()

    def resolve(self, query):
        self.calls.append(query)
        if (query.security_id, query.purpose) in self.unavailable:
            return None
        return TickRule(query.security_id, 'fixture-basis', query.purpose, D('.01'), D(0), D(0), None,
                        self.at-timedelta(days=1), None, self.at, 'verified-portfolio-fixture-ticks',
                        'fixture historical execution grid', True)


def remap(value, security, ticker, delta):
    if isinstance(value, datetime):
        return value+delta
    if is_dataclass(value):
        return replace(value, **{field.name: remap(getattr(value, field.name), security, ticker, delta)
                                 for field in fields(value)})
    if isinstance(value, tuple):
        return tuple(remap(item, security, ticker, delta) for item in value)
    if value == 'security':
        return security
    if value == 'TEST':
        return ticker
    return value


class PortfolioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handoff, context, calendar, _ = handoff_fixture()
        cls.template = EntryValidator(calendar, run_id='phase8', data_version='fixture').evaluate(handoff, context).approval

    def engine(self, config=FROZEN_V1, close_hour=16):
        calendar = Calendar(close_hour=close_hour)
        ticks = Ticks(self.template.session.open)
        engine = PortfolioEngine(calendar, ticks, run_id='phase8', data_version='fixture', config=config)
        engine.start_session(self.template.session.trading_date)
        return engine, ticks

    def approval(self, engine, ticker='AAA', at=None, ratio=Fraction(2), setup=None):
        at = at or self.template.scheduled_timestamp
        security = 'security-'+ticker
        approval = remap(self.template, security, ticker, at-self.template.scheduled_timestamp)
        volume = D(ratio.numerator)*D(105)/D(ratio.denominator)
        d = replace(approval.d, volume=volume)
        evidence = replace(approval.d_attempt.previous_20, candle=d, baseline=Fraction(105), ratio=ratio)
        return replace(approval, setup_id=setup or f'{ticker}-{at.isoformat()}', session=engine.state.session,
                       d=d, d_attempt=replace(approval.d_attempt, candle=d, previous_20=evidence))

    def bar(self, at, ticker='AAA', opening='1.11', high=None, low=None, close=None, kind=K.TRADED, cause=None):
        values = dict(security_id='security-'+ticker, ticker=ticker, timestamp=at, classification=kind,
                      source_id='portfolio-minute-fixture')
        if kind == K.TRADED:
            price = D(opening)
            values.update(open=price, high=D(high) if high else price, low=D(low) if low else price,
                          close=D(close) if close else price, volume=D(100))
        elif kind == K.NO_TRADE:
            values.update(volume=D(0), no_trade_verified=True)
        else:
            values.update(data_quality_reason=cause or 'explicit missing source interval')
        return MarketInterval(**values)

    def enter(self, engine, ticker='AAA', at=None):
        approval = self.approval(engine, ticker, at)
        bars = tuple(self.bar(approval.scheduled_timestamp, position.entry.approval.ticker)
                     for position in engine.state.positions)
        result = engine.on_open(approval.scheduled_timestamp, bars+(self.bar(approval.scheduled_timestamp, ticker),), (approval,))
        self.assertTrue(result.candidates[0].accepted)
        engine.on_close(approval.scheduled_timestamp+timedelta(minutes=1))
        return approval

    def opening(self, engine, prices=None, kinds=None, candidates=(), extra=()):
        at = engine._next
        prices, kinds = prices or {}, kinds or {}
        bars = tuple(self.bar(at, position.entry.approval.ticker,
                             opening=prices.get(position.entry.approval.ticker, '1.11'),
                             kind=kinds.get(position.entry.approval.ticker, K.TRADED))
                     for position in engine.state.positions)
        return engine.on_open(at, bars+extra, candidates)

    def complete(self, engine):
        return engine.on_close(engine._pending[0]+timedelta(minutes=1))

    def lose(self, engine, ticker, price='1.05', at=None):
        self.enter(engine, ticker, at)
        result = self.opening(engine, {ticker: price})
        self.complete(engine)
        return result

    def win(self, engine, ticker, at=None):
        self.enter(engine, ticker, at)
        self.opening(engine, {ticker: '1.23'})
        self.complete(engine)
        result = self.opening(engine, {ticker: '1.11'})
        self.complete(engine)
        return result

    def advance(self, engine, until):
        while engine._next < until:
            self.opening(engine, kinds={p.entry.approval.ticker: K.NO_TRADE for p in engine.state.positions})
            self.complete(engine)

    def test_session_starts_with_frozen_10000_equity_and_zero_counters(self):
        engine, _ = self.engine()
        self.assertEqual((engine.state.bod_equity, engine.state.cash, engine.state.current_equity), (D(10000),)*3)
        self.assertEqual((engine.state.new_entries, engine.state.realized_net, engine.state.consecutive_losses), (0,D(0),0))
        self.assertFalse(engine.state.locked)

    def test_verified_holiday_cannot_start_session(self):
        day = self.template.session.trading_date
        engine = PortfolioEngine(Calendar(holidays=(day,)), Ticks(self.template.session.open), run_id='x', data_version='f')
        with self.assertRaisesRegex(ValueError, 'non-session'): engine.start_session(day)

    def test_normal_close_ending_cash_becomes_next_bod_equity(self):
        engine, _ = self.engine()
        self.lose(engine, 'AAA')
        engine.close_session(engine.state.session.close)
        self.assertEqual(engine.state.ending_equity, D(9940))
        engine.start_session(engine.state.trading_date+timedelta(days=1))
        self.assertEqual((engine.state.bod_equity, engine.state.cash), (D(9940),D(9940)))
        self.assertEqual((engine.state.new_entries, engine.state.consecutive_losses, engine.state.realized_net), (0,0,D(0)))

    def test_session_cannot_reset_intraday(self):
        engine, _ = self.engine()
        with self.assertRaisesRegex(ValueError, 'resolved'): engine.start_session(engine.state.trading_date+timedelta(days=1))

    def test_entry_equity_is_preserved_without_price_movement(self):
        engine, _ = self.engine()
        approval = self.approval(engine)
        result = engine.on_open(approval.scheduled_timestamp, (self.bar(approval.scheduled_timestamp),), (approval,))
        self.assertEqual((result.state.cash, result.state.exposure, result.state.current_equity), (D(8890),D(1110),D(10000)))
        self.assertEqual(result.state.realized_net, D(0))

    def test_entry_registers_full_phase_six_seven_linkage(self):
        engine, _ = self.engine()
        approval = self.enter(engine)
        position = engine.state.positions[0]
        self.assertEqual((position.entry.approval, position.original_quantity), (approval,1000))
        self.assertEqual(position.entry.account_before.beginning_of_day_equity, D(10000))

    def test_exact_three_positions_allowed_and_fourth_rejected(self):
        engine, _ = self.engine()
        at = self.template.scheduled_timestamp
        candidates = tuple(self.approval(engine,ticker) for ticker in ('DDD','CCC','BBB','AAA'))
        result = engine.on_open(at, tuple(self.bar(at,t) for t in ('AAA','BBB','CCC','DDD')), candidates)
        self.assertEqual([c.approval.ticker for c in result.candidates], ['AAA','BBB','CCC','DDD'])
        self.assertEqual([c.accepted for c in result.candidates], [True,True,True,False])
        self.assertEqual(result.state.open_position_count, 3)
        self.assertEqual(result.candidates[-1].description, 'MAXIMUM_SIMULTANEOUS_POSITIONS')

    def test_rejected_candidate_does_not_increment_entry_count(self):
        engine, ticks = self.engine()
        ticks.unavailable.add(('security-AAA',TickPurpose.INITIAL_STOP))
        approval = self.approval(engine)
        result = engine.on_open(approval.scheduled_timestamp,(self.bar(approval.scheduled_timestamp),),(approval,))
        self.assertEqual((result.state.new_entries,result.state.cash), (0,D(10000)))
        self.assertTrue(result.candidates[0].consumed)

    def test_rejected_setup_cannot_retry_later(self):
        engine, ticks = self.engine()
        ticks.unavailable.add(('security-AAA',TickPurpose.INITIAL_STOP))
        approval = self.approval(engine)
        engine.on_open(approval.scheduled_timestamp,(self.bar(approval.scheduled_timestamp),),(approval,))
        self.complete(engine)
        later = self.approval(engine,at=engine._next,setup=approval.setup_id)
        with self.assertRaisesRegex(ValueError,'unique'): engine.on_open(engine._next,(self.bar(engine._next),),(later,))

    def test_fifth_entry_allowed_sixth_rejected(self):
        engine, _ = self.engine()
        for ticker in ('AAA','BBB','CCC','DDD','EEE'):
            self.win(engine,ticker, at=engine._next if engine.state.new_entries else None)
        self.assertEqual(engine.state.new_entries, 5)
        self.assertIn(L.DAILY_ENTRY_LIMIT,engine.state.lockout_reasons)
        candidate = self.approval(engine,'FFF',at=engine._next)
        result = engine.on_open(engine._next,(self.bar(engine._next,'FFF'),),(candidate,))
        self.assertFalse(result.candidates[0].accepted)
        self.assertEqual(result.state.new_entries,5)

    def test_daily_loss_above_threshold_does_not_lock(self):
        engine, _ = self.engine()
        self.lose(engine,'AAA',price='.91001')
        self.assertEqual(engine.state.realized_net,D('-199.99'))
        self.assertFalse(engine.state.locked)

    def test_daily_loss_exact_minus_two_percent_locks(self):
        engine, _ = self.engine()
        self.lose(engine,'AAA',price='.91')
        self.assertEqual(engine.state.realized_net,D('-200'))
        self.assertIn(L.DAILY_NET_REALIZED_LOSS,engine.state.lockout_reasons)

    def test_daily_loss_below_threshold_locks(self):
        engine, _ = self.engine()
        self.lose(engine,'AAA',price='.9099')
        self.assertLess(engine.state.realized_net,D('-200'))
        self.assertTrue(engine.state.locked)

    def test_unrealized_loss_does_not_trigger_realized_loss_lock(self):
        engine, _ = self.engine()
        self.enter(engine)
        result = self.opening(engine, {'AAA':'1.06'})
        self.assertEqual(result.state.realized_net,D(0))
        self.assertFalse(result.state.locked)
        self.assertEqual(result.state.current_equity,D(9950))

    def test_first_second_third_losses_count_and_third_locks(self):
        engine, _ = self.engine()
        for number,ticker in enumerate(('AAA','BBB','CCC'),1):
            self.lose(engine,ticker,at=engine._next if number>1 else None)
            self.assertEqual(engine.state.consecutive_losses,number)
            self.assertEqual(engine.state.locked,number==3)
        self.assertEqual(engine.state.realized_net,D('-180'))
        self.assertIn(L.CONSECUTIVE_NET_LOSSES,engine.state.lockout_reasons)

    def test_win_resets_loss_streak(self):
        engine, _ = self.engine()
        self.lose(engine,'AAA')
        self.win(engine,'BBB',at=engine._next)
        self.assertEqual(engine.state.consecutive_losses,0)

    def test_zero_net_breakeven_resets_loss_streak(self):
        engine, _ = self.engine()
        self.lose(engine,'AAA')
        self.enter(engine,'BBB',at=engine._next)
        self.opening(engine,{'BBB':'1.23'}); self.complete(engine)
        self.opening(engine,{'BBB':'.99'}); self.complete(engine)
        self.assertEqual(engine.completed[-1].net_pnl,D(0))
        self.assertEqual(engine.state.consecutive_losses,0)

    def test_partial_contributes_daily_pnl_but_not_completed_trade(self):
        engine, _ = self.engine()
        self.enter(engine)
        result = self.opening(engine,{'AAA':'1.23'})
        self.assertEqual((result.state.cash,result.state.realized_net,result.state.open_position_count), (D(9505),D(60),1))
        self.assertEqual((result.state.completed_trades,result.state.consecutive_losses), (0,0))
        self.assertEqual(result.state.exposure,D(615))

    def test_partial_gain_with_final_net_loss_is_completed_loss(self):
        engine, _ = self.engine()
        self.enter(engine)
        self.opening(engine,{'AAA':'1.23'}); self.complete(engine)
        self.opening(engine,{'AAA':'.90'}); self.complete(engine)
        self.assertEqual(engine.completed[-1].net_pnl,D('-45'))
        self.assertEqual(engine.state.consecutive_losses,1)

    def test_gap_stop_exit_before_same_open_entry_releases_slot(self):
        engine, _ = self.engine()
        self.enter(engine)
        at = engine._next
        candidate=self.approval(engine,'BBB',at)
        result=self.opening(engine,{'AAA':'1.05'},candidates=(candidate,),extra=(self.bar(at,'BBB'),))
        self.assertTrue(result.candidates[0].accepted)
        self.assertEqual(result.candidates[0].cash_before,D(9940))
        self.assertEqual(result.state.open_position_count,1)
        self.assertEqual(result.state.completed_trades,1)

    def test_gap_loss_threshold_blocks_same_timestamp_candidate(self):
        engine, _ = self.engine()
        self.enter(engine)
        at=engine._next
        candidate=self.approval(engine,'BBB',at)
        result=self.opening(engine,{'AAA':'.91'},candidates=(candidate,),extra=(self.bar(at,'BBB'),))
        self.assertFalse(result.candidates[0].accepted)
        self.assertEqual(result.candidates[0].description,'SUPPLIED_DAILY_ENTRY_LOCKOUT')

    def test_third_loss_blocks_same_timestamp_candidate(self):
        engine, _ = self.engine()
        self.lose(engine,'AAA'); self.lose(engine,'BBB',at=engine._next)
        self.enter(engine,'CCC',at=engine._next)
        at=engine._next; candidate=self.approval(engine,'DDD',at)
        result=self.opening(engine,{'CCC':'1.05'},candidates=(candidate,),extra=(self.bar(at,'DDD'),))
        self.assertFalse(result.candidates[0].accepted)
        self.assertEqual(result.state.consecutive_losses,3)

    def test_rvol_ranking_descending_then_ticker(self):
        engine, _ = self.engine()
        approvals=(self.approval(engine,'CCC',ratio=Fraction(2)),self.approval(engine,'BBB',ratio=Fraction(3)),
                   self.approval(engine,'AAA',ratio=Fraction(3)))
        self.assertEqual([a.ticker for a in rank_candidates(approvals)],['AAA','BBB','CCC'])

    def test_ranking_and_allocations_ignore_input_order(self):
        outcomes=[]
        for order in (('DDD','BBB','CCC','AAA'),('AAA','CCC','DDD','BBB')):
            engine,_=self.engine(); at=self.template.scheduled_timestamp
            approvals=tuple(self.approval(engine,t) for t in order)
            result=engine.on_open(at,tuple(self.bar(at,t) for t in order),approvals)
            outcomes.append((result.state,result.candidates))
        self.assertEqual(outcomes[0],outcomes[1])

    def test_sequential_allocations_use_updated_cash_exposure_and_registry(self):
        engine,_=self.engine(); at=self.template.scheduled_timestamp
        result=engine.on_open(at,tuple(self.bar(at,t) for t in ('AAA','BBB','CCC')),
                             tuple(self.approval(engine,t) for t in ('AAA','BBB','CCC')))
        self.assertEqual([c.cash_before for c in result.candidates],[D(10000),D(8890),D(7780)])
        self.assertEqual([c.exposure_before for c in result.candidates],[D(0),D(1110),D(2220)])
        self.assertEqual([c.construction.position.account_before.current_equity for c in result.candidates],[D(10000)]*3)

    def test_same_ticker_open_position_rejects_new_entry(self):
        engine,_=self.engine(); self.enter(engine)
        approval=self.approval(engine,at=engine._next)
        result=self.opening(engine,candidates=(approval,))
        self.assertEqual(result.candidates[0].description,'SAME_TICKER_POSITION_ALREADY_OPEN')
        self.assertEqual(result.state.new_entries,1)

    def test_final_exit_same_minute_cannot_reuse_old_setup_structure(self):
        engine,_=self.engine(); self.enter(engine)
        at=engine._next
        approval=self.approval(engine,at=at)
        # A failed, pre-formed approval cannot coexist with the held ticker.
        # OPEN matching is mandatory, so a target-gap fill offers that exact OPEN.
        approval=replace(approval,reference_open=D('1.11'))
        bar=self.bar(at,low='1.05')
        result=engine.on_open(at,(bar,),(approval,))
        self.assertFalse(result.candidates[0].accepted)
        engine.on_close(at+timedelta(minutes=1))
        self.assertFalse(engine.price_pattern_permitted(bar))
        self.assertTrue(engine.price_pattern_permitted(self.bar(bar.end)))

    def test_no_trade_held_open_blocks_all_candidates_even_with_cash(self):
        engine,_=self.engine(); self.enter(engine)
        at=engine._next; candidate=self.approval(engine,'BBB',at)
        result=self.opening(engine,kinds={'AAA':K.NO_TRADE},candidates=(candidate,),extra=(self.bar(at,'BBB'),))
        self.assertEqual(result.candidates[0].description,'PORTFOLIO_VALUATION_UNAVAILABLE')
        self.assertIsNone(result.state.current_equity)
        self.assertEqual(result.state.reporting_equity,D(10000))

    def test_completion_marks_are_reporting_only_until_next_open(self):
        engine,_=self.engine(); self.enter(engine)
        self.assertIsNone(engine.state.current_equity)
        self.assertEqual(engine.state.reporting_equity,D(10000))

    def test_open_decisions_ignore_later_high_low_close_and_volume(self):
        outcomes=[]
        for change in ({},{'high':D('1.30')},{'low':D('1.00')},{'close':D('1.15')},{'volume':D(999999)}):
            engine,_=self.engine(); self.enter(engine)
            at=engine._next; candidate=self.approval(engine,'BBB',at)
            existing=replace(self.bar(at,high='1.20',low='1.10'),**change)
            result=engine.on_open(at,(existing,self.bar(at,'BBB')),(candidate,))
            outcomes.append((result.state.cash,result.state.current_equity,result.candidates))
        self.assertTrue(all(outcome==outcomes[0] for outcome in outcomes))

    def test_intrabar_exit_cannot_free_slot_for_same_open_entry(self):
        engine,_=self.engine(); at=self.template.scheduled_timestamp
        engine.on_open(at,tuple(self.bar(at,t) for t in ('AAA','BBB','CCC')),
                       tuple(self.approval(engine,t) for t in ('AAA','BBB','CCC'))); self.complete(engine)
        at=engine._next; candidate=self.approval(engine,'DDD',at)
        bars=(self.bar(at,'AAA',low='1.05'),self.bar(at,'BBB'),self.bar(at,'CCC'),self.bar(at,'DDD'))
        opened=engine.on_open(at,bars,(candidate,))
        self.assertFalse(opened.candidates[0].accepted)
        closed=engine.on_close(at+timedelta(minutes=1))
        self.assertEqual(closed.state.open_position_count,2)
        self.assertEqual(closed.state.new_entries,3)

    def test_intrabar_loss_cannot_retroactively_cancel_open_entry(self):
        engine,_=self.engine(); self.enter(engine)
        at=engine._next; candidate=self.approval(engine,'BBB',at)
        result=engine.on_open(at,(self.bar(at,low='1.05'),self.bar(at,'BBB')),(candidate,))
        self.assertTrue(result.candidates[0].accepted)
        self.complete(engine)
        self.assertEqual(engine.state.new_entries,2)

    def test_missing_held_interval_pauses_whole_timestamp_without_cash_effect(self):
        engine,_=self.engine(); self.enter(engine)
        before=engine.state
        result=self.opening(engine,kinds={'AAA':K.MISSING})
        self.assertTrue(result.requires_shared_pause)
        self.assertEqual(result.state.cash,before.cash)
        self.assertEqual(result.state.positions[0].active_stop,D('1.05'))

    def test_paused_data_blocks_later_events_and_sessions(self):
        engine,_=self.engine(); self.enter(engine); self.opening(engine,kinds={'AAA':K.INVALID})
        with self.assertRaisesRegex(ValueError,'not active'): self.complete(engine)
        with self.assertRaisesRegex(ValueError,'resolved'): engine.start_session(engine.state.trading_date+timedelta(days=1))

    def test_replacement_replays_shared_cash_and_candidate_allocation(self):
        engine,_=self.engine(); self.enter(engine)
        at=engine._next; candidate=self.approval(engine,'BBB',at)
        engine.on_open(at,(self.bar(at,kind=K.MISSING),self.bar(at,'BBB')),(candidate,))
        result=engine.resume_with_replacements((self.bar(at,opening='1.05'),))
        self.assertTrue(result.candidates[0].accepted)
        self.assertEqual(result.candidates[0].cash_before,D(9940))
        self.assertEqual(result.state.completed_trades,1)
        self.assertEqual(result.state.new_entries,2)

    def test_replacement_cannot_bridge_failed_interval(self):
        engine,_=self.engine(); self.enter(engine); self.opening(engine,kinds={'AAA':K.MISSING})
        with self.assertRaisesRegex(ValueError,'exact-interval'):
            engine.resume_with_replacements((self.bar(engine._next+timedelta(minutes=1)),))

    def test_unresolved_missing_data_is_incomplete_without_ending_equity(self):
        engine,_=self.engine(); self.enter(engine); self.opening(engine,kinds={'AAA':K.INVALID})
        result=engine.mark_data_incomplete()
        self.assertTrue(result.requires_shared_stop)
        self.assertIsNone(result.state.ending_equity)
        self.assertEqual(result.state.open_position_count,1)
        self.assertEqual(result.state.completed_trades,0)

    def test_open_tick_failure_propagates_globally_with_partial_pnl(self):
        engine,ticks=self.engine(); self.enter(engine)
        ticks.unavailable.add(('security-AAA',TickPurpose.BREAKEVEN_STOP))
        result=self.opening(engine,{'AAA':'1.23'})
        self.assertEqual(result.state.run_status,RunStatus.INCOMPLETE_TICK_SIZE_UNAVAILABLE_OPEN_POSITION)
        self.assertEqual((result.state.cash,result.state.realized_net), (D(9505),D(60)))
        self.assertEqual(result.state.positions[0].remaining_quantity,500)
        self.assertTrue(result.requires_shared_stop)
        with self.assertRaisesRegex(ValueError,'not active'): self.complete(engine)

    def test_final_no_trade_stops_shared_run_at_official_close(self):
        engine,_=self.engine(); self.enter(engine)
        self.advance(engine,engine.state.session.close-timedelta(minutes=1))
        self.opening(engine,kinds={'AAA':K.NO_TRADE})
        result=self.complete(engine)
        self.assertEqual(result.state.run_status,RunStatus.INCOMPLETE_UNRESOLVED_EOD_LIQUIDITY)
        self.assertEqual(result.state.status,P.INCOMPLETE)
        self.assertIsNone(result.state.ending_equity)
        self.assertEqual(result.state.open_position_count,1)
        with self.assertRaisesRegex(ValueError,'resolved'): engine.start_session(engine.state.trading_date+timedelta(days=1))

    def test_eod_open_liquidates_before_any_later_minute_extrema(self):
        engine,_=self.engine(); self.enter(engine)
        self.advance(engine,engine.state.session.close-timedelta(minutes=1))
        at=engine._next
        result=engine.on_open(at,(self.bar(at,opening='1.18',high='1.30',low='1.00'),))
        self.assertEqual((result.state.cash,result.state.open_position_count), (D(10070),0))
        self.complete(engine)
        self.assertEqual(engine.state.completed_trades,1)
        self.assertEqual(engine.state.ending_equity,D(10070))

    def test_held_interval_gap_is_rejected_not_bridged(self):
        engine,_=self.engine(); self.enter(engine)
        later=engine._next+timedelta(minutes=1)
        with self.assertRaisesRegex(ValueError,'bridge'): engine.on_open(later,(self.bar(later),))

    def test_missing_held_record_must_be_explicit(self):
        engine,_=self.engine(); self.enter(engine)
        with self.assertRaisesRegex(ValueError,'explicitly'): engine.on_open(engine._next,())

    def test_duplicate_interval_is_rejected(self):
        engine,_=self.engine(); self.enter(engine); bar=self.bar(engine._next)
        with self.assertRaisesRegex(ValueError,'Duplicate'): engine.on_open(engine._next,(bar,bar))

    def test_completion_before_open_is_rejected(self):
        engine,_=self.engine()
        with self.assertRaisesRegex(ValueError,'pending'): engine.on_close(engine.state.session.open+timedelta(minutes=1))

    def test_another_open_before_completion_is_rejected(self):
        engine,_=self.engine(); at=self.template.scheduled_timestamp
        engine.on_open(at,())
        with self.assertRaisesRegex(ValueError,'Complete'): engine.on_open(at+timedelta(minutes=1),())

    def test_state_snapshots_are_immutable(self):
        engine,_=self.engine()
        with self.assertRaises(FrozenInstanceError): engine.state.cash=D(1)

    def test_financial_arithmetic_independent_of_decimal_context(self):
        engine,_=self.engine()
        with localcontext() as context:
            context.prec=2
            self.enter(engine)
            self.opening(engine,{'AAA':'.91001'})
        self.assertEqual(engine.state.realized_net,D('-199.99'))

    def test_cash_remains_nonnegative_through_sequential_entries(self):
        engine,_=self.engine(); at=self.template.scheduled_timestamp
        result=engine.on_open(at,tuple(self.bar(at,t) for t in ('AAA','BBB','CCC','DDD')),
                             tuple(self.approval(engine,t) for t in ('AAA','BBB','CCC','DDD')))
        self.assertTrue(all(c.cash_after>=0 for c in result.candidates))

    def test_final_exit_is_accounted_exactly_once(self):
        engine,_=self.engine(); self.lose(engine,'AAA')
        before=(engine.state.cash,engine.state.completed_trades,engine.state.realized_net)
        engine.on_open(engine._next,()); self.complete(engine)
        self.assertEqual((engine.state.cash,engine.state.completed_trades,engine.state.realized_net),before)

    def test_audit_preserves_ranking_state_and_partial_daily_values(self):
        engine,_=self.engine(); self.enter(engine); self.opening(engine,{'AAA':'1.23'})
        allocation=next(record for record in engine.audit if record.event_type=='PORTFOLIO_ALLOCATION')
        self.assertEqual(dict(allocation.details)['cash_before'],D(10000))
        exit_record=next(record for record in engine.audit if record.event_type=='PORTFOLIO_EXIT_ACCOUNTING')
        self.assertEqual(dict(exit_record.details)['realized_daily_net'],D(60))

    def test_paused_source_cause_remains_in_structured_audit(self):
        engine,_=self.engine(); self.enter(engine); at=engine._next
        result=engine.on_open(at,(self.bar(at,kind=K.INVALID,cause='provider conflict X'),))
        record=next(record for record in result.audit if record.event_type=='PORTFOLIO_DATA_PAUSE')
        self.assertEqual(dict(record.details)['data_quality_reason'],'provider conflict X')

    def two_runners(self, engine):
        self.enter(engine,'AAA')
        self.enter(engine,'BBB',at=engine._next)
        self.opening(engine,{'AAA':'1.23','BBB':'1.23'})
        self.complete(engine)
        self.assertEqual(engine.state.cash,D(9010))

    def test_exact_sixty_percent_after_entry_is_allowed(self):
        engine,_=self.engine(); self.two_runners(engine)
        at=engine._next; candidate=self.approval(engine,'CCC',at)
        result=self.opening(engine,{'AAA':'10.74','BBB':'10.74'},candidates=(candidate,),extra=(self.bar(at,'CCC'),))
        self.assertTrue(result.candidates[0].accepted)
        self.assertEqual((result.state.exposure,result.state.current_equity), (D(11850),D(19750)))
        self.assertEqual(result.state.exposure*D(5),result.state.current_equity*D(3))

    def test_existing_exposure_exactly_sixty_percent_prevents_new_entry(self):
        engine,_=self.engine(); self.two_runners(engine)
        at=engine._next; candidate=self.approval(engine,'CCC',at)
        result=self.opening(engine,{'AAA':'13.515','BBB':'13.515'},candidates=(candidate,),extra=(self.bar(at,'CCC'),))
        self.assertFalse(result.candidates[0].accepted)
        self.assertEqual(result.candidates[0].description,'TOTAL_EXPOSURE_ALREADY_AT_OR_ABOVE_CAP')
        self.assertEqual((result.state.exposure,result.state.current_equity), (D(13515),D(22525)))

    def test_price_appreciation_above_exposure_cap_does_not_force_exit(self):
        engine,_=self.engine(); self.two_runners(engine)
        result=self.opening(engine,{'AAA':'20','BBB':'20'})
        self.assertEqual((result.state.open_position_count,result.state.exposure), (2,D(20000)))
        self.assertEqual(result.state.realized_net,D(120))
        self.assertGreater(result.state.exposure*D(5),result.state.current_equity*D(3))

    def test_sequential_remaining_exposure_resizes_second_candidate(self):
        engine,_=self.engine(); self.enter(engine)
        self.opening(engine,{'AAA':'1.23'}); self.complete(engine)
        at=engine._next
        result=self.opening(engine,{'AAA':'21.015'},
            candidates=(self.approval(engine,'BBB',at),self.approval(engine,'CCC',at)),
            extra=(self.bar(at,'BBB'),self.bar(at,'CCC')))
        self.assertEqual([c.construction.position.quantity for c in result.candidates], [1000,351])
        self.assertEqual(result.candidates[1].construction.position.sizing.exposure,351)
        self.assertEqual(result.state.cash,D('8005.39'))

    def test_sequential_exposure_below_two_shares_rejects_later_candidate(self):
        engine,_=self.engine(); self.enter(engine)
        self.opening(engine,{'AAA':'1.23'}); self.complete(engine)
        at=engine._next
        result=self.opening(engine,{'AAA':'22.96'},
            candidates=(self.approval(engine,'BBB',at),self.approval(engine,'CCC',at)),
            extra=(self.bar(at,'BBB'),self.bar(at,'CCC')))
        self.assertEqual([c.accepted for c in result.candidates],[True,False])
        self.assertEqual(result.candidates[1].description,'ENTRY_QUANTITY_BELOW_MINIMUM')
        self.assertEqual(result.state.open_position_count,2)

    def test_later_profit_and_win_cannot_unlock_daily_loss(self):
        engine,_=self.engine(); self.enter(engine,'WIN')
        for ticker in ('AAA','BBB','CCC'):
            self.lose(engine,ticker,at=engine._next)
        self.assertIn(L.CONSECUTIVE_NET_LOSSES,engine.state.lockout_reasons)
        self.opening(engine,{'WIN':'1.80'}); self.complete(engine)
        self.assertGreater(engine.state.realized_net,0)
        self.opening(engine,{'WIN':'1.11'}); self.complete(engine)
        self.assertEqual(engine.state.consecutive_losses,0)
        self.assertTrue(engine.state.locked)

    def test_all_three_lockout_reasons_are_retained(self):
        engine,_=self.engine(); self.win(engine,'WIN1'); self.win(engine,'WIN2',at=engine._next)
        for ticker in ('AAA','BBB','CCC'):
            self.lose(engine,ticker,price='.97',at=engine._next)
        self.assertEqual(set(engine.state.lockout_reasons),set(L))
        self.assertEqual((engine.state.new_entries,engine.state.realized_net,engine.state.consecutive_losses),
                         (5,D('-300'),3))

    def test_sticky_lockouts_reset_only_at_next_verified_session(self):
        engine,_=self.engine(); self.lose(engine,'AAA',price='.91')
        engine.close_session(engine.state.session.close)
        self.assertTrue(engine.state.locked)
        engine.start_session(engine.state.trading_date+timedelta(days=1))
        self.assertFalse(engine.state.locked)
        self.assertEqual(engine.state.bod_equity,D(9800))

    def test_bod_risk_budget_not_replaced_by_current_profit(self):
        engine,_=self.engine(); self.win(engine,'AAA')
        at=engine._next; approval=self.approval(engine,'BBB',at)
        result=engine.on_open(at,(self.bar(at,'BBB'),),(approval,))
        position=result.candidates[0].construction.position
        self.assertEqual(position.account_before.current_equity,D(10060))
        self.assertEqual(position.risk_budget,D(100))

    def test_open_target_partial_cash_is_available_before_same_open_allocation(self):
        engine,_=self.engine(); self.enter(engine)
        at=engine._next; candidate=self.approval(engine,'BBB',at)
        result=self.opening(engine,{'AAA':'1.23'},candidates=(candidate,),extra=(self.bar(at,'BBB'),))
        self.assertEqual(result.candidates[0].cash_before,D(9505))
        self.assertEqual(result.state.current_equity,D(10120))
        self.assertEqual((result.state.completed_trades,result.state.realized_net),(0,D(60)))

    def test_completion_third_loss_blocks_next_open_but_not_prior_open(self):
        engine,_=self.engine(); self.lose(engine,'AAA'); self.lose(engine,'BBB',at=engine._next)
        self.enter(engine,'CCC',at=engine._next)
        at=engine._next; candidate=self.approval(engine,'DDD',at)
        result=engine.on_open(at,(self.bar(at,'CCC',low='1.05'),self.bar(at,'DDD')),(candidate,))
        self.assertTrue(result.candidates[0].accepted)
        self.complete(engine)
        later=self.approval(engine,'EEE',engine._next)
        next_result=self.opening(engine,candidates=(later,),extra=(self.bar(engine._next,'EEE'),))
        self.assertFalse(next_result.candidates[0].accepted)
        self.assertEqual(engine.state.new_entries,4)

    def test_unknown_intrabar_cross_ticker_losses_follow_alphabetical_order(self):
        engine,_=self.engine(); at=self.template.scheduled_timestamp
        engine.on_open(at,tuple(self.bar(at,t) for t in ('BBB','AAA')),
                       tuple(self.approval(engine,t) for t in ('BBB','AAA'))); self.complete(engine)
        at=engine._next
        engine.on_open(at,(self.bar(at,'BBB',low='1.05'),self.bar(at,'AAA',low='1.05')))
        self.complete(engine)
        self.assertEqual([c.entry.approval.ticker for c in engine.completed],['AAA','BBB'])

    def test_duplicate_candidate_ticker_is_input_error_not_a_new_ranking_rule(self):
        engine,_=self.engine(); approval=self.approval(engine)
        with self.assertRaisesRegex(ValueError,'Overlapping'): rank_candidates((approval,approval))

    def test_undefined_rvol_cannot_receive_a_ranking_value(self):
        engine,_=self.engine(); approval=self.approval(engine)
        invalid=replace(approval,d_attempt=replace(approval.d_attempt,
            previous_20=replace(approval.d_attempt.previous_20,ratio=None)))
        with self.assertRaisesRegex(ValueError,'finite'): rank_candidates((invalid,))

    def test_approvals_cannot_be_scheduled_in_another_session(self):
        engine,_=self.engine(); approval=self.approval(engine)
        with self.assertRaisesRegex(ValueError,'RTH'):
            engine.on_open(approval.scheduled_timestamp+timedelta(days=1),
                           (self.bar(approval.scheduled_timestamp+timedelta(days=1)),),(approval,))

    def test_final_exit_quarantine_rejects_old_prices_even_after_waiting(self):
        engine,_=self.engine(); first=self.enter(engine)
        self.opening(engine,{'AAA':'1.05'}); self.complete(engine)
        later=engine._next
        approval=self.approval(engine,at=later)
        result=engine.on_open(later,(self.bar(later),),(approval,))
        self.assertEqual(result.candidates[0].description,'FINAL_EXIT_QUARANTINE_PRICE_HISTORY')
        self.assertTrue(result.candidates[0].consumed)

    def test_fresh_post_quarantine_prices_can_form_later_same_ticker_entry(self):
        engine,_=self.engine(); self.lose(engine,'AAA')
        later=engine._next+timedelta(minutes=30)
        approval=self.approval(engine,at=later)
        result=engine.on_open(later,(self.bar(later),),(approval,))
        self.assertTrue(result.candidates[0].accepted)
        self.assertEqual(result.state.new_entries,2)

    def test_partial_position_remains_occupied_for_pattern_detection(self):
        engine,_=self.engine(); self.enter(engine)
        self.opening(engine,{'AAA':'1.23'}); self.complete(engine)
        self.assertFalse(engine.price_pattern_permitted(self.bar(engine._next)))

    def test_no_trade_cannot_seed_price_pattern_after_final_exit(self):
        engine,_=self.engine(); self.lose(engine,'AAA')
        self.assertFalse(engine.price_pattern_permitted(self.bar(engine._next,kind=K.NO_TRADE)))

    def test_new_entry_cannot_finalize_an_unresolved_session(self):
        engine,_=self.engine(); self.enter(engine)
        with self.assertRaisesRegex(ValueError,'fully resolved'): engine.close_session(engine.state.session.close)

    def test_early_close_liquidation_finalizes_without_overnight_position(self):
        engine,_=self.engine(close_hour=13); self.enter(engine)
        self.advance(engine,engine.state.session.close-timedelta(minutes=1))
        self.assertEqual((engine._next.hour,engine._next.minute),(12,59))
        self.opening(engine,{'AAA':'1.18'}); self.complete(engine)
        self.assertEqual((engine.state.status,engine.state.ending_equity),(P.SESSION_CLOSED,D(10070)))

    def test_early_close_no_trade_remains_incomplete_at_official_close(self):
        engine,_=self.engine(close_hour=13); self.enter(engine)
        self.advance(engine,engine.state.session.close-timedelta(minutes=1))
        self.opening(engine,kinds={'AAA':K.NO_TRADE}); self.complete(engine)
        self.assertEqual(engine.state.run_status,RunStatus.INCOMPLETE_UNRESOLVED_EOD_LIQUIDITY)
        self.assertIsNone(engine.state.ending_equity)

    def test_shared_unresolved_eod_preserves_other_tickers_known_liquidation(self):
        engine,_=self.engine(); self.enter(engine,'AAA'); self.enter(engine,'BBB',at=engine._next)
        self.advance(engine,engine.state.session.close-timedelta(minutes=1))
        self.opening(engine,{'BBB':'1.18'},kinds={'AAA':K.NO_TRADE}); self.complete(engine)
        self.assertEqual(engine.state.run_status,RunStatus.INCOMPLETE_UNRESOLVED_EOD_LIQUIDITY)
        self.assertEqual((engine.state.completed_trades,engine.state.open_position_count),(1,1))
        self.assertEqual(engine.completed[0].entry.approval.ticker,'BBB')

    def test_fatal_tick_state_does_not_use_later_price_or_start_next_session(self):
        engine,ticks=self.engine(); self.enter(engine)
        ticks.unavailable.add(('security-AAA',TickPurpose.BREAKEVEN_STOP))
        self.opening(engine,{'AAA':'1.23'})
        before=engine.state
        with self.assertRaisesRegex(ValueError,'not active'): engine.on_open(engine._next+timedelta(minutes=1),())
        with self.assertRaisesRegex(ValueError,'resolved'): engine.start_session(engine.state.trading_date+timedelta(days=1))
        self.assertEqual(engine.state,before)

    def test_missing_timestamp_preflight_does_not_apply_other_same_open_exit(self):
        engine,_=self.engine(); self.enter(engine,'AAA'); self.enter(engine,'BBB',at=engine._next)
        before=engine.state
        self.opening(engine,{'AAA':'1.05'},kinds={'BBB':K.MISSING})
        self.assertEqual((engine.state.cash,engine.state.realized_net),(before.cash,before.realized_net))
        self.assertEqual(engine.state.open_position_count,2)

    def test_all_paused_securities_require_trustworthy_replacements(self):
        engine,_=self.engine(); self.enter(engine,'AAA'); self.enter(engine,'BBB',at=engine._next)
        self.opening(engine,kinds={'AAA':K.MISSING,'BBB':K.INVALID})
        with self.assertRaisesRegex(ValueError,'every paused'):
            engine.resume_with_replacements((self.bar(engine._next,'AAA'),))

    def test_repeated_completed_events_are_rejected_and_do_not_double_cash(self):
        engine,_=self.engine(); self.lose(engine,'AAA')
        before=engine.state.cash
        with self.assertRaisesRegex(ValueError,'pending'): engine.on_close(engine._next)
        self.assertEqual(engine.state.cash,before)

    def test_successful_run_event_record_times_are_monotonic(self):
        engine,_=self.engine(); self.win(engine,'AAA')
        times=[record.recorded_at for record in engine.audit]
        self.assertEqual(times,sorted(times))

    def test_open_valuation_audit_has_no_future_candle_fields(self):
        engine,_=self.engine(); self.enter(engine)
        result=self.opening(engine)
        records=[r for r in result.audit if r.event_type.startswith('PORTFOLIO_')]
        for record in records:
            self.assertTrue({'high','low','close','volume'}.isdisjoint(dict(record.details)))

    def test_new_candidate_later_candle_fields_cannot_change_open_sizing(self):
        outcomes=[]
        for change in ({},{'high':D('1.40')},{'low':D('1.00')},{'close':D('1.15')},{'volume':D(1)}):
            engine,_=self.engine(); approval=self.approval(engine)
            bar=replace(self.bar(approval.scheduled_timestamp,high='1.20',low='1.10'),**change)
            result=engine.on_open(approval.scheduled_timestamp,(bar,),(approval,))
            outcomes.append(result.candidates[0].construction.position)
        self.assertTrue(all(position==outcomes[0] for position in outcomes))

    def test_unrealized_loss_beyond_daily_threshold_does_not_lock(self):
        engine,_=self.engine(); at=self.template.scheduled_timestamp
        handoff,context,calendar,_=handoff_fixture(factor='2',open_price='2.22')
        base=EntryValidator(calendar,run_id='phase8',data_version='fixture').evaluate(handoff,context).approval
        approvals=tuple(replace(remap(base,'security-'+ticker,ticker,timedelta()),setup_id=ticker)
                        for ticker in ('AAA','BBB','CCC'))
        engine.on_open(at,tuple(self.bar(at,t,opening='2.22') for t in ('AAA','BBB','CCC')),approvals)
        self.complete(engine)
        result=self.opening(engine,{'AAA':'2.11','BBB':'2.11','CCC':'2.11'})
        self.assertEqual(result.state.current_equity,D('9725.11'))
        self.assertEqual(result.state.realized_net,D(0))
        self.assertFalse(result.state.locked)

    def test_trailing_tick_failure_at_completion_stops_entire_run(self):
        engine,ticks=self.engine(); self.enter(engine)
        self.opening(engine,{'AAA':'1.23'}); self.complete(engine)
        ticks.unavailable.add(('security-AAA',TickPurpose.TRAILING_STOP))
        for _ in range(3):
            at=engine._next
            engine.on_open(at,(self.bar(at,opening='1.15',high='1.20',low='1.13',close='1.16'),))
            result=self.complete(engine)
        self.assertEqual(result.state.run_status,RunStatus.INCOMPLETE_TICK_SIZE_UNAVAILABLE_OPEN_POSITION)
        self.assertEqual((result.state.realized_net,result.state.positions[0].active_stop),(D(60),D('1.11')))
        self.assertIsNone(result.state.ending_equity)

    def test_known_open_exit_does_not_override_other_held_no_trade_valuation_gate(self):
        engine,_=self.engine(); self.enter(engine,'AAA'); self.enter(engine,'BBB',at=engine._next)
        at=engine._next; candidate=self.approval(engine,'CCC',at)
        result=self.opening(engine,{'AAA':'1.05'},kinds={'BBB':K.NO_TRADE},
                            candidates=(candidate,),extra=(self.bar(at,'CCC'),))
        self.assertEqual(result.state.completed_trades,1)
        self.assertEqual(result.candidates[0].description,'PORTFOLIO_VALUATION_UNAVAILABLE')
        self.assertEqual(result.state.realized_net,D(-60))
