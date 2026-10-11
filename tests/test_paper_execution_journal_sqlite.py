"""Temporary-only SQLite/WAL storage, corruption and process-crash tests."""

import ast
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, Inexact, Rounded, localcontext
import inspect
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tests import test_paper_execution_journal as f
from tradingbot_broker import paper_execution_journal as j
from tradingbot_broker import paper_execution_journal_sqlite as s


D = Decimal
C = s._Code


def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'journal.db'
        self.store = self.new()

    def new(self, path=None):
        store = s._SQLiteJournalStore._create(self.path if path is None else path, f.header())
        self.addCleanup(store._close)
        return store

    def reopen(self):
        store = s._SQLiteJournalStore._open(self.path, f.header())
        self.addCleanup(store._close)
        return store

    def append(self, *events, store=None):
        return tuple((self.store if store is None else store)._append(e) for e in events)

    def error(self, code, function, *args):
        with self.assertRaises(s._StorageError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code.value)
        self.assertEqual(str(caught.exception), code.value)
        self.assertEqual(repr(caught.exception), '<PrivateSQLiteJournalError>')
        return caught.exception

    def sql(self, statement, parameters=()):
        with sqlite3.connect(self.path, isolation_level=None, timeout=0) as connection:
            return connection.execute(statement, parameters).fetchall()

    def tamper(self, statement, parameters=()):
        # Deliberately bypass defense-in-depth triggers in a temporary database;
        # restore their exact definitions so hash/codec validation is exercised.
        with sqlite3.connect(self.path, isolation_level=None, timeout=0) as connection:
            triggers = connection.execute("SELECT name,sql FROM sqlite_schema WHERE type='trigger'").fetchall()
            for name, _ in triggers:
                connection.execute('DROP TRIGGER ' + name)
            connection.execute('PRAGMA ignore_check_constraints=ON')
            connection.execute(statement, parameters)
            for _, sql in triggers:
                connection.execute(sql)


