"""Exact private issuer provenance using authenticated anonymous fake accounts."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, FrozenInstanceError, replace
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from tradingbot_broker import ibkr_readonly as r
from tradingbot_broker import paper_execution_journal as j
from tradingbot_broker.broker import BrokerOperationError
from tradingbot_broker.models import TradingMode
from tradingbot_broker.paper_execution import PaperExecutionAuthorizationStatus as Status
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests.readonly_fixtures import sdk
from tests.test_ibkr_paper_enrollment import EnrollmentFixture, ACCOUNT, OTHER_ACCOUNT


class BindingTests(EnrollmentFixture):
    def ready(self, **options):
        self.enroll()
        self.broker, self.transport, self.api, self.clock = self.connected(sdk(**options))
        return self.transport._issue_journal_account_binding()

    def invalid(self, receipt):
        with self.assertRaisesRegex(ReadOnlyError, '^INVALID_JOURNAL_ACCOUNT_BINDING$') as raised:
            self.transport._validate_journal_account_binding(receipt)
        self.assertNotIn(ACCOUNT, str(raised.exception))
        self.assertIsNone(self.transport._journal_account_association)

    def test_healthy_authenticated_association_and_exact_receipt(self):
        receipt = self.ready()
        binding = self.transport._validate_journal_account_binding(receipt)
        self.assertEqual(binding, receipt.binding)
        self.assertIsNot(binding, receipt.binding)
        self.assertRegex(binding.opaque_id, '^binding-[0-9a-f]{32}$')
        self.assertEqual(receipt.read_generation, self.transport._generation)
        self.assertEqual(receipt.version, 1)

    def test_issuance_without_enrollment_fails_and_creates_nothing(self):
        _, self.transport, _, _ = self.connected()
        with self.assertRaises(ReadOnlyError): self.transport._issue_journal_account_binding()
        self.assertIsNone(self.transport._journal_account_association)
        self.assertFalse(self.path.exists())

    def test_issuance_without_connection_fails(self):
        self.enroll()
        with patch.object(r, '_load_official_api', return_value=sdk()):
            self.transport = r.ReadOnlyTWSTransport(r.TWSReadOnlyConfig(), enrollment_store=self.store)
        with self.assertRaises(ReadOnlyError): self.transport._issue_journal_account_binding()
        self.assertIsNone(self.transport._journal_account_association)

    def test_malformed_receipt_rejected_before_attribute_inspection(self):
        self.ready()
        class UnsafeReceipt:
            def __getattribute__(self, name): raise AssertionError('not an issued receipt')
        self.invalid(UnsafeReceipt())

    def test_binding_primitive_tampering_invalidates(self):
        receipt = self.ready()
        object.__setattr__(receipt.binding, 'opaque_id', 'binding-'+'0'*32)
        self.invalid(receipt)

    def test_bounded_single_lifetime_namespace_without_switching(self):
        receipt = self.ready()
        with self.assertRaises(ReadOnlyError): self.transport._issue_journal_account_binding()
        self.assertIsNone(self.transport._journal_account_association)
        self.invalid(receipt)
        with self.assertRaises(ReadOnlyError): self.transport._issue_journal_account_binding()

    def test_namespace_uses_independent_crypto_randomness(self):
        self.enroll()
        _, self.transport, _, _ = self.connected()
        with patch.object(r.secrets, 'token_hex', return_value='c'*32) as random:
            receipt = self.transport._issue_journal_account_binding()
        random.assert_called_once_with(16)
        self.assertEqual(receipt.binding.opaque_id, 'binding-'+'c'*32)

    def test_arbitrary_binding_is_not_a_receipt(self):
        self.ready()
        self.invalid(j._JournalAccountBinding('binding-'+'a'*32))

    def test_equal_reconstructed_receipt_is_not_issued(self):
        receipt = self.ready()
        reconstruction = replace(receipt)
        self.assertEqual(reconstruction, receipt)
        self.assertIsNot(reconstruction, receipt)
        self.invalid(reconstruction)

    def test_foreign_issuer_receipt(self):
        receipt = self.ready()
        _, other, _, _ = self.connected()
        self.transport, original = other, self.transport
        other._issue_journal_account_binding()
        self.invalid(receipt)
        self.assertEqual(original._validate_journal_account_binding(receipt), receipt.binding)

    def test_lost_registry_cannot_reconstruct_after_restart(self):
        receipt = self.ready()
        _, fresh, _, _ = self.connected()
        self.transport = fresh
        self.invalid(receipt)
        self.assertFalse(fresh._journal_account_binding_issued)

    def test_enrollment_replacement_same_account_invalidates(self):
        receipt = self.ready()
        self.enroll(replace_existing=True)
        self.invalid(receipt)

    def test_enrollment_other_account_invalidates(self):
        receipt = self.ready()
        self.enroll(OTHER_ACCOUNT, replace_existing=True)
        self.invalid(receipt)

    def test_current_account_replacement_invalidates(self):
        receipt = self.ready()
        self.transport._accounts = (OTHER_ACCOUNT,)
        self.invalid(receipt)

    def test_deleted_enrollment_invalidates(self):
        receipt = self.ready()
        self.path.unlink()
        self.invalid(receipt)

    def test_enrollment_authentication_tampering_invalidates(self):
        receipt = self.ready()
        record = json.loads(self.path.read_text())
        record['authentication'] = '0'*64
        self.path.write_text(json.dumps(record))
        self.invalid(receipt)

    def test_sdk_failure_text_cannot_escape(self):
        receipt = self.ready()
        with patch.object(self.api.Client, 'isConnected', side_effect=RuntimeError(ACCOUNT)):
            self.invalid(receipt)

    def test_key_removed_invalidates(self):
        receipt = self.ready()
        self.store._key_path.unlink()
        self.invalid(receipt)

    def test_generation_changed_invalidates(self):
        receipt = self.ready()
        self.transport._generation += 1
        self.invalid(receipt)

    def test_disconnect_discards_association_before_reconnect(self):
        receipt = self.ready()
        self.transport.disconnect()
        self.assertIsNone(self.transport._journal_account_association)
        self.broker.connect()
        self.invalid(receipt)
        with self.assertRaises(ReadOnlyError): self.transport._issue_journal_account_binding()

    def test_expired_snapshot_invalidates(self):
        receipt = self.ready()
        self.clock.advance(16)
        self.invalid(receipt)

    def test_local_sdk_disconnected_invalidates_without_requests(self):
        receipt = self.ready()
        self.api.clients[-1].connected = False
        before = list(self.api.calls)
        self.invalid(receipt)
        self.assertEqual(self.api.calls, before)

    def test_configuration_changed_invalidates(self):
        receipt = self.ready()
        self.transport.config = replace(self.transport.config, client_id=2)
        self.invalid(receipt)

    def test_snapshot_deadline_changed_invalidates(self):
        receipt = self.ready()
        snapshot = self.transport._snapshot
        self.transport._snapshot = replace(snapshot, account=replace(snapshot.account,
            valid_until=snapshot.account.valid_until+timedelta(seconds=1)))
        self.invalid(receipt)

    def test_validation_failure_is_terminal_even_if_state_restored(self):
        receipt = self.ready()
        self.transport._ready = False
        self.invalid(receipt)
        self.transport._ready = True
        self.invalid(receipt)

    def test_generation_change_during_authentication_invalidates(self):
        receipt = self.ready()
        read = self.store._read_record
        def change():
            record = read()
            self.transport._generation += 1
            return record
        with patch.object(self.store, '_read_record', side_effect=change): self.invalid(receipt)

    def test_expiration_during_authentication_invalidates(self):
        receipt = self.ready()
        read = self.store._read_record
        def delay():
            record = read()
            self.clock.advance(16)
            return record
        with patch.object(self.store, '_read_record', side_effect=delay): self.invalid(receipt)

    def test_enrollment_change_during_random_work_not_published(self):
        self.enroll()
        _, self.transport, _, _ = self.connected()
        def replace_enrollment(_):
            # Namespace randomness hook only, using existing authenticated store.
            self.path.unlink()
            return 'b'*32
        with patch.object(r.secrets, 'token_hex', side_effect=replace_enrollment):
            with self.assertRaises(ReadOnlyError): self.transport._issue_journal_account_binding()
        self.assertIsNone(self.transport._journal_account_association)

    def test_opaque_primitive_receipt_no_authentication_or_accounts(self):
        receipt = self.ready()
        self.assertEqual({f.name for f in fields(receipt)}, {'version', 'binding', 'read_generation'})
        self.assertEqual({f.name for f in fields(receipt.binding)}, {'opaque_id'})
        self.assertEqual(repr(receipt), '<PrivateJournalAccountBindingReceipt>')
        self.assertEqual(repr(self.transport._journal_account_association), '<PrivateJournalAccountAssociation>')
        record = json.loads(self.path.read_text())
        rendered = repr(receipt) + repr(receipt.binding) + repr(self.transport._journal_account_association)
        for private in (ACCOUNT, record['authentication'], record['fingerprint'], record['salt']):
            self.assertNotIn(private, rendered)
        self.assertFalse(hasattr(receipt, '__dict__'))
        with self.assertRaises(FrozenInstanceError): receipt.version = 2

    def test_binding_has_no_mode_or_authorization_effect(self):
        receipt = self.ready()
        self.assertIs(self.broker.paper_execution_status, Status.DISARMED)
        self.transport._validate_journal_account_binding(receipt)
        self.assertIs(self.broker.paper_execution_status, Status.DISARMED)
        self.assertIs(self.broker.account_mode, AccountMode.UNKNOWN)
        self.assertIsNone(self.broker.account_summary().mode)
        header = j._JournalHeader(1, 'journal-'+'a'*32, receipt.binding)
        self.assertIs(header.account_mode, AccountMode.UNKNOWN)
        self.assertTrue(j._replay_paper_execution_journal(header, ()).restart_quarantine_required)

    def test_no_io_except_authenticated_enrollment_no_requests_or_writes(self):
        receipt = self.ready()
        calls = list(self.api.calls)
        with patch('sqlite3.connect', side_effect=AssertionError('SQLite')), \
             patch.object(self.api.Client, 'placeOrder', side_effect=AssertionError('write')), \
             patch.object(self.api.Client, 'cancelOrder', side_effect=AssertionError('cancel')), \
             patch.object(self.api.Client, 'reqGlobalCancel', side_effect=AssertionError('global cancel')):
            self.transport._validate_journal_account_binding(receipt)
        self.assertEqual(self.api.calls, calls)

    def test_public_broker_still_blocks_write(self):
        self.ready()
        from tests.broker_fixtures import request
        with self.assertRaises(BrokerOperationError): self.broker.place_order(request(), self.clock())
        import tradingbot_broker
        self.assertFalse(hasattr(tradingbot_broker, '_JournalAccountBindingReceipt'))

    def test_concurrent_issuance_one_namespace_only(self):
        self.enroll()
        _, self.transport, _, _ = self.connected()
        def issue(_):
            try: return self.transport._issue_journal_account_binding()
            except ReadOnlyError: return None
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(issue, range(2)))
        self.assertEqual(sum(v is not None for v in results), 1)
        self.assertIsNone(self.transport._journal_account_association)

    def test_concurrent_validation_serialized(self):
        receipt = self.ready()
        with ThreadPoolExecutor(max_workers=4) as workers:
            bindings = list(workers.map(lambda _: self.transport._validate_journal_account_binding(receipt), range(8)))
        self.assertTrue(all(binding == receipt.binding for binding in bindings))


def binding_case(name, mutate):
    def test(self):
        receipt = self.ready()
        mutate(self)
        self.invalid(receipt)
    setattr(BindingTests, 'test_unusable_' + name, test)


for name, value in (('_ready', False), ('_collecting', True), ('_failure', 'SDK_FAILURE'),
                    ('_disconnect_emitted', True), ('_snapshot', None), ('_accounts', ()),
                    ('_accounts', (ACCOUNT, OTHER_ACCOUNT))):
    binding_case(name.strip('_') + '_' + str(len(value) if type(value) is tuple else value),
                 lambda self, name=name, value=value: setattr(self.transport, name, value))
binding_case('bool_generation', lambda self: setattr(self.transport, '_generation', True))
binding_case('unverified', lambda self: setattr(self.transport, '_snapshot', replace(self.transport._snapshot,
    account=replace(self.transport._snapshot.account, verified=False))))
binding_case('verified_integer', lambda self: object.__setattr__(self.transport._snapshot.account, 'verified', 1))
binding_case('snapshot_arbitrary', lambda self: setattr(self.transport, '_snapshot', object()))
binding_case('mode_asserted', lambda self: object.__setattr__(self.transport._snapshot.account, 'mode', TradingMode.PAPER))


for field, value in (('version', 2), ('version', True), ('read_generation', True),
                     ('read_generation', 999), ('binding', 'not-a-binding')):
    def test(self, field=field, value=value):
        receipt = self.ready()
        object.__setattr__(receipt, field, value)
        self.invalid(receipt)
    setattr(BindingTests, 'test_tampered_' + field + '_' + str(value), test)


for error_type, argument in ((KeyboardInterrupt, None), (SystemExit, 37), (GeneratorExit, None)):
    for boundary in ('issue_authentication', 'issue_random', 'validate_authentication', 'validate_clock', 'validate_sdk'):
        def test(self, error_type=error_type, argument=argument, boundary=boundary):
            self.enroll()
            _, self.transport, self.api, self.clock = self.connected()
            receipt = None if boundary.startswith('issue') else self.transport._issue_journal_account_binding()
            error = error_type() if argument is None else error_type(argument)
            target, name = ((self.store, '_read_record') if boundary.endswith('authentication')
                            else (r.secrets, 'token_hex') if boundary.endswith('random')
                            else (self.api.Client, 'isConnected') if boundary.endswith('sdk')
                            else (self.transport, '_clock'))
            with patch.object(target, name, side_effect=error):
                with self.assertRaises(error_type) as raised:
                    if receipt is None: self.transport._issue_journal_account_binding()
                    else: self.transport._validate_journal_account_binding(receipt)
                self.assertIs(raised.exception, error)
            self.assertIsNone(self.transport._journal_account_association)
            with self.transport._lifecycle, self.transport._condition: pass
            if receipt is not None: self.invalid(receipt)
        setattr(BindingTests, 'test_interrupt_' + error_type.__name__ + '_' + boundary, test)
