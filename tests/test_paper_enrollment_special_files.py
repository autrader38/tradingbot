"""Bounded anonymous storage probes; no SDK installation or broker networking."""

from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tradingbot_broker.paper_enrollment import (
    CONFIRMATION, PaperEnrollmentStatus as S, _read_regular_file)
from tradingbot_broker.readonly_models import ReadOnlyError
from tests.test_ibkr_paper_enrollment import EnrollmentFixture, ACCOUNT, OTHER_ACCOUNT


# Run potentially blocking regressions in bounded children. A broken reader can
# never hang the whole test suite, and no writer opens either FIFO.
_FIFO_PROBE = r'''
import errno
import json
import os
from pathlib import Path
import sys
from time import monotonic
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from tradingbot_broker.paper_enrollment import PaperEnrollmentStore, PaperEnrollmentStatus
from tradingbot_broker.readonly_models import ReadOnlyError, AccountMode
from tradingbot_broker.models import ConnectionStatus
from tradingbot_broker.ibkr_readonly import ReadOnlyTWSTransport, TWSReadOnlyConfig
from tradingbot_broker.ibkr_readonly_broker import ReadOnlyIBKRBroker
from tests.readonly_fixtures import Clock, sdk
path, kind, use_broker = Path(sys.argv[1]), sys.argv[2], sys.argv[3] == 'broker'
store = PaperEnrollmentStore(path)
opened = []
original_open = os.open
def tracked_open(*args, **kwargs):
    descriptor = original_open(*args, **kwargs)
    opened.append(descriptor)
    return descriptor
clock = Clock()
api = sdk()
broker = None
if use_broker:
    with patch('tradingbot_broker.ibkr_readonly._load_official_api', return_value=api):
        transport = ReadOnlyTWSTransport(TWSReadOnlyConfig(), clock=clock, enrollment_store=store)
    broker = ReadOnlyIBKRBroker(transport, clock=clock)
    broker.connect()
started = monotonic()
with patch('tradingbot_broker.paper_enrollment.os.open', side_effect=tracked_open):
    status = broker.paper_enrollment_status if use_broker else store._match_account('anonymous-fixture')
elapsed = monotonic() - started
assert status is PaperEnrollmentStatus.INVALID
assert elapsed < 2
assert opened
for descriptor in opened:
    try: os.fstat(descriptor)
    except OSError as error: assert error.errno == errno.EBADF
    else: raise AssertionError('descriptor not closed')
try:
    store._read_key() if kind == 'key' else store._read_record()
except ReadOnlyError as error:
    assert str(error) == ('INVALID_PAPER_ENROLLMENT_KEY' if kind == 'key' else 'INVALID_PAPER_ENROLLMENT')
    assert str(path) not in str(error) and 'anonymous-fixture' not in str(error)
    assert 'Errno' not in str(error)
else: raise AssertionError('special file accepted')
if broker is not None:
    assert broker.account_mode is AccountMode.UNKNOWN
    assert broker.account_summary().mode is None
    assert not broker.controls.trading_enabled
    # Different threads prove neither transport nor facade lock was left held.
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(broker.account_summary).result(timeout=1).mode is None
        api.clients[-1].wrapper.connectionClosed()
        assert executor.submit(broker.poll).result(timeout=1) is ConnectionStatus.DISCONNECTED
        assert executor.submit(broker.refresh).result(timeout=1) is ConnectionStatus.CONNECTED
        assert executor.submit(lambda: broker.paper_enrollment_status).result(timeout=1) is PaperEnrollmentStatus.INVALID
        assert executor.submit(broker.disconnect).result(timeout=1) is None
        assert executor.submit(broker.poll).result(timeout=1) is ConnectionStatus.DISCONNECTED
print(json.dumps({'status': status.value, 'closed_descriptors': len(opened),
                  'responsive_broker': use_broker}))
'''


