"""Synthetic offline PRE4A evidence; no provider credentials or collection."""

import ast
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, Inexact, Rounded, localcontext
from fractions import Fraction
import gc
import json
from pathlib import Path
import tracemalloc
import unittest
from unittest.mock import patch

from tradingbot_marketdata import discovery as d, multiday as m
from tradingbot_marketdata.research import _ratio

ROOT = Path(__file__).resolve().parents[1]
TARGET = date(2026, 10, 16)
DAYS = (date(2026,10,2),date(2026,10,5),date(2026,10,6),date(2026,10,7),date(2026,10,8),
        date(2026,10,9),date(2026,10,12),date(2026,10,13),date(2026,10,14),date(2026,10,15),TARGET)
SENTINEL = 'SYNTHETIC_PRIVATE_VALUE'
_DEFAULT_SOURCES = object()


def stamp(day, hour=16):
    delta = datetime(day.year,day.month,day.day,hour,tzinfo=timezone.utc)-datetime(1970,1,1,tzinfo=timezone.utc)
    return delta.days*86400000 + delta.seconds*1000


def bar(day, ticker='VELA', close='4.00', volume='2000000.00', *, timestamp=None):
    price = Decimal(close)
    return d._GroupedBar(ticker,stamp(day) if timestamp is None else timestamp,
                         price,price,price,price,Decimal(volume),None)


def summary(day, rows, *, excluded=0):
    return d._GroupedSummary(day,tuple(rows),len(rows)+excluded,len(rows)+excluded,0,excluded)


def window(*, target='4.12', previous='4.00', volumes=None, target_volume='4000000', tickers=('VELA',)):
    volumes = volumes if volumes is not None else ('2000000',)*10
    return tuple(summary(day,[bar(day,ticker,target if index==10 else previous,
                                  target_volume if index==10 else volumes[index]) for ticker in tickers])
                 for index,day in enumerate(DAYS))


def screen(sources=_DEFAULT_SOURCES, target=TARGET, **kwargs):
    return m._screen_grouped_history(window() if sources is _DEFAULT_SOURCES else sources,target,**kwargs)


