"""Session authorization with anonymous offline SDK responses; never real orders."""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
import hashlib
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from tradingbot_broker.broker import BrokerOperationError
from tradingbot_broker.ibkr_readonly import ReadOnlyTWSTransport, TWSReadOnlyConfig
from tradingbot_broker.ibkr_readonly_broker import ReadOnlyIBKRBroker
from tradingbot_broker.models import BrokerReason, ConnectionStatus, TradingMode
from tradingbot_broker.paper_execution import (ARM_CONFIRMATION, DEFAULT_PAPER_SCOPES,
    FUTURE_PAPER_WRITE_IDS, MAX_AUTHORIZATION_LIFETIME, PaperExecutionAuthorization,
    PaperExecutionAuthorizationStatus as Status, PaperExecutionScope as Scope)
from tradingbot_broker.paper_enrollment import CONFIRMATION, PaperEnrollmentStatus
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tradingbot_broker.safety import account_gates
from tests.broker_fixtures import request
from tests.readonly_fixtures import Clock, sdk
from tests.test_ibkr_paper_enrollment import ACCOUNT, OTHER_ACCOUNT, EnrollmentFixture
from tests.test_ibkr_protobuf_reads import protobuf_sdk, proto_frame


class MonotonicClock:
    def __init__(self): self.value = 1000.0
    def __call__(self): return self.value
    def advance(self, seconds): self.value += seconds


class AuthorizationFixture(EnrollmentFixture):
    def ready(self, *, enrolled=True, api=None, ttl=300):
        if enrolled and not self.path.exists(): self.enroll()
        api = protobuf_sdk() if api is None else api
        self.clock, self.monotonic = Clock(), MonotonicClock()
        with patch('tradingbot_broker.ibkr_readonly._load_official_api', return_value=api):
            transport = ReadOnlyTWSTransport(TWSReadOnlyConfig(snapshot_ttl_seconds=ttl),
                clock=self.clock, enrollment_store=self.store)
        broker = ReadOnlyIBKRBroker(transport, clock=self.clock,
                                   authorization_monotonic=self.monotonic)
        self.addCleanup(broker.disconnect)
        self.broker, self.transport, self.api = broker, transport, api
        broker.connect()
        return broker

    def arm(self, **kwargs):
        return self.broker.arm_paper_execution(ARM_CONFIRMATION, **kwargs)

    def assert_revoked(self, capability, status=Status.INVALIDATED):
        self.assertIs(self.broker.paper_execution_status, status)
        for scope in Scope: self.assertFalse(self.broker.validate_paper_execution(capability, scope))