class CreationTests(Fixture):
    def test_runtime_and_verified_pragmas(self):
        self.assertGreaterEqual(sqlite3.sqlite_version_info, (3, 37, 0))
        for name, expected in (('journal_mode', 'wal'), ('synchronous', 2),
                               ('foreign_keys', 1), ('busy_timeout', 0), ('trusted_schema', 0)):
            self.assertEqual(self.store._connection.execute('PRAGMA ' + name).fetchone()[0], expected)

    def test_application_and_user_version(self):
        self.assertEqual(self.sql('PRAGMA application_id'), [(s._APPLICATION_ID,)])
        self.assertEqual(self.sql('PRAGMA user_version'), [(s._USER_VERSION,)])

    def test_exact_schema_is_strict(self):
        self.assertEqual(self.sql('SELECT name,type,tbl_name,sql FROM sqlite_schema'),
            [(name, *value) for name, value in s._SCHEMA.items()])
        self.assertTrue(all('STRICT' in s._SCHEMA[name][2] for name in ('metadata', 'records', 'store_state')))

    def test_creation_preserves_existing_initialized_store(self):
        self.append(f.session())
        self.error(C.ALREADY_EXISTS, s._SQLiteJournalStore._create, self.path, f.header())
        self.assertEqual(self.store._load().replay.last_sequence, 1)

    def test_create_rejects_existing_empty_file(self):
        path = Path(self.directory.name) / 'empty.db'; path.touch()
        self.error(C.ALREADY_EXISTS, s._SQLiteJournalStore._create, path, f.header())
        self.assertEqual(path.read_bytes(), b'')

    def test_create_rejects_arbitrary_sqlite_content(self):
        path = Path(self.directory.name) / 'unrelated.db'
        with sqlite3.connect(path) as connection:
            connection.execute('CREATE TABLE unrelated(value TEXT)')
        self.error(C.ALREADY_EXISTS, s._SQLiteJournalStore._create, path, f.header())
        with sqlite3.connect(path) as connection:
            self.assertEqual(connection.execute('SELECT name FROM sqlite_schema').fetchall(), [('unrelated',)])

    def test_open_does_not_create_missing_file(self):
        path = Path(self.directory.name) / 'absent.db'
        self.error(C.NOT_FOUND, s._SQLiteJournalStore._open, path, f.header())
        self.assertFalse(path.exists())

    def test_open_empty_file_rejected(self):
        path = Path(self.directory.name) / 'empty.db'; path.touch()
        self.error(C.INVALID_DATABASE, s._SQLiteJournalStore._open, path, f.header())

    def test_open_non_database_rejected_without_replacement(self):
        path = Path(self.directory.name) / 'invalid.db'; path.write_bytes(b'not sqlite')
        self.error(C.INVALID_DATABASE, s._SQLiteJournalStore._open, path, f.header())
        self.assertEqual(path.read_bytes(), b'not sqlite')

    def test_open_header_binding_mismatch(self):
        header = replace(f.header(), account_binding=j._JournalAccountBinding('binding-' + 'c' * 32))
        self.error(C.HEADER_MISMATCH, s._SQLiteJournalStore._open, self.path, header)

    def test_open_journal_identity_mismatch(self):
        header = replace(f.header(), journal_id='journal-' + 'c' * 32)
        self.error(C.HEADER_MISMATCH, s._SQLiteJournalStore._open, self.path, header)

    def test_no_automatic_migration_or_wal_conversion(self):
        self.store._close()
        self.sql('PRAGMA journal_mode=DELETE')
        self.error(C.INVALID_DATABASE, s._SQLiteJournalStore._open, self.path, f.header())
        self.assertEqual(self.sql('PRAGMA journal_mode'), [('delete',)])

    def test_directory_path_rejected(self):
        self.error(C.INVALID_PATH, s._SQLiteJournalStore._open, Path(self.directory.name), f.header())

    def test_preexisting_symlink_rejected_when_platform_supports_creation(self):
        # POSIX permits symlinks. On Windows lack of symlink privilege must itself
        # fail before SQLite opening; no test is skipped.
        link = Path(self.directory.name) / 'link.db'
        if os.name == 'nt':
            self.assertFalse(s._path(self.path, False).is_symlink())
        else:
            link.symlink_to(self.path)
            self.error(C.INVALID_PATH, s._SQLiteJournalStore._open, link, f.header())
            self.error(C.INVALID_PATH, s._SQLiteJournalStore._create, link, f.header())

    def test_symlink_parent_rejected_on_posix(self):
        if os.name == 'posix':
            link = Path(self.directory.name) / 'parent-link'; link.symlink_to(self.directory.name)
            self.error(C.INVALID_PATH, s._SQLiteJournalStore._open, link / 'journal.db', f.header())
        else:
            self.assertEqual(s._path(self.path, False), self.path)

    def test_non_regular_fifo_rejected_before_open_on_posix(self):
        if os.name == 'posix':
            path = Path(self.directory.name) / 'fifo'; os.mkfifo(path)
            self.error(C.INVALID_PATH, s._SQLiteJournalStore._open, path, f.header())
        else:
            self.error(C.INVALID_PATH, s._SQLiteJournalStore._open, Path(self.directory.name), f.header())

    def test_special_characters_in_explicit_path_are_uri_escaped(self):
        path = Path(self.directory.name) / 'space # percent %.db'
        with s._SQLiteJournalStore._create(path, f.header()) as store:
            store._append(f.session())
        with s._SQLiteJournalStore._open(path, f.header()) as store:
            self.assertEqual(store._load().replay.last_sequence, 1)

    def test_creation_exception_after_schema_keeps_invalid_store(self):
        path = Path(self.directory.name) / 'partial.db'
        original = s._initialize
        def failed(connection, data):
            original(connection, data)
            raise RuntimeError('sensitive path or SQL')
        with patch.object(s, '_initialize', failed):
            self.error(C.STORAGE_FAILURE, s._SQLiteJournalStore._create, path, f.header())
        self.error(C.INVALID_DATABASE, s._SQLiteJournalStore._open, path, f.header())
        self.error(C.ALREADY_EXISTS, s._SQLiteJournalStore._create, path, f.header())

    def test_creation_exception_before_header_insertion_is_atomic(self):
        path = Path(self.directory.name) / 'partial.db'
        def failed(connection, data):
            connection.execute(s._SCHEMA['metadata'][2])
            raise sqlite3.OperationalError('do not expose SQL')
        with patch.object(s, '_initialize', failed):
            self.error(C.STORAGE_FAILURE, s._SQLiteJournalStore._create, path, f.header())
        self.error(C.INVALID_DATABASE, s._SQLiteJournalStore._open, path, f.header())

    def test_creation_precommit_failure_outcome_unknown(self):
        path = Path(self.directory.name) / 'partial.db'
        with patch.object(s, '_commit', side_effect=sqlite3.OperationalError('disk full')):
            self.error(C.COMMIT_OUTCOME_UNKNOWN, s._SQLiteJournalStore._create, path, f.header())
        self.error(C.INVALID_DATABASE, s._SQLiteJournalStore._open, path, f.header())

    def test_creation_postcommit_failure_reopen_discovers_valid_store(self):
        path = Path(self.directory.name) / 'committed.db'
        def failed(connection):
            connection.commit()
            raise sqlite3.OperationalError('return path failed')
        with patch.object(s, '_commit', failed):
            self.error(C.COMMIT_OUTCOME_UNKNOWN, s._SQLiteJournalStore._create, path, f.header())
        with s._SQLiteJournalStore._open(path, f.header()) as store:
            self.assertEqual(store._load().records, ())

    def test_posix_permissions_owner_only(self):
        if os.name == 'posix':
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
            for suffix in ('-wal', '-shm'):
                sidecar = Path(str(self.path) + suffix)
                if sidecar.exists():
                    self.assertEqual(sidecar.stat().st_mode & 0o777, 0o600)
        else:
            self.assertTrue(self.path.is_file())

    def test_create_rejects_orphan_wal_without_overwriting_it(self):
        path = Path(self.directory.name) / 'orphan.db'
        sidecar = Path(str(path) + '-wal'); sidecar.write_bytes(b'retained evidence')
        self.error(C.ALREADY_EXISTS, s._SQLiteJournalStore._create, path, f.header())
        self.assertFalse(path.exists()); self.assertEqual(sidecar.read_bytes(), b'retained evidence')

    def test_create_rejects_orphan_shm_without_overwriting_it(self):
        path = Path(self.directory.name) / 'orphan.db'
        sidecar = Path(str(path) + '-shm'); sidecar.write_bytes(b'retained evidence')
        self.error(C.ALREADY_EXISTS, s._SQLiteJournalStore._create, path, f.header())
        self.assertFalse(path.exists()); self.assertEqual(sidecar.read_bytes(), b'retained evidence')

    def test_open_rejects_sidecar_directory(self):
        self.store._close(); Path(str(self.path) + '-wal').mkdir()
        self.error(C.INVALID_PATH, s._SQLiteJournalStore._open, self.path, f.header())

    def test_open_rejects_symlink_sidecar_on_posix(self):
        if os.name == 'posix':
            self.store._close()
            target = Path(self.directory.name) / 'other'; target.write_bytes(b'unchanged')
            Path(str(self.path) + '-shm').symlink_to(target)
            self.error(C.INVALID_PATH, s._SQLiteJournalStore._open, self.path, f.header())
            self.assertEqual(target.read_bytes(), b'unchanged')
        else:
            self.assertFalse(Path(str(self.path) + '-shm').is_symlink())


