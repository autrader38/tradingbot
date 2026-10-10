"""Anonymous, offline enrollment evidence; no real accounts, SDK or network."""

from contextlib import redirect_stdout
from datetime import timedelta
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tradingbot_broker import ibkr_diagnostics, ibkr_paper_enroll
from tradingbot_broker.broker import BrokerOperationError
from tradingbot_broker.ibkr_readonly import ReadOnlyTWSTransport, TWSReadOnlyConfig
from tradingbot_broker.ibkr_readonly_broker import ReadOnlyIBKRBroker
from tradingbot_broker.models import BrokerReason, ConnectionStatus, TradingMode
from tradingbot_broker.paper_enrollment import (
    CONFIRMATION, PaperEnrollmentStatus as S, PaperEnrollmentStore, enrollment_path)
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tradingbot_broker.safety import account_gates
from tests.broker_fixtures import request
from tests.readonly_fixtures import AT, Clock, sdk
from tests.test_ibkr_protobuf_reads import protobuf_sdk, proto_frame


ACCOUNT = 'anonymous-fixture'
OTHER_ACCOUNT = 'different-anonymous-fixture'
ROOT = Path(__file__).resolve().parents[1]


class EnrollmentFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'local' / 'ibkr-paper-enrollment.json'
        self.store = PaperEnrollmentStore(self.path)

    def enroll(self, account=ACCOUNT, **kwargs):
        self.store._enroll_account(account, AT, CONFIRMATION, **kwargs)
        return json.loads(self.path.read_text())

    def connected(self, api=None):
        api = sdk() if api is None else api
        clock = Clock()
        with patch('tradingbot_broker.ibkr_readonly._load_official_api', return_value=api):
            transport = ReadOnlyTWSTransport(TWSReadOnlyConfig(), clock=clock, enrollment_store=self.store)
        broker = ReadOnlyIBKRBroker(transport, clock=clock)
        self.addCleanup(broker.disconnect)
        broker.connect()
        return broker, transport, api, clock


