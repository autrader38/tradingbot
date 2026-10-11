"""PRE1 pure projection from actual Phase 5/6 fixtures, never broker activity."""

import ast
from dataclasses import fields, FrozenInstanceError, replace
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from decimal import Decimal as D, localcontext
from fractions import Fraction
import hashlib
import inspect
import json
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tradingbot_backtest.config import FROZEN_V1, StrategyConfig
from tradingbot_backtest.entry_validation import EntryValidator
from tradingbot_backtest.trade_construction import InitialPosition, PortfolioSnapshot, TradeConstructor
from tradingbot_broker import paper_dispatch_journal_context as c
from tradingbot_broker import paper_execution_journal as j
from tradingbot_broker.ibkr import IBKRContract
from tradingbot_broker.models import (OrderRequest, RiskPermission, TradingMode, Side,
    OrderType, OrderIntent, TimeInForce)
from tradingbot_broker.readonly_models import ReadOnlyError, AccountMode
from tests.entry_fixtures import handoff_fixture
from tests.test_trade_construction import SuppliedTicks


def forged(value, **changes):
    copy = object.__new__(type(value))
    for f in fields(value):
        object.__setattr__(copy, f.name, changes.get(f.name, getattr(value, f.name)))
    return copy


def inputs(stop='.01', target='.01'):
    handoff, context, calendar, _ = handoff_fixture()
    approval = EntryValidator(calendar, run_id='pre1', data_version='fixture').evaluate(handoff, context).approval
    at = approval.scheduled_timestamp
    account = PortfolioSnapshot(at, at, D(10000), D(20000), D(20000), D(0), (), True, 'account-reference')
    position = TradeConstructor(run_id='pre1', data_version='fixture').construct(
        approval, account, SuppliedTicks(at, stop=stop, target=target), trade_id='trade-reference').position
    assert position is not None
    order = OrderRequest('entry-pre1', 'security', 'TEST', TradingMode.PAPER, Side.BUY,
        OrderType.MARKET, D(position.quantity), 'USD', at, at + timedelta(minutes=5))
    permission = RiskPermission(order, True, at, at + timedelta(minutes=5), 'risk-reference', 'OK')
    contract = IBKRContract('security', 'TEST', 17, 'SMART', 'USD', at - timedelta(days=1),
        at + timedelta(days=1), at, 'verified-contract-reference', True)
    return dict(order=order, permission=permission, approval=approval, position=position, contract=contract, at=at)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.values = inputs()

    def adapt(self):
        return c._adapt_dispatch_journal_context(**self.values)

    def invalid(self):
        with self.assertRaisesRegex(ReadOnlyError, '^INVALID_DISPATCH_JOURNAL_CONTEXT$'):
            self.adapt()

    def approval_change(self, **changes):
        approval = forged(self.values['approval'], **changes)
        self.values['approval'] = approval
        self.values['position'] = forged(self.values['position'], approval=approval)

    def test_complete_projection_all_eighteen_strategy_fields(self):
        result = self.adapt()
        a, p, at = self.values['approval'], self.values['position'], self.values['at']
        expected = dict(strategy_version='v1.0', configuration_id=c._configuration_fingerprint(FROZEN_V1),
            setup_id=a.setup_id, security_id='security', symbol='TEST', session_date=a.session.trading_date,
            eligible_entry_minute=at, reference_entry=D('1.11'), raw_reference_stop=D('1.05470'),
            tick_adjusted_reference_stop=D('1.05'), raw_reference_target=D('1.23'),
            tick_adjusted_reference_target=D('1.23'), original_quantity=D(1000), risk_budget=D(100),
            bod_equity=D(10000), stop_tick_source=p.stop_tick.source_id, target_tick_source=p.target_tick.source_id,
            witnesses=tuple(j._StrategyWitness(kind, level, origin.timestamp, origin.source_id)
                for kind, level, origin in ((j._WitnessKind.A, a.a.low, a.a),
                    (j._WitnessKind.B, a.b_price, a.b_origin),
                    (j._WitnessKind.C, a.c_price, a.c_evidence.c_origin),
                    (j._WitnessKind.D, a.d.close, a.d))))
        self.assertEqual({f.name: getattr(result.strategy, f.name) for f in fields(result.strategy)}, expected)
        self.assertEqual(result.contract.con_id, 17)
        self.assertEqual(result.risk.order, result.order)
        self.assertIsNot(result.risk.order, result.order)
        self.assertEqual(result.validated_at, at)

    def test_permission_false_copied_without_risk_approval(self):
        self.values['permission'] = replace(self.values['permission'], permits_entry=False)
        self.assertFalse(self.adapt().risk.permits_entry)

    def test_optional_risk_account_fields_absent_and_bod_not_current_equity(self):
        result = self.adapt()
        self.assertEqual(result.strategy.bod_equity, D(10000))
        self.assertEqual(self.values['position'].account_before.current_equity, D(20000))
        self.assertEqual(result.strategy.risk_budget, D(100))
        self.assertEqual((result.risk.current_equity, result.risk.current_exposure, result.risk.available_cash),
                         (None, None, None))

    def test_ignored_research_effects_never_become_evidence(self):
        baseline = self.adapt()
        self.values['position'] = forged(self.values['position'], full_fill=False,
            commission=object(), fees=object(), slippage=object(), account_after=object())
        self.assertEqual(self.adapt(), baseline)
        self.assertNotIn('fill', {f.name for f in fields(baseline)})

    def test_immutable_independent_copies_survive_source_tampering(self):
        result = self.adapt()
        object.__setattr__(self.values['position'], 'raw_stop', D(999))
        object.__setattr__(self.values['order'], 'symbol', 'OTHER')
        object.__setattr__(self.values['approval'].a, 'source_id', 'changed')
        self.assertEqual(result.strategy.raw_reference_stop, D('1.05470'))
        self.assertEqual(result.order.symbol, 'TEST')
        self.assertEqual(result.strategy.witnesses[0].source_id, 'fixture')
        self.assertIsNot(result.strategy.eligible_entry_minute, self.values['at'])
        with self.assertRaises(FrozenInstanceError):
            result.strategy = None
        self.assertFalse(hasattr(result, '__dict__'))

    def test_exact_decimal_under_hostile_context(self):
        expected = self.adapt()
        with localcontext() as ctx:
            ctx.prec = 2
            self.assertEqual(self.adapt(), expected)

    def test_fractional_tick_results_copied_without_penny_assumption(self):
        result = c._adapt_dispatch_journal_context(**inputs('.0001', '.0003'))
        self.assertEqual(result.strategy.tick_adjusted_reference_stop, D('1.0547'))
        self.assertEqual(result.strategy.tick_adjusted_reference_target, D('1.2207'))

    def test_equal_approval_supported(self):
        self.values['position'] = replace(self.values['position'], approval=replace(self.values['approval']))
        self.assertEqual(self.adapt().strategy.setup_id, self.values['approval'].setup_id)

    def test_run_and_data_labels_do_not_change_config_or_context(self):
        expected = self.adapt()
        self.values['position'] = replace(self.values['position'], run_id='other-run', data_version='other-data')
        self.assertEqual(self.adapt(), expected)

    def test_timestamp_copy_preserves_dst_fold(self):
        ny = ZoneInfo('America/New_York')
        earlier = datetime(2026, 11, 1, 1, 30, tzinfo=ny, fold=0)
        later = earlier.replace(fold=1)
        for value in (earlier, later):
            copy = c._timestamp(value)
            self.assertIsNot(copy, value)
            self.assertEqual(copy.fold, value.fold)
            self.assertEqual(copy.utcoffset(), value.utcoffset())
        self.assertNotEqual(c._instant(earlier), c._instant(later))

    def test_nonexistent_dst_time_rejected(self):
        self.values['at'] = datetime(2026, 3, 8, 2, 30, tzinfo=ZoneInfo('America/New_York'))
        self.invalid()

    def test_opaque_repr_and_no_sources_retained(self):
        result = self.adapt()
        self.assertEqual(repr(result), '<PrivateDispatchJournalContext>')
        def walk(value):
            if hasattr(type(value), '__dataclass_fields__'):
                self.assertTrue(type(value).__module__ in (j.__name__, c.__name__))
                for f in fields(value): walk(getattr(value, f.name))
            elif type(value) is tuple:
                for item in value: walk(item)
            else:
                self.assertIn(type(value), (str, int, bool, type(None), D, datetime, date, j._WitnessKind))
        walk(result)

    def test_no_clock_io_storage_sdk_sizing_or_tick_lookup(self):
        with patch('builtins.open', side_effect=AssertionError('file I/O')), \
             patch('sqlite3.connect', side_effect=AssertionError('SQLite')), \
             patch('socket.socket', side_effect=AssertionError('network')), \
             patch('time.monotonic', side_effect=AssertionError('clock')), \
             patch('time.time', side_effect=AssertionError('clock')), \
             patch.object(TradeConstructor, 'construct', side_effect=AssertionError('construction')), \
             patch('tradingbot_backtest.trade_construction.sizing_limits', side_effect=AssertionError('sizing')), \
             patch.object(SuppliedTicks, 'resolve', side_effect=AssertionError('tick lookup')), \
             patch('tradingbot_broker.ibkr_readonly._load_official_api', side_effect=AssertionError('SDK')):
            self.assertEqual(self.adapt(), self.adapt())

    def test_baseexception_propagates_without_result(self):
        for error in (KeyboardInterrupt(), SystemExit(37), GeneratorExit()):
            def fail(_): raise error
            with self.subTest(kind=type(error)), patch.object(InitialPosition, 'quantity', property(fail)):
                with self.assertRaises(type(error)) as raised: self.adapt()
                self.assertIs(raised.exception, error)

    def test_missing_explicit_at_rejected(self):
        del self.values['at']
        with self.assertRaises(TypeError): self.adapt()

    def test_unsupported_nested_timezone_not_inspected(self):
        class UnsafeZone(tzinfo):
            def utcoffset(self, at): raise AssertionError('unsafe timezone inspected')
        broken = self.values['approval'].a.timestamp.replace(tzinfo=UnsafeZone())
        self.approval_change(a=forged(self.values['approval'].a, timestamp=broken))
        self.invalid()

    def test_unqualified_equal_approval_does_not_invoke_custom_equality(self):
        class UnsafeEquality:
            def __eq__(self, other): raise AssertionError('unsafe equality invoked')
        self.values['position'] = replace(self.values['position'],
            approval=forged(self.values['approval'], intraday=UnsafeEquality()))
        self.invalid()

    def test_risk_timestamp_same_wall_clock_different_fold_is_not_equal_payload(self):
        at = datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo('America/New_York'), fold=0)
        order = replace(self.values['order'], created_at=at, expires_at=at+timedelta(hours=2))
        self.values['order'] = order
        self.values['permission'] = replace(self.values['permission'],
            order=replace(order, created_at=at.replace(fold=1)),
            available_at=at, valid_until=at+timedelta(hours=2))
        self.values['at'] = at
        self.invalid()

    def test_reference_copy_never_contains_actual_execution_or_authority(self):
        result = self.adapt()
        prohibited = {'full_fill', 'execution_id', 'sdk_invoked', 'account_after',
                      'commission', 'fees', 'slippage', 'capability', 'safe_to_trade'}
        for value in (result, result.order, result.contract, result.strategy, result.risk):
            self.assertTrue(prohibited.isdisjoint(f.name for f in fields(value)))