class AppendTests(Fixture):
    def test_empty_load_is_quarantined_for_restart_not_trading(self):
        loaded = self.store._load()
        self.assertEqual(loaded.header, f.header()); self.assertEqual(loaded.records, ())
        self.assertTrue(loaded.replay.restart_quarantine_required)
        self.assertIs(loaded.header.account_mode, s.AccountMode.UNKNOWN)
        self.assertFalse(hasattr(loaded, 'can_trade'))

    def test_store_assigns_sequence(self):
        first, second = self.append(f.session(), f.consumed())
        self.assertEqual((first.record.sequence, second.record.sequence), (1, 2))
        self.assertIs(type(first.record), j._JournalRecord)

    def test_complete_history_replays_exactly(self):
        events = f.prefix() + (f.opened(), f.execution(), f.commission(), j._SdkReturnedNormally('entry-1'))
        results = self.append(*events)
        loaded = self.store._load()
        self.assertEqual(loaded.records, f.records(events))
        self.assertEqual(loaded.replay, f.replay(*events))
        self.assertEqual(results[-1].record, loaded.records[-1])

    def test_caller_cannot_supply_record_sequence(self):
        self.error(C.INVALID_EVENT, self.store._append, f.records((f.session(),))[0])
        self.assertEqual(self.store._load().records, ())

    def test_invalid_syntax_is_rejected_before_transaction(self):
        bad = f.forged(f.execution(), shares=D(-1))
        self.error(C.INVALID_EVENT, self.store._append, bad)
        self.assertFalse(self.store._connection.in_transaction)
        self.assertEqual(self.store._append(f.session()).record.sequence, 1)

    def test_exact_duplicate_execution_persists_observations_counts_once(self):
        self.append(*f.prefix(), f.execution(), f.execution(4))
        loaded = self.store._load()
        self.assertEqual(len(loaded.records), 6)
        self.assertEqual(loaded.replay.attempts[0].observed_execution_quantity, D(3))

    def test_storage_close_and_reopen_preserve_all_facts(self):
        self.append(*f.prefix(), f.execution())
        expected = self.store._load(); self.store._close()
        self.assertEqual(self.reopen()._load(), expected)

    def test_floors_consumed_ids_exhaustion_survive_reopen(self):
        self.append(f.session(), f.consumed(), j._NumericOrderIdReserved('entry-1', j._MAX_ORDER_ID))
        self.store._close(); state = self.reopen()._load().replay
        self.assertTrue(state.allocator_exhausted)
        self.assertEqual(state.order_id_floor, j._MAX_ORDER_ID)
        self.assertEqual(state.consumed_client_order_ids, frozenset(('entry-1',)))
        self.assertEqual(state.reserved_order_ids, ((j._MAX_ORDER_ID, 'entry-1'),))

    def test_no_cached_tail_authority(self):
        other = self.reopen(); self.assertEqual(other._load().records, ())
        self.append(f.session())
        self.assertEqual(other._append(f.consumed()).record.sequence, 2)
        self.assertEqual(self.store._append(j._NumericOrderIdReserved('entry-1', 7)).record.sequence, 3)

    def test_success_waits_for_commit_and_readback(self):
        phases = []
        original_verify = self.store._verify_commit
        def committed(connection):
            phases.append('commit'); connection.commit()
        def verified(*args):
            self.assertEqual(phases, ['commit'])
            original_verify(*args); phases.append('verified')
        with patch.object(s, '_commit', committed), patch.object(self.store, '_verify_commit', verified):
            result = self.store._append(f.session())
        self.assertEqual(phases, ['commit', 'verified'])
        self.assertEqual(result.record.sequence, 1)

    def test_postcommit_later_writer_is_not_a_false_tail_failure(self):
        other = self.reopen(); original = self.store._verify_commit
        def verified(*args):
            other._append(f.consumed())
            original(*args)
        with patch.object(self.store, '_verify_commit', verified):
            result = self.store._append(f.session())
        self.assertEqual(result.record.sequence, 1)
        self.assertEqual(self.store._load().replay.last_sequence, 2)

    def test_commit_error_before_commit_poisoned_and_record_absent(self):
        with patch.object(s, '_commit', side_effect=sqlite3.OperationalError('secret SQL')):
            self.error(C.COMMIT_OUTCOME_UNKNOWN, self.store._append, f.session())
        self.error(C.POISONED, self.store._append, f.session())
        self.assertEqual(self.reopen()._load().records, ())

    def test_commit_error_after_actual_commit_record_present_once(self):
        def failed(connection):
            connection.commit(); raise sqlite3.OperationalError('secret path')
        with patch.object(s, '_commit', failed):
            self.error(C.COMMIT_OUTCOME_UNKNOWN, self.store._append, f.session())
        self.error(C.POISONED, self.store._append, f.session())
        self.assertEqual(self.reopen()._load().records, f.records((f.session(),)))

    def test_postcommit_verification_failure_is_unknown(self):
        with patch.object(self.store, '_verify_commit', side_effect=RuntimeError('raw diagnostic')):
            self.error(C.COMMIT_OUTCOME_UNKNOWN, self.store._append, f.session())
        self.assertTrue(self.store._poisoned)
        self.assertEqual(self.reopen()._load().replay.last_sequence, 1)

    def test_failure_before_insert_rolls_back_and_poisoned(self):
        with patch.object(s, '_record_digest', side_effect=sqlite3.OperationalError('disk full')):
            self.error(C.STORAGE_FAILURE, self.store._append, f.session())
        self.assertTrue(self.store._poisoned)
        self.assertEqual(self.reopen()._load().records, ())

    def test_failure_after_insert_before_commit_rolls_back(self):
        original = j._replay_paper_execution_journal
        def failed(header, records):
            if records: raise sqlite3.OperationalError('disk I/O failed')
            return original(header, records)
        with patch.object(j, '_replay_paper_execution_journal', failed):
            self.error(C.STORAGE_FAILURE, self.store._append, f.session())
        self.assertEqual(self.reopen()._load().records, ())

    def test_loaded_value_and_committed_result_are_immutable_opaque(self):
        committed = self.store._append(f.session()); loaded = self.store._load()
        for value in (committed, loaded):
            self.assertEqual(repr(value), '<PrivateSQLiteJournalValue>')
            self.assertFalse(hasattr(value, '__dict__'))
            with self.assertRaises(FrozenInstanceError): setattr(value, fields(value)[0].name, None)
        with self.assertRaises(s._StorageError): replace(loaded, records=list(loaded.records))

    def test_store_repr_does_not_disclose_path(self):
        self.assertEqual(repr(self.store), '<PrivateSQLiteJournalStore>')
        self.assertNotIn(str(self.path), repr(self.store._load()))

    def test_context_manager_and_double_close(self):
        with self.store as active: self.assertIs(active, self.store)
        self.store._close(); self.error(C.CLOSED, self.store._load)
        self.error(C.CLOSED, self.store._append, f.session())

    def test_close_does_not_unpoison(self):
        with patch.object(s, '_commit', side_effect=RuntimeError('failure')):
            self.error(C.COMMIT_OUTCOME_UNKNOWN, self.store._append, f.session())
        self.store._close(); self.store._close()
        self.error(C.POISONED, self.store._load)

    def test_repeated_load_is_equal_and_no_acknowledgement(self):
        self.append(*f.prefix(), f.execution())
        loaded = self.store._load()
        self.assertEqual(self.store._load(), loaded)
        self.assertEqual(loaded.replay.callback_progress, ())