class ArmingTests(AuthorizationFixture):
    def test_no_implicit_arm_from_environment_or_enrollment(self):
        self.ready()
        with patch.dict('os.environ', {'IBKR_PAPER_ARMED':'true', 'IBKR_PORT':'4002'}):
            self.assertIs(self.broker.paper_execution_status, Status.DISARMED)

    def test_arming_preserves_entry_pause_and_global_trading_disabled(self):
        self.ready()
        self.broker.pause_new_entries(self.clock())
        self.arm()
        self.assertTrue(self.broker.controls.entries_paused)
        self.assertFalse(self.broker.controls.trading_enabled)

    def test_live_requested_broker_mode_cannot_mint_authorization(self):
        self.ready()
        self.broker._mode = TradingMode.LIVE  # Simulated corrupted requested mode.
        with self.assertRaisesRegex(ReadOnlyError, 'LIVE_EXECUTION_DISABLED'): self.arm()

    def test_sdk_failure_during_arming_is_sanitized_and_denied(self):
        self.ready()
        with patch.object(self.api.clients[-1], 'isConnected', side_effect=RuntimeError(ACCOUNT)):
            with self.assertRaises(ReadOnlyError) as raised: self.arm()
        self.assertNotIn(ACCOUNT, str(raised.exception))
        self.assertNotIn(ACCOUNT, repr(self.broker.audit))

    def test_matched_alone_is_disarmed_and_unknown(self):
        broker = self.ready()
        self.assertIs(broker.paper_enrollment_status, PaperEnrollmentStatus.MATCHED)
        self.assertIs(broker.paper_execution_status, Status.DISARMED)
        self.assertIs(broker.account_mode, AccountMode.UNKNOWN)
        self.assertIsNone(broker.account_summary().mode)

    def test_port_and_request_mode_cannot_arm_unenrolled_account(self):
        broker = self.ready(enrolled=False)
        self.assertEqual(self.transport.config.port, 4002)
        self.assertIs(broker.mode, TradingMode.PAPER)
        self.assertIs(broker.paper_execution_status, Status.UNAVAILABLE)
        with self.assertRaises(ReadOnlyError): self.arm()
        self.assertFalse(self.path.exists())

    def test_exact_phrase_mints_current_scope_capability(self):
        broker = self.ready()
        cap = self.arm()
        self.assertIs(type(cap), PaperExecutionAuthorization)
        self.assertIs(broker.paper_execution_status, Status.ARMED)
        self.assertTrue(broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        self.assertTrue(broker.validate_paper_execution(cap, Scope.CANCEL_ORDER))
        self.assertFalse(broker.validate_paper_execution(cap, Scope.GLOBAL_CANCEL))
        self.assertIs(broker.account_mode, AccountMode.UNKNOWN)
        self.assertFalse(broker.controls.trading_enabled)

    def test_unenrolled_rejected(self):
        self.ready(enrolled=False)
        with self.assertRaisesRegex(ReadOnlyError, 'PAPER_EXECUTION_ENROLLMENT_UNENROLLED'): self.arm()

    def test_mismatch_rejected(self):
        self.enroll(OTHER_ACCOUNT)
        self.ready()
        with self.assertRaisesRegex(ReadOnlyError, 'PAPER_EXECUTION_ENROLLMENT_MISMATCH'): self.arm()

    def test_tampered_record_rejected(self):
        self.ready()
        record = json.loads(self.path.read_text())
        record['fingerprint'] = '0' * 64
        self.path.write_text(json.dumps(record))
        with self.assertRaises(ReadOnlyError): self.arm()
        self.assertIs(self.broker.paper_execution_status, Status.UNAVAILABLE)

    def test_missing_trust_anchor_rejected(self):
        self.ready()
        self.store._key_path.unlink()
        with self.assertRaises(ReadOnlyError): self.arm()

    def test_disconnected_rejected(self):
        self.ready().disconnect()
        with self.assertRaises(ReadOnlyError): self.arm()

    def test_stale_account_rejected(self):
        self.ready(ttl=15)
        self.clock.advance(16)
        with self.assertRaises(ReadOnlyError): self.arm()

    def test_reconciliation_required_rejected(self):
        self.ready()
        self.broker._reconciliation_required = True  # Simulated unresolved broker evidence.
        with self.assertRaisesRegex(ReadOnlyError, 'RECONCILIATION_REQUIRED'): self.arm()

    def test_conflicting_account_evidence_requires_reconciliation(self):
        self.ready()
        cap = self.arm()
        account = self.broker.account_summary()
        with self.assertRaises(BrokerOperationError):
            self.broker.report_account(replace(account, cash=account.cash + 1), self.clock())
        self.assertTrue(self.broker.reconciliation_required)
        self.assert_revoked(cap)
        with self.assertRaises(ReadOnlyError): self.arm()

    def test_emergency_stopped_rejected(self):
        self.ready().emergency_stop(self.clock())
        with self.assertRaisesRegex(ReadOnlyError, 'EMERGENCY_STOP'): self.arm()

    def test_zero_managed_accounts_connection_and_arm_fail_closed(self):
        with self.assertRaises(ReadOnlyError): self.ready(api=sdk(accounts=''))
        with self.assertRaises(ReadOnlyError): self.arm()

    def test_multiple_managed_accounts_connection_and_arm_fail_closed(self):
        with self.assertRaises(ReadOnlyError): self.ready(api=sdk(accounts=ACCOUNT + ',' + OTHER_ACCOUNT))
        with self.assertRaises(ReadOnlyError): self.arm()

    def test_client_id_zero_rejected(self):
        with self.assertRaises(ReadOnlyError): TWSReadOnlyConfig(client_id=0)

    def test_nonlocal_gateway_rejected(self):
        with self.assertRaises(ReadOnlyError): TWSReadOnlyConfig(host='remote.invalid')

    def test_non_readonly_configuration_rejected(self):
        with self.assertRaises(ReadOnlyError): TWSReadOnlyConfig(read_only=False)

    def test_account_mode_gates_still_reject_unknown_after_arm(self):
        self.ready()
        self.arm()
        account = self.broker.account_summary()
        self.assertIn(BrokerReason.ACCOUNT_MODE_UNVERIFIED, account_gates(
            TradingMode.PAPER, ConnectionStatus.CONNECTED, account, self.clock()))


class LifecycleTests(AuthorizationFixture):
    def test_invalidated_enrollment_not_automatically_restored_when_repaired(self):
        self.ready()
        original = self.path.read_bytes()
        cap = self.arm()
        self.path.write_bytes(b'{')
        self.assert_revoked(cap)
        self.path.write_bytes(original)
        self.assertIs(self.broker.paper_enrollment_status, PaperEnrollmentStatus.MATCHED)
        self.assert_revoked(cap)
        newer = self.arm()
        self.assertTrue(self.broker.validate_paper_execution(newer, Scope.PLACE_ORDER))

    def test_failed_reenrollment_still_revokes_existing_authorization(self):
        self.ready()
        cap = self.arm()
        with patch('tradingbot_broker.paper_enrollment.os.replace', side_effect=OSError(ACCOUNT)):
            with self.assertRaises(ReadOnlyError):
                self.broker.enroll_paper_account(CONFIRMATION, replace_existing=True)
        self.assertIs(self.broker.paper_enrollment_status, PaperEnrollmentStatus.MATCHED)
        self.assert_revoked(cap)

    def test_configuration_change_requires_new_authorization(self):
        self.ready()
        cap = self.arm()
        self.transport.config = replace(self.transport.config, port=4001)
        self.assert_revoked(cap)
        self.assertIs(self.broker.account_mode, AccountMode.UNKNOWN)

    def test_callback_during_issuance_cannot_retain_usable_capability(self):
        self.ready()
        issued, release = Event(), Event()
        original = self.broker._paper_authority._issue
        def issue(*args):
            value = original(*args)
            issued.set()
            self.assertTrue(release.wait(2))
            return value
        with patch.object(self.broker._paper_authority, '_issue', side_effect=issue), \
             ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(self.arm)
            self.assertTrue(issued.wait(2))
            disconnected = executor.submit(self.api.clients[-1].wrapper.connectionClosed)
            release.set()
            cap = future.result(timeout=2)
            disconnected.result(timeout=2)
        self.assert_revoked(cap)

    def test_disarm_waiting_for_issuance_revokes_result(self):
        self.ready()
        issued, release = Event(), Event()
        original = self.broker._paper_authority._issue
        def issue(*args):
            value = original(*args)
            issued.set()
            self.assertTrue(release.wait(2))
            return value
        with patch.object(self.broker._paper_authority, '_issue', side_effect=issue), \
             ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(self.arm)
            self.assertTrue(issued.wait(2))
            disarmed = executor.submit(self.broker.disarm_paper_execution)
            release.set()
            cap = future.result(timeout=2)
            self.assertIs(disarmed.result(timeout=2), Status.DISARMED)
        self.assert_revoked(cap, Status.DISARMED)

    def test_status_checks_do_not_extend_deadline(self):
        self.ready()
        cap = self.arm()
        for _ in range(3):
            self.monotonic.advance(90)
            self.assertIs(self.broker.paper_execution_status, Status.ARMED)
        self.monotonic.advance(31)
        self.assert_revoked(cap, Status.EXPIRED)

    def test_expiration_bounded_by_fresh_observation_and_15_minute_maximum(self):
        self.ready()
        cap = self.arm()
        self.assertGreater(cap.expires_at, cap.issued_at)
        self.assertLessEqual(cap.expires_at, cap.issued_at + timedelta(minutes=15))
        self.assertEqual(MAX_AUTHORIZATION_LIFETIME, timedelta(minutes=15))
        self.assertEqual(cap.expires_at, self.broker.account_summary().valid_until)

    def test_wall_clock_exact_expiry_rejects_without_renewal(self):
        self.ready()
        cap = self.arm()
        self.clock.now = cap.expires_at
        self.assert_revoked(cap, Status.EXPIRED)
        events = [event for event in self.broker.audit if event.action == 'PAPER_EXECUTION_EXPIRED']
        self.assertEqual(len(events), 1)

    def test_monotonic_expiry_with_unchanged_wall_clock(self):
        self.ready()
        cap = self.arm()
        self.monotonic.advance((cap.expires_at - cap.issued_at).total_seconds())
        self.assert_revoked(cap, Status.EXPIRED)

    def test_wall_clock_rollback_does_not_extend_monotonic_deadline(self):
        self.ready()
        cap = self.arm()
        self.clock.now -= timedelta(hours=1)
        self.monotonic.advance(301)
        self.assert_revoked(cap, Status.EXPIRED)

    def test_monotonic_rollback_invalidates(self):
        self.ready()
        cap = self.arm()
        self.monotonic.value = 999
        self.assert_revoked(cap)

    def test_disconnect_revokes_before_backend_cleanup(self):
        self.ready()
        cap = self.arm()
        original = self.transport.disconnect
        def disconnect():
            self.assertIs(self.broker._paper_authority.status, Status.INVALIDATED)
            return original()
        with patch.object(self.transport, 'disconnect', side_effect=disconnect): self.broker.disconnect()
        self.assert_revoked(cap)

    def test_reconnect_requires_new_explicit_arm(self):
        self.ready()
        cap = self.arm()
        self.broker.refresh()
        self.assert_revoked(cap)
        newer = self.arm()
        self.assertGreater(newer.generation, cap.generation)
        self.assertTrue(self.broker.validate_paper_execution(newer, Scope.PLACE_ORDER))
        self.assertFalse(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))

    def test_disconnect_callback_revokes_at_next_backend_boundary(self):
        self.ready()
        cap = self.arm()
        self.api.clients[-1].wrapper.connectionClosed()
        self.assertFalse(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        self.assertIs(self.broker.connection_status, ConnectionStatus.DISCONNECTED)

    def test_direct_transport_disconnect_cannot_leave_valid_capability(self):
        self.ready()
        cap = self.arm()
        self.transport.disconnect()
        self.assert_revoked(cap)

    def test_direct_transport_reconnect_generation_invalidates(self):
        self.ready()
        cap = self.arm()
        self.transport.disconnect()
        self.transport.connect()
        self.assert_revoked(cap)

    def test_emergency_stop_latched_and_revokes(self):
        self.ready()
        cap = self.arm()
        self.broker.emergency_stop(self.clock())
        self.assertTrue(self.broker.controls.emergency_stopped)
        self.assert_revoked(cap)
        self.broker.refresh()
        self.assertTrue(self.broker.controls.emergency_stopped)
        with self.assertRaises(ReadOnlyError): self.arm()

    def test_reconciliation_after_arm_revokes(self):
        self.ready()
        cap = self.arm()
        self.broker._reconciliation_required = True
        self.broker.poll()
        self.assert_revoked(cap)

    def test_authenticated_different_account_record_invalidates(self):
        self.ready()
        cap = self.arm()
        self.enroll(OTHER_ACCOUNT, replace_existing=True)
        self.assertIs(self.broker.paper_enrollment_status, PaperEnrollmentStatus.MISMATCH)
        self.assert_revoked(cap)

    def test_authenticated_same_account_reenrollment_invalidates(self):
        self.ready()
        cap = self.arm()
        self.enroll(replace_existing=True)
        self.assert_revoked(cap)

    def test_facade_reenrollment_revokes_immediately_even_same_account(self):
        self.ready()
        cap = self.arm()
        self.broker.enroll_paper_account(CONFIRMATION, replace_existing=True)
        self.assert_revoked(cap)

    def test_record_tampering_invalidates_not_just_mismatches(self):
        self.ready()
        cap = self.arm()
        record = json.loads(self.path.read_text())
        record['salt'] = '0' * 64
        self.path.write_text(json.dumps(record))
        self.assertIs(self.broker.paper_enrollment_status, PaperEnrollmentStatus.INVALID)
        self.assert_revoked(cap)

    def test_missing_record_invalidates_existing_authorization(self):
        self.ready()
        cap = self.arm()
        self.path.unlink()
        self.assert_revoked(cap)

    def test_disarm_idempotent_revokes(self):
        self.ready()
        cap = self.arm()
        self.assertIs(self.broker.disarm_paper_execution(), Status.DISARMED)
        self.assertIs(self.broker.disarm_paper_execution(), Status.DISARMED)
        self.assert_revoked(cap, Status.DISARMED)
        self.assertEqual(sum(e.action == 'PAPER_EXECUTION_DISARMED' for e in self.broker.audit), 1)

    def test_disarm_always_allowed_with_broken_clock(self):
        self.ready()
        cap = self.arm()
        with patch.object(self.broker, '_clock', side_effect=RuntimeError(ACCOUNT)):
            self.assertIs(self.broker.disarm_paper_execution(), Status.DISARMED)
        self.assertFalse(self.broker._paper_authority._accepts(cap, Scope.PLACE_ORDER))
        self.assertNotIn(ACCOUNT, repr(self.broker.audit))

    def test_expired_capability_not_restored_by_fresh_readonly_refresh(self):
        self.ready()
        cap = self.arm()
        self.monotonic.advance(301)
        self.assert_revoked(cap, Status.EXPIRED)
        self.broker.refresh()
        self.assert_revoked(cap, Status.EXPIRED)


class CapabilityTests(AuthorizationFixture):
    def test_registry_failure_cannot_be_replaced_with_user_confirmation(self):
        self.ready()
        cap = self.arm()
        self.broker.disarm_paper_execution()
        self.assertFalse(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        self.assertFalse(self.broker.validate_paper_execution(ARM_CONFIRMATION, Scope.PLACE_ORDER))

    def test_dataclass_reconstruction_rejected(self):
        self.ready()
        cap = self.arm()
        lookalike = replace(cap)
        self.assertEqual(cap, lookalike)
        self.assertFalse(self.broker.validate_paper_execution(lookalike, Scope.PLACE_ORDER))
        self.assertTrue(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))

    def test_modified_scopes_not_accepted(self):
        self.ready()
        cap = self.arm()
        lookalike = replace(cap, scopes=tuple(sorted(Scope, key=lambda value: value.value)))
        self.assertFalse(self.broker.validate_paper_execution(lookalike, Scope.GLOBAL_CANCEL))

    def test_low_level_mutation_of_issued_object_also_invalidates(self):
        self.ready()
        cap = self.arm()
        object.__setattr__(cap, 'scopes', (Scope.GLOBAL_CANCEL,))
        self.assert_revoked(cap)

    def test_another_broker_rejects_capability(self):
        broker = self.ready()
        cap = self.arm()
        other = self.ready()
        self.arm()
        self.assertFalse(other.validate_paper_execution(cap, Scope.PLACE_ORDER))
        self.assertTrue(broker.validate_paper_execution(cap, Scope.PLACE_ORDER))

    def test_second_explicit_arm_invalidates_first_nonce(self):
        self.ready()
        first, second = self.arm(), self.arm()
        self.assertNotEqual(first.capability_id, second.capability_id)
        self.assertFalse(self.broker.validate_paper_execution(first, Scope.PLACE_ORDER))
        self.assertTrue(self.broker.validate_paper_execution(second, Scope.PLACE_ORDER))

    def test_capability_is_frozen(self):
        self.ready()
        cap = self.arm()
        with self.assertRaises(FrozenInstanceError): cap.generation = 10

    def test_default_scopes_exclude_global_cancel(self):
        self.assertEqual(set(DEFAULT_PAPER_SCOPES), {Scope.PLACE_ORDER, Scope.CANCEL_ORDER})
        self.ready()
        cap = self.arm()
        self.assertFalse(self.broker.validate_paper_execution(cap, Scope.GLOBAL_CANCEL))

    def test_explicit_emergency_scope_does_not_imply_entry(self):
        self.ready()
        cap = self.arm(scopes=(Scope.GLOBAL_CANCEL,))
        self.assertTrue(self.broker.validate_paper_execution(cap, Scope.GLOBAL_CANCEL))
        self.assertFalse(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        self.assertFalse(self.broker.validate_paper_execution(cap, Scope.CANCEL_ORDER))

    def test_scope_order_is_canonical(self):
        self.ready()
        cap = self.arm(scopes=(Scope.PLACE_ORDER, Scope.GLOBAL_CANCEL, Scope.CANCEL_ORDER))
        self.assertEqual(cap.scopes, (Scope.CANCEL_ORDER, Scope.GLOBAL_CANCEL, Scope.PLACE_ORDER))

    def test_scope_must_be_enum_not_matching_string(self):
        self.ready()
        cap = self.arm()
        self.assertFalse(self.broker.validate_paper_execution(cap, 'PLACE_ORDER'))

    def test_forged_noncapability_is_rejected(self):
        self.ready()
        self.arm()
        for value in (None, True, object(), {}, 'ARMED'):
            self.assertFalse(self.broker.validate_paper_execution(value, Scope.PLACE_ORDER))

    def test_public_capability_and_audit_contain_no_account_enrollment_material(self):
        self.ready()
        record = json.loads(self.path.read_text())
        cap = self.arm()
        self.broker.disarm_paper_execution()
        for value in (ACCOUNT, record['salt'], record['fingerprint'], record['authentication'],
                      self.store._read_key().hex()):
            self.assertNotIn(value, repr(cap))
            self.assertNotIn(value, repr(self.broker.audit))
        self.assertNotIn(cap.capability_id, repr(cap))
        self.assertNotIn(cap.capability_id, repr(self.broker.audit))

    def test_fixed_audit_events_are_sanitized_and_deterministic_except_omitted_nonce(self):
        self.ready()
        cap = self.arm()
        self.broker.disarm_paper_execution()
        events = [event for event in self.broker.audit if event.action.startswith('PAPER_EXECUTION_')]
        self.assertEqual([event.action for event in events], ['PAPER_EXECUTION_ARMED','PAPER_EXECUTION_DISARMED'])
        self.assertEqual(dict(events[0].details)['scopes'], 'CANCEL_ORDER,PLACE_ORDER')
        self.assertEqual(dict(events[0].details)['generation'], cap.generation)
        self.assertEqual(dict(events[1].details)['reason_code'], 'EXPLICIT_USER_DISARM')

    def test_arming_and_checks_do_not_persist_authorization(self):
        self.ready()
        files = {p.name: hashlib.sha256(p.read_bytes()).digest() for p in self.path.parent.iterdir()}
        cap = self.arm()
        self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER)
        self.broker.disarm_paper_execution()
        self.assertEqual(files, {p.name: hashlib.sha256(p.read_bytes()).digest() for p in self.path.parent.iterdir()})

    def test_process_controller_restart_starts_disarmed(self):
        self.ready()
        cap = self.arm()
        self.broker.disconnect()
        new = self.ready()
        self.assertIs(new.paper_execution_status, Status.DISARMED)
        self.assertFalse(new.validate_paper_execution(cap, Scope.PLACE_ORDER))


def invalid_runtime_config_test(field, value):
    def test(self):
        self.ready()
        cap = self.arm()
        object.__setattr__(self.transport.config, field, value)
        self.assert_revoked(cap)
        with self.assertRaises(ReadOnlyError): self.arm()
    return test


for label, field, value in (('client_zero','client_id',0), ('remote','host','remote.invalid'),
                            ('readonly_false','read_only',False)):
    setattr(LifecycleTests, 'test_unsafe_runtime_config_' + label, invalid_runtime_config_test(field,value))


def invalid_capability_test(field, value):
    def test(self):
        self.ready()
        cap = self.arm()
        with self.assertRaises((ReadOnlyError, TypeError, ValueError)): replace(cap, **{field:value})
    return test


for label, field, value in (('bool_generation','generation',True),('zero_generation','generation',0),
    ('short_id','capability_id','ab'),('non_hex_id','capability_id','z'*64),('source','source','PAPER_ATTESTED')):
    setattr(CapabilityTests, 'test_malformed_capability_' + label, invalid_capability_test(field,value))

class WriteBarrierTests(AuthorizationFixture):
    def test_future_write_ids_are_contract_only_and_exact(self):
        self.assertEqual(FUTURE_PAPER_WRITE_IDS, (
            (Scope.PLACE_ORDER, 3, 203), (Scope.CANCEL_ORDER, 4, 204), (Scope.GLOBAL_CANCEL, 58, 258)))

    def test_read_allowlists_unchanged_when_all_scopes_armed(self):
        self.ready()
        self.arm(scopes=tuple(Scope))
        legacy = set(vars(self.transport._api.OUT).values())
        protobuf = set(vars(self.transport._api.PROTOBUF_OUT).values())
        self.assertEqual(legacy, {7,16,17,49,61,62,63,64,71,99})
        self.assertEqual(protobuf, {207,216,217,249,261,262,263,264,271,299})
        self.assertTrue(legacy.isdisjoint({3,4,58}))
        self.assertTrue(protobuf.isdisjoint({203,204,258}))

    def test_facade_place_cancel_replace_flatten_and_enable_remain_blocked(self):
        self.ready()
        cap = self.arm(scopes=tuple(Scope))
        before = tuple(self.api.calls)
        order = request()
        for operation in (
            lambda: self.broker.place_order(order, self.clock()),
            lambda: self.broker.replace_order('observed-1', order, self.clock()),
            lambda: self.broker.modify_order('observed-1', order, self.clock()),
            lambda: self.broker.cancel_order('observed-1', self.clock()),
            lambda: self.broker.cancel_working_orders(self.clock()),
            lambda: self.broker.flatten_positions(self.clock()),
            lambda: self.broker.set_trading_enabled(True, self.clock()),
            lambda: self.broker.place_order(request(mode=TradingMode.LIVE), self.clock()),
        ):
            with self.assertRaises(BrokerOperationError) as raised: operation()
            self.assertIs(raised.exception.reason, BrokerReason.READ_ONLY_BROKER_TRANSPORT)
        self.assertEqual(tuple(self.api.calls), before)
        self.assertTrue(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))

    def test_transport_public_surface_has_no_order_capability_even_when_armed(self):
        self.ready()
        self.arm(scopes=tuple(Scope))
        for name in ('place_order','replace_order','modify_order','cancel_order','flatten_positions'):
            with self.assertRaises(ReadOnlyError): getattr(self.transport, name)()
        for name in ('placeOrder','cancelOrder','reqGlobalCancel','sendMsg','sendMsgProtoBuf','conn','socket'):
            self.assertFalse(hasattr(self.transport._client, name))

    def test_all_named_sdk_write_methods_remain_blocked(self):
        self.ready()
        self.arm(scopes=tuple(Scope))
        client = self.api.clients[-1]  # Fake SDK test inspection only.
        before = list(self.api.calls)
        for name in ('placeOrder','cancelOrder','reqGlobalCancel'):
            with self.assertRaises(ReadOnlyError): getattr(client, name)()
        self.assertEqual(self.api.calls, before)