class RecordTests(EnrollmentFixture):
    def test_missing_record_is_unenrolled_and_creates_nothing(self):
        self.assertIs(self.store._match_account(ACCOUNT), S.UNENROLLED)
        self.assertFalse(self.path.parent.exists())

    def test_enrolled_account_matches(self):
        self.enroll()
        self.assertIs(self.store._match_account(ACCOUNT), S.MATCHED)

    def test_different_account_is_mismatch(self):
        self.enroll()
        self.assertIs(self.store._match_account(OTHER_ACCOUNT), S.MISMATCH)

    def test_exact_domain_separated_fingerprint_and_authentication(self):
        with patch('tradingbot_broker.paper_enrollment.secrets.token_hex', return_value='ab' * 32), \
             patch('tradingbot_broker.paper_enrollment.secrets.token_bytes', return_value=b'k' * 32):
            record = self.enroll()
        self.assertEqual(record['fingerprint'], hashlib.sha256(
            ('VelocityTradingGroup|IBKR|PaperEnrollment|v1|' + 'ab' * 32 + '|' + ACCOUNT).encode()).hexdigest())
        unsigned = {key: value for key, value in record.items() if key != 'authentication'}
        canonical = json.dumps(unsigned, sort_keys=True, separators=(',', ':')).encode()
        expected = hmac.new(b'k' * 32, b'VelocityTradingGroup|IBKR|PaperEnrollmentAuthentication|v1|' + canonical,
                            hashlib.sha256).hexdigest()
        self.assertEqual(record['authentication'], expected)

    def test_raw_account_never_stored_in_record_or_anchor(self):
        self.enroll()
        for path in self.path.parent.iterdir():
            self.assertNotIn(ACCOUNT.encode(), path.read_bytes())
        self.assertEqual(set(json.loads(self.path.read_text())),
                         {'schema', 'version', 'salt', 'fingerprint', 'created_at', 'authentication'})

    def test_salt_and_authentication_key_use_independent_randomness(self):
        with patch('tradingbot_broker.paper_enrollment.secrets.token_hex', return_value='cd' * 32) as salt, \
             patch('tradingbot_broker.paper_enrollment.secrets.token_bytes', return_value=b'q' * 32) as key:
            record = self.enroll()
        salt.assert_called_once_with(32)
        key.assert_called_once_with(32)
        self.assertEqual(record['salt'], 'cd' * 32)

    def test_reenrollment_generates_distinct_salt_and_digest(self):
        first = self.enroll()
        second = self.enroll(replace_existing=True)
        self.assertNotEqual(first['salt'], second['salt'])
        self.assertNotEqual(first['fingerprint'], second['fingerprint'])
        self.assertIs(self.store._match_account(ACCOUNT), S.MATCHED)

    def test_authentication_and_account_comparisons_are_constant_time(self):
        self.enroll()
        with patch('tradingbot_broker.paper_enrollment.hmac.compare_digest', wraps=hmac.compare_digest) as comparison:
            self.assertIs(self.store._match_account(ACCOUNT), S.MATCHED)
        self.assertEqual(comparison.call_count, 2)
        self.assertTrue(all(type(value) is str for call in comparison.call_args_list for value in call.args))

    def test_well_formed_digest_tampering_is_invalid_not_mismatch(self):
        record = self.enroll()
        record['fingerprint'] = '0' * 64
        self.path.write_text(json.dumps(record))
        self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)

    def test_well_formed_salt_tampering_is_invalid(self):
        record = self.enroll()
        record['salt'] = '0' * 64
        self.path.write_text(json.dumps(record))
        self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)

    def test_well_formed_timestamp_tampering_is_invalid(self):
        record = self.enroll()
        record['created_at'] = (AT + timedelta(seconds=1)).isoformat()
        self.path.write_text(json.dumps(record))
        self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)

    def test_missing_authentication_anchor_is_invalid_without_recreation(self):
        self.enroll()
        self.store._key_path.unlink()
        self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)
        self.assertFalse(self.store._key_path.exists())

    def test_modified_anchor_is_invalid(self):
        self.enroll()
        self.store._key_path.write_bytes(b'POSIX\0' + b'x' * 32)
        self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)

    def test_invalid_authentication_stops_before_account_comparison(self):
        record = self.enroll()
        record['fingerprint'] = '0' * 64
        self.path.write_text(json.dumps(record))
        with patch('tradingbot_broker.paper_enrollment.hmac.compare_digest', wraps=hmac.compare_digest) as compared:
            self.assertIs(self.store._match_account(OTHER_ACCOUNT), S.INVALID)
        self.assertEqual(compared.call_count, 1)

    def test_unreadable_record_is_invalid_and_error_text_sanitized(self):
        self.enroll()
        with patch('tradingbot_broker.paper_enrollment.os.open', side_effect=OSError(ACCOUNT)):
            self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)
            with self.assertRaises(ReadOnlyError) as raised:
                self.store._read_record()
        self.assertEqual(str(raised.exception), 'INVALID_PAPER_ENROLLMENT')
        self.assertNotIn(ACCOUNT, repr(raised.exception))

    def test_oversized_record_invalid(self):
        self.path.parent.mkdir()
        self.path.write_text(' ' * 4097)
        self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)

    def test_duplicate_json_keys_invalid(self):
        self.enroll()
        raw = self.path.read_text().rstrip()
        self.path.write_text(raw[:-1] + ',"version":1}')
        self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)

    def test_plaintext_unauthenticated_record_cannot_match(self):
        record = self.enroll()
        del record['authentication']
        self.path.write_text(json.dumps(record))
        self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)


def invalid_record_test(field, value):
    def test(self):
        record = self.enroll()
        record[field] = value
        self.path.write_text(json.dumps(record))
        self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)
    return test


for label, field, value in (
    ('schema', 'schema', 'other'), ('version', 'version', 2), ('bool_version', 'version', True),
    ('salt_short', 'salt', 'ab'), ('salt_non_hex', 'salt', 'z' * 64), ('salt_type', 'salt', 17),
    ('digest_short', 'fingerprint', 'ab'), ('digest_non_hex', 'fingerprint', 'z' * 64),
    ('digest_type', 'fingerprint', None), ('authentication', 'authentication', 'z' * 64),
    ('naive_timestamp', 'created_at', '2026-07-01T14:00:00'), ('timestamp_type', 'created_at', 17),
    ('extra_raw_field', 'account', ACCOUNT),
):
    setattr(RecordTests, 'test_invalid_' + label, invalid_record_test(field, value))