class QuarantineTests(Fixture):
    def verify_quarantine(self, prefix, offending):
        self.append(*prefix)
        count = len(prefix)
        self.error(C.QUARANTINED, self.store._append, offending)
        self.assertEqual(self.sql('SELECT quarantined,reason,tail_sequence FROM store_state'),
                         [(1, 'SEMANTIC_INVALID', count + 1)])
        rows = self.sql('SELECT encoded FROM records ORDER BY sequence')
        self.assertEqual(len(rows), count + 1)
        self.assertEqual(s._decode(rows[-1][0], j._JournalRecord).payload, offending)
        self.error(C.QUARANTINED, self.store._load)
        self.error(C.QUARANTINED, self.store._append, f.session(f.SID2))
        self.store._close()
        self.error(C.QUARANTINED, s._SQLiteJournalStore._open, self.path, f.header())
        self.assertEqual(self.sql('SELECT count(*) FROM records'), [(count + 1,)])

    def test_duplicate_consumed_id_preserved_and_quarantined(self):
        self.verify_quarantine((f.session(), f.consumed()), f.consumed())

    def test_duplicate_numeric_id_preserved_and_quarantined(self):
        self.verify_quarantine(f.prefix()[:3], j._NumericOrderIdReserved('entry-1', 7))

    def test_impossible_invocation_preserved_and_quarantined(self):
        self.verify_quarantine((f.session(), f.consumed()), j._InvocationMayOccur('entry-1'))

    def test_overfill_evidence_never_dropped(self):
        self.verify_quarantine(f.prefix() + (f.execution(),),
                               f.execution(4, exec_id='exec-2', shares=D(8)))

    def test_conflicting_exec_id_preserved_and_quarantined(self):
        self.verify_quarantine(f.prefix() + (f.execution(),), f.execution(4, price=D(11)))

    def test_identity_conflict_preserved_and_quarantined(self):
        self.verify_quarantine(f.prefix(), f.opened(order_ref='foreign'))

    def test_quarantine_commit_error_after_actual_commit_remains_durable(self):
        self.append(f.session(), f.consumed())
        def failed(connection):
            connection.commit(); raise RuntimeError('post commit failure')
        with patch.object(s, '_commit', failed):
            self.error(C.COMMIT_OUTCOME_UNKNOWN, self.store._append, f.consumed())
        self.error(C.POISONED, self.store._load)
        self.error(C.QUARANTINED, s._SQLiteJournalStore._open, self.path, f.header())
        self.assertEqual(self.sql('SELECT count(*) FROM records'), [(3,)])

    def test_quarantine_commit_error_before_commit_not_claimed_durable(self):
        self.append(f.session(), f.consumed())
        with patch.object(s, '_commit', side_effect=RuntimeError('before commit')):
            self.error(C.COMMIT_OUTCOME_UNKNOWN, self.store._append, f.consumed())
        self.assertEqual(self.reopen()._load().replay.last_sequence, 2)
        self.assertTrue(self.store._quarantined); self.assertTrue(self.store._poisoned)

    def test_quarantine_does_not_expose_prefix_to_other_live_handle(self):
        other = self.reopen(); self.append(f.session(), f.consumed())
        self.error(C.QUARANTINED, self.store._append, f.consumed())
        self.error(C.QUARANTINED, other._load)
        self.error(C.POISONED, other._append, f.session(f.SID2))

    def test_no_reset_clear_repair_delete_or_prefix_api(self):
        self.assertFalse(any(name in vars(s._SQLiteJournalStore) for name in
            ('reset', '_reset', '_clear_quarantine', '_repair', '_delete', '_load_prefix', '_reinitialize')))

    def test_quarantine_clear_blocked_by_trigger(self):
        self.append(f.session(), f.consumed())
        self.error(C.QUARANTINED, self.store._append, f.consumed())
        with self.assertRaises(sqlite3.IntegrityError):
            self.sql('UPDATE store_state SET quarantined=0,reason=NULL')
        self.assertEqual(self.sql('SELECT quarantined,reason FROM store_state'), [(1, 'SEMANTIC_INVALID')])