def wrong_phrase_test(phrase):
    def test(self):
        self.ready()
        with self.assertRaisesRegex(ReadOnlyError, 'PAPER_EXECUTION_CONFIRMATION_REQUIRED'):
            self.broker.arm_paper_execution(phrase)
        self.assertIsNot(self.broker.paper_execution_status, Status.ARMED)
        self.assertNotIn(ACCOUNT, repr(self.broker.audit))
    return test


for label, phrase in (('enrollment_phrase', CONFIRMATION), ('lowercase','i arm ibkr paper trading'),
                     ('trailing_space',ARM_CONFIRMATION + ' '), ('none',None), ('bool',True),
                     ('raw_identity',ACCOUNT)):
    setattr(ArmingTests, 'test_wrong_phrase_' + label, wrong_phrase_test(phrase))


def invalid_scopes_test(scopes):
    def test(self):
        self.ready()
        with self.assertRaises(ReadOnlyError): self.arm(scopes=scopes)
        self.assertIsNot(self.broker.paper_execution_status, Status.ARMED)
    return test


for label, scopes in (('empty',()), ('string',('PLACE_ORDER',)), ('list',[Scope.PLACE_ORDER]),
                      ('duplicate',(Scope.PLACE_ORDER, Scope.PLACE_ORDER)), ('none',None)):
    setattr(CapabilityTests, 'test_invalid_scopes_' + label, invalid_scopes_test(scopes))