for label, raw in (('malformed_json', b'{'), ('truncated', b'{"schema":'),
                   ('non_object', b'[]'), ('invalid_utf8', b'\xff'), ('empty', b'')):
    def test(self, raw=raw):
        self.path.parent.mkdir()
        self.path.write_bytes(raw)
        self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)
    setattr(RecordTests, 'test_' + label, test)


class StorageTests(EnrollmentFixture):
    def test_existing_enrollment_never_silently_overwritten(self):
        self.enroll()
        before = self.path.read_bytes(), self.store._key_path.read_bytes()
        with self.assertRaisesRegex(ReadOnlyError, '^PAPER_ENROLLMENT_ALREADY_EXISTS$'):
            self.enroll(OTHER_ACCOUNT)
        self.assertEqual(before, (self.path.read_bytes(), self.store._key_path.read_bytes()))

    def test_explicit_reenrollment_matches_new_account_only(self):
        self.enroll()
        self.enroll(OTHER_ACCOUNT, replace_existing=True)
        self.assertIs(self.store._match_account(OTHER_ACCOUNT), S.MATCHED)
        self.assertIs(self.store._match_account(ACCOUNT), S.MISMATCH)

    def test_replacement_still_requires_confirmation(self):
        self.enroll()
        before = self.path.read_bytes()
        with self.assertRaises(ReadOnlyError):
            self.store._enroll_account(OTHER_ACCOUNT, AT, 'yes', replace_existing=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_initial_enrollment_requires_confirmation_and_no_files_created(self):
        for phrase in ('yes', CONFIRMATION.lower(), CONFIRMATION + ' ', '', True, None):
            with self.subTest(phrase=phrase):
                with self.assertRaisesRegex(ReadOnlyError, '^PAPER_ENROLLMENT_CONFIRMATION_REQUIRED$'):
                    self.store._enroll_account(ACCOUNT, AT, phrase)
        self.assertFalse(self.path.parent.exists())

    def test_replacement_flag_must_be_boolean(self):
        for value in (1, 'true', None):
            with self.subTest(value=value):
                with self.assertRaises(ReadOnlyError): self.enroll(replace_existing=value)
        self.assertFalse(self.path.parent.exists())

    def test_record_is_complete_before_atomic_install(self):
        original = os.link
        observed = []
        def install(source, target):
            if Path(target) == self.path:
                self.assertFalse(self.path.exists())
                observed.append(json.loads(Path(source).read_text()))
            return original(source, target)
        with patch('tradingbot_broker.paper_enrollment.os.link', side_effect=install):
            record = self.enroll()
        self.assertEqual(observed, [record])
        self.assertFalse(list(self.path.parent.glob('*.tmp')))

    def test_atomic_replacement_keeps_old_record_until_install(self):
        self.enroll()
        old = self.path.read_bytes()
        original = os.replace
        def replace(source, target):
            if Path(target) == self.path:
                self.assertEqual(self.path.read_bytes(), old)
                self.assertNotEqual(Path(source).read_bytes(), old)
            return original(source, target)
        with patch('tradingbot_broker.paper_enrollment.os.replace', side_effect=replace) as installed:
            self.enroll(OTHER_ACCOUNT, replace_existing=True)
        self.assertEqual(installed.call_count, 1)
        self.assertIs(self.store._match_account(OTHER_ACCOUNT), S.MATCHED)

    def test_racing_create_cannot_overwrite_winner(self):
        # An independently installed enrollment wins immediately before final link.
        self.enroll()
        winner = self.path.read_bytes()
        self.path.unlink()
        original = os.link
        def race(source, target):
            if Path(target) == self.path: self.path.write_bytes(winner)
            return original(source, target)
        with patch('tradingbot_broker.paper_enrollment.os.link', side_effect=race):
            with self.assertRaisesRegex(ReadOnlyError, '^PAPER_ENROLLMENT_ALREADY_EXISTS$'):
                self.enroll(OTHER_ACCOUNT)
        self.assertEqual(self.path.read_bytes(), winner)
        self.assertIs(self.store._match_account(ACCOUNT), S.MATCHED)

    def test_failed_write_preserves_original_and_cleans_temporary(self):
        self.enroll()
        before = self.path.read_bytes()
        with patch('tradingbot_broker.paper_enrollment.os.replace', side_effect=OSError(ACCOUNT)):
            with self.assertRaisesRegex(ReadOnlyError, '^PAPER_ENROLLMENT_WRITE_FAILED$'):
                self.enroll(OTHER_ACCOUNT, replace_existing=True)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(list(self.path.parent.glob('*.tmp')))

    def test_parent_directory_created_owner_only(self):
        self.enroll()
        if os.name == 'posix':
            self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.store._key_path.stat().st_mode & 0o777, 0o600)

    def test_overpermissive_key_rejected(self):
        self.enroll()
        if os.name != 'posix': self.skipTest('POSIX permission boundary')
        self.store._key_path.chmod(0o644)
        self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)

    def test_overpermissive_directory_invalidates_authentication_anchor(self):
        self.enroll()
        if os.name != 'posix': self.skipTest('POSIX permission boundary')
        self.path.parent.chmod(0o755)
        self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)
        with self.assertRaisesRegex(ReadOnlyError, '^UNSAFE_PAPER_ENROLLMENT_DIRECTORY$'):
            self.enroll(replace_existing=True)

    def test_authentication_key_owner_must_match_process_user(self):
        self.enroll()
        if os.name != 'posix': self.skipTest('POSIX permission boundary')
        with patch('tradingbot_broker.paper_enrollment.os.getuid', return_value=os.getuid() + 1):
            self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)

    def test_explicit_reenrollment_can_recover_missing_anchor(self):
        self.enroll()
        self.store._key_path.unlink()
        self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)
        self.enroll(replace_existing=True)
        self.assertIs(self.store._match_account(ACCOUNT), S.MATCHED)

    def test_windows_anchor_uses_dpapi_without_plaintext_fallback(self):
        with patch('tradingbot_broker.paper_enrollment.sys.platform', 'win32'), \
             patch('tradingbot_broker.paper_enrollment._windows_protect',
                   side_effect=lambda data, **kwargs: data[::-1]) as protected:
            self.enroll()
            self.assertTrue(self.store._key_path.read_bytes().startswith(b'DPAPI\0'))
            self.assertIs(self.store._match_account(ACCOUNT), S.MATCHED)
        self.assertTrue(any(call.kwargs.get('decrypt') for call in protected.call_args_list))

    def test_windows_protection_failure_sanitized_no_plaintext_key(self):
        with patch('tradingbot_broker.paper_enrollment.sys.platform', 'win32'), \
             patch('tradingbot_broker.paper_enrollment._windows_protect', side_effect=OSError(ACCOUNT)):
            with self.assertRaisesRegex(ReadOnlyError, '^PAPER_ENROLLMENT_WRITE_FAILED$'):
                self.enroll()
        self.assertFalse(self.path.exists())
        self.assertFalse(self.store._key_path.exists())

    def test_repository_path_rejected(self):
        for path in (ROOT / 'ibkr-paper-enrollment.json', ROOT / 'nested' / 'enrollment.json'):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ReadOnlyError, '^INVALID_PAPER_ENROLLMENT_PATH$'):
                    PaperEnrollmentStore(path)

    def test_other_git_repository_path_rejected(self):
        other = Path(self.temporary.name) / 'another-repository'
        (other / '.git').mkdir(parents=True)
        (other / '.git' / 'HEAD').write_text('ref: refs/heads/main\n')
        with self.assertRaises(ReadOnlyError): PaperEnrollmentStore(other / 'enrollment.json')

    def test_relative_path_rejected(self):
        with self.assertRaises(ReadOnlyError): PaperEnrollmentStore('ibkr-paper-enrollment.json')

    def test_symlink_into_repository_rejected(self):
        link = Path(self.temporary.name) / 'repository-alias'
        link.symlink_to(ROOT, target_is_directory=True)
        with self.assertRaises(ReadOnlyError): PaperEnrollmentStore(link / 'enrollment.json')

    def test_record_symlink_not_followed(self):
        self.enroll()
        target = self.path.with_name('copy.json')
        self.path.rename(target)
        self.path.symlink_to(target)
        self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)

    def test_anchor_symlink_not_followed(self):
        self.enroll()
        target = self.path.with_name('copy.key')
        self.store._key_path.rename(target)
        self.store._key_path.symlink_to(target)
        self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)

    def test_windows_location(self):
        result = enrollment_path(platform='win32', home=self.temporary.name,
                                 environment={'LOCALAPPDATA': self.temporary.name})
        self.assertEqual(result, Path(self.temporary.name) / 'VelocityTradingGroup' / self.path.name)

    def test_windows_fallback_location(self):
        result = enrollment_path(platform='win32', home=self.temporary.name, environment={})
        self.assertEqual(result, Path(self.temporary.name) / 'AppData' / 'Local' / 'VelocityTradingGroup' / self.path.name)

    def test_posix_location(self):
        result = enrollment_path(platform='linux', home=self.temporary.name, environment={})
        self.assertEqual(result, Path(self.temporary.name) / '.local' / 'share' / 'VelocityTradingGroup' / self.path.name)

    def test_xdg_location(self):
        result = enrollment_path(platform='linux', home='/', environment={'XDG_DATA_HOME': self.temporary.name})
        self.assertEqual(result, Path(self.temporary.name) / 'VelocityTradingGroup' / self.path.name)

    def test_macos_location(self):
        result = enrollment_path(platform='darwin', home=self.temporary.name, environment={})
        self.assertEqual(result, Path(self.temporary.name) / 'Library' / 'Application Support' / 'VelocityTradingGroup' / self.path.name)

    def test_relative_environment_location_rejected(self):
        with self.assertRaises(ReadOnlyError):
            enrollment_path(platform='linux', environment={'XDG_DATA_HOME': 'relative'}, home='/')