class Offline(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for name in ('socket.socket.connect','socket.getaddrinfo'):
            self.stack.enter_context(patch(name,side_effect=AssertionError('Network forbidden')))

    def incompatible(self, sources, reason=None, target=TARGET):
        result = screen(sources,target)
        self.assertIs(result.qualification,m._SourceQualification.SOURCE_INCOMPATIBLE)
        self.assertEqual((result.rows,result.sources),((),()))
        self.assertIsNone(result.received_rows)
        self.assertNotIn(SENTINEL,repr(result))
        if reason is not None:self.assertIs(result.reason,reason)
        return result


class CalculationTests(Offline):
    def test_eleven_summaries_exact_metrics_and_two_independent_classifications(self):
        result = screen(); row = result.rows[0]
        self.assertEqual(result.source_dates,DAYS)
        self.assertEqual(result.received_rows,11)
        self.assertEqual((row.price,row.change_pct,row.average_volume_10,row.relative_volume),
                         (Fraction(103,25),Fraction(3),Fraction(2000000),Fraction(2)))
        self.assertIs(row.outcome,m._ScreenOutcome.PRELIMINARY_MATCH)
        self.assertIs(row.qualification,m._SourceQualification.SESSION_CONTINUITY_UNVERIFIED)
        self.assertEqual((row.failed,row.missing,row.unavailable),((),(),()))

    def test_exact_nonterminating_change(self):
        row = screen(window(target='4.99',previous='4.25')).rows[0]
        self.assertEqual(row.change_pct,Fraction(296,17))

    def test_previous_ten_average_excludes_target_and_retains_all_support_dates(self):
        sources = window(volumes=tuple(str(1000001+i) for i in range(10)),target_volume='100000000')
        row = screen(sources).rows[0]
        self.assertEqual(row.average_volume_10,Fraction(2000011,2))
        self.assertEqual(row.relative_volume,Fraction(200000000,2000011))
        self.assertEqual(row.baseline_dates,DAYS[:-1])
        self.assertIs(row.target.bar,sources[-1].rows[0])
        for observation,source in zip(row.history,sources[:-1]):
            self.assertEqual(observation.requested_date,source.requested_date)
            self.assertIs(observation.bar,source.rows[0])

    def test_relative_volume_is_exact_not_intraday_rvol(self):
        row = screen(window(volumes=('3',)*10,target_volume='1')).rows[0]
        self.assertEqual(row.relative_volume,Fraction(1,3))
        self.assertIs(row.outcome,m._ScreenOutcome.PRELIMINARY_NOT_MET)

    def test_zero_target_volume_is_observed_zero(self):
        row = screen(window(target_volume='0')).rows[0]
        self.assertEqual(row.relative_volume,Fraction(0))
        self.assertIs(row.outcome,m._ScreenOutcome.PRELIMINARY_MATCH)

    def test_zero_baseline_is_valid_volume_but_relative_volume_unavailable(self):
        row = screen(window(volumes=('0',)*10)).rows[0]
        self.assertEqual(row.average_volume_10,Fraction(0))
        self.assertIsNone(row.relative_volume)
        self.assertIn(m._HistoryGap.ZERO_BASELINE_VOLUME,row.unavailable)
        self.assertIs(row.outcome,m._ScreenOutcome.PRELIMINARY_NOT_MET)

    def test_decimal_context_does_not_change_results_or_source_shapes(self):
        sources = window(previous='4.0000',target='4.120000',target_volume='4000000.000')
        expected = screen(sources)
        with localcontext() as context:
            context.prec=1;context.traps[Inexact]=context.traps[Rounded]=True
            actual = screen(sources)
        self.assertEqual(actual,expected)
        self.assertEqual(actual.rows[0].target.bar.close.as_tuple(),Decimal('4.120000').as_tuple())
        self.assertEqual(actual.rows[0].previous.bar.close.as_tuple(),Decimal('4.0000').as_tuple())

    def test_display_rounding_cannot_make_change_pass(self):
        row = screen(window(target='4.119999999996')).rows[0]
        self.assertEqual(_ratio(row.change_pct)['display'],'3.00')
        self.assertLess(row.change_pct,3)
        self.assertIn(m._Criterion.OBSERVED_CHANGE,row.failed)

    def test_deterministic_repeated_evaluation(self):
        sources = window(tickers=('VELA','ABCD','NEXA'))
        self.assertEqual(screen(sources),screen(sources))
        self.assertEqual(tuple(row.ticker for row in screen(sources).rows),('ABCD','NEXA','VELA'))

    def test_ticker_order_changes_do_not_change_result(self):
        sources = window(tickers=('VELA','NEXA','ABCD'))
        reordered = tuple(replace(source,rows=tuple(reversed(source.rows))) for source in sources)
        self.assertEqual(screen(sources),screen(reordered))

    def test_actual_decoder_produces_supported_adjusted_source(self):
        sources=[]
        for index,day in enumerate(DAYS):
            price='4.12' if index==10 else '4'
            # Literal JSON numbers preserve the original decimal representation.
            raw=('{'+'"status":"OK","adjusted":true,"resultsCount":1,"count":1,"results":['
                 '{"T":"VELA","o":'+price+',"h":'+price+',"l":'+price+',"c":'+price+
                 ',"v":2000000,"t":'+str(stamp(day))+'}]}').encode()
            sources.append(d._decode_grouped(raw,day,datetime(2026,10,19,16,tzinfo=timezone.utc)))
        self.assertIs(screen(tuple(sources)).rows[0].outcome,m._ScreenOutcome.PRELIMINARY_MATCH)


def threshold_case(target, previous, volume, expected, failure):
    def test(self):
        row = screen(window(target=target,previous=previous,volumes=(volume,)*10)).rows[0]
        self.assertIs(row.outcome,expected)
        if failure is not None:self.assertIn(failure,row.failed)
    return test


for name,target,previous,volume,passes,failure in (
    ('price_below','4.99','4.8','2000000',True,None),
    ('price_equal','5','4.8','2000000',False,m._Criterion.PRICE),
    ('price_just_below','4.999999999999','4.8','2000000',True,None),
    ('change_equal','4.12','4','2000000',True,None),
    ('change_below','4.119999999996','4','2000000',False,m._Criterion.OBSERVED_CHANGE),
    ('change_negative','3.9','4','2000000',False,m._Criterion.OBSERVED_CHANGE),
    ('volume_equal','4.12','4','1000000',False,m._Criterion.AVERAGE_VOLUME_10),
    ('volume_below','4.12','4','999999',False,m._Criterion.AVERAGE_VOLUME_10),
    ('volume_above','4.12','4','1000001',True,None),
    ('volume_just_above','4.12','4','1000000.000000000001',True,None)):
    outcome=m._ScreenOutcome.PRELIMINARY_MATCH if passes else m._ScreenOutcome.PRELIMINARY_NOT_MET
    setattr(CalculationTests,'test_boundary_'+name,threshold_case(target,previous,volume,outcome,failure))


class CoverageTests(Offline):
    def test_target_only_has_no_invented_previous_close_or_baseline(self):
        row = screen((window()[-1],)).rows[0]
        self.assertIs(row.outcome,m._ScreenOutcome.INSUFFICIENT_HISTORY)
        self.assertEqual((row.previous,row.change_pct,row.average_volume_10,row.relative_volume),(None,None,None,None))
        self.assertEqual(row.missing,(m._Criterion.OBSERVED_CHANGE,m._Criterion.AVERAGE_VOLUME_10))

    def test_nine_prior_observations_do_not_become_a_ten_day_average(self):
        sources = window()
        row = screen(sources[1:]).rows[0]
        self.assertEqual(row.change_pct,Fraction(3))
        self.assertEqual((row.average_volume_10,row.relative_volume),(None,None))
        self.assertEqual(row.baseline_dates,())
        self.assertEqual(len(row.history),9)
        self.assertIs(row.outcome,m._ScreenOutcome.INSUFFICIENT_HISTORY)

    def test_missing_ticker_nearest_date_uses_previous_actual_observation(self):
        sources = list(window())
        sources[-2] = summary(DAYS[-2],[])
        row = screen(tuple(sources)).rows[0]
        self.assertEqual(row.previous.requested_date,DAYS[-3])
        self.assertEqual(row.change_pct,Fraction(3))
        self.assertIsNone(row.average_volume_10)
        self.assertIs(row.outcome,m._ScreenOutcome.INSUFFICIENT_HISTORY)

    def test_missing_ticker_all_prior_dates_no_substitution_from_other_symbol(self):
        sources = window(tickers=('NEXA',))[:-1]+window()[-1:]
        row = screen(sources).rows[0]
        self.assertEqual(row.history,())
        self.assertIsNone(row.change_pct)

    def test_history_only_ticker_never_becomes_target_candidate(self):
        sources = window(tickers=('VELA','NEXA'))
        sources = sources[:-1]+(summary(TARGET,[sources[-1].rows[0]]),)
        self.assertEqual(tuple(row.ticker for row in screen(sources).rows),('VELA',))

    def test_known_failure_dominates_missing_but_missing_remains_explicit(self):
        row = screen((window(target='5')[-1],)).rows[0]
        self.assertIs(row.outcome,m._ScreenOutcome.PRELIMINARY_NOT_MET)
        self.assertEqual(row.failed,(m._Criterion.PRICE,))
        self.assertEqual(len(row.missing),2)

    def test_empty_target_means_no_candidates_not_complete_market_absence(self):
        sources = window()[:-1]+(summary(TARGET,[]),)
        result = screen(sources)
        self.assertEqual(result.rows,())
        self.assertIsNone(result.reason)
        self.assertIn('RESPONSE_COVERAGE_IS_NOT_COMPLETE_US_MARKET_COVERAGE',result.limitations)

    def test_weekend_holiday_gaps_stay_unverified_not_filled(self):
        days=(date(2026,7,2),date(2026,7,6),date(2026,7,20))
        sources=tuple(summary(day,[bar(day)]) for day in days)
        result=screen(sources,days[-1])
        self.assertEqual(result.source_dates,days)
        self.assertEqual(tuple(item.requested_date for item in result.rows[0].history),days[:-1])
        self.assertIs(result.qualification,m._SourceQualification.SESSION_CONTINUITY_UNVERIFIED)

    def test_weekday_holiday_observation_is_not_calendar_completeness_proof(self):
        day=date(2026,7,3)
        result=screen((summary(day,[bar(day)]),),day)
        self.assertIsNone(result.reason)
        self.assertIn('OBSERVED_DATES_NOT_VERIFIED_CONSECUTIVE_MARKET_SESSIONS',result.limitations)

    def test_split_like_price_jump_not_corrected_or_promoted_to_qualified_strategy(self):
        row=screen(window(previous='400',target='4')).rows[0]
        self.assertEqual(row.change_pct,Fraction(-99))
        self.assertIn('NO_SPLIT_FACTOR_OR_CORPORATE_ACTION_CONTINUITY_PROOF',row.limitations)
        self.assertIn('SPLIT_ADJUSTED_SOURCE_CONTRACT_NOT_COMMON_ADJUSTMENT_VINTAGE_PROOF',row.limitations)

    def test_same_timestamp_for_different_tickers_is_not_duplicate_identity(self):
        self.assertIsNone(screen(window(tickers=('VELA','NEXA'))).reason)

    def test_source_coverage_counts_preserve_exclusions_not_complete_market_claim(self):
        source=summary(TARGET,[bar(TARGET)],excluded=2)
        result=screen((source,))
        self.assertEqual((result.received_rows,result.sources[0].supported_rows,result.sources[0].excluded_unsupported),(3,1,2))


class InvalidSourceTests(Offline):
    def test_reversed_or_shuffled_summaries_are_rejected_not_sorted(self):
        sources=window()
        self.incompatible(tuple(reversed(sources)),m._SourceIssue.DATE_ORDER_INVALID)
        self.incompatible(sources[:4]+(sources[5],sources[4])+sources[6:],m._SourceIssue.DATE_ORDER_INVALID)

    def test_duplicate_dates_rejected_even_same_object(self):
        sources=window()
        self.incompatible(sources[:-1]+(sources[-2],),m._SourceIssue.DATE_ORDER_INVALID)

    def test_missing_or_wrong_target_date_rejects_whole_batch(self):
        self.incompatible(window()[:-1],m._SourceIssue.TARGET_SOURCE_MISSING)
        self.incompatible(window(),m._SourceIssue.TARGET_SOURCE_MISSING,target=date(2026,10,19))

    def test_duplicate_ticker_identity_within_summary_rejects_all(self):
        sources=window();bad=replace(sources[-1])
        object.__setattr__(bad,'rows',(bad.rows[0],bad.rows[0]))
        object.__setattr__(bad,'received_count',2)
        self.incompatible(sources[:-1]+(bad,),m._SourceIssue.SOURCE_VALUES_INVALID)

    def test_duplicate_source_identity_across_permitted_midnight_boundaries(self):
        earlier,later=date(2026,10,12),date(2026,10,13)
        same=stamp(later,4)
        sources=(summary(earlier,[bar(earlier,timestamp=same)]),summary(later,[bar(later,timestamp=same)]))
        self.incompatible(sources,m._SourceIssue.SOURCE_IDENTITY_CONFLICT,target=later)

    def test_mismatched_row_requested_date_rejected(self):
        sources=window();bad=replace(sources[-1],requested_date=date(2026,10,19))
        self.incompatible(sources[:-1]+(bad,),m._SourceIssue.TIMESTAMP_DATE_MISMATCH,target=date(2026,10,19))

    def test_unrelated_malformed_historical_row_cannot_be_ignored(self):
        sources=list(window());broken=bar(DAYS[0],'NEXA')
        object.__setattr__(broken,'close',None)
        sources[0]=summary(DAYS[0],[])  # Corruption simulates substituted retained evidence.
        object.__setattr__(sources[0],'rows',(broken,))
        object.__setattr__(sources[0],'received_count',1)
        self.incompatible(tuple(sources),m._SourceIssue.SOURCE_VALUES_INVALID)

    def test_substituted_row_or_identity_cannot_execute_property_or_hash_hooks(self):
        calls=[]
        class RawRow:
            @property
            def ticker(self):calls.append('property');raise AssertionError('Must not execute')
        class RawIdentity:
            def __hash__(self):calls.append('hash');raise AssertionError('Must not execute')
        source=window()[-1]
        object.__setattr__(source,'rows',(RawRow(),))
        self.incompatible((source,),m._SourceIssue.SOURCE_VALUES_INVALID)
        source=window()[-1]
        object.__setattr__(source.rows[0],'ticker',RawIdentity())
        self.incompatible((source,),m._SourceIssue.SOURCE_VALUES_INVALID)
        self.assertEqual(calls,[])

    def test_different_adjustment_policy_representation_is_not_accepted_as_summary(self):
        class Unqualified:
            adjusted=False
        self.incompatible((Unqualified(),),m._SourceIssue.SOURCE_TYPE_INVALID)
        with self.assertRaises(AttributeError):object.__setattr__(window()[0],'adjusted',False)

    def test_existing_decoder_rejects_unadjusted_source_before_screening(self):
        raw=b'{"adjusted":false,"status":"OK","resultsCount":0}'
        with self.assertRaises(d._GroupedValidationError):d._decode_grouped(raw,TARGET,datetime(2026,10,19,tzinfo=timezone.utc))

    def test_source_error_cannot_leak_unvalidated_string(self):
        sources=window();bad=replace(sources[-1])
        object.__setattr__(bad.rows[0],'ticker',SENTINEL)
        result=self.incompatible(sources[:-1]+(bad,))
        self.assertEqual(result.sources,())


def bad_source_field(field,value):
    def test(self):
        sources=window();bad=replace(sources[-1])
        object.__setattr__(bad.rows[0],field,value)
        self.incompatible(sources[:-1]+(bad,))
    return test


for name,field,value in (('missing_close','close',None),('zero_close','close',Decimal(0)),
    ('negative_volume','volume',Decimal(-1)),('bool_volume','volume',True),('float_close','close',4.12),
    ('nan_close','close',Decimal('NaN')),('bad_timestamp','timestamp_ms',True),('bad_symbol','ticker','vela')):
    setattr(InvalidSourceTests,'test_'+name,bad_source_field(field,value))


class ContractTests(Offline):
    def test_empty_or_excess_summary_count_bounded_before_traversal(self):
        self.incompatible((),m._SourceIssue.SUMMARY_COUNT_INVALID)
        with patch.object(d._GroupedSummary,'__post_init__',side_effect=AssertionError('No traversal')):
            self.incompatible((None,)*12,m._SourceIssue.SUMMARY_COUNT_INVALID)

    def test_row_capacity_bounded_before_model_traversal(self):
        source=window()[-1]
        object.__setattr__(source,'rows',(source.rows[0],)*(d._MAX_ROWS+1))
        with patch.object(d._GroupedSummary,'__post_init__',side_effect=AssertionError('No traversal')):
            self.incompatible((source,),m._SourceIssue.ROW_LIMIT_EXCEEDED)

    def test_invalid_input_types_are_not_iterated_or_reinterpreted(self):
        for sources in (None,[],{},iter(window()),SENTINEL):
            self.incompatible(sources,m._SourceIssue.INPUT_TYPE_INVALID)
        for source in (None,{},object(),True):
            self.incompatible((source,),m._SourceIssue.SOURCE_TYPE_INVALID)

    def test_invalid_target_type_and_bounds(self):
        for target in (None,True,'2026-10-16',datetime(2026,10,16),date(2006,1,2),date(2100,1,1),date(2026,10,17)):
            self.incompatible(window(),m._SourceIssue.TARGET_DATE_INVALID,target=target)

    def test_no_live_clock_required_for_historical_evaluation(self):
        sources=window()
        for name in ('tradingbot_marketdata.massive._now','time.time','time.monotonic'):
            self.stack.enter_context(patch(name,side_effect=AssertionError('No clocks')))
        self.assertEqual(screen(sources),screen(sources))

    def test_result_models_frozen_slotted_opaque_and_do_not_expose_whole_market(self):
        result=screen(window(tickers=('VELA','NEXA')));row=result.rows[0]
        for value,field in ((result,'reason'),(row,'price'),(row.target,'bar'),(result.sources[0],'received_rows')):
            self.assertFalse(hasattr(value,'__dict__'))
            with self.assertRaises(FrozenInstanceError):setattr(value,field,None)
            self.assertNotIn('VELA',repr(value))
        self.assertFalse(hasattr(row,'sources'))
        self.assertEqual({item.bar.ticker for item in row.history},{row.ticker})

    def test_all_unqualified_requirements_retained_for_match(self):
        result=screen();row=result.rows[0]
        self.assertEqual(row.limitations,result.limitations)
        for marker in ('MARKET_CAP_50_MILLION_UNVERIFIED','INSTRUMENT_EXCHANGE_ACTIVE_LISTING_UNVERIFIED',
                       'NO_SYNCHRONIZED_POINT_IN_TIME_REFERENCE_DATA','HIGH_VERY_HIGH_VOLATILITY_UNVERIFIED',
                       'ABCD_SETUP_UNQUALIFIED','NO_INTRADAY_MOMENTUM_OR_ENTRY_SIGNAL','NO_TRADING_AUTHORITY'):
            self.assertIn(marker,row.limitations)

    def test_interruptions_propagate_exact_objects_without_publication(self):
        sources=window()
        for error in (KeyboardInterrupt('synthetic'),SystemExit(43),GeneratorExit('synthetic')):
            with patch.object(d._GroupedSummary,'__post_init__',side_effect=error):
                with self.assertRaises(type(error)) as caught:screen(sources)
                self.assertIs(caught.exception,error)

    def test_ordinary_validation_and_calculation_failures_are_sanitized(self):
        sources=window()
        for target,name in ((d._GroupedSummary,'__post_init__'),(m,'_calculate')):
            with patch.object(target,name,side_effect=RuntimeError(SENTINEL)):
                result=self.incompatible(sources,m._SourceIssue.SOURCE_VALUES_INVALID)
                self.assertNotIn(SENTINEL,repr(result))

    def test_invalid_filters_use_fixed_error_no_unvalidated_text(self):
        for value in (None,{},SENTINEL):
            with self.assertRaisesRegex(ValueError,'^Invalid multi-day research filters$'):screen(filters=value)
        for field,value in (('max_price',Decimal(0)),('max_price',Decimal('NaN')),
                            ('min_change',True),('min_average_volume',Decimal(-1))):
            with self.assertRaises(ValueError):m._MultiDayFilters(**{field:value})

    def test_pure_module_inactive_and_no_protected_imports_or_operations(self):
        path=ROOT/'tradingbot_marketdata/multiday.py'
        tree=ast.parse(path.read_text(encoding='utf-8'))
        forbidden=('os','time','socket','http','pathlib','sqlite3','threading','tradingbot_broker',
                   'tradingbot_backtest','strategy','massive','transport','reference')
        for node in ast.walk(tree):
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                names=[alias.name for alias in node.names] if isinstance(node,ast.Import) else [node.module or '']
                self.assertFalse(any(name.startswith(forbidden) for name in names))
            if isinstance(node,ast.Call):
                name=node.func.attr if isinstance(node.func,ast.Attribute) else node.func.id if isinstance(node.func,ast.Name) else ''
                self.assertNotIn(name,('now','today','open','read','write','connect','request','append_event','execute','eval','exec'))
        for directory in ('tradingbot_dashboard','tradingbot_marketdata'):
            for other in (ROOT/directory).glob('*.py'):
                # PRE4B authorizes only the explicit internal collection handoff;
                # no dashboard/provider automatic evaluation is activated.
                if other!=path and other.name!='collection.py':
                    self.assertNotIn('multiday',other.read_text(encoding='utf-8'))
        self.assertEqual(m.__all__,())


def symbol(index):
    letters=''
    for _ in range(4):letters=chr(65+index%26)+letters;index//=26
    return 'D'+letters


class ResourceTests(Offline):
    def test_worst_case_shared_and_disjoint_universes_are_bounded(self):
        self.assertEqual((m._MAX_SUMMARIES,m._MAX_TOTAL_ROWS,m._MAX_TARGET_TICKERS,m._MAX_RETAINED_OBSERVATIONS),
                         (11,330000,30000,330000))
        for disjoint in (False,True):
            sources=[]
            tickers=tuple(symbol(i) for i in range(330000 if disjoint else 30000))
            for index,day in enumerate(DAYS):
                offset=index*30000 if disjoint else 0
                sources.append(summary(day,[bar(day,ticker) for ticker in tickers[offset:offset+30000]]))
            sources=tuple(sources);gc.collect();tracemalloc.start()
            try:
                result=screen(sources)
                _,peak=tracemalloc.get_traced_memory()
            finally:tracemalloc.stop()
            self.assertIsNone(result.reason)
            self.assertEqual(result.received_rows,330000)
            self.assertEqual(len(result.rows),30000)
            retained=sum(1+len(row.history) for row in result.rows)
            self.assertEqual(retained,30000 if disjoint else 330000)
            self.assertLess(peak,128*1024*1024,'Auxiliary allocation exceeds the reviewed CPython test budget')
            del result,sources,tickers
            gc.collect()


if __name__=='__main__':unittest.main()
