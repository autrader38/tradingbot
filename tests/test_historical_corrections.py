"""Regression contracts and hand-calculated, coherent historical integration."""

import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from fractions import Fraction as F

from tests import test_historical_delivery as delivery
from tests.historical_fixtures import (DAY, Calendar, NEW_YORK, dataset, minute,
                                      provenance, with_setup)
from tradingbot_backtest.historical_data import (DailySession, HistoricalInputError,
    HistoricalWeekly, MarketCapitalization, PriorClose, SplitAction)
from tradingbot_backtest.historical_results import serialized
from tradingbot_backtest.historical_runner import HistoricalBacktest
from tradingbot_backtest.historical_universe import evaluate_universe, normalize
from tradingbot_backtest.market import IntervalClassification as K, MarketInterval
from tradingbot_backtest.numerics import multiply
from tradingbot_backtest.portfolio import PortfolioEngine
from tradingbot_backtest.position_management import ManagementStatus as M

ENTRY = datetime(2026, 10, 8, 10, 15, tzinfo=NEW_YORK)


def events(result, name):
    return [a for a in result.audit
            if getattr(a, 'event_type', getattr(a, 'event', None)) == name]


class ReplayValidationTests(unittest.TestCase):
    def paused(self):
        helper = delivery.DeliveryTests()
        engine, bar = helper.target_open()
        manager = engine._managers['security']
        manager.complete_interval(helper.missing(bar))
        return manager, bar

    def assert_rejected_unchanged(self, change):
        manager, bar = self.paused()
        before = (manager.state, manager._checkpoint, manager._pending,
                  manager._completion_open, manager.next_interval, manager.audit)
        with self.assertRaises(ValueError):
            manager.resume_with_replacement(change(bar))
        self.assertEqual(before, (manager.state, manager._checkpoint, manager._pending,
                                 manager._completion_open, manager.next_interval, manager.audit))
        self.assertIs(manager._checkpoint, before[1])
        self.assertEqual(manager.state.status, M.PAUSED_DATA)
        self.assertEqual(manager.state.remaining_quantity, 500)
        self.assertEqual(manager.state.active_stop, D('1.11'))
        self.assertEqual(manager.state.net_realized_pnl, D(60))
        # The rejection did not consume the reliable-replacement opportunity.
        accepted = manager.resume_with_replacement(bar)
        self.assertEqual(accepted.state.status, M.ACTIVE)
        self.assertEqual(accepted.exit_fills, ())
        self.assertEqual(accepted.state.remaining_quantity, 500)

    def test_wrong_security_preserves_entire_pause(self):
        self.assert_rejected_unchanged(lambda bar: replace(bar, security_id='different-security'))

    def test_wrong_ticker_preserves_entire_pause(self):
        self.assert_rejected_unchanged(lambda bar: replace(bar, ticker='DIFFERENT'))

    def test_wrong_timestamp_preserves_entire_pause(self):
        self.assert_rejected_unchanged(lambda bar: replace(bar, timestamp=bar.end))

    def test_changed_open_preserves_entire_pause(self):
        self.assert_rejected_unchanged(lambda bar: replace(bar, open=D('1.235')))

    def test_changed_classification_preserves_entire_pause(self):
        self.assert_rejected_unchanged(lambda bar: MarketInterval(bar.security_id, bar.ticker,
            bar.timestamp, K.NO_TRADE, 'replacement', volume=D(0), no_trade_verified=True))

    def test_untrustworthy_replacement_preserves_entire_pause(self):
        self.assert_rejected_unchanged(lambda bar: delivery.DeliveryTests().missing(bar, K.INVALID))

    def test_open_pause_rejects_wrong_identity_before_checkpoint_restoration(self):
        helper = delivery.DeliveryTests()
        engine, entry = helper.engine()
        manager = engine._managers['security']
        manager.complete_interval(entry)
        bar = replace(entry, timestamp=entry.end)
        manager.on_open(helper.missing(bar))
        before = (manager.state, manager._checkpoint, manager._pending, manager.audit)
        for changes in ({'security_id': 'wrong'}, {'ticker': 'WRONG'}, {'timestamp': bar.end}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                manager.resume_with_replacement(replace(bar, **changes))
            self.assertEqual(before, (manager.state, manager._checkpoint, manager._pending, manager.audit))

    def test_entry_open_correction_preserves_completion_pause(self):
        helper = delivery.DeliveryTests()
        engine, entry = helper.engine()
        manager = engine._managers['security']
        manager.complete_interval(helper.missing(entry))
        before = (manager.state, manager._checkpoint, manager._pending, manager.audit)
        with self.assertRaises(ValueError):
            manager.resume_with_replacement(replace(entry, open=D('1.115')))
        self.assertEqual(before, (manager.state, manager._checkpoint, manager._pending, manager.audit))


class ReferenceChronologyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = with_setup(dataset(calendar=Calendar(early=(DAY,))))
        cls.gap = ENTRY - timedelta(minutes=4)
        cls.restored = cls.gap + timedelta(minutes=2)

    def renewed(self, renamed=False, missing=False, fresh=False):
        old = replace(self.base.references[0], valid_until=self.gap)
        new = replace(self.base.references[0], valid_from=self.gap, available_at=self.restored,
                      ticker='RENAMED' if renamed else 'AAA')
        records = []
        for source in self.base.minute_records:
            if renamed and source.interval.timestamp >= self.gap:
                source = replace(source, interval=replace(source.interval, ticker='RENAMED'))
            if missing and source.interval.timestamp == self.gap:
                source = minute('id-AAA', source.interval.ticker, self.gap,
                                kind=K.MISSING, cause='independent source gap during reference outage')
            records.append(source)
        data = replace(self.base, references=(new, old), minute_records=tuple(records))
        if fresh:
            data = with_setup(data, ticker=new.ticker, scheduled=ENTRY+timedelta(minutes=30))
        return HistoricalBacktest(data).run(DAY, DAY)

    def test_reference_renewal_represents_each_gap_slot(self):
        result = self.renewed()
        self.assertFalse(result.summary.incomplete)
        excluded = [a for a in events(result, 'PRICE_PATTERN_EXCLUDED')
                    if dict(a.details)['reference_unavailable']]
        self.assertEqual([dict(a.details)['interval'] for a in excluded],
                         [self.gap, self.gap+timedelta(minutes=1)])
        source = [dict(a.details)['interval'] for a in events(result, 'INTERVAL_COMPLETED_SOURCE')]
        self.assertTrue(all(any(bar.timestamp == at for bar in source)
                            for at in (self.gap, self.gap+timedelta(minutes=1))))

    def test_stale_setup_is_discarded_and_never_retrospectively_completed(self):
        result = self.renewed()
        activated = events(result, 'A_ACTIVATED')
        self.assertGreaterEqual(len(activated), 1)
        self.assertLess(activated[0].recorded_at, self.gap)
        self.assertTrue(all(dict(a.details)['a_timestamp'] >= self.restored for a in activated[1:]))
        self.assertEqual(len({a.setup_id for a in activated}),len(activated))
        self.assertEqual(result.summary.entries, 0)
        self.assertEqual(events(result, 'D_CONFIRMED'), [])
        after = [a for a in events(result, 'A_SELECTED') if a.recorded_at > self.restored]
        self.assertTrue(after)
        self.assertTrue(all(dict(a.details)['a_timestamp'] >= self.restored for a in after))

    def test_reference_restoration_across_rename_keeps_stable_identity(self):
        result = self.renewed(renamed=True, fresh=True)
        self.assertEqual(result.summary.entries, 1)
        trade = result.trades[0]
        self.assertEqual((trade.security_id, trade.ticker), ('id-AAA', 'RENAMED'))
        self.assertGreaterEqual(trade.completed.entry.approval.a.timestamp, self.restored)
        self.assertEqual(len(events(result, 'SECURITY_SYMBOL_CHANGED')), 1)

    def test_verified_volume_survives_price_reference_reset(self):
        result = self.renewed()
        evaluated = next(a for a in events(result, 'RVOL_EVALUATED') if a.interval_start == self.restored)
        slots = dict(evaluated.details)
        self.assertEqual(slots['baseline_slots'], 20)
        self.assertIn(self.gap.isoformat(), slots['baseline_intervals'])
        self.assertIn((self.gap+timedelta(minutes=1)).isoformat(), slots['baseline_intervals'])

    def test_unverified_reference_also_excludes_gap_price_evidence(self):
        old = replace(self.base.references[0],valid_until=self.gap)
        unknown = replace(self.base.references[0],valid_from=self.gap,valid_until=self.restored,
                          available_at=self.gap,verified=False)
        restored = replace(self.base.references[0],valid_from=self.restored,available_at=self.restored)
        result = HistoricalBacktest(replace(self.base,references=(old,unknown,restored))).run(DAY,DAY)
        excluded = [a for a in events(result,'PRICE_PATTERN_EXCLUDED')
                    if dict(a.details)['reference_unavailable']]
        self.assertEqual([dict(a.details)['interval'] for a in excluded],
                         [self.gap,self.gap+timedelta(minutes=1)])
        after = [a for a in events(result,'A_SELECTED') if a.recorded_at>self.restored]
        self.assertTrue(all(dict(a.details)['a_timestamp']>=self.restored for a in after))
        self.assertFalse(result.summary.incomplete)

    def test_actual_data_gap_breaks_volume_even_during_reference_outage(self):
        result = self.renewed(missing=True)
        evaluated = next(a for a in events(result, 'RVOL_EVALUATED') if a.interval_start == self.restored)
        self.assertEqual(dict(evaluated.details)['baseline_slots'], 1)
        self.assertTrue(any('independent source gap' in str(a.details) for a in result.audit))

    def test_late_initial_identity_across_rename_seeds_only_available_history(self):
        known = ENTRY-timedelta(minutes=6)
        old = replace(self.base.references[0], valid_until=known, available_at=known)
        new = replace(self.base.references[0], ticker='RENAMED', valid_from=known, available_at=known)
        records = tuple(replace(r, interval=replace(r.interval, ticker='RENAMED'))
                        if r.interval.timestamp >= known else r for r in self.base.minute_records)
        result = HistoricalBacktest(replace(self.base, references=(new, old),
                                            minute_records=records)).run(DAY, DAY)
        activated = events(result, 'A_ACTIVATED')
        self.assertEqual(activated[0].recorded_at, known+timedelta(minutes=1))
        self.assertEqual(dict(activated[0].details)['a_timestamp'], known-timedelta(minutes=1))
        self.assertEqual(result.trades[0].ticker, 'RENAMED')
        self.assertEqual(result.trades[0].completed.entry.approval.a.ticker, 'AAA')

    def test_same_ticker_reused_by_different_security_cannot_transfer_setup(self):
        data = dataset((('id-OLD', 'AAA'), ('id-NEW', 'AAA')), calendar=Calendar(early=(DAY,)))
        data = with_setup(data, 'id-OLD', 'AAA', outcome='eod')
        old, new = data.references
        old = replace(old, valid_until=self.gap, delisting_at=self.gap)
        previous_new = replace(new,ticker='BBB',valid_until=self.gap)
        new = replace(new, valid_from=self.gap, available_at=self.gap)
        # NEW receives only the late half of OLD's pattern. There is no fresh impulse.
        old_minutes = {r.interval.timestamp:r for r in data.minute_records if r.interval.security_id == 'id-OLD'}
        records = []
        for r in data.minute_records:
            at = r.interval.timestamp
            if r.interval.security_id == 'id-NEW':
                r = (replace(old_minutes[at],interval=replace(old_minutes[at].interval,security_id='id-NEW'))
                     if at >= self.gap else minute('id-NEW','BBB',at,'1.08','1.09','1.08','1.08'))
            elif at >= self.gap:
                r = minute('id-OLD','AAA',at,kind=K.NO_TRADE)
            records.append(r)
        result = HistoricalBacktest(replace(data, references=(old, previous_new, new),
                                            minute_records=tuple(records))).run(DAY, DAY)
        self.assertFalse(result.summary.incomplete)
        self.assertEqual(result.summary.entries, 0)
        self.assertTrue(events(result, 'A_ACTIVATED'))
        self.assertTrue(any(a.security_id == 'id-OLD' for a in events(result, 'A_ACTIVATED')))
        self.assertTrue(all(dict(a.details)['a_timestamp'] >= self.gap
                            and dict(a.details)['a_price'] >= D('1.07')
                            for a in events(result,'A_ACTIVATED') if a.security_id=='id-NEW'))
        self.assertEqual(events(result,'D_CONFIRMED'),[])


class CorporateFlagTests(unittest.TestCase):
    def action(self, **changes):
        at = datetime.combine(DAY, datetime.min.time(), NEW_YORK).replace(hour=9, minute=30)
        action = SplitAction('id-AAA', 'old', 'basis', F(2), at, at, 'verified-split', True, True)
        return replace(action, **changes)

    def test_true_flags_authorize_exact_normalization(self):
        action = self.action()
        item = normalize(D(2), provenance(action.effective_at, 'old'), 'id-AAA', 'basis',
                         action.effective_at, replace(dataset(), actions=(action,)))
        self.assertEqual(item.value, F(1))

    def test_explicit_false_verification_still_rejects(self):
        action = self.action(verified=False)
        with self.assertRaises(HistoricalInputError):
            normalize(D(2), provenance(action.effective_at, 'old'), 'id-AAA', 'basis',
                      action.effective_at, replace(dataset(), actions=(action,)))

    def test_explicit_false_simple_action_still_rejects(self):
        action = self.action(simple_share_denomination=False)
        with self.assertRaises(HistoricalInputError):
            normalize(D(2), provenance(action.effective_at, 'old'), 'id-AAA', 'basis',
                      action.effective_at, replace(dataset(), actions=(action,)))

    def test_every_non_boolean_flag_is_rejected_without_coercion(self):
        for field in ('verified', 'simple_share_denomination'):
            for value in ('true', 'false', 1, 0, D(1), D(0), F(1), F(0), None, object(), [], {}):
                with self.subTest(field=field, value=value), self.assertRaises(TypeError):
                    self.action(**{field:value})

    def test_malformed_flags_cannot_enter_or_change_universe_and_weekly_inputs(self):
        data = with_setup(dataset())
        action = self.action()
        data = replace(data, actions=(action,),
            daily=tuple(replace(r, high=multiply(r.high,D(2)), low=multiply(r.low,D(2)),
                                close=multiply(r.close,D(2)), volume=D(1000000),
                                provenance=replace(r.provenance,share_basis_id='old')) for r in data.daily),
            closes=tuple(replace(r,price=multiply(r.price,D(2)),
                                provenance=replace(r.provenance,share_basis_id='old')) for r in data.closes),
            weekly_records=tuple(replace(r,bar=replace(r.bar,high=multiply(r.bar.high,D(2)),share_basis_id='old'),
                                    provenance=replace(r.provenance,share_basis_id='old')) for r in data.weekly_records))
        before = evaluate_universe(data, 'id-AAA', ENTRY, D('1.11'))
        weekly = HistoricalBacktest(data)._weekly('id-AAA', DAY, 'basis', ENTRY)
        result = HistoricalBacktest(data).run(DAY, DAY)
        self.assertEqual(result.summary.total_net_pnl, D(60))
        for field in ('verified', 'simple_share_denomination'):
            for value in ('false', 'true', 1, 0, None):
                with self.subTest(field=field, value=value), self.assertRaises(TypeError):
                    replace(data, actions=(replace(action, **{field:value}),))
        self.assertEqual(evaluate_universe(data, 'id-AAA', ENTRY, D('1.11')), before)
        self.assertEqual((before.prior_close,before.adv10,before.adr20_pct,before.eligible),
                         (F(9,10),F(2000000),F(10),True))
        self.assertEqual(HistoricalBacktest(data)._weekly('id-AAA', DAY, 'basis', ENTRY), weekly)


class WeeklyOrderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = with_setup(dataset())
        original = data.weekly_records[-2]
        records = data.weekly_records + (replace(original,bar=replace(original.bar,high=D('1.5'))),
                                       replace(original,bar=replace(original.bar,high=D('1.7'))))
        orders = (records, tuple(reversed(records)), records[::2]+records[1::2])
        cls.results = tuple(HistoricalBacktest(replace(data,weekly_records=order)).run(DAY,DAY)
                            for order in orders)

    def test_conflicts_remain_unavailable_with_identical_economics_and_reason(self):
        for result in self.results:
            self.assertEqual(result.summary.entries, 0)
            self.assertEqual(events(result,'ENTRY_CONSUMED')[0].reason.value,'ENTRY_WEEKLY_DATA_UNAVAILABLE')
            self.assertEqual(result.summary,self.results[0].summary)

    def test_all_conflicting_evidence_is_retained(self):
        weekly = events(self.results[0],'ENTRY_WEEKLY_BAR')
        highs = [dict(a.details)['high'] for a in weekly]
        self.assertIn(F(3,2),highs)
        self.assertIn(F(17,10),highs)

    def test_normal_reversed_shuffled_audits_and_json_are_identical(self):
        self.assertTrue(all(r.audit == self.results[0].audit for r in self.results))
        self.assertTrue(all(r.to_json() == self.results[0].to_json() for r in self.results))

    def test_normal_reversed_shuffled_fingerprints_are_identical(self):
        self.assertTrue(all(r.manifest == self.results[0].manifest for r in self.results))


class MultiSecurityReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = dataset((('id-A','A'),('id-B','B')), calendar=Calendar(early=(DAY,)))
        cls.data = with_setup(with_setup(cls.data,'id-A','A'),'id-B','B',outcome='loss')
        cls.reference = HistoricalBacktest(cls.data).run(DAY,DAY)

    def compare_replay(self, phase):
        data, reference = self.data, self.reference
        engines = tuple(PortfolioEngine(data.calendar,data.ticks,run_id='historical-run',
                        data_version=reference.manifest.dataset_sha256) for _ in range(2))
        for engine in engines:
            engine.start_session(DAY)
        approvals = tuple(t.completed.entry.approval for t in reference.trades)
        by_time = {}
        for source in data.minute_records:
            if source.interval.timestamp >= data.calendar.session_for(DAY).open:
                by_time.setdefault(source.interval.timestamp, []).append(source.interval)
        paused_at = ENTRY+timedelta(minutes=1)
        for at, values in sorted(by_time.items()):
            bars = tuple(sorted(values,key=lambda b:b.security_id))
            scheduled = tuple(a for a in approvals if a.scheduled_timestamp == at)
            clean, replay = engines
            clean.on_open(at,bars,scheduled)
            bad = tuple(MarketInterval(b.security_id,b.ticker,b.timestamp,K.MISSING,'late-source',
                        data_quality_reason='replacement required') if b.security_id=='id-A' else b for b in bars)
            if at == paused_at and phase == 'OPEN':
                replay.on_open(at,bad,scheduled)
                replay.resume_with_replacements(tuple(b for b in bars if b.security_id=='id-A'))
            else:
                replay.on_open(at,bars,scheduled)
            clean.on_close(at+timedelta(minutes=1),bars)
            if at == paused_at and phase == 'COMPLETION':
                # A's OPEN partial and B's OPEN final exit already happened.
                # 10,000 - two 1,110 entries + A's 615 partial + B's 1,050 exit.
                self.assertEqual(replay.state.cash,D(9445))
                self.assertEqual(replay.state.realized_net,D(0))
                replay.on_close(at+timedelta(minutes=1),bad)
                replay.resume_with_replacements(tuple(b for b in bars if b.security_id=='id-A'))
            else:
                replay.on_close(at+timedelta(minutes=1),bars)
            self.assertEqual(clean.state,replay.state)
            self.assertEqual(clean.completed,replay.completed)
        self.assertEqual(clean.completed,tuple(t.completed for t in reference.trades))
        self.assertEqual(clean.state.ending_equity,D(10000))
        self.assertEqual((clean.state.cash,clean.state.exposure,clean.state.realized_net,
                          clean.state.completed_trades),(D(10000),D(0),D(0),2))
        # Pause/replay diagnostics intentionally differ; economic serialization must not.
        self.assertEqual(serialized((clean.state,clean.completed)),serialized((replay.state,replay.completed)))

    def test_multi_security_completion_replay_matches_clean_state_and_results(self):
        self.compare_replay('COMPLETION')

    def test_multi_security_open_replay_matches_clean_state_and_results(self):
        self.compare_replay('OPEN')


class MidLookbackSplitTests(unittest.TestCase):
    def setUp(self):
        data = dataset()
        effective = datetime(2026,9,30,9,30,tzinfo=NEW_YORK)
        self.action = SplitAction('id-AAA','old','basis',F(2),effective,effective,'mid-lookback-split',True,True)
        daily = tuple(replace(r,high=D('2.2'),low=D(2),close=D(2),volume=D(600000),
                              provenance=replace(r.provenance,share_basis_id='old'))
                      if r.completed_at < effective else replace(r,volume=D(1800000)) for r in data.daily)
        weekly = tuple(replace(r,bar=replace(r.bar,high=D('1.6'),share_basis_id='old'),
                               provenance=replace(r.provenance,share_basis_id='old'))
                       if r.bar.completed_at < effective else r for r in data.weekly_records)
        self.data = replace(data,actions=(self.action,),daily=daily,weekly_records=weekly,
                            closes=(replace(data.closes[0],price=D(1)),))

    def test_mixed_old_and_new_observations_normalize_exactly_once(self):
        result = evaluate_universe(self.data,'id-AAA',ENTRY,D('1.03'))
        # Sept 24,25,28,29: 600k * 2; Sept 30, Oct 1,2,5,6,7: 1.8m.
        self.assertEqual(result.adv10,F(1560000))
        self.assertEqual(result.prior_close,F(1))
        self.assertTrue(result.change_eligible)
        volumes = [e for e in result.normalization if e.volume]
        self.assertEqual([e.cumulative_factor for e in volumes],[F(2)]*4+[F(1)]*6)

    def test_mixed_weekly_prices_and_adr_remain_on_compatible_basis(self):
        result = evaluate_universe(self.data,'id-AAA',ENTRY,D('1.03'))
        self.assertEqual(result.adr20_pct,F(10))
        self.assertTrue(result.eligible)
        weekly = HistoricalBacktest(self.data)._weekly('id-AAA',DAY,'basis',ENTRY)
        self.assertTrue(all(b.high==F(4,5) for b in weekly.bars))

    def test_future_split_does_not_rewrite_mid_lookback_results(self):
        future = SplitAction('id-AAA','basis','future',F(3),ENTRY+timedelta(days=1),ENTRY,
                             'announced-future-split',True,True)
        before = evaluate_universe(self.data,'id-AAA',ENTRY,D('1.03'))
        after = evaluate_universe(replace(self.data,actions=(self.action,future)),'id-AAA',ENTRY,D('1.03'))
        self.assertEqual(after,before)


def coherent_two_day_dataset():
    """Daily/weekly history and official closes agree with actual RTH minute data."""
    second = date(2026,10,9)
    data = dataset(days=(DAY,second))
    data = with_setup(data)
    data = with_setup(data,scheduled=ENTRY+timedelta(days=1),outcome='loss')
    records = tuple(replace(r,interval=replace(r.interval,**{
        name:multiply(getattr(r.interval,name),D('1.2')) for name in ('open','high','low','close')}))
        if r.interval.timestamp.date()==second else r for r in data.minute_records)
    daily = []
    day = data.references[0].listing_date
    while day <= second:
        session = data.calendar.session_for(day)
        if session:
            bars = [r.interval for r in records if session.open <= r.interval.timestamp < session.close]
            high,low,close,volume = ((max(b.high for b in bars),min(b.low for b in bars),bars[-1].close,
                                     sum((b.volume for b in bars),D(0))) if bars
                                    else (D('1.08'),D('.90'),D('.90'),D(2000000)))
            daily.append(DailySession('id-AAA',day,session.close,provenance(session.close),
                                      high,low,close,volume,True))
        day += timedelta(days=1)
    by_day = {r.trading_date:r for r in daily}
    closes = tuple(PriorClose(r.security_id,r.trading_date,by_day[r.trading_date].close,
                              by_day[r.trading_date].provenance,True) for r in data.closes)
    weekly = []
    for source in data.weekly_records:
        observations = [r for r in daily if source.bar.week_start <= r.trading_date
                        < source.bar.week_start+timedelta(days=7)]
        if observations:
            weekly.append(HistoricalWeekly(replace(source.bar,high=max(r.high for r in observations),
                open=observations[0].close,low=min(r.low for r in observations),close=observations[-1].close),
                source.provenance))
    caps = tuple(MarketCapitalization('id-AAA',r.interval.timestamp,r.interval.end,r.interval.timestamp,
                 'verified-historical-share-count-and-open',True,shares_outstanding=D(50000000),
                 compatible_price=r.interval.open,shares_basis_id='basis',price_basis_id='basis',
                 share_count_point_in_time_verified=True) for r in records
                 if data.calendar.session_for(r.interval.timestamp.date()).open <= r.interval.timestamp)
    return replace(data,minute_records=records,daily=tuple(daily),closes=closes,weekly_records=tuple(weekly),caps=caps)


class CoherentMultiDayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = coherent_two_day_dataset()
        cls.result = HistoricalBacktest(cls.data).run(DAY,date(2026,10,9))

    def test_official_prior_close_chain_matches_actual_daily_and_minute_close(self):
        daily = {r.trading_date:r for r in self.data.daily}
        for close in self.data.closes:
            self.assertEqual(close.price,daily[close.trading_date].close)
        session = self.data.calendar.session_for(DAY)
        bars = [r.interval for r in self.data.minute_records if session.open <= r.interval.timestamp < session.close]
        self.assertEqual(daily[DAY].volume,sum((b.volume for b in bars),D(0)))
        self.assertEqual((daily[DAY].high,daily[DAY].low,daily[DAY].close),
                         (max(b.high for b in bars),min(b.low for b in bars),bars[-1].close))
        self.assertEqual(self.result.trades[1].completed.entry.approval.prior_regular_close,F(111,100))

    def test_bod_carry_and_daily_counters_are_coherent(self):
        first,second = self.result.sessions
        self.assertEqual((first.state.bod_equity,first.state.ending_equity),(D(10000),D(10060)))
        self.assertEqual((second.state.bod_equity,second.state.ending_equity),(D(10060),D(9988)))
        self.assertEqual([s.state.new_entries for s in self.result.sessions],[1,1])
        self.assertEqual([s.state.realized_net for s in self.result.sessions],[D(60),D(-72)])
        self.assertEqual([s.state.consecutive_losses for s in self.result.sessions],[0,1])
        self.assertTrue(all(not s.state.locked for s in self.result.sessions))

    def test_ledger_and_summary_agree_with_hand_calculated_two_day_cash(self):
        result = self.result
        self.assertEqual([t.completed.net_pnl for t in result.trades],[D(60),D(-72)])
        self.assertEqual([t.realized_r for t in result.trades],[F(1),F(-1)])
        self.assertEqual(result.summary.ending_confirmed_equity,D(9988))
        self.assertEqual(result.summary.total_net_pnl,D(-12))
        self.assertEqual(result.summary.total_return,F(-3,2500))
        self.assertEqual(result.summary.profit_factor,F(5,6))
        self.assertEqual(result.summary.maximum_drawdown_confirmed_path,F(33,2530))
        self.assertEqual(result.summary.total_net_pnl,sum((t.completed.net_pnl for t in result.trades),D(0)))

    def test_second_day_universe_uses_measured_prior_session_history_only(self):
        opening = self.data.calendar.session_for(date(2026,10,9)).open
        evaluation = next(e for e in self.result.universe_evaluations if e.evaluated_at==opening)
        daily = {r.trading_date:r for r in self.data.daily}
        self.assertEqual(evaluation.history_dates[-1],DAY)
        self.assertEqual(evaluation.prior_close,F(111,100))
        self.assertEqual(evaluation.adv10,(F(18000000)+F(daily[DAY].volume))/10)
        self.assertTrue(all(r.completed_at < opening for r in evaluation.daily_records))
        self.assertTrue(all(r.trading_date < date(2026,10,9) for r in evaluation.daily_records))