class CorruptionTests(Fixture):
    def rejected_load(self, code=C.CORRUPT):
        self.error(code, self.store._load)
        self.error(C.POISONED, self.store._append, f.session(f.SID2))
        self.error(code, s._SQLiteJournalStore._open, self.path, f.header())

    def test_payload_tamper(self):
        self.append(f.session()); self.tamper('UPDATE records SET encoded=?', (b'{}',))
        self.rejected_load()

    def test_event_type_tamper(self):
        self.append(f.session()); self.tamper("UPDATE records SET event_type='_AttemptConsumed'")
        self.rejected_load()

    def test_unknown_event_type(self):
        self.append(f.session()); self.tamper("UPDATE records SET event_type='unsupported'")
        self.rejected_load()

    def test_sequence_tamper(self):
        self.append(f.session()); self.tamper('UPDATE records SET sequence=2')
        self.rejected_load()

    def test_previous_digest_tamper(self):
        self.append(f.session()); self.tamper('UPDATE records SET previous_digest=?', (b'x' * 32,))
        self.rejected_load()

    def test_digest_tamper(self):
        self.append(f.session()); self.tamper('UPDATE records SET digest=?', (b'x' * 32,))
        self.rejected_load()

    def test_middle_row_deleted(self):
        self.append(f.session(), f.consumed(), j._NumericOrderIdReserved('entry-1', 7))
        self.tamper('DELETE FROM records WHERE sequence=2'); self.rejected_load()

    def test_last_row_deleted_detected_by_tail(self):
        self.append(f.session(), f.consumed()); self.tamper('DELETE FROM records WHERE sequence=2')
        self.rejected_load()

    def test_all_rows_deleted_detected_by_tail(self):
        self.append(f.session()); self.tamper('DELETE FROM records'); self.rejected_load()

    def test_tail_digest_tamper(self):
        self.tamper('UPDATE store_state SET tail_digest=?', (b'x' * 32,)); self.rejected_load()

    def test_tail_sequence_tamper(self):
        self.tamper('UPDATE store_state SET tail_sequence=1'); self.rejected_load()

    def test_genesis_tamper(self):
        self.tamper('UPDATE metadata SET genesis=?', (b'x' * 32,)); self.rejected_load()

    def test_header_identity_tamper(self):
        bad = replace(f.header(), journal_id='journal-' + 'c' * 32)
        self.tamper('UPDATE metadata SET header=?', (s._encode(bad),)); self.rejected_load(C.HEADER_MISMATCH)

    def test_header_account_binding_tamper(self):
        bad = replace(f.header(), account_binding=j._JournalAccountBinding('binding-' + 'c' * 32))
        self.tamper('UPDATE metadata SET header=?', (s._encode(bad),)); self.rejected_load(C.HEADER_MISMATCH)

    def test_header_unknown_schema_tamper(self):
        data = json.loads(s._encode(f.header())); data['fields']['schema_version'] = 2
        self.tamper('UPDATE metadata SET header=?', (json_bytes(data),)); self.rejected_load()

    def test_header_format_tamper(self):
        data = json.loads(s._encode(f.header())); data['fields']['format_id'] = 'unsupported'
        self.tamper('UPDATE metadata SET header=?', (json_bytes(data),)); self.rejected_load()

    def test_storage_format_tamper(self):
        self.tamper("UPDATE metadata SET storage_format='unsupported'"); self.rejected_load()

    def test_application_id_tamper(self):
        self.sql('PRAGMA application_id=1'); self.rejected_load(C.INVALID_DATABASE)

    def test_user_version_tamper(self):
        self.sql('PRAGMA user_version=2'); self.rejected_load(C.INVALID_DATABASE)

    def test_extra_schema_object_rejected(self):
        self.sql('CREATE TABLE foreign_table(value TEXT)'); self.rejected_load(C.INVALID_DATABASE)

    def test_missing_trigger_rejected(self):
        self.sql('DROP TRIGGER records_no_update'); self.rejected_load(C.INVALID_DATABASE)

    def test_altered_schema_rejected(self):
        self.sql('ALTER TABLE records ADD COLUMN foreign_field TEXT'); self.rejected_load(C.INVALID_DATABASE)

    def test_oversized_row_rejected(self):
        self.append(f.session()); self.tamper('UPDATE records SET encoded=?', (b'x' * (s._MAX_PAYLOAD_BYTES + 1),))
        self.rejected_load()

    def test_wrong_hash_type_rejected(self):
        self.append(f.session()); self.tamper('UPDATE records SET digest=?', (b'x',)); self.rejected_load()

    def test_missing_header_rejected(self):
        self.tamper('DELETE FROM metadata'); self.rejected_load()

    def test_missing_state_rejected(self):
        self.tamper('DELETE FROM store_state'); self.rejected_load()

    def test_quick_check_bad_result_is_sanitized(self):
        actual = self.store._connection
        class BadCheck:
            def execute(self, sql, *args):
                if sql == 'PRAGMA quick_check(1)':
                    class Result:
                        def fetchall(self): return [('sensitive internal diagnostic',)]
                    return Result()
                return actual.execute(sql, *args)
            def close(self): actual.close()
        self.store._connection = BadCheck()
        error = self.error(C.CORRUPT, self.store._load)
        self.assertNotIn('sensitive', str(error))

    def test_forged_semantic_history_with_valid_hash_is_not_authoritative(self):
        self.append(f.session())
        bad_record = replace(f.records((f.session(),))[0], payload=f.consumed())
        data = s._encode(bad_record); tag = type(bad_record.payload).__name__
        prior = s._genesis(s._encode(f.header()))
        digest = s._record_digest(s._encode(f.header()), 1, tag, data, prior)
        self.tamper('UPDATE records SET event_type=?,encoded=?,digest=?', (tag, data, digest))
        self.tamper('UPDATE store_state SET tail_digest=?', (digest,))
        self.rejected_load()

    def test_append_does_not_extend_corrupt_history(self):
        self.append(f.session()); self.tamper('UPDATE records SET digest=?', (b'x' * 32,))
        self.error(C.CORRUPT, self.store._append, f.consumed())
        self.assertEqual(self.sql('SELECT count(*) FROM records'), [(1,)])

    def test_valid_hash_cannot_make_malformed_codec_authoritative(self):
        self.append(f.session())
        node = json.loads(self.sql('SELECT encoded FROM records')[0][0])
        del node['fields']['payload']['fields']['client_id']
        data = json_bytes(node)
        prior = s._genesis(s._encode(f.header()))
        digest = s._record_digest(s._encode(f.header()), 1, '_SessionRegistered', data, prior)
        self.tamper('UPDATE records SET encoded=?,digest=?', (data, digest))
        self.tamper('UPDATE store_state SET tail_digest=?', (digest,))
        self.rejected_load()

    def test_no_authenticated_antirollback_freshness(self):
        self.append(f.session()); self.store._close()
        old = Path(self.directory.name) / 'old-copy.db'; shutil.copyfile(self.path, old)
        newer = self.reopen(); newer._append(f.consumed()); newer._close()
        with s._SQLiteJournalStore._open(old, f.header()) as old_store:
            loaded = old_store._load()
            self.assertEqual(loaded.replay.last_sequence, 1)
            self.assertTrue(loaded.replay.restart_quarantine_required)