class TransportTests(EnrollmentFixture):
    def test_unenrolled_connection_does_not_enroll_automatically(self):
        broker, transport, _, _ = self.connected()
        self.assertIs(broker.paper_enrollment_status, S.UNENROLLED)
        self.assertIs(transport.paper_enrollment_status, S.UNENROLLED)
        self.assertFalse(self.path.parent.exists())

    def test_enrollment_through_facade_matches_without_mode_change(self):
        broker, transport, _, _ = self.connected()
        before = broker.account_summary()
        self.assertIs(broker.enroll_paper_account(CONFIRMATION), S.MATCHED)
        self.assertIs(broker.paper_enrollment_status, S.MATCHED)
        self.assertIs(transport.paper_enrollment_status, S.MATCHED)
        self.assertIs(broker.account_mode, AccountMode.UNKNOWN)
        self.assertEqual(broker.account_summary(), before)
        self.assertIsNone(before.mode)
        self.assertIn(BrokerReason.ACCOUNT_MODE_UNVERIFIED,
                      account_gates(TradingMode.PAPER, ConnectionStatus.CONNECTED, before, AT + timedelta(seconds=1)))

    def test_different_enrolled_account_surfaces_mismatch_only(self):
        self.enroll(OTHER_ACCOUNT)
        broker, _, _, _ = self.connected()
        self.assertIs(broker.paper_enrollment_status, S.MISMATCH)
        self.assertIs(broker.account_mode, AccountMode.UNKNOWN)

    def test_enrollment_audit_does_not_contain_account_salt_key_or_digest(self):
        broker, _, _, _ = self.connected()
        broker.enroll_paper_account(CONFIRMATION)
        record = json.loads(self.path.read_text())
        audit = repr(broker.audit)
        for value in (ACCOUNT, record['salt'], record['fingerprint'], record['authentication']):
            self.assertNotIn(value, audit)
        self.assertIn('PAPER_ACCOUNT_ENROLLED', audit)
        self.assertIn('MATCHED', audit)

    def test_public_snapshots_and_identities_never_expose_raw_account(self):
        broker, transport, _, _ = self.connected()
        broker.enroll_paper_account(CONFIRMATION)
        output = repr((broker.account_summary(), broker.account_identities(), transport.connect(), transport.diagnostics))
        self.assertNotIn(ACCOUNT, output)
        self.assertIn('account-1', output)

    def test_account_number_shape_or_port_does_not_establish_paper(self):
        broker, _, _, _ = self.connected()
        self.assertIs(broker.account_mode, AccountMode.UNKNOWN)
        self.assertIs(broker.paper_enrollment_status, S.UNENROLLED)

    def test_disconnected_transport_cannot_enroll(self):
        broker, transport, _, _ = self.connected()
        broker.disconnect()
        with self.assertRaisesRegex(ReadOnlyError, '^PAPER_ENROLLMENT_CONNECTION_UNUSABLE$'):
            transport.enroll_paper_account(CONFIRMATION)
        self.assertIs(broker.paper_enrollment_status, S.INVALID)
        self.assertFalse(self.path.exists())

    def test_stale_snapshot_cannot_enroll_or_remain_matched(self):
        broker, transport, _, clock = self.connected()
        broker.enroll_paper_account(CONFIRMATION)
        clock.advance(30)
        self.assertIs(broker.paper_enrollment_status, S.INVALID)
        self.assertIs(transport.paper_enrollment_status, S.INVALID)
        with self.assertRaisesRegex(ReadOnlyError, '^PAPER_ENROLLMENT_SNAPSHOT_STALE$'):
            transport.enroll_paper_account(CONFIRMATION, replace_existing=True)

    def test_disconnect_callback_invalidates_enrollment_usability(self):
        broker, transport, api, _ = self.connected()
        broker.enroll_paper_account(CONFIRMATION)
        api.clients[-1].wrapper.connectionClosed()
        self.assertIs(broker.paper_enrollment_status, S.INVALID)
        self.assertIs(broker.connection_status, ConnectionStatus.DISCONNECTED)
        with self.assertRaises(ReadOnlyError): transport.enroll_paper_account(CONFIRMATION)

    def test_reconnect_does_not_require_reenrollment(self):
        broker, _, _, _ = self.connected()
        broker.enroll_paper_account(CONFIRMATION)
        broker.refresh()
        self.assertIs(broker.paper_enrollment_status, S.MATCHED)
        self.assertIs(broker.account_mode, AccountMode.UNKNOWN)

    def test_wrong_phrase_does_not_create_record_or_enrollment_audit(self):
        broker, transport, _, _ = self.connected()
        before = broker.audit
        for target in (broker, transport):
            with self.assertRaises(ReadOnlyError): target.enroll_paper_account(ACCOUNT)
        self.assertEqual(broker.audit, before)
        self.assertFalse(self.path.exists())

    def test_matched_enrollment_does_not_enable_any_facade_write(self):
        broker, transport, api, clock = self.connected()
        broker.enroll_paper_account(CONFIRMATION)
        before = tuple(api.calls)
        order = request()
        operations = (
            lambda: broker.place_order(order, clock()),
            lambda: broker.replace_order('observed-1', order, clock()),
            lambda: broker.modify_order('observed-1', order, clock()),
            lambda: broker.cancel_order('observed-1', clock()),
            lambda: broker.cancel_working_orders(clock()),
            lambda: broker.flatten_positions(clock()),
            lambda: broker.set_trading_enabled(True, clock()),
            lambda: broker.place_order(request(mode=TradingMode.LIVE), clock()),
        )
        for operation in operations:
            with self.assertRaises(BrokerOperationError) as raised: operation()
            self.assertIs(raised.exception.reason, BrokerReason.READ_ONLY_BROKER_TRANSPORT)
        for name in ('place_order', 'replace_order', 'modify_order', 'cancel_order', 'flatten_positions'):
            with self.assertRaises(ReadOnlyError): getattr(transport, name)()
        self.assertEqual(tuple(api.calls), before)

    def test_enrolled_client_and_socket_still_block_all_write_ids(self):
        broker, transport, api, _ = self.connected(protobuf_sdk())
        broker.enroll_paper_account(CONFIRMATION)
        self.assertEqual(set(vars(transport._api.OUT).values()), {7, 16, 17, 49, 61, 62, 63, 64, 71, 99})
        self.assertEqual(set(vars(transport._api.PROTOBUF_OUT).values()), {207, 216, 217, 249, 261, 262, 263, 264, 271, 299})
        client = api.clients[-1]  # Fake SDK inspection only; application receives no raw client.
        before = tuple(call for call in api.calls if call[0] == 'socket-send')
        for method in ('placeOrder', 'cancelOrder', 'reqGlobalCancel'):
            with self.assertRaises(ReadOnlyError): getattr(client, method)()
        for opcode in (203, 204, 258):
            with self.assertRaises(ReadOnlyError): client.sendMsgProtoBuf(opcode, b'')
            with self.assertRaises(ReadOnlyError): api.Client.sendMsgProtoBuf(client, opcode, b'')
        for opcode in (3, 4, 58, 203, 204, 258):
            for send in (client.conn.sendMsg, client.conn.socket.send, client.conn.socket.sendall):
                with self.assertRaises(ReadOnlyError): send(proto_frame(opcode))
        self.assertEqual(tuple(call for call in api.calls if call[0] == 'socket-send'), before)