def write_id_test(opcode):
    def test(self):
        self.ready()
        self.arm(scopes=tuple(Scope))
        client = self.api.clients[-1]
        before = tuple(call for call in self.api.calls if call[0] == 'socket-send')
        if opcode >= 200:
            for send in (client.sendMsgProtoBuf, lambda code, data: self.api.Client.sendMsgProtoBuf(client,code,data)):
                with self.assertRaises(ReadOnlyError): send(opcode, b'')
        else:
            with self.assertRaises(ReadOnlyError): client.sendMsg(opcode, '1\0')
        for send in (client.conn.sendMsg, client.conn.socket.send, client.conn.socket.sendall):
            with self.assertRaises(ReadOnlyError): send(proto_frame(opcode))
        self.assertEqual(tuple(call for call in self.api.calls if call[0] == 'socket-send'), before)
    return test


for opcode in (3,4,58,203,204,258):
    setattr(WriteBarrierTests, 'test_write_id_' + str(opcode) + '_blocked_at_all_layers', write_id_test(opcode))


def invalid_clock_test(value):
    def test(self):
        self.ready()
        cap = self.arm()
        self.monotonic.value = value
        self.assert_revoked(cap)
    return test


for label, value in (('bool',True), ('nan',float('nan')), ('infinity',float('inf')), ('negative',-1), ('text','1000')):
    setattr(LifecycleTests, 'test_malformed_monotonic_' + label, invalid_clock_test(value))