class DescriptorFixture(EnrollmentFixture):
    @contextmanager
    def tracked_descriptors(self):
        opened = []
        original = os.open
        def track(*args, **kwargs):
            descriptor = original(*args, **kwargs)
            opened.append(descriptor)
            return descriptor
        with patch('tradingbot_broker.paper_enrollment.os.open', side_effect=track):
            yield opened
        for descriptor in opened:
            with self.assertRaises(OSError) as raised: os.fstat(descriptor)
            self.assertEqual(raised.exception.errno, errno.EBADF)

    def replace_with_fifo(self, kind):
        if os.name != 'posix' or not hasattr(os, 'mkfifo'):
            self.skipTest('POSIX FIFO regression')
        self.enroll()
        target = self.path if kind == 'record' else self.store._key_path
        target.unlink()
        os.mkfifo(target, 0o600)

    def fifo_probe(self, kind, *, broker=False):
        self.replace_with_fifo(kind)
        try:
            result = subprocess.run([sys.executable, '-B', '-c', _FIFO_PROBE,
                                     str(self.path), kind, 'broker' if broker else 'store'],
                                    capture_output=True, text=True, timeout=5)
        except subprocess.TimeoutExpired:
            self.fail('Special-file reader or broker lifecycle blocked; child terminated')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['status'], 'INVALID')
        self.assertGreaterEqual(data['closed_descriptors'], 1)
        self.assertEqual(data['responsive_broker'], broker)


class SpecialFileTests(DescriptorFixture):
    def test_fifo_record_prompt_invalid_no_writer_and_closed_descriptor(self):
        self.fifo_probe('record')

    def test_fifo_key_prompt_invalid_no_writer_and_closed_descriptor(self):
        self.fifo_probe('key')

    def test_record_fifo_does_not_hold_locks_or_block_disconnect_and_events(self):
        self.fifo_probe('record', broker=True)

    def test_key_fifo_does_not_hold_locks_or_block_disconnect_and_events(self):
        self.fifo_probe('key', broker=True)

    def test_record_directory_rejected_and_descriptor_closed(self):
        self.enroll()
        self.path.unlink()
        self.path.mkdir(mode=0o700)
        with self.tracked_descriptors():
            self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)

    def test_key_directory_rejected_and_descriptor_closed(self):
        self.enroll()
        self.store._key_path.unlink()
        self.store._key_path.mkdir(mode=0o700)
        with self.tracked_descriptors():
            self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)

    def test_unix_socket_record_rejected(self):
        if not hasattr(socket, 'AF_UNIX'): self.skipTest('Unix filesystem socket')
        self.enroll()
        self.path.unlink()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as endpoint:
            endpoint.bind(str(self.path))
            started = time.monotonic()
            self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)
            self.assertLess(time.monotonic() - started, 2)

    def test_unix_socket_key_rejected(self):
        if not hasattr(socket, 'AF_UNIX'): self.skipTest('Unix filesystem socket')
        self.enroll()
        self.store._key_path.unlink()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as endpoint:
            endpoint.bind(str(self.store._key_path))
            self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)

    def test_actual_character_device_rejected_before_read(self):
        if os.name != 'posix': self.skipTest('POSIX null device')
        with self.tracked_descriptors(), patch('tradingbot_broker.paper_enrollment.os.read') as read:
            with self.assertRaises(ValueError): _read_regular_file(Path('/dev/null'), 4096)
        read.assert_not_called()


