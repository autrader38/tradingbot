"""Actual Phase 1–8 integration, independent cash expectations and timing."""

import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D, localcontext
from fractions import Fraction as F

from tradingbot_backtest.historical_data import Adjustment, HistoricalInputError, SecurityType, SplitAction
from tradingbot_backtest.historical_results import canonical, serialized
from tradingbot_backtest.historical_runner import HistoricalBacktest
from tradingbot_backtest.market import IntervalClassification as K
from tradingbot_backtest.portfolio import LockoutReason as L
from tradingbot_backtest.trade_construction import TickPurpose
from tests.historical_fixtures import (DAY, NEW_YORK, Calendar, Ticks, dataset,
                                      minute, replace_minute, with_setup)

ENTRY=datetime(2026,10,8,10,15,tzinfo=NEW_YORK)


def events(result, name):
    return [a for a in result.audit if getattr(a,'event_type',getattr(a,'event',None))==name]


def economic_entry(entry):
    # Dataset mutations intentionally change provenance/fingerprint identities.
    # Preserve EVERY financial/strategy field when comparing OPEN decisions.
    def strip(value):
        if isinstance(value,dict):
            return {k:strip(v) for k,v in value.items() if k not in ('run_id','data_version','trade_id')}
        if isinstance(value,list): return [strip(v) for v in value]
        return value
    return strip(canonical(entry))


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base=dataset()
        cls.winning=with_setup(cls.base)
        cls.win=HistoricalBacktest(cls.winning).run(DAY,DAY)
        cls.loss=HistoricalBacktest(with_setup(cls.base,outcome='loss')).run(DAY,DAY)

    def run_data(self,data): return HistoricalBacktest(data).run(DAY,DAY)

    def test_complete_abcd_trade_drives_all_phases(self):
        r=self.win
        for name in ('A_ACTIVATED','B_CONFIRMED','C_LOCKED','D_CONFIRMED','ENTRY_APPROVED',
                     'SIMULATED_ENTRY_FILL','SIMULATED_EXIT_FILL','PORTFOLIO_SESSION_CLOSED'):
            self.assertTrue(events(r,name),name)
        self.assertEqual(r.summary.entries,1)
        self.assertEqual(r.summary.completed_trades,1)

    def test_partial_runner_hand_calculated_cash(self):
        t=self.win.trades[0].completed
        self.assertEqual(t.entry.quantity,1000)
        self.assertEqual([(f.quantity,f.simulated_fill_price) for f in t.fills],[(500,D('1.23')),(500,D('1.11'))])
        self.assertEqual(t.net_pnl,D(60))
        self.assertEqual(self.win.summary.ending_confirmed_equity,D(10060))
        self.assertEqual(self.win.sessions[0].state.realized_net,D(60))

    def test_full_stop_loss_hand_calculated(self):
        self.assertEqual(self.loss.trades[0].completed.fills[0].quantity,1000)
        self.assertEqual(self.loss.summary.total_net_pnl,D(-60))
        self.assertEqual(self.loss.summary.ending_confirmed_equity,D(9940))

    def test_eod_closes_at_final_open(self):
        r=self.run_data(with_setup(self.base,outcome='eod'))
        t=r.trades[0].completed
        self.assertEqual(t.final_exit_available_at,ENTRY.replace(hour=15,minute=59))
        self.assertEqual(t.final_exit_price,D('1.11'))
        self.assertEqual(t.net_pnl,D(0))

    def test_zero_trade_multisymbol_dataset(self):
        r=self.run_data(dataset((('id-Z','Z'),('id-A','A'))))
        self.assertEqual(r.summary.entries,0)
        self.assertEqual(r.summary.ending_confirmed_equity,D(10000))

    def test_holiday_no_session(self):
        r=self.run_data(dataset(calendar=Calendar(holidays=(DAY,))))
        self.assertEqual(r.sessions,())
        self.assertFalse(r.summary.incomplete)

    def test_early_close_eod(self):
        r=self.run_data(with_setup(dataset(calendar=Calendar(early=(DAY,))),outcome='eod'))
        self.assertEqual(r.trades[0].completed.final_exit_available_at,ENTRY.replace(hour=12,minute=59))

    def test_no_prior_session_pattern_reuse(self):
        other=date(2026,10,9)
        data=dataset(days=(DAY,other))
        r=HistoricalBacktest(data).run(DAY,other)
        self.assertEqual(len(r.sessions),2)
        self.assertEqual(r.summary.entries,0)
        selected=events(r,'A_SELECTED')
        for a in selected:
            self.assertEqual(dict(a.details)['a_timestamp'].date(),a.recorded_at.date())

    def test_bod_equity_carries_confirmed_prior_ending(self):
        other=date(2026,10,9)
        r=HistoricalBacktest(with_setup(dataset(days=(DAY,other)))).run(DAY,other)
        self.assertEqual(r.sessions[1].state.bod_equity,D(10060))
        self.assertEqual(r.sessions[1].state.new_entries,0)
        self.assertEqual(r.sessions[1].state.realized_net,D(0))
        self.assertFalse(r.sessions[1].state.locked)

    def test_premarket_cannot_supply_pattern(self):
        data=replace_minute(self.base,'id-AAA',ENTRY.replace(hour=9,minute=10),
            low=D('.1'),high=D(4),open=D(1),close=D(1),volume=D(100000))
        r=self.run_data(data)
        self.assertFalse(events(r,'A_ACTIVATED'))

    def test_premarket_supplies_immediate_previous_twenty(self):
        early=ENTRY.replace(hour=9,minute=40)
        r=self.run_data(with_setup(self.base,scheduled=early))
        activated=events(r,'A_ACTIVATED')
        self.assertTrue(activated)
        # Context rejects before 10:15, but impulse legitimately uses premarket volume.
        self.assertEqual(r.summary.entries,0)
        evaluations=events(r,'RVOL_EVALUATED')
        self.assertEqual(dict(evaluations[0].details)['baseline_slots'],20)

    def test_absent_premarket_does_not_use_yesterday(self):
        data=replace(self.base,minute_records=tuple(r for r in self.base.minute_records
            if r.interval.timestamp>=self.base.calendar.session_for(DAY).open))
        r=self.run_data(with_setup(data,scheduled=ENTRY.replace(hour=9,minute=40)))
        self.assertFalse(events(r,'A_ACTIVATED'))
        self.assertEqual(dict(events(r,'RVOL_EVALUATED')[0].details)['baseline_slots'],0)

    def test_verified_zero_premarket_slots_retained(self):
        at=self.base.calendar.session_for(DAY).open
        records=[]
        for rec in self.base.minute_records:
            if at-timedelta(minutes=20)<=rec.interval.timestamp<at:
                rec=minute('id-AAA','AAA',rec.interval.timestamp,kind=K.NO_TRADE)
            records.append(rec)
        r=self.run_data(replace(self.base,minute_records=tuple(records)))
        first=dict(events(r,'RVOL_EVALUATED')[0].details)
        self.assertEqual(first['baseline_slots'],20)
        self.assertEqual(first['baseline'],'0')
        self.assertIsNone(first['ratio'])

    def test_later_high_cannot_alter_entry_construction(self):
        data=replace_minute(self.winning,'id-AAA',ENTRY,high=D(9))
        self.assertEqual(economic_entry(self.run_data(data).trades[0].completed.entry),economic_entry(self.win.trades[0].completed.entry))

    def test_later_low_cannot_alter_entry_construction(self):
        data=replace_minute(self.winning,'id-AAA',ENTRY,low=D('.1'))
        r=self.run_data(data)
        self.assertEqual(economic_entry(r.trades[0].completed.entry),economic_entry(self.win.trades[0].completed.entry))
        self.assertEqual(r.trades[0].completed.final_exit_available_at,ENTRY+timedelta(minutes=1))

    def test_later_close_cannot_alter_entry_construction(self):
        data=replace_minute(self.winning,'id-AAA',ENTRY,close=D('1.105'))
        self.assertEqual(economic_entry(self.run_data(data).trades[0].completed.entry),economic_entry(self.win.trades[0].completed.entry))

    def test_later_volume_cannot_alter_entry_construction(self):
        data=replace_minute(self.winning,'id-AAA',ENTRY,volume=D('10000000'))
        self.assertEqual(economic_entry(self.run_data(data).trades[0].completed.entry),economic_entry(self.win.trades[0].completed.entry))

    def test_scheduled_no_trade_cancels_single_opportunity(self):
        data=replace(self.winning,minute_records=tuple(minute('id-AAA','AAA',ENTRY,kind=K.NO_TRADE)
            if r.interval.timestamp==ENTRY else r for r in self.winning.minute_records))
        r=self.run_data(data)
        self.assertEqual(r.summary.entries,0)
        self.assertEqual(events(r,'PENDING_ENTRY_CANCELED')[0].reason.value,'ENTRY_NO_TRADE')

    def test_scheduled_missing_cause_preserved(self):
        data=replace(self.winning,minute_records=tuple(minute('id-AAA','AAA',ENTRY,kind=K.MISSING,cause='point-in-time gap')
            if r.interval.timestamp==ENTRY else r for r in self.winning.minute_records))
        r=self.run_data(data)
        event=events(r,'PENDING_ENTRY_CANCELED')[0]
        self.assertEqual(event.reason.value,'ENTRY_DATA_MISSING')
        self.assertEqual(dict(event.details)['data_quality_reason'],'point-in-time gap')

    def test_unavailable_open_cancels_not_postpones(self):
        data=replace(self.winning,minute_records=tuple(replace(r,opening_available_at=r.interval.end)
            if r.interval.timestamp==ENTRY else r for r in self.winning.minute_records))
        self.assertEqual(self.run_data(data).summary.entries,0)

    def test_missing_completed_entry_minute_preserves_known_entry(self):
        data=replace(self.winning,minute_records=tuple(replace(r,completed_available_at=r.interval.end+timedelta(seconds=1))
            if r.interval.timestamp==ENTRY else r for r in self.winning.minute_records))
        r=self.run_data(data)
        self.assertTrue(r.summary.incomplete)
        self.assertEqual(r.summary.entries,1)
        self.assertEqual(r.unresolved_positions[0].remaining_quantity,1000)
        self.assertEqual(r.unresolved_positions[0].entry.entry_timestamp,ENTRY)
        self.assertEqual(r.last_processed_at,ENTRY+timedelta(minutes=1))
        self.assertIsNone(r.summary.ending_confirmed_equity)
        self.assertIsNone(r.summary.total_return)

    def test_missing_held_open_stops_without_using_next_prices(self):
        missing=ENTRY+timedelta(minutes=1)
        data=replace(self.winning,minute_records=tuple(r for r in self.winning.minute_records if r.interval.timestamp!=missing))
        r=self.run_data(data)
        self.assertTrue(r.summary.incomplete)
        self.assertEqual(r.last_processed_at,missing)
        self.assertEqual(r.trades,())
        self.assertEqual(r.unresolved_positions[0].data_quality_reason,'missing source candle')

    def test_fatal_tick_stops_later_sessions(self):
        other=date(2026,10,9)
        data=with_setup(dataset(days=(DAY,other),ticks=Ticks((TickPurpose.BREAKEVEN_STOP,))))
        r=HistoricalBacktest(data).run(DAY,other)
        self.assertEqual(r.incomplete_reason,'INCOMPLETE_TICK_SIZE_UNAVAILABLE_OPEN_POSITION')
        self.assertEqual(len(r.sessions),1)
        self.assertEqual(r.unresolved_positions[0].remaining_quantity,500)
        self.assertIsNone(r.summary.ending_confirmed_equity)

    def test_final_no_trade_unresolved_normal_close(self):
        self.check_eod_unresolved(Calendar(),15)

    def test_final_no_trade_unresolved_early_close(self):
        self.check_eod_unresolved(Calendar(early=(DAY,)),12)

    def check_eod_unresolved(self,calendar,hour):
        other=date(2026,10,9)
        data=with_setup(dataset(days=(DAY,other),calendar=calendar),outcome='eod')
        at=ENTRY.replace(hour=hour,minute=59)
        data=replace(data,minute_records=tuple(minute('id-AAA','AAA',at,kind=K.NO_TRADE)
            if r.interval.timestamp==at else r for r in data.minute_records))
        r=HistoricalBacktest(data).run(DAY,other)
        self.assertEqual(r.incomplete_reason,'INCOMPLETE_UNRESOLVED_EOD_LIQUIDITY')
        self.assertEqual(len(r.sessions),1)
        self.assertEqual(r.trades,())
        self.assertEqual(r.unresolved_positions[0].remaining_quantity,1000)
        self.assertTrue(r.unresolved_positions[0].eod_instruction_active)
        self.assertIsNone(r.summary.total_net_pnl)

    def test_simultaneous_rvol_ranking_and_input_order_independence(self):
        data=dataset((('id-Z','Z'),('id-A','A')))
        data=with_setup(data,'id-Z','Z',boost='900')
        data=with_setup(data,'id-A','A',boost='600')
        r=self.run_data(data)
        allocations=events(r,'PORTFOLIO_ALLOCATION')
        self.assertEqual([a.ticker for a in allocations],['Z','A'])
        changed=replace(data,references=tuple(reversed(data.references)),minute_records=tuple(reversed(data.minute_records)),
            daily=tuple(reversed(data.daily)),weekly_records=tuple(reversed(data.weekly_records)),
            caps=tuple(reversed(data.caps)),closes=tuple(reversed(data.closes)),bases=tuple(reversed(data.bases)))
        self.assertEqual(r.to_json(),self.run_data(changed).to_json())

    def test_simultaneous_equal_rvol_alphabetical(self):
        data=with_setup(with_setup(dataset((('id-Z','Z'),('id-A','A'))),'id-Z','Z'),'id-A','A')
        self.assertEqual([a.ticker for a in events(self.run_data(data),'PORTFOLIO_ALLOCATION')],['A','Z'])

    def four_symbol_case(self, opening_exit):
        symbols=tuple(('id-'+ticker,ticker) for ticker in ('A','B','C','D'))
        data=dataset(symbols)
        for security,ticker in symbols[:3]:
            data=with_setup(data,security,ticker,outcome='eod')
        data=with_setup(data,'id-D','D',scheduled=ENTRY+timedelta(minutes=2),outcome='eod')
        at=ENTRY+timedelta(minutes=2)
        return replace_minute(data,'id-A',at,open=D('1.05') if opening_exit else D('1.11'),
            low=D('1.04'),high=D('1.12'),close=D('1.11'))

    def test_same_open_exit_releases_slot_before_new_allocation(self):
        r=self.run_data(self.four_symbol_case(True))
        self.assertEqual(r.summary.entries,4)
        at=ENTRY+timedelta(minutes=2)
        ordered=[a for a in r.audit if getattr(a,'recorded_at',None)==at]
        sale=next(i for i,a in enumerate(ordered) if getattr(a,'event_type',None)=='PORTFOLIO_EXIT_ACCOUNTING')
        allocation=next(i for i,a in enumerate(ordered) if getattr(a,'event_type',None)=='PORTFOLIO_ALLOCATION')
        self.assertLess(sale,allocation)
        self.assertTrue(dict(ordered[allocation].details)['accepted'])

    def test_later_intraminute_exit_cannot_release_open_slot(self):
        r=self.run_data(self.four_symbol_case(False))
        self.assertEqual(r.summary.entries,3)
        allocation=next(a for a in events(r,'PORTFOLIO_ALLOCATION') if a.ticker=='D')
        self.assertFalse(dict(allocation.details)['accepted'])
        self.assertEqual(dict(allocation.details)['description'],'MAXIMUM_SIMULTANEOUS_POSITIONS')

    def test_sequential_multisymbol_sizing_uses_updated_state(self):
        data=dataset(tuple(('id-'+t,t) for t in ('Z','A','B','C')))
        for t in ('Z','A','B','C'):
            data=with_setup(data,'id-'+t,t,outcome='eod')
        r=self.run_data(data)
        allocations=events(r,'PORTFOLIO_ALLOCATION')
        self.assertEqual([a.ticker for a in allocations],['A','B','C','Z'])
        self.assertEqual([dict(a.details)['cash_before'] for a in allocations],
                         [D(10000),D(8890),D(7780),D(6670)])
        self.assertEqual([dict(a.details)['accepted'] for a in allocations],[True,True,True,False])

    def test_repeated_runs_are_identical(self):
        self.assertEqual(self.win.to_json(),self.run_data(self.winning).to_json())

    def test_current_active_list_not_used(self):
        ref=replace(self.winning.references[0],delisting_at=ENTRY+timedelta(days=30))
        r=self.run_data(replace(self.winning,references=(ref,)))
        self.assertEqual(r.summary.entries,1)

    def test_already_delisted_population_exclusion_is_auditable(self):
        ref=replace(self.winning.references[0],delisting_at=self.base.calendar.session_for(DAY).open)
        r=self.run_data(replace(self.winning,references=(ref,)))
        self.assertEqual(r.summary.entries,0)
        exclusion=events(r,'UNIVERSE_EXCLUDED_AT_OPEN')[0]
        self.assertTrue(dict(exclusion.details)['delisted'])
        self.assertIn('SECURITY_MASTER_UNAVAILABLE',dict(exclusion.details)['evaluation'].failures)

    def test_excluded_etf_never_activates(self):
        ref=replace(self.winning.references[0],security_type=SecurityType.ETF)
        self.assertEqual(self.run_data(replace(self.winning,references=(ref,))).summary.entries,0)

    def test_missing_history_prevents_activation(self):
        data=replace(self.winning,daily=tuple(r for r in self.winning.daily if r.trading_date!=date(2026,10,1)))
        self.assertFalse(events(self.run_data(data),'A_ACTIVATED'))

    def test_no_same_interval_price_reuse_after_final_exit(self):
        r=self.win
        exit_at=r.trades[0].completed.exit_interval_start
        excluded=events(r,'PRICE_PATTERN_EXCLUDED')
        self.assertTrue(any(dict(a.details)['interval']==exit_at for a in excluded))
        for a in events(r,'A_SELECTED'):
            if a.recorded_at>exit_at+timedelta(minutes=1):
                self.assertGreater(dict(a.details)['a_timestamp'],exit_at)

    def test_consecutive_loss_lockout_end_to_end(self):
        data=self.base
        for offset in (0,40,80,120):
            data=with_setup(data,scheduled=ENTRY+timedelta(minutes=offset),outcome='loss')
        r=self.run_data(data)
        self.assertEqual(r.summary.entries,3)
        self.assertEqual(r.sessions[0].state.consecutive_losses,3)
        self.assertIn(L.CONSECUTIVE_NET_LOSSES,r.sessions[0].state.lockout_reasons)

    def test_daily_realized_loss_lockout_end_to_end(self):
        data=with_setup(self.base,outcome='big_loss')
        for offset in (40,80):
            data=with_setup(data,scheduled=ENTRY+timedelta(minutes=offset),outcome='big_loss')
        r=self.run_data(data)
        self.assertEqual(r.summary.entries,2)
        self.assertEqual(r.sessions[0].state.realized_net,D(-220))
        self.assertIn(L.DAILY_NET_REALIZED_LOSS,r.sessions[0].state.lockout_reasons)

    def test_fifth_entry_locks_sixth_end_to_end(self):
        data=self.base
        # Half-hour boundaries deliberately make both required context extremes
        # identical; this isolates the entry-count rule from context rejection.
        for offset in (0,30,60,90,120,150):
            data=with_setup(data,scheduled=ENTRY+timedelta(minutes=offset),outcome='win')
        r=self.run_data(data)
        self.assertEqual(r.summary.entries,5)
        self.assertIn(L.DAILY_ENTRY_LIMIT,r.sessions[0].state.lockout_reasons)

    def test_quarantine_does_not_erase_volume_history(self):
        excluded=events(self.win,'PRICE_PATTERN_EXCLUDED')
        self.assertTrue(excluded)
        self.assertTrue(all(dict(a.details)['volume_retained'] for a in excluded))
        after=ENTRY+timedelta(minutes=3)
        baseline=next(a for a in events(self.win,'RVOL_EVALUATED') if a.interval_start==after)
        self.assertEqual(dict(baseline.details)['baseline_slots'],20)

    def test_audit_information_timestamps_are_monotonic(self):
        at=[getattr(a,'recorded_at',getattr(a,'available_at',None)) for a in self.win.audit]
        self.assertEqual(at,sorted(at))

    def test_source_provenance_in_audit(self):
        event=events(self.win,'INTERVAL_COMPLETED_SOURCE')[0]
        p=dict(event.details)['provenance']
        self.assertEqual(p.source_id,'independent-source')
        self.assertEqual(p.share_basis_id,'basis')

    def test_result_is_immutable(self):
        with self.assertRaises(FrozenInstanceError): self.win.summary.entries=3

    def test_manifest_changes_with_data_not_storage_order(self):
        changed=replace_minute(self.winning,'id-AAA',ENTRY,volume=D(12345))
        self.assertNotEqual(self.win.manifest.dataset_sha256,self.run_data(changed).manifest.dataset_sha256)

    def test_utc_stored_intervals_keep_same_decisions(self):
        records=tuple(replace(r,interval=replace(r.interval,timestamp=r.interval.timestamp.astimezone(timezone.utc)),
            opening_available_at=r.opening_available_at.astimezone(timezone.utc),
            completed_available_at=r.completed_available_at.astimezone(timezone.utc)) for r in self.winning.minute_records)
        r=self.run_data(replace(self.winning,minute_records=records))
        self.assertEqual(r.summary,self.win.summary)
        self.assertEqual(serialized(r.trades),serialized(self.win.trades))

    def test_low_decimal_context_cannot_change_economics(self):
        with localcontext() as context:
            context.prec=3
            r=self.run_data(self.winning)
        self.assertEqual(r.summary,self.win.summary)

    def test_conflicting_minute_not_silently_accepted(self):
        record=next(r for r in self.winning.minute_records if r.interval.timestamp==ENTRY)
        data=replace(self.winning,minute_records=self.winning.minute_records+(record,))
        self.assertEqual(self.run_data(data).summary.entries,0)

    def test_unknown_calendar_is_contract_error_not_holiday(self):
        class UnknownCalendar:
            def session_for(self, day): raise HistoricalInputError('unverified exchange calendar')
        with self.assertRaisesRegex(HistoricalInputError,'unverified'):
            self.run_data(replace(self.base,calendar=UnknownCalendar()))

    def test_complete_trade_with_verified_split_normalized_history(self):
        opening=self.base.calendar.session_for(DAY).open
        data=self.winning
        action=SplitAction('id-AAA','old','basis',F(3),opening,opening,'known-effective-split',True,True)
        daily=tuple(replace(r,volume=D(600000),provenance=replace(r.provenance,share_basis_id='old')) for r in data.daily)
        close=replace(data.closes[0],price=D('2.70'),provenance=replace(data.closes[0].provenance,share_basis_id='old'))
        weekly=tuple(replace(r,bar=replace(r.bar,high=D('2.4'),share_basis_id='old'),
                     provenance=replace(r.provenance,share_basis_id='old')) for r in data.weekly_records)
        data=replace(data,actions=(action,),daily=daily,closes=(close,),weekly_records=weekly)
        r=self.run_data(data)
        self.assertEqual(r.summary.entries,1)
        self.assertEqual(r.summary.total_net_pnl,D(60))
        self.assertEqual(r.trades[0].completed.entry.approval.prior_regular_close,F(9,10))
        self.assertTrue(all(b.high==F(4,5) for b in r.trades[0].completed.entry.approval.weekly.bars))

    def test_unknown_weekly_adjustment_rejects_with_original_cause(self):
        record=self.winning.weekly_records[-2]
        unknown=replace(record,provenance=replace(record.provenance,adjustment=Adjustment.UNKNOWN,
                                                  quality_reason='unsafe adjustment lineage'))
        data=replace(self.winning,weekly_records=tuple(unknown if r==record else r for r in self.winning.weekly_records))
        r=self.run_data(data)
        self.assertEqual(r.summary.entries,0)
        failures=events(r,'ENTRY_WEEKLY_RESULT')
        self.assertTrue(any('unsafe adjustment lineage' in str(a.details) for a in r.audit))

    def test_future_weekly_high_cannot_confirm_resistance(self):
        current=next(r for r in self.winning.weekly_records if r.bar.week_start==date(2026,10,5))
        future=replace(current,bar=replace(current.bar,high=D(100)))
        data=replace(self.winning,weekly_records=tuple(future if r==current else r for r in self.winning.weekly_records))
        self.assertEqual(economic_entry(self.run_data(data).trades[0].completed.entry),
                         economic_entry(self.win.trades[0].completed.entry))

    def test_activation_static_metadata_uses_completion_availability(self):
        activation=ENTRY-timedelta(minutes=5)
        cap=self.winning.caps[0]
        before=replace(cap,valid_until=activation,authoritative_value=D(49000000))
        after=replace(cap,valid_from=activation,available_at=activation,authoritative_value=D(50000000))
        data=replace(self.winning,caps=(before,after))
        r=self.run_data(data)
        activated=events(r,'A_ACTIVATED')[0]
        self.assertEqual(activated.recorded_at,activation)
        self.assertEqual(r.summary.entries,1)

    def test_finite_market_cap_expiry_at_activation_cannot_use_stale_open_flag(self):
        activation=ENTRY-timedelta(minutes=5)
        cap=replace(self.winning.caps[0],valid_until=activation)
        r=self.run_data(replace(self.winning,caps=(cap,)))
        self.assertFalse(events(r,'A_ACTIVATED'))
        self.assertEqual(r.summary.entries,0)

    def test_verified_symbol_change_preserves_active_stable_identity(self):
        changed=ENTRY-timedelta(minutes=3)
        old=replace(self.winning.references[0],valid_until=changed)
        new=replace(self.winning.references[0],ticker='RENAMED',valid_from=changed,available_at=changed)
        records=tuple(replace(r,interval=replace(r.interval,ticker='RENAMED'))
                      if r.interval.timestamp>=changed else r for r in self.winning.minute_records)
        data=replace(self.winning,references=(new,old),minute_records=records)
        r=self.run_data(data)
        self.assertEqual(r.summary.entries,1)
        self.assertEqual(r.trades[0].security_id,'id-AAA')
        self.assertEqual(r.trades[0].ticker,'RENAMED')
        self.assertEqual(r.trades[0].completed.entry.approval.a.ticker,'AAA')
        self.assertEqual(r.summary.total_net_pnl,D(60))
        self.assertEqual(len(events(r,'SECURITY_SYMBOL_CHANGED')),1)

    def test_unavailable_minute_methodology_not_exposed_as_available_evidence(self):
        records=tuple(replace(r,provenance=replace(r.provenance,available_at=r.interval.end+timedelta(days=1),
                      share_basis_id='unavailable-future-basis',adjustment=Adjustment.UNKNOWN))
                      if r.interval.timestamp==ENTRY else r for r in self.winning.minute_records)
        r=self.run_data(replace(self.winning,minute_records=records))
        self.assertEqual(r.summary.entries,0)
        event=next(a for a in events(r,'INTERVAL_COMPLETED_SOURCE') if a.available_at==ENTRY+timedelta(minutes=1))
        self.assertIsNone(dict(event.details)['provenance'])
        self.assertNotIn('unavailable-future-basis',str(event.details))

    def test_unavailable_weekly_basis_cannot_change_rejection_reason(self):
        record=self.winning.weekly_records[-2]
        outputs=[]
        for basis in ('unknown-basis-A','unknown-basis-B'):
            future=replace(record,bar=replace(record.bar,high=D(100),share_basis_id=basis),
                provenance=replace(record.provenance,share_basis_id=basis,available_at=ENTRY+timedelta(seconds=1)))
            data=replace(self.winning,weekly_records=tuple(future if r==record else r for r in self.winning.weekly_records))
            result=self.run_data(data)
            decision=events(result,'ENTRY_CONSUMED')[0]
            self.assertEqual(decision.reason.value,'ENTRY_WEEKLY_DATA_UNAVAILABLE')
            context=events(result,'ENTRY_WEEKLY_BAR')
            self.assertTrue(context)
            self.assertNotIn(basis,str(context))
            outputs.append((decision.reason,dict(decision.details)))
        self.assertEqual(outputs[0],outputs[1])

    def test_late_initial_prior_close_does_not_erase_trustworthy_price_history(self):
        close=replace(self.winning.closes[0],provenance=replace(self.winning.closes[0].provenance,
                      available_at=ENTRY-timedelta(minutes=5)))
        r=self.run_data(replace(self.winning,closes=(close,)))
        activated=events(r,'A_ACTIVATED')[0]
        self.assertEqual(activated.recorded_at,ENTRY-timedelta(minutes=5))
        self.assertEqual(dict(activated.details)['a_timestamp'],ENTRY-timedelta(minutes=7))
        self.assertEqual(r.summary.entries,1)

    def test_initial_unavailable_reference_preserves_premarket_volume(self):
        early=ENTRY.replace(hour=9,minute=40)
        data=with_setup(self.base,scheduled=early)
        close=replace(data.closes[0],provenance=replace(data.closes[0].provenance,
                      available_at=early-timedelta(minutes=5)))
        r=self.run_data(replace(data,closes=(close,)))
        activated=events(r,'A_ACTIVATED')[0]
        self.assertEqual(activated.recorded_at,early-timedelta(minutes=5))
        self.assertEqual(r.summary.entries,0)

    def test_late_initial_identity_seeds_known_rth_lookback_without_retro_signals(self):
        ref=replace(self.winning.references[0],available_at=ENTRY-timedelta(minutes=6))
        r=self.run_data(replace(self.winning,references=(ref,)))
        activated=events(r,'A_ACTIVATED')[0]
        self.assertEqual(activated.recorded_at,ENTRY-timedelta(minutes=5))
        self.assertEqual(dict(activated.details)['a_timestamp'],ENTRY-timedelta(minutes=7))
        self.assertEqual(r.summary.entries,1)