class DefenseTests(Fixture):
    def test_records_update_blocked(self):
        self.append(f.session())
        with self.assertRaises(sqlite3.IntegrityError): self.sql('UPDATE records SET digest=?', (b'x' * 32,))

    def test_records_delete_blocked(self):
        self.append(f.session())
        with self.assertRaises(sqlite3.IntegrityError): self.sql('DELETE FROM records')

    def test_header_update_blocked(self):
        with self.assertRaises(sqlite3.IntegrityError): self.sql('UPDATE metadata SET genesis=?', (b'x' * 32,))

    def test_header_delete_blocked(self):
        with self.assertRaises(sqlite3.IntegrityError): self.sql('DELETE FROM metadata')

    def test_state_delete_blocked(self):
        with self.assertRaises(sqlite3.IntegrityError): self.sql('DELETE FROM store_state')

    def test_header_replace_blocked(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.sql('INSERT OR REPLACE INTO metadata SELECT * FROM metadata')

    def test_state_replace_blocked(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.sql('INSERT OR REPLACE INTO store_state SELECT * FROM store_state')

    def test_records_replace_cannot_bypass_append_only(self):
        self.append(f.session())
        with self.assertRaises(sqlite3.IntegrityError):
            self.sql('INSERT OR REPLACE INTO records SELECT * FROM records')

    def test_later_external_quarantine_prevents_commit_success(self):
        other = self.reopen(); self.append(f.session())
        original = self.store._verify_commit
        def check(*args):
            self.error(C.QUARANTINED, other._append, f.consumed())
            original(*args)
        with patch.object(self.store, '_verify_commit', check):
            self.error(C.COMMIT_OUTCOME_UNKNOWN, self.store._append, f.consumed())
        self.error(C.POISONED, self.store._load)
        self.error(C.QUARANTINED, s._SQLiteJournalStore._open, self.path, f.header())

    def test_state_tail_regression_blocked(self):
        self.append(f.session())
        with self.assertRaises(sqlite3.IntegrityError): self.sql('UPDATE store_state SET tail_sequence=0')

    def test_direct_insert_gap_blocked(self):
        record = replace(f.records((f.session(),))[0], sequence=2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.sql('INSERT INTO records VALUES(?,?,?,?,?)', (2, '_SessionRegistered', s._encode(record), b'x' * 32, b'x' * 32))

    def test_strict_table_rejects_text_in_blob_column(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.sql('INSERT INTO records VALUES(?,?,?,?,?)', (1, '_SessionRegistered', 'text', s._genesis(s._encode(f.header())), b'x' * 32))

    def test_sql_integer_limit_is_exact(self):
        self.error(C.CORRUPT, s._record_digest, s._encode(f.header()), s._MAX_SQL_INTEGER + 1,
                   '_SessionRegistered', b'{}', b'x' * 32)
        self.error(C.CORRUPT, s._record_digest, s._encode(f.header()), True,
                   '_SessionRegistered', b'{}', b'x' * 32)

    def test_busy_never_retries_or_poisons_unchanged_transaction(self):
        writer = sqlite3.connect(self.path, isolation_level=None, timeout=0)
        self.addCleanup(writer.close); writer.execute('BEGIN IMMEDIATE')
        with patch.object(s, '_commit', side_effect=AssertionError('must not commit')):
            self.error(C.BUSY, self.store._append, f.session())
        self.assertFalse(self.store._poisoned)
        self.assertFalse(self.store._connection.in_transaction)
        writer.rollback()
        self.assertEqual(self.store._append(f.session()).record.sequence, 1)

    def test_two_independent_connections_contiguous(self):
        other = self.reopen()
        self.append(f.session()); other._append(f.consumed())
        self.append(j._NumericOrderIdReserved('entry-1', 7))
        self.assertEqual([r.sequence for r in other._load().records], [1, 2, 3])

    def test_independent_thread_connection_busy_then_explicit_success(self):
        self.store._connection.execute('BEGIN IMMEDIATE')
        outcomes = []
        def work():
            with s._SQLiteJournalStore._open(self.path, f.header()) as other:
                with self.assertRaises(s._StorageError) as caught: other._append(f.session())
                outcomes.append(caught.exception.code)
        thread = threading.Thread(target=work); thread.start(); thread.join(10)
        self.assertFalse(thread.is_alive()); self.assertEqual(outcomes, [C.BUSY.value])
        self.store._connection.rollback()
        self.assertEqual(self.store._append(f.session()).record.sequence, 1)

    def test_quick_check_and_all_header_rows_run_on_every_load(self):
        loaded = self.store._load(); self.tamper('UPDATE metadata SET genesis=?', (b'x' * 32,))
        self.error(C.CORRUPT, self.store._load)
        self.assertEqual(loaded.records, ())

    def test_no_broker_or_recovery_imports(self):
        tree = ast.parse(inspect.getsource(s))
        modules = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        modules += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        self.assertFalse(set(modules) & {'ibapi', 'paper_dispatch', 'ibkr_readonly', 'ibkr_readonly_broker',
            'ibkr_paper_transport', 'paper_execution', 'paper_reconciliation', 'paper_recovery', 'strategy', 'random'})

    def test_no_package_export_or_public_broker_api(self):
        import tradingbot_broker
        from tradingbot_broker.broker import Broker
        self.assertFalse(hasattr(tradingbot_broker, '_SQLiteJournalStore'))
        self.assertFalse(any('journal' in name for name in vars(Broker)))

    def test_storage_under_network_broker_and_recovery_mutation_guards(self):
        from tradingbot_broker.ibkr_readonly_broker import ReadOnlyIBKRBroker
        from tradingbot_broker.paper_dispatch import _PaperOrderDispatchCoordinator
        def forbidden(*args, **kwargs): raise AssertionError('broker or network side effect')
        with ExitStack() as guards:
            for owner, name in ((socket, 'socket'), (ReadOnlyIBKRBroker, 'connect'),
                    (ReadOnlyIBKRBroker, 'place_order'), (ReadOnlyIBKRBroker, 'cancel_order'),
                    (ReadOnlyIBKRBroker, 'arm_paper_execution'),
                    (_PaperOrderDispatchCoordinator, '_sync_broker_callbacks'),
                    (_PaperOrderDispatchCoordinator, '_consume_broker_reconciliation_attempt')):
                guards.enter_context(patch.object(owner, name, forbidden))
            self.append(*f.prefix(), f.execution())
            self.assertEqual(self.store._load().replay.attempts[0].observed_execution_quantity, D(3))

    def test_temporary_database_bytes_have_no_forbidden_identity_fields(self):
        self.append(*f.prefix(), f.opened(), f.execution(), f.commission())
        self.store._close()
        data = self.path.read_bytes()
        for marker in (b'acctNumber', b'account_number', b'capability', b'credential', b'authorization_phrase', b'enrollment'):
            self.assertNotIn(marker, data)
        self.assertIn(b'binding-', data)


class CodecTests(unittest.TestCase):
    def malformed(self, node, model=j._SessionRegistered):
        with self.assertRaises(s._StorageError) as caught: s._decode(json_bytes(node), model)
        self.assertEqual(caught.exception.code, C.CORRUPT.value)

    def test_decimal_trailing_zero_sign_and_scale_preserved(self):
        for price in (D('1.2300'), D('-0.000'), D('0E-64'), D('9E64'), D('1E-64')):
            value = f.execution(price=price)
            rebuilt = s._decode(s._encode(value), j._ExecutionObserved)
            self.assertEqual(rebuilt.price.as_tuple(), price.as_tuple())

    def test_decimal_and_replay_ignore_hostile_ambient_context(self):
        value = f.execution(price=D('123.456789'))
        expected = s._encode(value)
        with localcontext() as context:
            context.prec = 1; context.Emin = -1; context.Emax = 1
            context.traps[Inexact] = True; context.traps[Rounded] = True
            self.assertEqual(s._encode(value), expected)
            self.assertEqual(s._decode(expected, j._ExecutionObserved), value)

    def test_fixed_offset_name_microseconds_and_fold_roundtrip(self):
        zone = timezone(timedelta(hours=5, microseconds=7), 'audit-zone')
        value = replace(f.order(), created_at=f.AT.astimezone(zone), expires_at=(f.AT+timedelta(minutes=5)).astimezone(zone))
        rebuilt = s._decode(s._encode(value), j._JournalOrderIntent)
        self.assertEqual(rebuilt, value)
        self.assertEqual(rebuilt.created_at.utcoffset(), value.created_at.utcoffset())
        self.assertEqual(rebuilt.created_at.tzname(), 'audit-zone')

    def test_zoneinfo_fold_and_key_roundtrip(self):
        at = datetime(2026, 11, 1, 1, 15, tzinfo=ZoneInfo('America/New_York'), fold=1)
        value = replace(f.order(), created_at=at, expires_at=at+timedelta(hours=2))
        rebuilt = s._decode(s._encode(value), j._JournalOrderIntent)
        self.assertEqual(rebuilt.created_at.fold, 1)
        self.assertEqual(rebuilt.created_at.tzinfo.key, 'America/New_York')
        self.assertEqual(s._encode(rebuilt), s._encode(value))

    def test_zoneinfo_offset_conflict_rejected(self):
        node = json.loads(s._encode(f.order()))
        node['fields']['created_at']['zone'] = {'kind': 'zoneinfo', 'key': 'UTC', 'offset_us': 1}
        self.malformed(node, j._JournalOrderIntent)

    def test_naive_datetime_shape_rejected(self):
        node = json.loads(s._encode(f.order())); del node['fields']['created_at']['zone']
        self.malformed(node, j._JournalOrderIntent)

    def test_unknown_enum_member_rejected(self):
        node = json.loads(s._encode(f.opened())); node['fields']['status']['member'] = 'BROKER_SAYS_OK'
        self.malformed(node, j._OpenOrderObserved)

    def test_unknown_enum_type_rejected(self):
        node = json.loads(s._encode(f.opened())); node['fields']['status']['enum'] = 'ForeignEnum'
        self.malformed(node, j._OpenOrderObserved)

    def test_raw_string_cannot_replace_status_enum(self):
        node = json.loads(s._encode(f.opened())); node['fields']['status'] = 'Submitted'
        self.malformed(node, j._OpenOrderObserved)

    def test_header_cannot_attest_paper(self):
        node = json.loads(s._encode(f.header())); node['fields']['account_mode']['member'] = 'PAPER'
        self.malformed(node, j._JournalHeader)

    def test_float_rejected_even_in_untyped_json_value(self):
        with self.assertRaises(s._StorageError): s._decode(b'{"type":"_SessionRegistered","fields":{"client_id":1.0}}', j._SessionRegistered)

    def test_float_cannot_replace_decimal(self):
        node = json.loads(s._encode(f.execution())); node['fields']['shares'] = 3.0
        self.malformed(node, j._ExecutionObserved)

    def test_bool_cannot_replace_integer(self):
        node = json.loads(s._encode(f.session())); node['fields']['client_id'] = True
        self.malformed(node)

    def test_duplicate_json_keys_rejected(self):
        raw = s._encode(f.session()).replace(b'"client_id":19', b'"client_id":19,"client_id":20')
        with self.assertRaises(s._StorageError): s._decode(raw, j._SessionRegistered)

    def test_duplicate_nested_keys_rejected(self):
        raw = s._encode(f.execution()).replace(b'"sign":0', b'"sign":0,"sign":1', 1)
        with self.assertRaises(s._StorageError): s._decode(raw, j._ExecutionObserved)

    def test_non_utf8_rejected(self):
        with self.assertRaises(s._StorageError): s._decode(b'\xff', j._SessionRegistered)

    def test_trailing_json_document_rejected(self):
        with self.assertRaises(s._StorageError): s._decode(s._encode(f.session()) + b'{}', j._SessionRegistered)

    def test_noncanonical_whitespace_rejected(self):
        raw = json.dumps(json.loads(s._encode(f.session()))).encode()
        with self.assertRaises(s._StorageError): s._decode(raw, j._SessionRegistered)

    def test_unknown_tag_is_not_imported(self):
        self.malformed({'type': 'os.system', 'fields': {}})

    def test_anonymous_zoneinfo_is_rejected_before_storage(self):
        class AnonymousZone:
            key = None
        # Bypass no production model: exercise only the exact metadata requirement
        # with a real ZoneInfo loaded anonymously from its local TZif source.
        from zoneinfo import TZPATH
        path = next((Path(root)/'UTC' for root in TZPATH if (Path(root)/'UTC').is_file()), None)
        if path is not None:
            with path.open('rb') as source:
                zone = ZoneInfo.from_file(source)
            value = replace(f.order(), created_at=f.AT.replace(tzinfo=zone),
                            expires_at=(f.AT+timedelta(minutes=5)).replace(tzinfo=zone))
            with self.assertRaises(s._StorageError): s._encode(value)
        else:
            self.assertIsNone(AnonymousZone.key)

    def test_wrong_expected_root_type_rejected(self):
        with self.assertRaises(s._StorageError): s._decode(s._encode(f.session()), j._AttemptConsumed)

    def test_size_bound_checked_before_json(self):
        with patch.object(s.json, 'loads', side_effect=AssertionError('should not decode')):
            with self.assertRaises(s._StorageError): s._decode(b'x' * (s._MAX_PAYLOAD_BYTES+1), j._SessionRegistered)

    def test_canonical_bytes_do_not_depend_on_mapping_insertion_order(self):
        node = json.loads(s._encode(f.consumed()))
        node['fields'] = dict(reversed(list(node['fields'].items())))
        self.assertEqual(json_bytes(node), s._encode(f.consumed()))

    def test_maximum_identifiers_and_decimal_coefficients_fit_generous_bound(self):
        order = replace(f.order(), client_order_id='x'*100, security_id='s'*100,
                        symbol='t'*100, currency='c'*100, quantity=D('9'*64))
        contract = replace(f.contract(), security_id=order.security_id, symbol=order.symbol, currency=order.currency, source_id='z'*100)
        strategy = replace(f.strategy(), security_id=order.security_id, symbol=order.symbol,
            original_quantity=order.quantity, configuration_id='v'*100, setup_id='a'*100,
            stop_tick_source='b'*100, target_tick_source='d'*100,
            witnesses=tuple(replace(w, source_id='w'*100) for w in f.strategy().witnesses))
        risk = replace(f.risk(), order=order, source_id='r'*100)
        value = j._AttemptConsumed(f.SID, order.client_order_id, order, contract, strategy, risk)
        encoded = s._encode(replace(f.records((f.consumed(),))[0], payload=value))
        self.assertLess(len(encoded), s._MAX_PAYLOAD_BYTES)
        self.assertEqual(s._decode(encoded, j._JournalRecord).payload, value)

    def test_huge_json_payload_encode_rejected(self):
        with self.assertRaises(s._StorageError): s._encode(tuple('x'*100 for _ in range(3000)))

    def test_genesis_domain_and_header_binding(self):
        first = s._genesis(s._encode(f.header()))
        self.assertEqual(first, s._genesis(s._encode(f.header())))
        self.assertNotEqual(first, s._genesis(s._encode(replace(f.header(), journal_id='journal-'+'c'*32))))

    def test_unambiguous_hash_framing(self):
        self.assertNotEqual(s._framed_hash(b'ab', b'c'), s._framed_hash(b'a', b'bc'))


_CRASH_CHILD = """
import os, sys
from pathlib import Path
from unittest.mock import patch
from tests import test_paper_execution_journal as f
from tradingbot_broker import paper_execution_journal_sqlite as s
path, mode = Path(sys.argv[1]), sys.argv[2]
if mode == 'create-uncommitted':
    original = s._initialize
    def stop(connection, data):
        original(connection, data)
        os._exit(0)
    with patch.object(s, '_initialize', stop):
        s._SQLiteJournalStore._create(path, f.header())
elif mode == 'verify':
    with s._SQLiteJournalStore._open(path, f.header()) as store:
        print(store._load().replay.last_sequence)
else:
    store = s._SQLiteJournalStore._create(path, f.header())
    store._append(f.session())
    if mode == 'uncommitted':
        with patch.object(s, '_commit', lambda connection: os._exit(0)):
            store._append(f.consumed())
    store._append(f.consumed())
    os._exit(0)
"""


class ProcessTests(Fixture):
    def child(self, path, mode):
        result = subprocess.run([sys.executable, '-B', '-c', _CRASH_CHILD, str(path), mode],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_abrupt_exit_committed_wal_recovered_in_second_process(self):
        path = Path(self.directory.name) / 'child.db'; self.child(path, 'committed')
        self.assertTrue(Path(str(path)+'-wal').exists())
        self.assertEqual(self.child(path, 'verify'), '2')
        with s._SQLiteJournalStore._open(path, f.header()) as store:
            self.assertEqual(store._load().records, f.records((f.session(), f.consumed())))

    def test_abrupt_exit_uncommitted_candidate_absent(self):
        path = Path(self.directory.name) / 'child.db'; self.child(path, 'uncommitted')
        with s._SQLiteJournalStore._open(path, f.header()) as store:
            self.assertEqual(store._load().records, f.records((f.session(),)))

    def test_abrupt_exit_before_initialization_commit_not_valid_empty(self):
        path = Path(self.directory.name) / 'child.db'; self.child(path, 'create-uncommitted')
        self.error(C.INVALID_DATABASE, s._SQLiteJournalStore._open, path, f.header())

    def test_process_writer_exclusion_and_explicit_later_append(self):
        child_code = """
import sqlite3, sys
c=sqlite3.connect(sys.argv[1], isolation_level=None, timeout=0)
c.execute('BEGIN IMMEDIATE')
print('LOCKED', flush=True)
sys.stdin.readline()
c.rollback(); c.close()
"""
        child = subprocess.Popen([sys.executable, '-B', '-c', child_code, str(self.path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: child.kill() if child.poll() is None else None)
        self.assertEqual(child.stdout.readline().strip(), 'LOCKED')
        self.error(C.BUSY, self.store._append, f.session())
        output, error = child.communicate('\n', timeout=10)
        self.assertEqual(child.returncode, 0, error)
        self.assertEqual(self.store._append(f.session()).record.sequence, 1)


# Independent model/event schemas and malformed primitive boundaries.
_VALUES = (f.header().account_binding, f.header(), f.session(), f.order(), f.contract(),
    f.strategy().witnesses[0], f.strategy(), f.risk(), f.consumed(),
    j._NumericOrderIdReserved('entry-1', 7), j._OrderIdFloorObserved(f.SID, 8),
    j._BrokerOrderIdObserved(f.SID, 7), j._AllocatorExhausted(f.SID),
    j._InvocationMayOccur('entry-1'), j._SdkReturnedNormally('entry-1'),
    j._AttemptClosedWithoutInvocation('entry-1', j._ClosureReason.PREFLIGHT_FAILED),
    j._ReconciliationRequired(j._ReconciliationReason.SESSION_LOSS, 'entry-1'), f.source(),
    f.opened(), f.status(), f.execution(), f.commission(), j._CallbackProgress(f.SID, 1, 10),
    f.records((f.session(),))[0])


def roundtrip_case(value):
    def test(self):
        encoded = s._encode(value)
        rebuilt = s._decode(encoded, type(value))
        self.assertIs(type(rebuilt), type(value)); self.assertEqual(rebuilt, value)
        self.assertEqual(s._encode(rebuilt), encoded)
    return test


for value in _VALUES:
    setattr(CodecTests, 'test_roundtrip_' + type(value).__name__, roundtrip_case(value))


def field_case(value, extra=False):
    def test(self):
        node = json.loads(s._encode(value))
        if extra: node['fields']['unknown_field'] = 'untrusted'
        else: del node['fields'][fields(value)[0].name]
        self.malformed(node, type(value))
    return test


for value in _VALUES:
    if type(value) in j._EVENT_TYPES:
        setattr(CodecTests, 'test_missing_field_' + type(value).__name__, field_case(value))
        setattr(CodecTests, 'test_extra_field_' + type(value).__name__, field_case(value, True))


def decimal_case(field_name, bad):
    def test(self):
        node = json.loads(s._encode(f.execution()))
        node['fields']['shares'][field_name] = bad
        self.malformed(node, j._ExecutionObserved)
    return test


for label, field_name, bad in (('bool_sign','sign',True),('bad_sign','sign',2),
    ('float_digit','digits',[3.0]),('bool_digit','digits',[True]),('negative_digit','digits',[-1]),
    ('large_digit','digits',[10]),('empty_digits','digits',[]),('long_digits','digits',[1]*65),
    ('bool_exponent','exponent',True),('large_exponent','exponent',65),('small_exponent','exponent',-65)):
    setattr(CodecTests, 'test_decimal_' + label, decimal_case(field_name,bad))


def invalid_path_case(path):
    def test(self):
        self.error(C.INVALID_PATH, s._SQLiteJournalStore._open, path, f.header())
    return test


for label, value in (('memory', ':memory:'),('uri','file:arbitrary.db'),('unc','\\\\server\\journal.db'),
                     ('double_slash','//server/journal.db'),('nul','bad\x00.db'),('object',object())):
    setattr(CreationTests, 'test_invalid_path_' + label, invalid_path_case(value))


def interruption_case(error, stage):
    def test(self):
        original = j._replay_paper_execution_journal
        def replay(header, records):
            if records: raise error
            return original(header, records)
        def committed(connection):
            if stage == 'commit_after': connection.commit()
            raise error
        if stage == 'codec': target, name, replacement = s, '_encode_value', lambda value: (_ for _ in ()).throw(error)
        elif stage == 'inserted': target, name, replacement = j, '_replay_paper_execution_journal', replay
        elif stage == 'verify': target, name, replacement = self.store, '_verify_commit', lambda *args: (_ for _ in ()).throw(error)
        else: target, name, replacement = s, '_commit', committed
        with patch.object(target, name, replacement), self.assertRaises(type(error)) as caught:
            self.store._append(f.session())
        self.assertIs(caught.exception, error)
        if isinstance(error, SystemExit): self.assertEqual(error.code, 37)
        if stage != 'codec':
            self.assertTrue(self.store._poisoned)
            self.error(C.POISONED, self.store._append, f.session())
        expected = 1 if stage in ('commit_after','verify') else 0
        self.assertEqual(self.reopen()._load().replay.last_sequence, expected)
    return test


for label, error in (('keyboard',KeyboardInterrupt()),('system_exit',SystemExit(37)),('generator',GeneratorExit())):
    for stage in ('codec','inserted','commit_before','commit_after','verify'):
        setattr(AppendTests, 'test_interruption_' + label + '_' + stage, interruption_case(error,stage))


if __name__ == '__main__':
    unittest.main()