class DescriptorReadTests(DescriptorFixture):
    def test_regular_authenticated_record_and_key_still_load(self):
        record = self.enroll()
        with self.tracked_descriptors():
            self.assertEqual(self.store._read_record(), record)
            self.assertEqual(len(self.store._read_key()), 32)
            self.assertIs(self.store._match_account(ACCOUNT), S.MATCHED)
            self.assertIs(self.store._match_account(OTHER_ACCOUNT), S.MISMATCH)

    def test_missing_record_stays_unenrolled_without_open(self):
        with patch('tradingbot_broker.paper_enrollment.os.open') as opened:
            self.assertIs(self.store._match_account(ACCOUNT), S.UNENROLLED)
        opened.assert_not_called()

    def test_missing_key_stays_invalid_without_recovery(self):
        self.enroll()
        self.store._key_path.unlink()
        with self.tracked_descriptors():
            self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)
        self.assertFalse(self.store._key_path.exists())

    def test_malformed_record_closes_descriptor_before_parse_error(self):
        self.enroll()
        self.path.write_bytes(b'{')
        with self.tracked_descriptors():
            self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)

    def test_malformed_key_closes_descriptor_and_is_invalid(self):
        self.enroll()
        self.store._key_path.write_bytes(b'POSIX\0truncated')
        with self.tracked_descriptors():
            self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)

    def test_record_size_limit_rejected_before_read(self):
        self.enroll()
        self.path.write_bytes(b'x' * 4097)
        with self.tracked_descriptors(), patch('tradingbot_broker.paper_enrollment.os.read') as read:
            self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)
        read.assert_not_called()

    def test_key_size_limit_rejected_before_read(self):
        self.enroll()
        self.store._key_path.write_bytes(b'x' * 8193)
        with self.tracked_descriptors(), patch('tradingbot_broker.paper_enrollment.os.read') as read:
            with self.assertRaises(ReadOnlyError): self.store._read_key()
        read.assert_not_called()

    def test_reads_do_not_reopen_pathname(self):
        self.enroll()
        with patch.object(Path, 'open', side_effect=AssertionError('pathname reopened')), self.tracked_descriptors():
            self.assertIs(self.store._match_account(ACCOUNT), S.MATCHED)

    def test_all_partial_reads_use_validated_descriptor(self):
        self.enroll()
        original = os.read
        seen = []
        def partial(descriptor, size):
            self.assertTrue(stat.S_ISREG(os.fstat(descriptor).st_mode))
            seen.append(descriptor)
            return original(descriptor, min(size, 7))
        with self.tracked_descriptors() as opened, patch('tradingbot_broker.paper_enrollment.os.read', side_effect=partial):
            record = self.store._read_record()
        self.assertTrue(set(seen) <= set(opened))
        self.assertGreater(len(seen), 2)
        self.assertEqual(record['version'], 1)

    def test_fstat_failure_closes_descriptor_and_sanitizes_error(self):
        self.enroll()
        with self.tracked_descriptors(), patch('tradingbot_broker.paper_enrollment.os.fstat', side_effect=OSError(ACCOUNT)):
            with self.assertRaisesRegex(ReadOnlyError, '^INVALID_PAPER_ENROLLMENT$'):
                self.store._read_record()

    def test_read_failure_closes_descriptor_and_sanitizes_error(self):
        self.enroll()
        with self.tracked_descriptors(), patch('tradingbot_broker.paper_enrollment.os.read', side_effect=OSError(ACCOUNT)):
            with self.assertRaisesRegex(ReadOnlyError, '^INVALID_PAPER_ENROLLMENT_KEY$'):
                self.store._read_key()

    def test_open_failure_sanitized_and_no_descriptor_acquired(self):
        self.enroll()
        with patch('tradingbot_broker.paper_enrollment.os.open', side_effect=PermissionError(ACCOUNT)):
            with self.assertRaises(ReadOnlyError) as raised: self.store._read_record()
        self.assertEqual(str(raised.exception), 'INVALID_PAPER_ENROLLMENT')
        self.assertNotIn(ACCOUNT, str(raised.exception))
        self.assertNotIn(str(self.path), str(raised.exception))

    def test_growth_after_fstat_still_bounded(self):
        self.enroll()
        original = os.read
        count = 0
        def grow(descriptor, size):
            nonlocal count
            if count == 0:
                # Append after the descriptor passed its original size check.
                with self.path.open('ab') as stream: stream.write(b'x' * 8192)
            count += 1
            self.assertLessEqual(size, 4096)
            return original(descriptor, size)
        with self.tracked_descriptors(), patch('tradingbot_broker.paper_enrollment.os.read', side_effect=grow):
            self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)
        self.assertLessEqual(count, 2)

    def test_identity_change_between_lstat_and_open_rejected_before_read(self):
        self.enroll()
        replacement = self.path.with_name('replacement.json')
        replacement.write_bytes(self.path.read_bytes())
        original = os.open
        def swap(path, flags):
            replacement.replace(self.path)
            return original(path, flags)
        with patch('tradingbot_broker.paper_enrollment.os.open', side_effect=swap), \
             patch('tradingbot_broker.paper_enrollment.os.read') as read:
            self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)
        read.assert_not_called()

    def test_symlink_swap_blocked_when_nofollow_unavailable(self):
        self.enroll()
        original = os.open
        def swap(path, flags):
            target = self.path.with_name('original.json')
            self.path.rename(target)
            self.path.symlink_to(target)
            return original(path, flags)
        with patch('tradingbot_broker.paper_enrollment.os.O_NOFOLLOW', 0, create=True), \
             patch('tradingbot_broker.paper_enrollment.os.open', side_effect=swap), \
             patch('tradingbot_broker.paper_enrollment.os.read') as read:
            self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)
        read.assert_not_called()

    def test_posix_acquisition_flags_and_noninheritance(self):
        if os.name != 'posix': self.skipTest('POSIX flags')
        self.enroll()
        original = os.open
        def flags_checked(path, flags):
            self.assertTrue(flags & os.O_NONBLOCK)
            for name in ('O_NOFOLLOW', 'O_CLOEXEC'):
                value = getattr(os, name, 0)
                if value: self.assertTrue(flags & value)
            descriptor = original(path, flags)
            self.assertFalse(os.get_inheritable(descriptor))
            return descriptor
        with patch('tradingbot_broker.paper_enrollment.os.open', side_effect=flags_checked):
            self.assertIs(self.store._match_account(ACCOUNT), S.MATCHED)

    def test_posix_missing_nonblocking_flag_fails_before_open(self):
        if os.name != 'posix': self.skipTest('POSIX flags')
        self.enroll()
        with patch('tradingbot_broker.paper_enrollment.os.O_NONBLOCK', None), \
             patch('tradingbot_broker.paper_enrollment.os.open') as opened:
            self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)
        opened.assert_not_called()

    def test_posix_zero_nonblocking_flag_fails_before_open(self):
        if os.name != 'posix': self.skipTest('POSIX flags')
        self.enroll()
        with patch('tradingbot_broker.paper_enrollment.os.O_NONBLOCK', 0), \
             patch('tradingbot_broker.paper_enrollment.os.open') as opened:
            self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)
        opened.assert_not_called()

    def test_optional_posix_flags_can_be_absent(self):
        self.enroll()
        with patch('tradingbot_broker.paper_enrollment.os.O_NOFOLLOW', 0, create=True), \
             patch('tradingbot_broker.paper_enrollment.os.O_CLOEXEC', 0, create=True):
            self.assertIs(self.store._match_account(ACCOUNT), S.MATCHED)

    def test_windows_regular_protected_key_with_optional_flags_absent(self):
        with patch('tradingbot_broker.paper_enrollment.sys.platform', 'win32'), \
             patch('tradingbot_broker.paper_enrollment._windows_protect', side_effect=lambda data, **kwargs: data[::-1]), \
             patch('tradingbot_broker.paper_enrollment.os.O_NONBLOCK', None, create=True), \
             patch('tradingbot_broker.paper_enrollment.os.O_NOFOLLOW', 0, create=True), \
             patch('tradingbot_broker.paper_enrollment.os.O_CLOEXEC', 0, create=True):
            self.enroll()
            with self.tracked_descriptors():
                self.assertIs(self.store._match_account(ACCOUNT), S.MATCHED)
        self.assertTrue(self.store._key_path.read_bytes().startswith(b'DPAPI\0'))

    def test_windows_protection_parse_failure_still_sanitized(self):
        with patch('tradingbot_broker.paper_enrollment.sys.platform', 'win32'), \
             patch('tradingbot_broker.paper_enrollment._windows_protect', side_effect=lambda data, **kwargs: data[::-1]):
            self.enroll()
        with patch('tradingbot_broker.paper_enrollment.sys.platform', 'win32'), \
             patch('tradingbot_broker.paper_enrollment._windows_protect', side_effect=OSError(ACCOUNT)), self.tracked_descriptors():
            self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)


def descriptor_type_test(kind):
    def test(self):
        self.enroll()
        original = os.fstat
        def unsafe_type(descriptor):
            evidence = original(descriptor)
            return SimpleNamespace(st_mode=kind | 0o600, st_size=evidence.st_size)
        with self.tracked_descriptors(), patch('tradingbot_broker.paper_enrollment.os.fstat', side_effect=unsafe_type), \
             patch('tradingbot_broker.paper_enrollment.os.read') as read:
            self.assertIs(self.store._match_account(ACCOUNT), S.INVALID)
        read.assert_not_called()
    return test


for label, kind in (('fifo', stat.S_IFIFO), ('socket', stat.S_IFSOCK), ('directory', stat.S_IFDIR),
                    ('character', stat.S_IFCHR), ('block', stat.S_IFBLK), ('unknown', 0)):
    setattr(DescriptorReadTests, 'test_descriptor_rejects_' + label, descriptor_type_test(kind))


if __name__ == '__main__':
    unittest.main()