def context_case(name, mutate):
    def test(self):
        mutate(self)
        self.invalid()
    test.__name__ = 'test_reject_' + name
    setattr(ContextTests, test.__name__, test)


for field, value in (
    ('client_order_id', ''), ('security_id', 'other'), ('symbol', 'OTHER'), ('currency', 'EUR'),
    ('side', Side.SELL), ('quantity', D(999)), ('quantity', D('1000.1')),
    ('quantity', True), ('quantity', D('NaN')), ('quantity', D('1e100')),
    ('order_type', OrderType.LIMIT), ('time_in_force', 'DAY'), ('mode', TradingMode.LIVE),
    ('intent', OrderIntent.EXIT), ('limit_price', D(1)), ('stop_price', D(1)),
    ('created_at', datetime(2026, 10, 8)), ('expires_at', datetime(2020, 1, 1, tzinfo=timezone.utc))):
    suffix = str(value).replace('.', '_').replace(' ', '_').replace(':', '_').replace('+', '_')
    def mutate(self, field=field, value=value):
        self.values['order'] = forged(self.values['order'], **{field: value})
        self.values['permission'] = forged(self.values['permission'], order=self.values['order'])
    context_case('order_' + field + '_' + suffix, mutate)

for field, value in (('security_id', 'other'), ('symbol', 'OTHER'), ('currency', 'EUR'),
                     ('con_id', True), ('con_id', 0), ('verified', False), ('verified', 1),
                     ('security_type', 'OPT'), ('exchange', 'NYSE'), ('source_id', 'bad\x00source')):
    context_case('contract_' + field + '_' + str(value).replace('\x00', '_'),
        lambda self, field=field, value=value: self.values.update(
            contract=forged(self.values['contract'], **{field: value})))