class CommandTests(EnrollmentFixture):
    def run_enroll(self, args=(), phrase=CONFIRMATION, api=None, environment=None):
        api = sdk() if api is None else api
        output = io.StringIO()
        with patch('tradingbot_broker.ibkr_readonly._load_official_api', return_value=api), \
             patch('tradingbot_broker.paper_enrollment.enrollment_path', return_value=self.path), \
             patch.dict(os.environ, environment or {}, clear=True), \
             patch('builtins.input', return_value=phrase), redirect_stdout(output):
            result = ibkr_paper_enroll.main(list(args))
        return result, output.getvalue(), api

    def test_command_creates_enrollment_and_disconnects(self):
        result, output, api = self.run_enroll()
        self.assertEqual(result, 0)
        self.assertIs(self.store._match_account(ACCOUNT), S.MATCHED)
        self.assertIn('Paper enrollment: MATCHED', output)
        self.assertIn('Account mode: UNKNOWN', output)
        self.assertIn('Trading permission: UNUSABLE', output)
        self.assertFalse(api.clients[-1].isConnected())
        self.assertNotIn(ACCOUNT, output)

    def test_command_wrong_phrase_rejected_before_connect_or_write(self):
        result, output, api = self.run_enroll(phrase=ACCOUNT)
        self.assertEqual(result, 1)
        self.assertIn('PAPER_ENROLLMENT_CONFIRMATION_REQUIRED', output)
        self.assertNotIn(ACCOUNT, output)
        self.assertEqual(api.calls, [])
        self.assertFalse(self.path.exists())

    def test_command_existing_record_requires_explicit_replace(self):
        self.enroll(OTHER_ACCOUNT)
        before = self.path.read_bytes()
        result, output, _ = self.run_enroll()
        self.assertEqual(result, 1)
        self.assertIn('PAPER_ENROLLMENT_ALREADY_EXISTS', output)
        self.assertEqual(self.path.read_bytes(), before)
        result, output, _ = self.run_enroll(('--replace-existing',))
        self.assertEqual(result, 0)
        self.assertIs(self.store._match_account(ACCOUNT), S.MATCHED)

    def test_replace_option_does_not_bypass_confirmation(self):
        self.enroll(OTHER_ACCOUNT)
        before = self.path.read_bytes()
        result, _, api = self.run_enroll(('--replace-existing',), phrase='yes')
        self.assertEqual(result, 1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(api.calls, [])

    def test_zero_accounts_rejected(self):
        result, output, _ = self.run_enroll(api=sdk(accounts=''))
        self.assertEqual(result, 1)
        self.assertIn('ACCOUNT_NOT_READY', output)
        self.assertFalse(self.path.exists())

    def test_multiple_accounts_rejected_without_identity_leak(self):
        result, output, _ = self.run_enroll(api=sdk(accounts=ACCOUNT + ',' + OTHER_ACCOUNT))
        self.assertEqual(result, 1)
        self.assertIn('MULTIPLE_ACCOUNTS_UNSUPPORTED', output)
        self.assertNotIn(ACCOUNT, output)
        self.assertNotIn(OTHER_ACCOUNT, output)
        self.assertFalse(self.path.exists())

    def test_client_zero_rejected(self):
        result, output, api = self.run_enroll(environment={'IBKR_CLIENT_ID': '0'})
        self.assertEqual(result, 1)
        self.assertIn('INVALID_IBKR_CONFIGURATION', output)
        self.assertEqual(api.calls, [])

    def test_remote_gateway_rejected(self):
        result, _, api = self.run_enroll(environment={'IBKR_HOST': 'example.invalid'})
        self.assertEqual(result, 1)
        self.assertEqual(api.calls, [])

    def test_readonly_false_rejected(self):
        result, _, api = self.run_enroll(environment={'IBKR_READ_ONLY': 'false'})
        self.assertEqual(result, 1)
        self.assertEqual(api.calls, [])

    def test_connection_failure_sanitized(self):
        result, output, _ = self.run_enroll(api=sdk(connect_error=True))
        self.assertEqual(result, 1)
        self.assertNotIn('private fixture value', output)
        self.assertNotIn('Traceback', output)
        self.assertFalse(self.path.exists())

    def test_missing_sdk_sanitized(self):
        output = io.StringIO()
        with patch('builtins.input', return_value=CONFIRMATION), \
             patch('tradingbot_broker.ibkr_readonly.importlib.import_module', side_effect=ImportError(ACCOUNT)), \
             patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
            result = ibkr_paper_enroll.main([])
        self.assertEqual(result, 1)
        self.assertIn('OFFICIAL_IBAPI_DEPENDENCY_UNAVAILABLE', output.getvalue())
        self.assertNotIn(ACCOUNT, output.getvalue())
        self.assertNotIn('Traceback', output.getvalue())

    def test_unknown_arguments_not_echoed_and_no_connect(self):
        result, output, api = self.run_enroll(('--account', ACCOUNT))
        self.assertEqual(result, 1)
        self.assertIn('INVALID_PAPER_ENROLLMENT_ARGUMENTS', output)
        self.assertNotIn(ACCOUNT, output)
        self.assertEqual(api.calls, [])

    def test_diagnostics_matched_remains_unknown_unusable_and_masked(self):
        self.enroll()
        output = io.StringIO()
        with patch('tradingbot_broker.ibkr_readonly._load_official_api', return_value=sdk()), \
             patch('tradingbot_broker.paper_enrollment.enrollment_path', return_value=self.path), \
             patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
            result = ibkr_diagnostics.main()
        text = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn('Paper enrollment: MATCHED', text)
        self.assertIn('Account mode: UNKNOWN (not independently attested)', text)
        self.assertIn('Trading permission: UNUSABLE', text)
        self.assertIn('Account: account-1 (masked)', text)
        self.assertNotIn(ACCOUNT, text)

    def test_diagnostics_unenrolled_creates_no_local_state(self):
        output = io.StringIO()
        with patch('tradingbot_broker.ibkr_readonly._load_official_api', return_value=sdk()), \
             patch('tradingbot_broker.paper_enrollment.enrollment_path', return_value=self.path), \
             patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
            self.assertEqual(ibkr_diagnostics.main(), 0)
        self.assertIn('Paper enrollment: UNENROLLED', output.getvalue())
        self.assertFalse(self.path.parent.exists())

    def test_diagnostics_tampered_record_is_invalid_but_inspection_readonly(self):
        record = self.enroll()
        record['fingerprint'] = '0' * 64
        self.path.write_text(json.dumps(record))
        output = io.StringIO()
        with patch('tradingbot_broker.ibkr_readonly._load_official_api', return_value=sdk()), \
             patch('tradingbot_broker.paper_enrollment.enrollment_path', return_value=self.path), \
             patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
            self.assertEqual(ibkr_diagnostics.main(), 0)
        self.assertIn('Paper enrollment: INVALID', output.getvalue())
        self.assertIn('Trading permission: UNUSABLE', output.getvalue())


if __name__ == '__main__':
    unittest.main()