class EqualitySpoof:
    def __eq__(self, other):
        raise AssertionError('Untrusted equality must never run')


class StrictCapabilityCorrectionTests(AuthorizationFixture):
    def test_monkey_patched_enum_equality_cannot_authorize_extra_scope(self):
        self.ready()
        cap = self.arm(scopes=(Scope.PLACE_ORDER,))
        with patch.object(Scope, '__eq__', return_value=True):
            self.assertTrue(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
            self.assertFalse(self.broker.validate_paper_execution(cap, Scope.CANCEL_ORDER))
            self.assertFalse(self.broker.validate_paper_execution(cap, Scope.GLOBAL_CANCEL))

    def test_datetime_subclass_mutation_rejected_even_when_value_is_equal(self):
        from datetime import datetime
        class EqualDatetime(datetime): pass
        self.ready()
        cap = self.arm()
        object.__setattr__(cap, 'issued_at', EqualDatetime.fromisoformat(cap.issued_at.isoformat()))
        self.assertFalse(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        self.assertIs(self.broker.paper_execution_status, Status.INVALIDATED)

    def test_caller_mutable_timezone_is_not_allowed_in_capability(self):
        from datetime import tzinfo
        class MutableTimezone(tzinfo):
            def utcoffset(self, value): return timedelta(0)
            def dst(self, value): return timedelta(0)
            def tzname(self, value): return 'UTC'
        self.ready()
        cap = self.arm()
        with self.assertRaises(ReadOnlyError):
            replace(cap, issued_at=cap.issued_at.replace(tzinfo=MutableTimezone()))

    def test_untouched_place_only_capability_uses_exact_controller_scope(self):
        self.ready()
        cap = self.arm(scopes=(Scope.PLACE_ORDER,))
        self.assertTrue(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        self.assertFalse(self.broker.validate_paper_execution(cap, Scope.CANCEL_ORDER))
        self.assertFalse(self.broker.validate_paper_execution(cap, Scope.GLOBAL_CANCEL))

    def test_always_equal_scope_cannot_gain_cancel_or_global_cancel(self):
        class AlwaysEqual:
            def __eq__(self, other): return True
        self.ready()
        cap = self.arm(scopes=(Scope.PLACE_ORDER,))
        snapshot = self.broker._paper_authority._issued_values
        object.__setattr__(cap, 'scopes', (AlwaysEqual(),))
        self.assertFalse(self.broker.validate_paper_execution(cap, Scope.CANCEL_ORDER))
        self.assertFalse(self.broker.validate_paper_execution(cap, Scope.GLOBAL_CANCEL))
        self.assertIs(self.broker.paper_execution_status, Status.INVALIDATED)
        # Kept test reference is independent, primitive and unchanged by mutation.
        self.assertEqual(snapshot[3], (0,))

    def test_mutation_does_not_change_independent_registry_snapshot(self):
        self.ready()
        cap = self.arm(scopes=(Scope.PLACE_ORDER,))
        snapshot = self.broker._paper_authority._issued_values
        object.__setattr__(cap, 'scopes', (Scope.GLOBAL_CANCEL,))
        self.assertIs(self.broker._paper_authority._issued_values, snapshot)
        self.assertEqual(snapshot[3], (0,))
        self.assertFalse(self.broker.validate_paper_execution(cap, Scope.GLOBAL_CANCEL))

    def test_reconstructed_exact_snapshot_is_not_issued_identity(self):
        self.ready()
        cap = self.arm(scopes=(Scope.PLACE_ORDER,))
        self.assertFalse(self.broker.validate_paper_execution(replace(cap), Scope.PLACE_ORDER))
        self.assertTrue(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))

    def test_spoofed_requested_scope_is_not_typed_scope(self):
        self.ready()
        cap = self.arm(scopes=(Scope.PLACE_ORDER,))
        self.assertFalse(self.broker.validate_paper_execution(cap, EqualitySpoof()))
        self.assertTrue(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))


def mutated_field_test(field, change):
    def test(self):
        self.ready()
        cap = self.arm(scopes=(Scope.PLACE_ORDER,))
        object.__setattr__(cap, field, change(getattr(cap, field)))
        for scope in Scope:
            self.assertFalse(self.broker.validate_paper_execution(cap, scope))
        self.assertIs(self.broker.paper_execution_status, Status.INVALIDATED)
    return test


class EqualString(str): pass
class EqualInteger(int): pass
class EqualTuple(tuple): pass


for label, field, change in (
    ('scope_object', 'scopes', lambda old: (EqualitySpoof(),)),
    ('scope_strings', 'scopes', lambda old: tuple(scope.value for scope in old)),
    ('scope_integer', 'scopes', lambda old: (0,)),
    ('scope_list', 'scopes', lambda old: list(old)),
    ('scope_tuple_subclass', 'scopes', lambda old: EqualTuple(old)),
    ('cancel_scope', 'scopes', lambda old: (Scope.CANCEL_ORDER,)),
    ('global_scope', 'scopes', lambda old: (Scope.GLOBAL_CANCEL,)),
    ('generation_bool', 'generation', lambda old: True),
    ('generation_subclass', 'generation', EqualInteger),
    ('generation_changed', 'generation', lambda old: old + 1),
    ('generation_spoof', 'generation', lambda old: EqualitySpoof()),
    ('issued_spoof', 'issued_at', lambda old: EqualitySpoof()),
    ('expires_spoof', 'expires_at', lambda old: EqualitySpoof()),
    ('issued_changed', 'issued_at', lambda old: old + timedelta(microseconds=1)),
    ('expires_changed', 'expires_at', lambda old: old + timedelta(microseconds=1)),
    ('id_subclass', 'capability_id', EqualString),
    ('id_changed', 'capability_id', lambda old: ('0' if old[0] != '0' else '1') + old[1:]),
    ('source_subclass', 'source', EqualString),
    ('source_changed', 'source', lambda old: 'ALTERED_SOURCE'),
):
    setattr(StrictCapabilityCorrectionTests, 'test_mutated_' + label,
            mutated_field_test(field, change))


class FinalEvidenceCorrectionTests(AuthorizationFixture):
    def concurrent_revocation(self, revoke, expected):
        self.ready()
        cap = self.arm()
        reading, release, revoking = Event(), Event(), Event()
        original = self.store._read_record
        calls = 0
        def read():
            nonlocal calls
            calls += 1
            record = original()
            if calls == 2:
                reading.set()
                if not release.wait(5): raise AssertionError('Evidence test release missing')
            return record
        def action():
            revoking.set()
            return revoke()
        with patch.object(self.store, '_read_record', side_effect=read), \
             ThreadPoolExecutor(max_workers=2) as executor:
            validating = executor.submit(self.broker.validate_paper_execution, cap, Scope.PLACE_ORDER)
            try:
                self.assertTrue(reading.wait(5))
                revocation = executor.submit(action)
                self.assertTrue(revoking.wait(5))
                self.assertFalse(revocation.done())
            finally:
                release.set()
            self.assertTrue(validating.result(timeout=5))
            revocation.result(timeout=5)
        self.assert_revoked(cap, expected)

    def test_validation_disconnect_serialized_until_revocation_effective(self):
        self.concurrent_revocation(lambda: self.broker.disconnect(), Status.INVALIDATED)

    def test_validation_disarm_serialized_until_revocation_effective(self):
        self.concurrent_revocation(lambda: self.broker.disarm_paper_execution(), Status.DISARMED)

    def test_validation_emergency_stop_serialized_until_revocation_effective(self):
        def stop():
            # Existing controls require a current timestamp at their effective boundary.
            with self.broker._lock:
                self.broker.emergency_stop(self.clock())
        self.concurrent_revocation(stop, Status.INVALIDATED)

    def evidence_delay(self, *, wall=0, elapsed=0, call=1, after_read=None):
        original = self.store._read_record
        count = 0
        def read():
            nonlocal count
            count += 1
            record = original()
            if count == call:
                self.clock.advance(wall)
                self.monotonic.advance(elapsed)
                if after_read is not None: after_read()
            return record
        return patch.object(self.store, '_read_record', side_effect=read)

    def assert_no_issuance(self):
        self.assertIsNot(self.broker.paper_execution_status, Status.ARMED)
        self.assertIsNone(self.broker._paper_authority._issued)
        self.assertIsNone(self.broker._paper_authority._issued_values)
        self.assertIsNone(self.broker._paper_authority._deadline)
        self.assertFalse(any(event.action == 'PAPER_EXECUTION_ARMED' for event in self.broker.audit))

    def test_wall_expiry_during_final_validation_collection(self):
        self.ready()
        cap = self.arm()
        self.assertTrue(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        with self.evidence_delay(wall=301, call=2):
            self.assertFalse(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        self.assert_revoked(cap, Status.EXPIRED)

    def test_monotonic_expiry_during_final_validation_collection(self):
        self.ready()
        cap = self.arm()
        with self.evidence_delay(elapsed=301, call=2):
            self.assertFalse(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        self.assert_revoked(cap, Status.EXPIRED)

    def test_account_expires_during_final_validation_before_capability_deadline(self):
        self.ready()
        cap = self.arm()
        account = self.broker.account_summary()
        now = self.clock()
        self.broker.report_account(replace(account, observed_at=now, available_at=now,
                                          valid_until=now + timedelta(seconds=1)), now)
        with self.evidence_delay(wall=2, call=2):
            self.assertFalse(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        self.assertLess(self.clock(), cap.expires_at)
        self.assert_revoked(cap)

    def test_wall_expiry_during_arming_leaves_no_capability_or_success_audit(self):
        self.ready()
        with self.evidence_delay(wall=301):
            with self.assertRaises(ReadOnlyError): self.arm()
        self.assert_no_issuance()

    def test_monotonic_expiry_during_arming_leaves_no_capability_or_success_audit(self):
        self.ready()
        with self.evidence_delay(elapsed=301):
            with self.assertRaisesRegex(ReadOnlyError, 'PAPER_EXECUTION_EXPIRED'): self.arm()
        self.assert_no_issuance()

    def test_account_expiry_during_arming_with_shorter_observation(self):
        self.ready(ttl=15)
        with self.evidence_delay(wall=16):
            with self.assertRaises(ReadOnlyError): self.arm()
        self.assert_no_issuance()

    def test_elapsed_collection_time_is_not_added_back_to_deadline(self):
        self.ready()
        initial_monotonic = self.monotonic()
        with self.evidence_delay(elapsed=100): cap = self.arm()
        self.assertTrue(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        self.assertLessEqual(self.broker._paper_authority._deadline, initial_monotonic + 300)
        self.monotonic.advance(201)
        self.assert_revoked(cap, Status.EXPIRED)

    def test_issued_at_is_sampled_after_successful_evidence_collection(self):
        self.ready()
        with self.evidence_delay(wall=10, elapsed=10): cap = self.arm()
        self.assertLess(self.clock.now - cap.issued_at, timedelta(seconds=1))
        self.assertLess(cap.issued_at, cap.expires_at)
        self.assertTrue(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        success = [event for event in self.broker.audit if event.action == 'PAPER_EXECUTION_ARMED']
        self.assertEqual(success[0].timestamp, cap.issued_at)

    def test_failed_rearm_clears_previous_registry_without_second_success_audit(self):
        self.ready()
        cap = self.arm()
        # poll performs the first read for the old authorization, arm the second.
        with self.evidence_delay(elapsed=301, call=2):
            with self.assertRaises(ReadOnlyError): self.arm()
        self.assertFalse(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        self.assertIsNone(self.broker._paper_authority._issued_values)
        self.assertEqual(sum(event.action == 'PAPER_EXECUTION_ARMED' for event in self.broker.audit), 1)

    def test_refreshed_financial_evidence_does_not_renew_existing_deadline(self):
        self.ready()
        cap = self.arm()
        deadline = self.broker._paper_authority._deadline
        account = self.broker.account_summary()
        now = self.clock()
        self.broker.report_account(replace(account, observed_at=now, available_at=now,
            valid_until=now + timedelta(seconds=300)), now)
        self.assertEqual(self.broker._paper_authority._deadline, deadline)
        with self.evidence_delay(elapsed=301, call=2):
            self.assertFalse(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))


def invalid_final_state_test(change):
    def test(self):
        self.ready()
        cap = self.arm()
        with self.evidence_delay(call=2, after_read=lambda: change(self)):
            self.assertFalse(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        self.assert_revoked(cap)
    return test


for label, change in (
    ('generation', lambda self: setattr(self.transport, '_generation', self.transport._generation + 1)),
    ('disconnect', lambda self: self.api.clients[-1].wrapper.connectionClosed()),
    ('emergency_stop', lambda self: self.broker.emergency_stop(self.clock())),
    ('disarm', lambda self: self.broker.disarm_paper_execution()),
    ('reconciliation', lambda self: setattr(self.broker, '_reconciliation_required', True)),
):
    # Reentrant test injection simulates an effective change at the last evidence boundary.
    test = invalid_final_state_test(change)
    if label == 'disarm':
        # Disarming preserves DISARMED rather than changing it to INVALIDATED.
        def test(self):
            self.ready()
            cap = self.arm()
            with self.evidence_delay(call=2, after_read=self.broker.disarm_paper_execution):
                self.assertFalse(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
            self.assert_revoked(cap, Status.DISARMED)
    setattr(FinalEvidenceCorrectionTests, 'test_final_effective_' + label + '_rejects', test)


def invalid_interval_test(delta):
    def test(self):
        self.ready()
        cap = self.arm()
        with self.assertRaises(ReadOnlyError):
            replace(cap, expires_at=cap.issued_at + delta)
    return test


for label, delta in (('equal', timedelta(0)), ('earlier', timedelta(microseconds=-1))):
    setattr(FinalEvidenceCorrectionTests, 'test_issued_expiry_' + label + '_rejected', invalid_interval_test(delta))


class SDKConnectionCorrectionTests(AuthorizationFixture):
    def collection_change(self, change, *, call=1):
        return FinalEvidenceCorrectionTests.evidence_delay(self, call=call, after_read=change)

    def assert_failed_arm(self):
        authority = self.broker._paper_authority
        self.assertIsNot(authority.status, Status.ARMED)
        self.assertIsNone(authority._issued)
        self.assertIsNone(authority._issued_values)
        self.assertIsNone(authority._deadline)
        self.assertFalse(any(event.action == 'PAPER_EXECUTION_ARMED' for event in self.broker.audit))

    def test_connected_sdk_arm_and_validation_succeed_without_account_attestation(self):
        self.ready()
        cap = self.arm()
        self.assertTrue(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        self.assertIs(self.broker.account_mode, AccountMode.UNKNOWN)
        self.assertIn(BrokerReason.ACCOUNT_MODE_UNVERIFIED, account_gates(TradingMode.PAPER,
            ConnectionStatus.CONNECTED, self.broker.account_summary(), self.clock()))

    def test_disconnected_during_collection_arm_rejects_before_callback(self):
        self.ready()
        client = self.api.clients[-1]
        def disconnect():
            client.connected = False
            self.assertIs(self.broker._connection, ConnectionStatus.CONNECTED)
            self.assertFalse(self.transport._disconnect_emitted)
            self.assertIsNone(self.transport._failure)
        with self.collection_change(disconnect):
            with self.assertRaisesRegex(ReadOnlyError, 'PAPER_EXECUTION_SDK_DISCONNECTED'): self.arm()
        self.assert_failed_arm()

    def test_disconnected_during_final_validation_rejects_before_callback(self):
        self.ready()
        cap = self.arm()
        def disconnect():
            self.api.clients[-1].connected = False
            self.assertIs(self.broker._connection, ConnectionStatus.CONNECTED)
            self.assertFalse(self.transport._disconnect_emitted)
        with self.collection_change(disconnect, call=2):
            self.assertFalse(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        self.assertIs(self.broker._paper_authority.status, Status.INVALIDATED)
        self.assertIsNone(self.broker._paper_authority._issued)

    def test_sdk_exception_during_arm_is_unavailable_and_sanitized(self):
        self.ready()
        client = self.api.clients[-1]
        def fail(): raise RuntimeError(ACCOUNT + '|private-sdk-token')
        with self.collection_change(lambda: setattr(client, 'isConnected', fail)):
            with self.assertRaisesRegex(ReadOnlyError, 'PAPER_EXECUTION_SDK_UNAVAILABLE') as raised:
                self.arm()
        self.assert_failed_arm()
        self.assertNotIn(ACCOUNT, str(raised.exception))
        self.assertNotIn('private-sdk-token', repr(self.broker.audit))

    def test_sdk_exception_during_validation_is_unavailable_and_sanitized(self):
        self.ready()
        cap = self.arm()
        client = self.api.clients[-1]
        def fail(): raise RuntimeError(ACCOUNT + '|private-sdk-token')
        with self.collection_change(lambda: setattr(client, 'isConnected', fail), call=2):
            self.assertFalse(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        self.assertIs(self.broker._paper_authority.status, Status.INVALIDATED)
        self.assertNotIn(ACCOUNT, repr(self.broker.audit))
        self.assertNotIn('private-sdk-token', repr(self.broker.audit))

    def remove_client(self):
        # Retain only in the test cleanup so an intentionally orphaned fake is closed.
        self.addCleanup(self.transport._client.disconnect)
        self.transport._client = None

    def test_missing_sdk_client_during_arm_rejects(self):
        self.ready()
        with self.collection_change(self.remove_client):
            with self.assertRaisesRegex(ReadOnlyError, 'PAPER_EXECUTION_SDK_UNAVAILABLE'): self.arm()
        self.assert_failed_arm()

    def test_missing_sdk_client_during_validation_rejects(self):
        self.ready()
        cap = self.arm()
        with self.collection_change(self.remove_client, call=2):
            self.assertFalse(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        self.assertIs(self.broker._paper_authority.status, Status.INVALIDATED)

    def test_evidence_is_strict_boolean_and_generation_bound(self):
        self.ready()
        generation = self.transport._generation
        self.assertIs(self.transport._current_sdk_connection_evidence(generation), True)
        self.assertIsNone(self.transport._current_sdk_connection_evidence(generation - 1))
        self.api.clients[-1].connected = False
        self.assertIs(self.transport._current_sdk_connection_evidence(generation), False)

    def test_generation_change_during_sdk_inspection_cannot_publish_connected_evidence(self):
        self.ready()
        generation = self.transport._generation
        def changed():
            self.transport._generation += 1
            return True
        with patch.object(self.api.clients[-1], 'isConnected', side_effect=changed):
            self.assertIsNone(self.transport._current_sdk_connection_evidence(generation))

    def test_client_change_during_sdk_inspection_cannot_publish_connected_evidence(self):
        self.ready()
        def changed():
            self.remove_client()
            return True
        with patch.object(self.api.clients[-1], 'isConnected', side_effect=changed):
            self.assertIsNone(self.transport._current_sdk_connection_evidence(self.transport._generation))

    def test_final_generation_change_during_arm_rejects(self):
        self.ready()
        client = self.api.clients[-1]
        def changed():
            self.transport._generation += 1
            return True
        with self.collection_change(lambda: setattr(client, 'isConnected', changed)):
            with self.assertRaises(ReadOnlyError): self.arm()
        self.assert_failed_arm()

    def test_final_generation_change_during_validation_rejects(self):
        self.ready()
        cap = self.arm()
        def changed():
            self.transport._generation += 1
            return True
        with self.collection_change(lambda: setattr(self.api.clients[-1], 'isConnected', changed), call=2):
            self.assertFalse(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        self.assertIs(self.broker._paper_authority.status, Status.INVALIDATED)

    def test_inspection_is_local_without_requests_filesystem_or_worker_wait(self):
        self.ready()
        before = tuple(self.api.calls)
        with patch.object(self.store, '_read_record', side_effect=AssertionError('Unexpected file read')), \
             patch.object(self.transport._thread, 'join', side_effect=AssertionError('Unexpected worker wait')), \
             patch.object(self.transport, '_clock', side_effect=AssertionError('Unexpected clock read')):
            self.assertIs(self.transport._current_sdk_connection_evidence(self.transport._generation), True)
        self.assertEqual(tuple(self.api.calls), before)

    def test_closing_transport_cannot_return_connected_evidence(self):
        self.ready()
        generation = self.transport._generation
        self.transport.disconnect()
        self.assertIsNone(self.transport._current_sdk_connection_evidence(generation))

    def test_callback_flag_overrides_sdk_connected_value(self):
        self.ready()
        self.api.clients[-1].wrapper.connectionClosed()
        self.assertTrue(self.api.clients[-1].connected)
        self.assertIs(self.transport._current_sdk_connection_evidence(self.transport._generation), False)

    def test_sdk_inspection_precedes_final_clock_samples_during_arm(self):
        self.ready()
        observed = []
        client = self.api.clients[-1]
        def current():
            self.clock.advance(1)
            self.monotonic.advance(1)
            observed.append(self.clock.now)
            return True
        with self.collection_change(lambda: setattr(client, 'isConnected', current)):
            cap = self.arm()
        self.assertGreaterEqual(cap.issued_at, observed[0])
        self.assertTrue(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))

    def test_expiry_during_sdk_inspection_cannot_authorize_validation(self):
        self.ready()
        cap = self.arm()
        def current():
            self.monotonic.advance(301)
            return True
        with self.collection_change(lambda: setattr(self.api.clients[-1], 'isConnected', current), call=2):
            self.assertFalse(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        self.assertIs(self.broker._paper_authority.status, Status.EXPIRED)

    def test_expiry_during_sdk_inspection_cannot_mint_authorization(self):
        self.ready()
        def current():
            self.clock.advance(301)
            return True
        with self.collection_change(lambda: setattr(self.api.clients[-1], 'isConnected', current)):
            with self.assertRaises(ReadOnlyError): self.arm()
        self.assert_failed_arm()

    def callback_race(self, *, arming):
        self.ready()
        old_cap = None if arming else self.arm()
        original = self.store._read_record
        reading, release, callback_started = Event(), Event(), Event()
        calls = 0
        client = self.api.clients[-1]
        def read():
            nonlocal calls
            calls += 1
            record = original()
            if calls == (1 if arming else 2):
                reading.set()
                if not release.wait(5): raise AssertionError('Evidence release missing')
            return record
        def callback():
            callback_started.set()
            client.wrapper.connectionClosed()
        with patch.object(self.store, '_read_record', side_effect=read), \
             ThreadPoolExecutor(max_workers=2) as executor:
            operation = executor.submit(self.arm if arming else
                lambda: self.broker.validate_paper_execution(old_cap, Scope.PLACE_ORDER))
            try:
                self.assertTrue(reading.wait(5))
                client.connected = False
                closed = executor.submit(callback)
                self.assertTrue(callback_started.wait(5))
            finally:
                release.set()
            if arming:
                with self.assertRaises(ReadOnlyError): operation.result(timeout=5)
                self.assert_failed_arm()
            else:
                self.assertFalse(operation.result(timeout=5))
            closed.result(timeout=5)
            self.assertIs(self.broker.connection_status, ConnectionStatus.DISCONNECTED)
            executor.submit(self.broker.disconnect).result(timeout=5)
        # Callback/cleanup locks were released; new read-only connection is usable.
        self.broker.connect()
        self.assertIsNot(self.broker.paper_execution_status, Status.ARMED)
        newer = self.arm()
        self.assertTrue(self.broker.validate_paper_execution(newer, Scope.PLACE_ORDER))
        if old_cap is not None:
            self.assertFalse(self.broker.validate_paper_execution(old_cap, Scope.PLACE_ORDER))

    def test_sdk_disconnect_and_callback_racing_with_arm_remain_responsive(self):
        self.callback_race(arming=True)

    def test_sdk_disconnect_and_callback_racing_with_validation_remain_responsive(self):
        self.callback_race(arming=False)


def unexpected_sdk_state_test(value, *, arming):
    def test(self):
        self.ready()
        cap = None if arming else self.arm()
        change = lambda: setattr(self.api.clients[-1], 'isConnected', lambda: value)
        with self.collection_change(change, call=1 if arming else 2):
            if arming:
                with self.assertRaisesRegex(ReadOnlyError, 'PAPER_EXECUTION_SDK_UNAVAILABLE'): self.arm()
            else:
                self.assertFalse(self.broker.validate_paper_execution(cap, Scope.PLACE_ORDER))
        if arming: self.assert_failed_arm()
        else: self.assertIs(self.broker._paper_authority.status, Status.INVALIDATED)
    return test


for label, value in (('integer_true', 1), ('integer_false', 0), ('text', 'CONNECTED'),
                      ('float', 1.0), ('missing', None), ('object', EqualitySpoof())):
    for arming in (True, False):
        setattr(SDKConnectionCorrectionTests,
                'test_unexpected_sdk_state_' + label + ('_arm' if arming else '_validation'),
                unexpected_sdk_state_test(value, arming=arming))


if __name__ == '__main__':
    unittest.main()