for field, delta in (('available_at', 1), ('valid_from', 1), ('valid_until', 0)):
    context_case('contract_time_' + field, lambda self, field=field, delta=delta:
        self.values.update(contract=forged(self.values['contract'],
            **{field: self.values['at'] + timedelta(seconds=delta)})))

for field, value in (('setup_id', ''), ('security_id', 'other'), ('ticker', 'OTHER'),
                     ('reference_open', D('NaN')), ('opening_source_id', 'bad\nsource'),
                     ('a', None), ('b_origin', None), ('c_evidence', None), ('d', None), ('session', None)):
    context_case('approval_' + field, lambda self, field=field, value=value: self.approval_change(**{field: value}))

for field, value in (('quantity', True), ('quantity', D(1000)), ('quantity', 999), ('quantity', 0),
                     ('risk_budget', None), ('risk_budget', D(101)), ('raw_stop', D('1.055')),
                     ('executable_stop', D('1.055')), ('raw_target', D('1.24')),
                     ('executable_target', D('1.225')), ('risk_per_share', D('.07')),
                     ('stop_tick', None), ('target_tick', None), ('account_before', None)):
    context_case('position_' + field + '_' + str(value).replace('.', '_'),
        lambda self, field=field, value=value: self.values.update(
            position=forged(self.values['position'], **{field: value})))

