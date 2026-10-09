"""Exact point-in-time boundaries, expected history and split denominators."""

import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D, localcontext
from fractions import Fraction as F

from tradingbot_backtest.historical_data import Adjustment, HistoricalInputError, SecurityType, SplitAction
from tradingbot_backtest.historical_universe import evaluate_universe, historical_reference, normalize
from tests.historical_fixtures import DAY, NEW_YORK, dataset, provenance


class UniverseTests(unittest.TestCase):
    def setUp(self):
        self.data=dataset()
        self.open=self.data.calendar.session_for(DAY).open

    def evaluate(self, data=None, price='1.11', at=None):
        return evaluate_universe(data or self.data,'id-AAA',at or self.open,D(price))

    def test_common_stock_passes_all_gates(self):
        r=self.evaluate()
        self.assertTrue(r.eligible)
        self.assertEqual(r.adv10,F(2000000))
        self.assertEqual(r.adr20_pct,F(10))

    def test_explicit_excluded_instruments(self):
        for kind in SecurityType:
            if kind==SecurityType.COMMON_STOCK: continue
            with self.subTest(kind=kind):
                r=self.evaluate(replace(self.data,references=(replace(self.data.references[0],security_type=kind),)))
                self.assertFalse(r.static_eligible)
                self.assertIn('EXCLUDED_SECURITY_TYPE_OR_LISTING',r.failures)

    def test_otc_common_stock_excluded(self):
        r=self.evaluate(replace(self.data,references=(replace(self.data.references[0],otc=True),)))
        self.assertFalse(r.static_eligible)

    def test_non_us_listing_excluded(self):
        self.assertFalse(self.evaluate(replace(self.data,references=(replace(self.data.references[0],us_listed=False),))).static_eligible)

    def test_unverified_classification_cannot_be_inferred_from_symbol(self):
        r=self.evaluate(replace(self.data,references=(replace(self.data.references[0],verified=False,ticker='COMMON'),)))
        self.assertIn('SECURITY_MASTER_UNAVAILABLE',r.failures)

    def test_unsupported_listing_cannot_be_guessed(self):
        self.assertFalse(self.evaluate(replace(self.data,references=(replace(self.data.references[0],supported_exchange=False),))).static_eligible)

    def test_price_exact_five_passes(self): self.assertTrue(self.evaluate(price='5').price_eligible)
    def test_price_below_five_passes(self): self.assertTrue(self.evaluate(price='4.99').price_eligible)
    def test_price_above_five_fails(self): self.assertFalse(self.evaluate(price='5.0001').price_eligible)

    def gain(self, price):
        data=replace(self.data,closes=(replace(self.data.closes[0],price=D(1)),))
        return self.evaluate(data,price)

    def test_gain_exact_three_passes(self): self.assertTrue(self.gain('1.03').change_eligible)
    def test_gain_below_three_fails(self): self.assertFalse(self.gain('1.02999').change_eligible)
    def test_gain_above_three_passes(self): self.assertTrue(self.gain('1.03001').change_eligible)

    def test_missing_official_prior_close_excludes(self):
        r=self.evaluate(replace(self.data,closes=()))
        self.assertIsNone(r.prior_close)
        self.assertFalse(r.static_eligible)

    def test_unofficial_prior_close_cannot_substitute(self):
        self.assertIsNone(self.evaluate(replace(self.data,closes=(replace(self.data.closes[0],official_regular_close=False),))).prior_close)

    def cap(self, value):
        return self.evaluate(replace(self.data,caps=(replace(self.data.caps[0],authoritative_value=D(value)),)))

    def test_market_cap_exact_fifty_million_passes(self): self.assertTrue(self.cap('50000000').static_eligible)
    def test_market_cap_below_fifty_million_fails(self): self.assertIn('MARKET_CAP_BELOW_MINIMUM',self.cap('49999999').failures)
    def test_market_cap_above_fifty_million_passes(self): self.assertTrue(self.cap('50000001').static_eligible)

    def test_unavailable_market_cap_has_canonical_reason(self):
        self.assertIn('MARKET_CAP_DATA_UNAVAILABLE',self.evaluate(replace(self.data,caps=())).failures)

    def test_future_market_cap_cannot_affect_past(self):
        future=replace(self.data.caps[0],available_at=self.open+timedelta(minutes=1),authoritative_value=D('999999999'))
        self.assertEqual(self.evaluate(replace(self.data,caps=(future,))).market_cap,None)

    def test_verified_historical_shares_times_compatible_price(self):
        cap=replace(self.data.caps[0],authoritative_value=None,shares_outstanding=D('25000000'),
            compatible_price=D(2),shares_basis_id='basis',price_basis_id='basis',share_count_point_in_time_verified=True)
        self.assertEqual(self.evaluate(replace(self.data,caps=(cap,))).market_cap,D('50000000'))

    def test_current_shares_without_historical_verification_not_used(self):
        cap=replace(self.data.caps[0],authoritative_value=None,shares_outstanding=D('25000000'),
            compatible_price=D(2),shares_basis_id='basis',price_basis_id='basis')
        self.assertIn('MARKET_CAP_DATA_UNAVAILABLE',self.evaluate(replace(self.data,caps=(cap,))).failures)

    def test_adv_exact_one_million_fails(self):
        data=replace(self.data,daily=tuple(replace(r,volume=D('1000000')) for r in self.data.daily))
        r=self.evaluate(data)
        self.assertEqual(r.adv10,F(1000000))
        self.assertIn('ADV10_NOT_ABOVE_MINIMUM',r.failures)

    def test_adv_just_above_one_million_passes(self):
        data=replace(self.data,daily=tuple(replace(r,volume=D('1000000.1')) for r in self.data.daily))
        self.assertTrue(self.evaluate(data).static_eligible)

    def test_adv_exact_ten_required_dates(self):
        r=self.evaluate()
        self.assertEqual(r.history_dates[-10:],(date(2026,9,24),date(2026,9,25),date(2026,9,28),
            date(2026,9,29),date(2026,9,30),date(2026,10,1),date(2026,10,2),date(2026,10,5),date(2026,10,6),date(2026,10,7)))

    def test_adv_missing_middle_not_replaced_with_older(self):
        data=replace(self.data,daily=tuple(r for r in self.data.daily if r.trading_date!=date(2026,10,1)))
        self.assertIsNone(self.evaluate(data).adv10)

    def test_current_session_excluded_from_adv_and_adr(self):
        future=replace(self.data.daily[-1],trading_date=DAY,volume=D('999999999'),
            completed_at=self.data.calendar.session_for(DAY).close,provenance=provenance(self.data.calendar.session_for(DAY).close))
        self.assertEqual(self.evaluate(replace(self.data,daily=self.data.daily+(future,))),self.evaluate())

    def adr(self, high):
        data=replace(self.data,daily=tuple(replace(r,high=D(high),low=D(1),close=D(1)) for r in self.data.daily))
        return self.evaluate(data)

    def test_adr_499_percent_fails(self): self.assertEqual(self.adr('1.0499').adr_category,'LOW')
    def test_adr_exact_five_passes(self): self.assertTrue(self.adr('1.05').static_eligible)
    def test_adr_exact_ten_very_high(self): self.assertEqual(self.adr('1.1').adr_category,'VERY_HIGH')
    def test_adr_just_below_ten_high(self): self.assertEqual(self.adr('1.099999').adr_category,'HIGH')

    def test_adr_requires_twenty_exact_sessions(self):
        dates=self.evaluate().history_dates
        self.assertEqual(len(dates),20)
        data=replace(self.data,daily=tuple(r for r in self.data.daily if r.trading_date!=dates[3]))
        self.assertIn('ADR20_DATA_UNAVAILABLE',self.evaluate(data).failures)

    def test_recent_listing_insufficient_adr(self):
        data=replace(self.data,references=(replace(self.data.references[0],listing_date=date(2026,9,22)),))
        self.assertIn('INSUFFICIENT_ADR20_HISTORY',self.evaluate(data).failures)

    def test_late_daily_record_not_known_before_rth(self):
        bad=replace(self.data.daily[-1],provenance=provenance(self.open+timedelta(minutes=1)))
        data=replace(self.data,daily=self.data.daily[:-1]+(bad,))
        self.assertIn('ADR20_DATA_UNAVAILABLE',self.evaluate(data,at=self.open+timedelta(hours=1)).failures)

    def test_bad_daily_cause_retained(self):
        bad=replace(self.data.daily[-1],trustworthy=False,quality_reason='provider daily conflict')
        r=self.evaluate(replace(self.data,daily=self.data.daily[:-1]+(bad,)))
        self.assertTrue(any('provider daily conflict' in f for f in r.failures))

    def test_duplicate_required_daily_not_silently_chosen(self):
        self.assertIsNone(self.evaluate(replace(self.data,daily=self.data.daily+(self.data.daily[-1],))).adr20_pct)

    def test_delisted_security_remains_eligible_before_delisting(self):
        data=replace(self.data,references=(replace(self.data.references[0],delisting_at=self.open+timedelta(days=30)),))
        self.assertTrue(self.evaluate(data).eligible)

    def test_delisted_date_excludes_after_effective_time(self):
        data=replace(self.data,references=(replace(self.data.references[0],delisting_at=self.open),))
        self.assertFalse(self.evaluate(data).static_eligible)

    def test_symbol_changes_keep_stable_identity(self):
        old=self.data.references[0]
        old=replace(old,ticker='OLD',valid_until=self.open)
        new=replace(self.data.references[0],ticker='NEW',valid_from=self.open,available_at=self.open)
        data=replace(self.data,references=(new,old))
        self.assertEqual(historical_reference(data,'id-AAA',self.open-timedelta(minutes=1)).ticker,'OLD')
        self.assertEqual(self.evaluate(data).ticker,'NEW')

    def test_unavailable_listing_metadata_does_not_derive_history(self):
        ref=replace(self.data.references[0],listing_available_at=self.open+timedelta(minutes=1))
        r=self.evaluate(replace(self.data,references=(ref,)))
        self.assertEqual(r.history_dates,())

    def test_future_security_classification_not_used(self):
        future=replace(self.data.references[0],available_at=self.open+timedelta(minutes=1),security_type=SecurityType.ETF)
        self.assertEqual(self.evaluate(replace(self.data,references=(future,))).ticker,'')

    def split(self, factor):
        action=SplitAction('id-AAA','old','basis',F(factor),self.open,self.open,'verified-split',True,True)
        daily=tuple(replace(r,provenance=replace(r.provenance,share_basis_id='old')) for r in self.data.daily)
        prior=replace(self.data.closes[0],provenance=replace(self.data.closes[0].provenance,share_basis_id='old'))
        return replace(self.data,actions=(action,),daily=daily,closes=(prior,))

    def test_simple_split_prior_close_and_volume(self):
        r=self.evaluate(self.split(2))
        self.assertEqual(r.prior_close,F(9,20)); self.assertEqual(r.adv10,F(4000000))
        self.assertEqual(r.adr20_pct,F(10))

    def test_reverse_split_volume_and_price(self):
        r=self.evaluate(self.split(F(1,2)))
        self.assertEqual(r.prior_close,F(9,5)); self.assertEqual(r.adv10,F(1000000))

    def test_nonterminating_split_prices_are_not_rounded(self):
        data=self.split(7)
        with localcontext() as ctx:
            ctx.prec=2
            r=self.evaluate(data)
        self.assertEqual(r.prior_close,F(9,70))

    def test_future_split_cannot_rewrite_past(self):
        data=self.split(2)
        action=replace(data.actions[0],old_basis='basis',new_basis='future',effective_at=self.open+timedelta(days=1))
        self.assertEqual(self.evaluate(replace(self.data,actions=(action,))),self.evaluate())

    def test_unknown_adjustment_methodology_unavailable(self):
        bad=replace(self.data.closes[0],provenance=replace(self.data.closes[0].provenance,adjustment=Adjustment.UNKNOWN))
        self.assertTrue(any(f.startswith('CORPORATE_ACTION_DATA_UNAVAILABLE') for f in self.evaluate(replace(self.data,closes=(bad,))).failures))

    def test_already_normalized_basis_not_adjusted_twice(self):
        data=self.split(2)
        item=normalize(D(3),provenance(self.open,adjustment=Adjustment.VERIFIED_POINT_IN_TIME),'id-AAA','basis',self.open,data)
        self.assertEqual(item.value,F(3)); self.assertEqual(item.action_sources,())

    def test_conflicting_split_terms_fail(self):
        data=self.split(2)
        with self.assertRaises(HistoricalInputError):
            normalize(D(1),provenance(self.open,'old'),'id-AAA','basis',self.open,
                replace(data,actions=data.actions+(replace(data.actions[0],factor=F(3)),)))

    def test_unavailable_split_terms_fail(self):
        data=self.split(2)
        data=replace(data,actions=(replace(data.actions[0],available_at=self.open+timedelta(seconds=1)),))
        self.assertIsNone(self.evaluate(data).prior_close)

    def test_complex_action_not_invented(self):
        data=self.split(2)
        self.assertIsNone(self.evaluate(replace(data,actions=(replace(data.actions[0],simple_share_denomination=False),))).prior_close)

    def test_utc_evaluation_matches_new_york(self):
        self.assertEqual(self.evaluate(at=self.open.astimezone(timezone.utc)),self.evaluate())

    def test_holiday_is_non_session(self):
        from tests.historical_fixtures import Calendar
        r=self.evaluate(replace(self.data,calendar=Calendar(holidays=(DAY,))))
        self.assertIn('NON_SESSION',r.failures)

    def test_direct_universe_price_rejects_binary_float(self):
        with self.assertRaises(HistoricalInputError):
            evaluate_universe(self.data,'id-AAA',self.open,1.03)

    def test_direct_universe_price_rejects_nonpositive_or_nonfinite(self):
        for value in (D(0),D(-1),D('NaN'),D('Infinity')):
            with self.subTest(value=value), self.assertRaises(HistoricalInputError):
                evaluate_universe(self.data,'id-AAA',self.open,value)

    def test_history_and_normalization_provenance_can_be_reconstructed(self):
        r=self.evaluate(self.split(2))
        self.assertEqual(len(r.daily_records),20)
        self.assertTrue(all(d.provenance.available_at<=self.open for d in r.daily_records))
        self.assertEqual(r.official_prior_close_record.price,D('.90'))
        self.assertEqual(r.market_cap_records[0].source_id,'historical-market-cap')
        self.assertEqual(r.normalization[0].actions[0].source_id,'verified-split')
        self.assertTrue(r.normalization[0].provenance.verified)
