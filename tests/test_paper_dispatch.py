"""Atomic entry bridge exercised only with anonymous SDK/socket doubles."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace, FrozenInstanceError
from datetime import timedelta
from decimal import Decimal
from threading import Event, Barrier
from unittest.mock import patch, PropertyMock
import importlib
import unittest
import gc
import weakref

from tradingbot_broker.paper_dispatch import (_PaperOrderDispatchCoordinator as Coordinator,
    _BridgeState as State, _BridgeReason as Reason)
from tradingbot_broker.ibkr import IBKRContract
from tradingbot_broker.ibkr_readonly_broker import ReadOnlyIBKRBroker
from tradingbot_broker.ibkr_paper_transport import (PaperTWSTransport, PaperTWSConfig,
    PaperTransportIntent, _DispatchState, _DispatchResult, _Operation, _Reason)
from tradingbot_broker.models import (TradingMode, Side, OrderType, TimeInForce, OrderIntent,
    BrokerReason, RiskPermission, OrderRequest)
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tradingbot_broker.paper_execution import PaperExecutionScope as Scope, PaperExecutionAuthorizationStatus as AuthStatus
from tradingbot_broker.safety import submission_gates
from tests.test_paper_execution_authorization import AuthorizationFixture
from tests.test_ibkr_paper_transport import sdk
from tests.broker_fixtures import request, permission, contract
from tests.test_ibkr_paper_enrollment import OTHER_ACCOUNT, ACCOUNT


class BridgeFixture(AuthorizationFixture):
    def build(self, *, arm=True, scopes=(Scope.PLACE_ORDER,), paper_api=None, mappings=None):
        self.ready()
        self.capability = self.arm(scopes=scopes) if arm else None
        self.paper_api = sdk() if paper_api is None else paper_api
        with patch('tradingbot_broker.ibkr_paper_transport._load_paper_api', return_value=self.paper_api):
            self.write = PaperTWSTransport(PaperTWSConfig(PaperTransportIntent.LOCAL_PAPER_FOUNDATION, client_id=2))
        self.addCleanup(self.write.disconnect)
        self.write._begin_generation()
        self.client = self.paper_api.clients[-1]
        self.client.wrapper.nextValidId(10)
        self.write._synchronize_open_orders()
        self.order = request()
        self.permission = permission(self.order)
        defaults = (contract(exchange='SMART', con_id=17),)
        self.contracts = defaults if mappings is None else mappings(defaults) if callable(mappings) else mappings
        self.coordinator = Coordinator(self.broker, self.write, self.contracts)
        return self.coordinator

    def invoke(self, order=None, permission_value=None, capability=None):
        return self.coordinator._dispatch_entry(self.order if order is None else order,
            self.permission if permission_value is None else permission_value,
            self.capability if capability is None else capability)

    def write_calls(self):
        return [c for c in self.paper_api.calls if c[0] in ('place', 'cancel', 'global')]

    def assert_denied(self, outcome):
        self.assertIs(outcome.state, State.DENIED)
        self.assertFalse(self.write_calls())
        self.assertFalse(hasattr(outcome, 'accepted'))

    def intervene(self, change, *, preparation=False):
        owner, name = (self.write, '_build_order') if preparation else (self.coordinator, '_translate')
        original = getattr(owner, name)
        def changed(order):
            result = original(order)
            change()
            return result
        setattr(owner, name, changed)


class ConstructionTests(BridgeFixture):
    def test_exact_broker_required(self):
        self.build()
        for candidate in (None, object(), self.write):
            with self.assertRaises(ReadOnlyError): Coordinator(candidate, self.write, self.contracts)
        class Derived(ReadOnlyIBKRBroker): pass
        fake = object.__new__(Derived)
        with self.assertRaises(ReadOnlyError): Coordinator(fake, self.write, self.contracts)

    def test_exact_transport_required(self):
        self.build()
        class Derived(PaperTWSTransport): pass
        for candidate in (None, object(), self.transport, object.__new__(Derived)):
            with self.assertRaises(ReadOnlyError): Coordinator(self.broker, candidate, self.contracts)

    def test_tuple_required(self):
        self.build()
        for candidate in (list(self.contracts), iter(self.contracts), None):
            with self.assertRaises(ReadOnlyError): Coordinator(self.broker, self.write, candidate)

    def test_exact_contract_required(self):
        self.build()
        class Derived(IBKRContract): pass
        for candidate in (None, object(), Derived(**self.contracts[0].__dict__) if hasattr(self.contracts[0], '__dict__') else object.__new__(Derived)):
            with self.assertRaises(ReadOnlyError): Coordinator(self.broker, self.write, (candidate,))

    def test_duplicate_security_id(self):
        self.build()
        with self.assertRaises(ReadOnlyError): Coordinator(self.broker, self.write,
            self.contracts + (replace(self.contracts[0], con_id=18),))

    def test_duplicate_con_id(self):
        self.build()
        with self.assertRaises(ReadOnlyError): Coordinator(self.broker, self.write,
            self.contracts + (replace(self.contracts[0], security_id='other'),))

    def test_constructor_and_import_send_nothing(self):
        self.build()
        before = list(self.paper_api.calls)
        importlib.import_module('tradingbot_broker.paper_dispatch')
        with self.assertRaises(ReadOnlyError): Coordinator(self.broker, self.write, self.contracts)
        self.assertEqual(self.paper_api.calls, before)

    def test_no_public_activation_or_reset_surface(self):
        self.build()
        self.assertEqual([n for n in dir(Coordinator) if not n.startswith('_')], [])
        for name in ('connect','dispatch','place_order','cancel','global_cancel','modify','flatten','reconcile','clear_pending'):
            self.assertFalse(hasattr(self.coordinator, name))

    def test_pairing_same_client_rejected(self):
        self.build()
        self.write._config = replace(self.write._config, client_id=1)
        with self.assertRaises(ReadOnlyError): Coordinator(self.broker, self.write, self.contracts)

    def test_pairing_host_mismatch_rejected(self):
        self.build()
        self.write._config = replace(self.write._config, host='localhost')
        with self.assertRaises(ReadOnlyError): Coordinator(self.broker, self.write, self.contracts)

    def test_pairing_port_mismatch_rejected(self):
        self.build()
        self.write._config = replace(self.write._config, port=4001)
        with self.assertRaises(ReadOnlyError): Coordinator(self.broker, self.write, self.contracts)

    def test_remote_config_mutation_rejected(self):
        self.build()
        object.__setattr__(self.write._config, 'host', 'remote.invalid')
        with self.assertRaises(ReadOnlyError): Coordinator(self.broker, self.write, self.contracts)


class PolicyTests(BridgeFixture):
    def test_clean_pending_is_not_acceptance(self):
        self.build()
        outcome = self.invoke()
        self.assertIs(outcome.state, State.DISPATCHED_PENDING_CONFIRMATION)
        self.assertEqual(outcome.order_id, 10)
        self.assertTrue(outcome.pending_confirmation)
        self.assertFalse(outcome.reconciliation_required)
        self.assertFalse(hasattr(outcome, 'accepted'))
        self.assertEqual(len(self.write_calls()), 1)
        self.assertEqual(self.write_calls()[0][1:], (10, 17, 'BUY', Decimal('10')))
        self.assertIs(self.broker.account_mode, AccountMode.UNKNOWN)
        self.assertIsNone(self.broker._account.mode)
        self.assertFalse(self.broker.controls.trading_enabled)

    def test_generic_submission_remains_blocked(self):
        self.build()
        gates = submission_gates(self.broker.mode, self.broker._connection, self.broker._account,
            self.broker.controls, self.order, self.permission, self.clock())
        self.assertEqual(gates, (BrokerReason.ACCOUNT_MODE_UNVERIFIED, BrokerReason.TRADING_DISABLED))

    def test_exact_only_unknown_failure_required(self):
        self.build()
        with patch('tradingbot_broker.paper_dispatch.submission_gates', return_value=()): self.assert_denied(self.invoke())

    def test_extra_generic_failure_denied(self):
        self.build()
        with patch('tradingbot_broker.paper_dispatch.submission_gates',
            return_value=(BrokerReason.ACCOUNT_MODE_UNVERIFIED, BrokerReason.RECONCILIATION_REQUIRED)):
            self.assert_denied(self.invoke())

    def test_unarmed_matched_insufficient(self):
        self.build(arm=False)
        self.assert_denied(self.coordinator._dispatch_entry(self.order, self.permission, None))

    def test_duplicate_after_denial_never_retries(self):
        self.build()
        self.broker.pause_new_entries(self.clock())
        self.assert_denied(self.invoke())
        self.broker.resume_new_entries(self.clock())
        outcome = self.invoke()
        self.assertIs(outcome.reason, Reason.DUPLICATE_ORDER)
        self.assert_denied(outcome)
        self.assertEqual(self.write._lifetime_order_floor, 10)

    def test_changed_duplicate_payload_rejected(self):
        self.build()
        self.invoke()
        different = replace(self.order, quantity=Decimal('99'))
        outcome = self.invoke(different, permission(different))
        self.assertIs(outcome.reason, Reason.DUPLICATE_ORDER)
        self.assertEqual(len(self.write_calls()), 1)
        self.assertEqual(self.write._lifetime_order_floor, 11)

    def test_second_new_blocked_pending(self):
        self.build()
        self.invoke()
        other = request('second')
        outcome = self.invoke(other, permission(other))
        self.assertIs(outcome.reason, Reason.PENDING_CONFIRMATION)
        self.assertEqual(len(self.write_calls()), 1)
        self.assertEqual(self.write._lifetime_order_floor, 11)

    def test_existing_production_broker_still_blocks(self):
        self.build()
        for method, args in ((self.broker.place_order,(self.order,self.clock(),self.permission)),
            (self.broker.replace_order,('1',self.order,self.clock(),self.permission)),
            (self.broker.cancel_order,('1',self.clock())), (self.broker.cancel_working_orders,(self.clock(),)),
            (self.broker.flatten_positions,(self.clock(),)),(self.broker.set_trading_enabled,(True,self.clock()))):
            with self.assertRaises(Exception): method(*args)
        self.assertFalse(self.write_calls())

    def test_read_source_has_no_coordinator_bridge(self):
        import inspect
        self.assertNotIn('paper_dispatch', inspect.getsource(ReadOnlyIBKRBroker))

    def test_account_paper_not_fabricated(self):
        self.build()
        self.broker._account = replace(self.broker._account, mode=TradingMode.PAPER)
        self.assert_denied(self.invoke())

    def test_account_live_denied(self):
        self.build()
        self.broker._account = replace(self.broker._account, mode=TradingMode.LIVE)
        self.assert_denied(self.invoke())

    def test_account_unverified_denied(self):
        self.build()
        self.broker._account = replace(self.broker._account, verified=False)
        self.assert_denied(self.invoke())

    def test_unknown_account_mode_required(self):
        self.build()
        with patch.object(ReadOnlyIBKRBroker,'account_mode',new_callable=PropertyMock,
                          return_value=AccountMode.PAPER):
            self.assert_denied(self.invoke())

    def test_account_not_yet_available(self):
        self.build()
        self.broker._account=replace(self.broker._account,
            available_at=self.clock.now+timedelta(seconds=10))
        self.assert_denied(self.invoke())

    def test_control_reconciliation_blocks(self):
        self.build()
        self.broker._reconciliation_required=True
        outcome=self.invoke()
        self.assertIs(outcome.state,State.OUTCOME_UNKNOWN)
        self.assertIs(outcome.reason,Reason.RECONCILIATION_REQUIRED)
        self.assertTrue(self.write._requires_reconciliation())
        self.assertFalse(self.write_calls())

    def test_entries_pause_blocks(self):
        self.build()
        self.broker.pause_new_entries(self.clock())
        self.assert_denied(self.invoke())

    def test_emergency_stop_blocks(self):
        self.build()
        self.broker.emergency_stop(self.clock())
        self.assert_denied(self.invoke())

    def test_order_not_yet_valid(self):
        self.build()
        self.order=replace(self.order,created_at=self.clock.now+timedelta(seconds=10))
        self.permission=permission(self.order)
        self.assert_denied(self.invoke())

    def test_expired_order(self):
        self.build()
        self.order=replace(self.order,expires_at=self.clock.now)
        self.permission=permission(self.order)
        self.assert_denied(self.invoke())

    def test_foreign_authority_capability_denied(self):
        from tradingbot_broker.paper_execution import _PaperExecutionAuthority
        self.build()
        authority=_PaperExecutionAuthority()
        foreign=authority._issue(self.broker._paper_authority._binding,self.clock(),
            self.monotonic(),self.capability.expires_at,(Scope.PLACE_ORDER,))
        self.assert_denied(self.invoke(capability=foreign))

    def test_exact_permission_required(self):
        self.build()
        class Derived(RiskPermission): pass
        values={f.name:getattr(self.permission,f.name) for f in __import__('dataclasses').fields(RiskPermission)}
        self.assert_denied(self.invoke(permission_value=Derived(**values)))

    def test_exact_request_required(self):
        self.build()
        class Derived(OrderRequest): pass
        values={f.name:getattr(self.order,f.name) for f in __import__('dataclasses').fields(OrderRequest)}
        with self.assertRaisesRegex(ReadOnlyError,'INVALID_PAPER_BRIDGE_INPUT'):
            self.invoke(order=Derived(**values))

    def test_input_permission_equality_spoof_rejected(self):
        self.build()
        class Spoof:
            def __eq__(self, other): return True
        object.__setattr__(self.permission,'order',Spoof())
        self.assert_denied(self.invoke())

    def test_wrong_risk_order(self):
        self.build()
        self.assert_denied(self.invoke(permission_value=permission(request('different'))))

    def test_missing_permission(self):
        self.build()
        self.assert_denied(self.coordinator._dispatch_entry(self.order, None, self.capability))

    def test_reconstructed_capability(self):
        self.build()
        self.assert_denied(self.invoke(capability=replace(self.capability)))

    def test_mutated_capability_equality_spoof(self):
        self.build()
        class Spoof:
            def __eq__(self, other): return True
        object.__setattr__(self.capability, 'scopes', (Spoof(),))
        self.assert_denied(self.invoke())

    def test_mutated_order_enum_spoof(self):
        self.build()
        object.__setattr__(self.order, 'side', 'BUY')
        self.assert_denied(self.invoke())

    def test_missing_contract(self):
        self.build(mappings=())
        self.assert_denied(self.invoke())

    def test_no_public_risk_creation(self):
        self.assertFalse(hasattr(Coordinator, 'permission'))
        self.assertFalse(hasattr(Coordinator, 'RiskPermission'))

    def test_scope_cancel_not_place(self):
        self.build(scopes=(Scope.CANCEL_ORDER,))
        self.assert_denied(self.invoke())

    def test_scope_global_not_place(self):
        self.build(scopes=(Scope.GLOBAL_CANCEL,))
        self.assert_denied(self.invoke())

    def test_enrollment_mismatch_denied(self):
        self.build()
        self.enroll(OTHER_ACCOUNT, replace_existing=True)
        self.assert_denied(self.invoke())

    def test_enrollment_invalid_denied(self):
        self.build()
        self.path.write_text('{')
        self.assert_denied(self.invoke())

    def test_unenrolled_denied(self):
        self.build()
        self.path.unlink()
        self.assert_denied(self.invoke())


def order_shape(changes, mutated=False):
    def test(self):
        self.build()
        if mutated:
            for name, value in changes.items(): object.__setattr__(self.order, name, value)
        else:
            self.order = replace(self.order, **changes)
            self.permission = permission(self.order)
        self.assert_denied(self.invoke())
    return test


for name, values, mutated in (
    ('live', {'mode':TradingMode.LIVE}, False), ('sell',{'side':Side.SELL},False),
    ('exit',{'intent':OrderIntent.EXIT},False), ('protective',{'intent':OrderIntent.PROTECTIVE},False),
    ('limit',{'order_type':OrderType.LIMIT,'limit_price':Decimal('1')},False),
    ('stop',{'order_type':OrderType.STOP,'stop_price':Decimal('1')},False),
    ('price',{'limit_price':Decimal('1')},True),('tif',{'time_in_force':'GTC'},True),
    ('zero_quantity',{'quantity':Decimal('0')},True),('float_quantity',{'quantity':1.0},True)):
    setattr(PolicyTests,'test_shape_'+name,order_shape(values,mutated))


def invalid_contract(changes, mutate=False):
    def test(self):
        self.build(mappings=None if mutate else lambda mappings:(replace(mappings[0],**changes),))
        if mutate:
            for name,value in changes.items(): object.__setattr__(self.contracts[0],name,value)
        self.assert_denied(self.invoke())
    return test


for name, changes, mutate in (
    ('unverified',{'verified':False},False),('future',{'available_at':request().expires_at},False),
    ('before_valid',{'valid_from':request().created_at+timedelta(minutes=1)},False),
    ('expired',{'valid_until':request().created_at+timedelta(microseconds=1)},False),
    ('symbol',{'symbol':'OTHER'},False),('currency',{'currency':'EUR'},False),
    ('security',{'security_id':'other'},False),('exchange',{'exchange':'SIM'},False),
    ('non_stock',{'security_type':'FUT'},True),('bool_conid',{'con_id':True},True),
    ('string_conid',{'con_id':'17'},True),('changed_id',{'con_id':18},True)):
    setattr(PolicyTests,'test_contract_'+name,invalid_contract(changes,mutate))


def invalid_risk(changes):
    def test(self):
        self.build()
        self.permission=replace(self.permission,**changes)
        self.assert_denied(self.invoke())
    return test


for name,changes in (('future',{'available_at':request().created_at+timedelta(minutes=1)}),
    ('expired',{'valid_until':request().created_at+timedelta(microseconds=1)}),('denied',{'permits_entry':False})):
    setattr(PolicyTests,'test_permission_'+name,invalid_risk(changes))


class FreshnessTests(BridgeFixture):
    def transition(self, name):
        if name=='authorization_wall': self.clock.now=self.capability.expires_at
        elif name=='authorization_monotonic': self.monotonic.value=self.broker._paper_authority._deadline
        elif name=='account': self.clock.now=self.broker._account.valid_until
        elif name=='contract': self.clock.now=self.contracts[0].valid_until
        elif name=='risk': self.clock.now=self.permission.valid_until
        elif name=='order': self.clock.now=self.order.expires_at
        elif name=='disarm': self.broker.disarm_paper_execution()
        elif name=='disconnect': self.broker.disconnect()
        elif name=='refresh': self.broker.refresh()
        elif name=='emergency': self.broker.emergency_stop(self.clock())
        elif name=='pause': self.broker.pause_new_entries(self.clock())
        elif name=='enrollment': self.path.write_text('{')
        elif name=='write_disconnect': self.write.disconnect()
        elif name=='write_generation': self.write._begin_generation()
        elif name=='permission_mutation': object.__setattr__(self.permission,'permits_entry',False)
        elif name=='order_mutation': object.__setattr__(self.order,'quantity',Decimal('99'))
        elif name=='contract_mutation': object.__setattr__(self.contracts[0],'con_id',99)
        elif name=='capability_mutation': object.__setattr__(self.capability,'generation',99)
        elif name=='pairing': self.write._config=replace(self.write._config,port=4001)
        elif name=='reconciliation': self.write._latch_reconciliation()


def final_transition(name, preparation):
    def test(self):
        self.build(mappings=(lambda mappings:(replace(mappings[0],
            valid_until=request().created_at+timedelta(seconds=2)),)) if name=='contract' else None)
        # Isolate contract/risk/order expiry from the account/authorization window.
        end=self.clock.now+timedelta(seconds=2)
        if name=='risk': self.permission=replace(self.permission,valid_until=end)
        elif name=='order':
            self.order=replace(self.order,expires_at=end)
            self.permission=permission(self.order)
        self.intervene(lambda:self.transition(name),preparation=preparation)
        outcome=self.invoke()
        if name=='reconciliation':
            self.assertIs(outcome.state,State.OUTCOME_UNKNOWN)
            self.assertTrue(outcome.reconciliation_required)
            self.assertFalse(self.write_calls())
        else:self.assert_denied(outcome)
    return test


for name in ('authorization_wall','authorization_monotonic','account','contract','risk','order',
    'disarm','disconnect','refresh','emergency','pause','enrollment','write_disconnect','write_generation',
    'permission_mutation','order_mutation','contract_mutation','capability_mutation','pairing','reconciliation'):
    for preparation in (False,True):
        setattr(FreshnessTests,'test_'+name+('_during_sdk_preparation' if preparation else '_after_preliminary'),
            final_transition(name,preparation))


class OutcomeTests(BridgeFixture):
    def assert_reconciled(self):
        self.assertTrue(self.coordinator._reconciliation_required)
        self.assertTrue(self.write._requires_reconciliation())
        self.assertTrue(self.broker.reconciliation_required)
        self.assertIs(self.broker._paper_authority.status,AuthStatus.INVALIDATED)
        before=len(self.write_calls())
        other=request('second')
        self.assertIs(self.invoke(other,permission(other)).state,State.OUTCOME_UNKNOWN)
        self.assertEqual(len(self.write_calls()),before)
        self.write._begin_generation()
        self.paper_api.clients[-1].wrapper.nextValidId(10)
        self.write._synchronize_open_orders()
        self.assertTrue(self.write._requires_reconciliation())
        self.assertTrue(self.coordinator._reconciliation_required)

    def test_unknown_propagates_to_control_plane(self):
        self.build(paper_api=sdk(raises=True))
        outcome=self.invoke()
        self.assertIs(outcome.state,State.OUTCOME_UNKNOWN)
        self.assertTrue(outcome.reconciliation_required)
        self.assertNotIn('anonymous private error',repr(outcome))
        self.assert_reconciled()

    def test_synchronous_error_is_not_clean_pending(self):
        self.build(paper_api=sdk(error=True))
        outcome=self.invoke()
        self.assertIs(outcome.state,State.OUTCOME_UNKNOWN)
        self.assertFalse(outcome.pending_confirmation)
        self.assert_reconciled()

    def test_c1_local_preflight_denial(self):
        self.build()
        self.write._initialized=False
        self.assert_denied(self.invoke())
        self.assertFalse(self.coordinator._reconciliation_required)

    def test_unexpected_exception_sanitized(self):
        self.build()
        with patch.object(self.write,'_dispatch_new',side_effect=RuntimeError('anonymous-private-exception')):
            outcome=self.invoke()
        self.assertIs(outcome.state,State.OUTCOME_UNKNOWN)
        self.assertNotIn('anonymous-private-exception',repr(outcome))
        self.assert_reconciled()

    def test_result_clock_failure_sanitized(self):
        self.build()
        with patch.object(self.broker,'_clock',side_effect=RuntimeError('anonymous-secret-clock')):
            with self.assertRaisesRegex(ReadOnlyError,'^PAPER_BRIDGE_RESULT_UNAVAILABLE$') as caught:
                self.invoke()
        self.assertNotIn('anonymous-secret-clock',str(caught.exception))
        self.assertFalse(self.write_calls())

    def test_reporting_failure_cannot_clear_committed_pending(self):
        self.build()
        with patch('tradingbot_broker.paper_dispatch._BridgeResult',side_effect=RuntimeError('anonymous-report-failure')):
            with self.assertRaisesRegex(ReadOnlyError,'^PAPER_BRIDGE_RESULT_UNAVAILABLE$'): self.invoke()
        self.assertTrue(self.coordinator._pending_confirmation)
        self.assertEqual(len(self.write_calls()),1)

    def test_reporting_failure_cannot_clear_reconciliation(self):
        self.build(paper_api=sdk(raises=True))
        with patch('tradingbot_broker.paper_dispatch._BridgeResult',side_effect=RuntimeError('anonymous-report-failure')):
            with self.assertRaisesRegex(ReadOnlyError,'^PAPER_BRIDGE_RESULT_UNAVAILABLE$'): self.invoke()
        self.assert_reconciled()

    def test_delayed_error_blocked_and_propagated(self):
        self.build()
        self.invoke()
        self.client.wrapper.error(10,550,'anonymous-private-error')
        self.assertIs(self.invoke().state,State.OUTCOME_UNKNOWN)
        self.assert_reconciled()

    def test_result_immutable_and_private(self):
        self.build()
        outcome=self.invoke()
        with self.assertRaises(FrozenInstanceError): outcome.order_id=99
        for text in (ACCOUNT,'capability_id','fingerprint','salt','trust','EClient','OrderCancel'):
            self.assertNotIn(text,repr(outcome))
        self.assertNotIn(ACCOUNT,repr(self.broker.audit))
        self.assertEqual(repr(self.coordinator),'<OfflinePaperOrderDispatchCoordinator>')

    def test_denied_reserved_id_still_consumed(self):
        self.build()
        allocate=self.write._allocate_order_id
        def reserved(generation):
            value=allocate(generation)
            self.broker.pause_new_entries(self.clock())
            return value
        self.write._allocate_order_id=reserved
        self.assert_denied(self.invoke())
        self.assertEqual(self.write._lifetime_order_floor,11)

    def test_pending_survives_reconnect(self):
        self.build(); self.invoke()
        self.write._begin_generation()
        self.assertTrue(self.coordinator._pending_confirmation)
        other=request('second')
        outcome=self.invoke(other,permission(other))
        self.assertIs(outcome.reason,Reason.PENDING_CONFIRMATION)
        self.assertEqual(len(self.write_calls()),1)


def interruption_case(exception_type,preflight=False):
    def test(self):
        self.build()
        interruption=exception_type(37)
        if preflight:
            original=self.write._allocate_order_id
            def interrupted(generation):
                original(generation)
                raise interruption
            self.write._allocate_order_id=interrupted
        else:
            original=self.client.placeOrder
            def interrupted(*args):
                original(*args)
                raise interruption
            self.client.placeOrder=interrupted
        with self.assertRaises(exception_type) as caught: self.invoke()
        self.assertIs(caught.exception,interruption)
        self.assertEqual(self.write._lifetime_order_floor,11)
        if preflight:
            self.assertFalse(self.write_calls())
            self.assertFalse(self.coordinator._reconciliation_required)
        else:
            self.assert_reconciled()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(self.client.wrapper.openOrder,100,None,None,None).result(timeout=5)
            pool.submit(self.write.disconnect).result(timeout=5)
        self.assertNotIn('37',repr(self.coordinator))
    return test


for exc in (KeyboardInterrupt,SystemExit,GeneratorExit):
    for preflight in (False,True):
        setattr(OutcomeTests,'test_'+exc.__name__+('_before_invocation' if preflight else '_after_invocation'),
            interruption_case(exc,preflight))


class ConcurrencyTests(BridgeFixture):
    def test_two_attempts_cannot_duplicate_or_bypass_pending(self):
        self.build()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(self.invoke) for _ in range(2)]
            outcomes=[f.result(timeout=5) for f in futures]
        self.assertEqual(sum(o.state is State.DISPATCHED_PENDING_CONFIRMATION for o in outcomes),1)
        self.assertEqual(len(self.write_calls()),1)

    def committed_control_race(self,action):
        self.build()
        inside,release,started=Event(),Event(),Event()
        original=self.client.placeOrder
        def paused(*args):
            inside.set()
            if not release.wait(5): raise AssertionError('offline coordination timeout')
            return original(*args)
        self.client.placeOrder=paused
        def control():
            started.set()
            if action=='disarm': return self.broker.disarm_paper_execution()
            if action=='disconnect': return self.broker.disconnect()
            if action=='pause': return self.broker.pause_new_entries(self.clock())
            return self.broker.emergency_stop(self.clock())
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempt=pool.submit(self.invoke)
            try:
                self.assertTrue(inside.wait(5))
                transition=pool.submit(control)
                self.assertTrue(started.wait(5))
                self.assertFalse(transition.done())
            finally: release.set()
            self.assertIs(attempt.result(timeout=5).state,State.DISPATCHED_PENDING_CONFIRMATION)
            transition.result(timeout=5)
        self.assertEqual(len(self.write_calls()),1)

    def test_control_wins_before_final_commit(self):
        self.build()
        translated,release=Event(),Event()
        original=self.coordinator._translate
        def paused(order):
            result=original(order); translated.set()
            if not release.wait(5): raise AssertionError('offline coordination timeout')
            return result
        self.coordinator._translate=paused
        with ThreadPoolExecutor(max_workers=1) as pool:
            attempt=pool.submit(self.invoke)
            try:
                self.assertTrue(translated.wait(5))
                self.broker.disarm_paper_execution()
            finally: release.set()
            self.assert_denied(attempt.result(timeout=5))


for action in ('disarm','disconnect','pause','emergency'):
    setattr(ConcurrencyTests,'test_commit_wins_before_'+action,
        lambda self,action=action:self.committed_control_race(action))


class OwnershipCorrections(BridgeFixture):
    def assert_second_rejected(self, mappings=None):
        with self.assertRaisesRegex(ReadOnlyError,'^PAPER_COORDINATOR_ALREADY_BOUND$'):
            Coordinator(self.broker,self.write,self.contracts if mappings is None else mappings)
        self.assertIs(self.write._dispatch_coordinator_owner,self.coordinator)

    def test_exact_first_owner_retained(self):
        self.build()
        self.assertIs(self.write._dispatch_coordinator_owner,self.coordinator)
        self.assert_second_rejected()

    def test_distinct_equal_contract_tuple_does_not_reset_owner(self):
        self.build()
        mappings=tuple(list(self.contracts))
        self.assertIsNot(mappings,self.contracts)
        self.assert_second_rejected(mappings)

    def test_denied_client_id_cannot_be_retried_by_reconstruction(self):
        self.build()
        self.broker.pause_new_entries(self.clock())
        self.assert_denied(self.invoke())
        self.broker.resume_new_entries(self.clock())
        self.assert_second_rejected()
        self.assertIs(self.invoke().reason,Reason.DUPLICATE_ORDER)
        self.assertFalse(self.write_calls())
        self.assertEqual(self.write._lifetime_order_floor,10)

    def test_pending_cannot_be_bypassed_by_reconstruction(self):
        self.build(); self.invoke(); self.assert_second_rejected()
        other=request('other')
        self.assertIs(self.invoke(other,permission(other)).reason,Reason.PENDING_CONFIRMATION)
        self.assertEqual(len(self.write_calls()),1)

    def test_owner_has_strong_lifetime_reference(self):
        self.build()
        ref=weakref.ref(self.coordinator)
        self.coordinator=None
        gc.collect()
        self.assertIsNotNone(ref())
        self.assertIs(self.write._dispatch_coordinator_owner,ref())
        with self.assertRaises(ReadOnlyError): Coordinator(self.broker,self.write,self.contracts)

    def test_binding_cannot_be_assigned_or_cleared(self):
        self.build()
        with self.assertRaises(AttributeError): self.write._dispatch_coordinator_owner=None
        with self.assertRaises(ReadOnlyError): self.write._PaperTWSTransport__dispatch_binding=None
        with self.assertRaises(ReadOnlyError): del self.write._PaperTWSTransport__dispatch_binding
        with self.assertRaises(ReadOnlyError): self.write._claim_dispatch_coordinator(self.coordinator)
        for name in ('release_owner','reset_owner','clear_coordinator','unbind_coordinator'):
            self.assertFalse(hasattr(self.write,name))
        self.assert_second_rejected()

    def test_two_concurrent_constructions_exactly_one_wins(self):
        self.build()
        with patch('tradingbot_broker.ibkr_paper_transport._load_paper_api',return_value=self.paper_api):
            unowned=PaperTWSTransport(self.write._config)
        self.addCleanup(unowned.disconnect)
        rendezvous=Barrier(2)
        def claim():
            rendezvous.wait(timeout=5)
            try:return Coordinator(self.broker,unowned,self.contracts)
            except ReadOnlyError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(claim) for _ in range(2)]
            results=[f.result(timeout=5) for f in futures]
        owners=[r for r in results if r is not None]
        self.assertEqual(len(owners),1)
        self.assertIs(unowned._dispatch_coordinator_owner,owners[0])


def ownership_survives(action):
    def test(self):
        self.build()
        if action=='disconnect':self.write.disconnect()
        elif action=='generation':self.write._begin_generation()
        elif action=='reset':self.write._reset()
        elif action=='pending':self.invoke()
        else:self.write._latch_reconciliation()
        self.assert_second_rejected()
    return test


for action in ('disconnect','generation','reset','pending','reconciliation'):
    setattr(OwnershipCorrections,'test_owner_survives_'+action,ownership_survives(action))


class ProofCorrections(BridgeFixture):
    def test_legitimate_proof_exact_identity_inside_commitment(self):
        original=Coordinator._validate_commit
        observations=[]
        def checked(owner,challenge):
            self.assertTrue(owner._write._arrival_lock._is_owned())
            self.assertFalse(owner._write._write_invocation_started)
            value=original(owner,challenge)
            self.assertIs(value,challenge)
            observations.append(True)
            return value
        with patch.object(Coordinator,'_validate_commit',checked):self.build()
        self.assertIs(self.invoke().state,State.DISPATCHED_PENDING_CONFIRMATION)
        self.assertEqual(observations,[True])

    def test_captured_validator_ignores_instance_replacement(self):
        self.build()
        self.coordinator._validate_commit=lambda challenge:False
        self.coordinator._prepare_commit=lambda: (_ for _ in ()).throw(RuntimeError('anonymous'))
        self.assertIs(self.invoke().state,State.DISPATCHED_PENDING_CONFIRMATION)

    def test_captured_validator_ignores_later_class_replacement(self):
        self.build()
        with patch.object(Coordinator,'_validate_commit',lambda owner,challenge:False):
            self.assertIs(self.invoke().state,State.DISPATCHED_PENDING_CONFIRMATION)

    def test_replacement_cannot_bypass_disarm(self):
        self.build()
        self.coordinator._validate_commit=lambda challenge:challenge
        self.intervene(self.broker.disarm_paper_execution,preparation=True)
        self.assert_denied(self.invoke())

    def test_per_dispatch_hook_argument_removed(self):
        self.build()
        with self.assertRaises(TypeError):
            self.write._dispatch_new(self.coordinator._translate(self.order),_before_commit=lambda:True)
        self.assertFalse(self.write_calls())

    def test_stale_proof_cannot_be_replayed(self):
        challenges=[]
        def replay(owner,challenge):
            challenges.append(challenge)
            return False if len(challenges)==1 else challenges[0]
        with patch.object(Coordinator,'_validate_commit',replay):self.build()
        self.assert_denied(self.invoke())
        other=request('new-attempt')
        self.assert_denied(self.invoke(other,permission(other)))
        self.assertEqual(len(challenges),2)
        self.assertIsNot(challenges[0],challenges[1])
        self.assertEqual(self.write._lifetime_order_floor,12)

    def test_validator_ordinary_exception_denies_without_uncertainty(self):
        def failed(owner,challenge):raise RuntimeError('anonymous-validator-secret')
        with patch.object(Coordinator,'_validate_commit',failed):self.build()
        outcome=self.invoke()
        self.assert_denied(outcome)
        self.assertEqual(outcome.order_id,10)
        self.assertEqual(self.write._lifetime_order_floor,11)
        self.assertFalse(self.write._requires_reconciliation())
        self.assertNotIn('anonymous-validator-secret',repr(outcome))

    def test_enrollment_io_is_outside_commitment_lock(self):
        self.build()
        original=self.store._read_record
        seen=[]
        def read():
            self.assertFalse(self.write._arrival_lock._is_owned())
            seen.append(True)
            return original()
        with patch.object(self.store,'_read_record',read):
            self.assertIs(self.invoke().state,State.DISPATCHED_PENDING_CONFIRMATION)
        self.assertTrue(seen)

    def test_owned_transport_direct_dispatch_without_attempt_denied(self):
        self.build()
        outcome=self.write._dispatch_new(self.coordinator._translate(self.order))
        self.assertIs(outcome.state,_DispatchState.NOT_DISPATCHED)
        self.assertFalse(self.write_calls())

    def test_configuration_validation_never_runs_under_arrival_lock(self):
        self.build()
        original=type(self.transport.config).__post_init__
        seen=[]
        def validate(config):
            self.assertFalse(self.write._arrival_lock._is_owned())
            seen.append(True)
            return original(config)
        with patch.object(type(self.transport.config),'__post_init__',validate):
            self.assertIs(self.invoke().state,State.DISPATCHED_PENDING_CONFIRMATION)
        self.assertTrue(seen)

    def test_challenge_absent_from_public_result_audit_repr(self):
        self.build()
        outcome=self.invoke()
        for item in (repr(outcome),repr(self.broker.audit),repr(self.write),repr(self.coordinator)):
            self.assertNotIn('object at 0x',item)
            self.assertNotIn('challenge',item)


def wrong_proof(kind):
    def test(self):
        class Equal:
            def __eq__(self,other):raise AssertionError('Identity must not invoke equality')
            def __bool__(self):return True
        values={'false':False,'true':True,'none':None,'int':1,'object':object(),'equal':Equal()}
        def validator(owner,challenge):return values[kind]
        with patch.object(Coordinator,'_validate_commit',validator):self.build()
        outcome=self.invoke()
        self.assert_denied(outcome)
        self.assertEqual(outcome.order_id,10)
        self.assertEqual(self.write._lifetime_order_floor,11)
        self.assertIn(self.order.client_order_id,self.coordinator._attempts)
        self.assertFalse(self.write._requires_reconciliation())
    return test


for kind in ('false','true','none','int','object','equal'):
    setattr(ProofCorrections,'test_wrong_proof_'+kind,wrong_proof(kind))


def validator_interruption(exception_type):
    def test(self):
        interruption=exception_type(37)
        def validator(owner,challenge):raise interruption
        with patch.object(Coordinator,'_validate_commit',validator):self.build()
        with self.assertRaises(exception_type) as caught:self.invoke()
        self.assertIs(caught.exception,interruption)
        if exception_type is SystemExit:self.assertEqual(caught.exception.code,37)
        self.assertFalse(self.write_calls())
        self.assertFalse(self.write._requires_reconciliation())
        self.assertFalse(self.coordinator._reconciliation_required)
        self.assertEqual(self.write._lifetime_order_floor,11)
        self.assertIn(self.order.client_order_id,self.coordinator._attempts)
        self.assertIsNone(self.coordinator._active_attempt)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(self.write.disconnect).result(timeout=5)
    return test


for exc in (KeyboardInterrupt,SystemExit,GeneratorExit):
    setattr(ProofCorrections,'test_validator_'+exc.__name__,validator_interruption(exc))


class CommitmentCorrections(BridgeFixture):
    def test_prior_arrival_lock_expiry_exploit_is_closed(self):
        gate={'at_commitment':False}
        prepare=Coordinator._prepare_commit
        def prepared(owner):
            prepare(owner)
            gate['at_commitment']=True
        with patch.object(Coordinator,'_prepare_commit',prepared):self.build()
        real=self.write._arrival_lock
        class Delay:
            def __enter__(lock):
                real.acquire()
                if gate['at_commitment']:
                    gate['at_commitment']=False
                    self.clock.now=self.capability.expires_at
                    self.monotonic.value=self.broker._paper_authority._deadline
                return lock
            def __exit__(lock,*args):real.release()
            def _is_owned(lock):return real._is_owned()
        self.write._arrival_lock=Delay()
        self.assert_denied(self.invoke())
        self.assertEqual(self.write._lifetime_order_floor,11)

    def test_final_wall_sampling_can_revoke_before_proof(self):
        self.build()
        original=self.broker._clock
        def sample():
            if self.write._arrival_lock._is_owned():return self.capability.expires_at
            return original()
        self.broker._clock=sample
        self.assert_denied(self.invoke())
        self.assertEqual(self.write._lifetime_order_floor,11)

    def test_final_clock_sampling_can_revoke_before_proof(self):
        self.build()
        original=self.broker._authorization_monotonic
        def sample():
            if self.write._arrival_lock._is_owned():
                return self.broker._paper_authority._deadline
            return original()
        self.broker._authorization_monotonic=sample
        self.assert_denied(self.invoke())
        self.assertEqual(self.write._lifetime_order_floor,11)

    def test_final_validator_checks_read_sdk_local_state_again(self):
        original=Coordinator._validate_commit
        def changed(owner,challenge):
            self.api.clients[-1].isConnected=lambda:False
            return original(owner,challenge)
        with patch.object(Coordinator,'_validate_commit',changed):self.build()
        self.assert_denied(self.invoke())

    def test_final_validator_checks_write_sdk_local_state_again(self):
        original=Coordinator._validate_commit
        def changed(owner,challenge):
            self.client.connected=False
            return original(owner,challenge)
        with patch.object(Coordinator,'_validate_commit',changed):self.build()
        self.assert_denied(self.invoke())

    def test_bound_generation_and_attempt_still_required(self):
        original=Coordinator._validate_commit
        def changed(owner,challenge):
            owner._prepared_attempt=None
            return original(owner,challenge)
        with patch.object(Coordinator,'_validate_commit',changed):self.build()
        self.assert_denied(self.invoke())


def commitment_expiration(name):
    def test(self):
        original=Coordinator._validate_commit
        def expired(owner,challenge):
            if name=='monotonic':self.monotonic.value=self.broker._paper_authority._deadline
            elif name=='authorization':self.clock.now=self.capability.expires_at
            elif name=='account':self.clock.now=self.broker._account.valid_until
            elif name=='risk':self.clock.now=self.permission.valid_until
            elif name=='order':self.clock.now=self.order.expires_at
            else:self.clock.now=self.contracts[0].valid_until
            return original(owner,challenge)
        with patch.object(Coordinator,'_validate_commit',expired):
            self.build(mappings=(lambda mappings:(replace(mappings[0],
                valid_until=request().created_at+timedelta(seconds=2)),)) if name=='contract' else None)
        end=self.clock.now+timedelta(seconds=2)
        if name=='risk':self.permission=replace(self.permission,valid_until=end)
        elif name=='order':
            self.order=replace(self.order,expires_at=end);self.permission=permission(self.order)
        self.assert_denied(self.invoke())
        self.assertEqual(self.write._lifetime_order_floor,11)
    return test


for name in ('monotonic','authorization','account','risk','order','contract'):
    setattr(CommitmentCorrections,'test_commitment_expiration_'+name,commitment_expiration(name))


class EscalationCorrections(BridgeFixture):
    def pending_error(self):
        self.build()
        self.assertIs(self.invoke().state,State.DISPATCHED_PENDING_CONFIRMATION)
        self.client.wrapper.error(10,550,'anonymous-delayed-secret')
        self.assertTrue(self.write._requires_reconciliation())
        # Callback does not acquire earlier broker/coordinator locks.
        self.assertFalse(self.broker.reconciliation_required)

    def assert_escalated(self,outcome):
        self.assertIs(outcome.state,State.OUTCOME_UNKNOWN)
        self.assertIs(outcome.reason,Reason.RECONCILIATION_REQUIRED)
        self.assertTrue(outcome.reconciliation_required)
        self.assertTrue(self.coordinator._pending_confirmation)
        self.assertTrue(self.coordinator._reconciliation_required)
        self.assertTrue(self.broker.reconciliation_required)
        self.assertIs(self.broker._paper_authority.status,AuthStatus.INVALIDATED)
        self.assertEqual(len(self.write_calls()),1)
        self.assertNotIn('anonymous-delayed-secret',repr(outcome))

    def test_pending_delayed_error_next_new_escalates(self):
        self.pending_error();other=request('other')
        self.assert_escalated(self.invoke(other,permission(other)))

    def test_duplicate_cannot_hide_delayed_reconciliation(self):
        self.pending_error()
        self.assert_escalated(self.invoke())

    def test_control_reconciliation_outranks_pending_and_duplicate(self):
        self.build();self.invoke()
        self.broker._reconciliation_required=True
        self.assert_escalated(self.invoke())

    def test_repeated_escalation_idempotent(self):
        self.pending_error();self.invoke()
        count=len(self.broker.audit)
        self.assert_escalated(self.invoke())
        self.assertEqual(len(self.broker.audit),count)

    def test_reconnect_and_risk_refresh_do_not_clear_escalation(self):
        self.pending_error();self.invoke()
        self.write._begin_generation()
        self.paper_api.clients[-1].wrapper.nextValidId(10)
        self.write._synchronize_open_orders()
        other=request('other')
        self.assert_escalated(self.invoke(other,permission(other)))

    def test_new_arm_cannot_clear_reconciliation(self):
        self.pending_error();self.invoke()
        with self.assertRaises(ReadOnlyError):self.arm()
        self.assertTrue(self.broker.reconciliation_required)
        self.assertTrue(self.coordinator._reconciliation_required)

    def test_old_generation_error_does_not_escalate_clean_pending(self):
        self.build();self.invoke();old=self.client
        self.write._begin_generation()
        old.wrapper.error(10,550,'anonymous')
        other=request('other')
        outcome=self.invoke(other,permission(other))
        self.assertIs(outcome.reason,Reason.PENDING_CONFIRMATION)
        self.assertFalse(outcome.reconciliation_required)

    def test_malformed_current_error_latches_safely(self):
        self.build();self.invoke()
        self.client.wrapper.error(10,True,'anonymous')
        self.assert_escalated(self.invoke())

    def test_error_callback_never_waits_for_earlier_locks(self):
        self.build();self.invoke()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.coordinator._lock,self.broker._lock:
                pool.submit(self.client.wrapper.error,10,550,'anonymous').result(timeout=5)
        self.assert_escalated(self.invoke())

    def test_concurrent_duplicate_pending_escalations(self):
        self.pending_error();other=request('other')
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(self.invoke),pool.submit(self.invoke,other,permission(other))]
            for future in futures:self.assert_escalated(future.result(timeout=5))

    def test_delayed_error_during_sdk_send_no_escape_or_deadlock(self):
        self.build()
        inside,release,announced=Event(),Event(),Event()
        real=self.write._arrival_lock
        write=self.write
        class Announce:
            def __enter__(lock):real.acquire();return lock
            def __exit__(lock,*args):
                latched=write._reconciliation_required
                real.release()
                if latched:announced.set()
            def _is_owned(lock):return real._is_owned()
        write._arrival_lock=Announce()
        original=self.client.placeOrder
        def paused(*args):
            original(*args);inside.set()
            if not release.wait(5):raise AssertionError('offline coordination failed')
        self.client.placeOrder=paused
        with ThreadPoolExecutor(max_workers=3) as pool:
            first=pool.submit(self.invoke)
            try:
                self.assertTrue(inside.wait(5))
                callback=pool.submit(self.client.wrapper.error,10,550,'anonymous')
                self.assertTrue(announced.wait(5))
                duplicate=pool.submit(self.invoke)
            finally:release.set()
            self.assertIs(first.result(timeout=5).state,State.OUTCOME_UNKNOWN)
            self.assertIs(duplicate.result(timeout=5).state,State.OUTCOME_UNKNOWN)
            callback.result(timeout=5)
        self.assertEqual(len(self.write_calls()),1)
        self.assertTrue(self.broker.reconciliation_required)
        self.assertIs(self.broker._paper_authority.status,AuthStatus.INVALIDATED)


class AdditionalControlRaces(BridgeFixture):
    def transition(self,action):
        if action=='refresh':return self.broker.refresh()
        if action=='account':
            now=self.clock()
            snapshot=replace(self.broker._account,observed_at=now,available_at=now)
            return self.broker.report_account(snapshot,now)
        return self.broker.enroll_paper_account(__import__('tradingbot_broker.paper_enrollment',
            fromlist=['CONFIRMATION']).CONFIRMATION,replace_existing=True)

    def race(self,action,commit_wins):
        self.build()
        inside,release,started=Event(),Event(),Event()
        if commit_wins:
            original=self.client.placeOrder
            def paused(*args):
                inside.set()
                if not release.wait(5):raise AssertionError('offline coordination failed')
                return original(*args)
            self.client.placeOrder=paused
        else:
            original=self.coordinator._translate
            def paused(order):
                value=original(order);inside.set()
                if not release.wait(5):raise AssertionError('offline coordination failed')
                return value
            self.coordinator._translate=paused
        def control():
            started.set()
            return self.transition(action)
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempt=pool.submit(self.invoke)
            try:
                self.assertTrue(inside.wait(5))
                change=pool.submit(control)
                self.assertTrue(started.wait(5))
                if commit_wins:self.assertFalse(change.done())
                else:change.result(timeout=5)
            finally:release.set()
            outcome=attempt.result(timeout=5)
            change.result(timeout=5)
        if commit_wins:
            self.assertIs(outcome.state,State.DISPATCHED_PENDING_CONFIRMATION)
            self.assertEqual(len(self.write_calls()),1)
        else:self.assert_denied(outcome)


for action in ('refresh','account','enrollment'):
    for wins in (False,True):
        setattr(AdditionalControlRaces,'test_'+action+('_commit_wins' if wins else '_control_wins'),
            lambda self,action=action,wins=wins:self.race(action,wins))


def gate_typing(value):
    def test(self):
        self.build()
        with patch('tradingbot_broker.paper_dispatch.submission_gates',return_value=value):
            self.assert_denied(self.invoke())
    return test


class GateIdentityCorrections(BridgeFixture):pass


for name,value in (('string',('ACCOUNT_MODE_UNVERIFIED',)),('list',[BrokerReason.ACCOUNT_MODE_UNVERIFIED]),
    ('empty',()),('extra',(BrokerReason.ACCOUNT_MODE_UNVERIFIED,BrokerReason.TRADING_DISABLED)),('integer',(1,))):
    setattr(GateIdentityCorrections,'test_generic_gate_rejects_'+name,gate_typing(value))