for witness in ('a', 'b_origin', 'd'):
    context_case('witness_provenance_' + witness, lambda self, witness=witness:
        self.approval_change(**{witness: forged(getattr(self.values['approval'], witness), source_id='bad\nsource')}))
context_case('c_provenance', lambda self: self.approval_change(c_evidence=replace(
    self.values['approval'].c_evidence, c_origin=forged(self.values['approval'].c_evidence.c_origin, source_id=''))))
context_case('d_completion', lambda self: self.approval_change(d_confirmation_timestamp=self.values['at']-timedelta(minutes=1)))
context_case('scheduled_entry', lambda self: self.approval_change(scheduled_timestamp=self.values['at']+timedelta(minutes=1)))
context_case('future_a', lambda self: self.approval_change(a=replace(self.values['approval'].a, timestamp=self.values['at'])))
context_case('witness_chronology', lambda self: self.approval_change(a=replace(self.values['approval'].a,
    timestamp=self.values['approval'].d.timestamp)))
context_case('b_price_origin', lambda self: self.approval_change(b_price=D('1.101')))
context_case('c_price_origin', lambda self: self.approval_change(c_price=D('1.061')))
context_case('approval_substitution', lambda self: self.values.update(position=replace(
    self.values['position'], approval=replace(self.values['approval'], setup_id='another-setup'))))
context_case('permission_order', lambda self: self.values.update(permission=replace(
    self.values['permission'], order=replace(self.values['order'], client_order_id='other-entry'))))
context_case('risk_expired', lambda self: self.values.update(at=self.values['at']+timedelta(minutes=5)))
context_case('risk_unavailable', lambda self: self.values.update(permission=replace(
    self.values['permission'], available_at=self.values['at']+timedelta(seconds=1))))
context_case('missing_bod', lambda self: self.values.update(position=replace(self.values['position'],
    account_before=forged(self.values['position'].account_before, beginning_of_day_equity=None))))
for tick_name in ('stop_tick', 'target_tick'):
    for field, value in (('source_id', ''), ('verified', False), ('security_id', 'other'), ('share_basis_id', 'other')):
        context_case(tick_name + '_' + field, lambda self, tick_name=tick_name, field=field, value=value:
            self.values.update(position=replace(self.values['position'], **{tick_name:
                forged(getattr(self.values['position'], tick_name), **{field: value})})))
    for timestamp in ('available_at', 'valid_from', 'valid_until'):
        context_case(tick_name + '_time_' + timestamp,
            lambda self, tick_name=tick_name, timestamp=timestamp:
                self.values.update(position=replace(self.values['position'], **{tick_name:
                    forged(getattr(self.values['position'], tick_name), **{timestamp:
                        self.values['at'] + timedelta(seconds=1 if timestamp != 'valid_until' else 0)})})))

for root in ('order', 'permission', 'approval', 'position', 'contract'):
    context_case('unsupported_root_' + root,
        lambda self, root=root: self.values.update(**{root: object()}))
context_case('missing_eligible_minute', lambda self: self.approval_change(scheduled_timestamp=None))
context_case('nonminute_eligible', lambda self: self.approval_change(scheduled_timestamp=self.values['at']+timedelta(seconds=1)))
context_case('unknown_c_origin', lambda self: self.approval_change(c_evidence=replace(
    self.values['approval'].c_evidence, c_origin=None)))
context_case('future_reference_account', lambda self: self.values.update(position=replace(self.values['position'],
    account_before=replace(self.values['position'].account_before, available_at=self.values['at']+timedelta(seconds=1)))))


class ConfigurationTests(unittest.TestCase):
    def test_canonical_stable_and_equal_numeric_representation(self):
        key = c._configuration_fingerprint(FROZEN_V1)
        self.assertRegex(key, r'^cfg-v1-[0-9a-f]{64}$')
        self.assertEqual(key, c._configuration_fingerprint(replace(FROZEN_V1)))
        self.assertEqual(key, c._configuration_fingerprint(replace(FROZEN_V1, risk_fraction_bod=D('.0100'))))

    def test_all_names_and_derived_limit_bound_in_hash_preimage(self):
        with patch.object(c.hashlib, 'sha256', wraps=hashlib.sha256) as digest:
            c._configuration_fingerprint(FROZEN_V1)
        domain, rows = json.loads(digest.call_args.args[0])
        self.assertEqual(domain, 'paper-dispatch-journal-configuration-v1|strategy-v1.0')
        self.assertEqual(len(rows), 64)
        self.assertEqual([row[0] for row in rows], [f.name for f in fields(StrategyConfig)] + ['max_total_breakout_attempts'])
        self.assertEqual(rows[-1], ['max_total_breakout_attempts', ['int', '3']])

    def test_type_and_fraction_decimal_encodings_unambiguous(self):
        values = (1, D(1), Fraction(1), timedelta(seconds=1), time(1), Side.BUY,
                  timezone.utc, ZoneInfo('America/New_York'), date(2026, 1, 1),
                  datetime(2026, 1, 1, tzinfo=timezone.utc))
        encodings = [json.dumps(c._canonical_configuration_value(v)) for v in values]
        self.assertEqual(len(set(encodings)), len(values))
        for unsupported in (True, 1.0, object(), D('NaN')):
            with self.assertRaises(ReadOnlyError): c._canonical_configuration_value(unsupported)

    def test_low_decimal_context_deterministic(self):
        expected = c._configuration_fingerprint(FROZEN_V1)
        with localcontext() as ctx:
            ctx.prec = 1
            self.assertEqual(c._configuration_fingerprint(FROZEN_V1), expected)

    def test_identity_has_no_caller_override(self):
        with self.assertRaises(TypeError): c._configuration_fingerprint(FROZEN_V1, configuration_id='caller')


for config_field in fields(StrategyConfig):
    def test(self, config_field=config_field):
        old = getattr(FROZEN_V1, config_field.name)
        if type(old) is D: changed = D(Fraction(old).numerator + Fraction(old).denominator) / D(Fraction(old).denominator)
        elif type(old) is int: changed = old + 1
        elif type(old) is timedelta: changed = old + timedelta(seconds=1)
        else: changed = time(5, 0)
        research = replace(FROZEN_V1, **{config_field.name: changed})
        self.assertNotEqual(c._configuration_fingerprint(research), c._configuration_fingerprint(FROZEN_V1))
        values = inputs()
        values['position'] = replace(values['position'], config=research)
        with self.assertRaises(ReadOnlyError): c._adapt_dispatch_journal_context(**values)
    setattr(ConfigurationTests, 'test_each_field_' + config_field.name, test)


class BoundaryTests(unittest.TestCase):
    def test_private_exports_and_no_authority_fields(self):
        self.assertFalse(hasattr(__import__('tradingbot_broker'), '_adapt_dispatch_journal_context'))
        names = {f.name for f in fields(c._DispatchJournalContext)}
        self.assertEqual(names, {'order', 'contract', 'strategy', 'risk', 'validated_at'})
        self.assertNotIn('capability', inspect.signature(c._adapt_dispatch_journal_context).parameters)

    def test_no_storage_transport_or_clock_import_in_adapter(self):
        tree = ast.parse(inspect.getsource(c))
        imports = [node.module or '' for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        self.assertFalse(any(any(bad in name for bad in ('sqlite', 'paper_dispatch.', 'ibkr_paper_transport', 'socket'))
                             for name in imports))

    def test_restart_and_mode_contract_unchanged(self):
        header = j._JournalHeader(1, 'journal-'+'a'*32, j._JournalAccountBinding('binding-'+'b'*32))
        self.assertIs(header.account_mode, AccountMode.UNKNOWN)
        self.assertTrue(j._replay_paper_execution_journal(header, ()).restart_quarantine_required)
