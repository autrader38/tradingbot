"""Private C3C4A storage. SQLite commit confirmation is never broker authority."""

from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
from zoneinfo import ZoneInfo

from . import paper_execution_journal as journal
from .readonly_models import AccountMode, ReadOnlyError


_APPLICATION_ID = 0x504A4231
_USER_VERSION = 1
_STORE_FORMAT = 'paper-execution-journal-sqlite-v1'
_MAX_PAYLOAD_BYTES = 262_144
_MAX_SQL_INTEGER = (1 << 63) - 1
_DOMAIN = b'paper-execution-journal-sqlite-v1'


class _Code(StrEnum):
    NOT_FOUND = 'NOT_FOUND'
    ALREADY_EXISTS = 'ALREADY_EXISTS'
    INVALID_PATH = 'INVALID_PATH'
    INVALID_DATABASE = 'INVALID_DATABASE'
    HEADER_MISMATCH = 'HEADER_MISMATCH'
    CORRUPT = 'CORRUPT'
    QUARANTINED = 'QUARANTINED'
    BUSY = 'BUSY'
    COMMIT_OUTCOME_UNKNOWN = 'COMMIT_OUTCOME_UNKNOWN'
    CLOSED = 'CLOSED'
    POISONED = 'POISONED'
    INVALID_EVENT = 'INVALID_EVENT'
    STORAGE_FAILURE = 'STORAGE_FAILURE'


class _StorageError(ReadOnlyError):
    def __init__(self, code):
        if type(code) is not _Code:
            code = _Code.STORAGE_FAILURE
        super().__init__(code.value)

    def __repr__(self):
        return '<PrivateSQLiteJournalError>'


def _fail(code):
    raise _StorageError(code) from None


def _check(condition, code=_Code.CORRUPT):
    if not condition:
        _fail(code)


_MODELS = (journal._JournalAccountBinding, journal._JournalHeader,
    journal._SessionRegistered, journal._JournalOrderIntent,
    journal._JournalContractSnapshot, journal._StrategyWitness,
    journal._JournalStrategyContext, journal._JournalRiskContext,
    journal._AttemptConsumed, journal._NumericOrderIdReserved,
    journal._OrderIdFloorObserved, journal._BrokerOrderIdObserved,
    journal._AllocatorExhausted, journal._InvocationMayOccur,
    journal._SdkReturnedNormally, journal._AttemptClosedWithoutInvocation,
    journal._ReconciliationRequired, journal._CallbackSource,
    journal._OpenOrderObserved, journal._OrderStatusObserved,
    journal._ExecutionObserved, journal._CommissionReceived,
    journal._CallbackProgress, journal._JournalRecord)
_MODEL_TAGS = {model.__name__: model for model in _MODELS}
_ENUMS = (journal._LifecycleStatus, journal._ReconciliationReason,
          journal._ClosureReason, journal._WitnessKind, AccountMode)
_ENUM_TAGS = {model.__name__: model for model in _ENUMS}
_EVENT_TAGS = {model.__name__ for model in journal._EVENT_TYPES}


def _offset_microseconds(value):
    return (value.days * 86400 + value.seconds) * 1_000_000 + value.microseconds


def _encode_value(value):
    kind = type(value)
    if kind in _ENUMS:
        return {'type': 'enum', 'enum': kind.__name__, 'member': value.name}
    if kind in (str, int, bool, type(None)):
        return value
    if kind is Decimal:
        journal._amount(value, signed=True)
        parts = value.as_tuple()
        return {'type': 'decimal', 'sign': parts.sign, 'digits': list(parts.digits),
                'exponent': parts.exponent}
    if kind is datetime:
        journal._timestamp(value)
        offset = _offset_microseconds(value.utcoffset())
        if type(value.tzinfo) is ZoneInfo:
            _check(type(value.tzinfo.key) is str and 1 <= len(value.tzinfo.key) <= 256,
                   _Code.INVALID_EVENT)
            zone = {'kind': 'zoneinfo', 'key': value.tzinfo.key, 'offset_us': offset}
        else:
            name = value.tzname()
            journal._text(name)
            zone = {'kind': 'fixed', 'name': name, 'offset_us': offset}
        return {'type': 'datetime', 'parts': [value.year, value.month, value.day,
            value.hour, value.minute, value.second, value.microsecond],
            'fold': value.fold, 'zone': zone}
    if kind is date:
        return {'type': 'date', 'parts': [value.year, value.month, value.day]}
    if kind is tuple:
        return {'type': 'tuple', 'items': [_encode_value(v) for v in value]}
    _check(kind in _MODELS, _Code.INVALID_EVENT)
    journal._exact(value, kind)
    return {'type': kind.__name__, 'fields': {
        f.name: _encode_value(getattr(value, f.name)) for f in fields(kind)}}


def _keys(value, expected):
    _check(type(value) is dict and set(value) == set(expected))


def _integers(value, length):
    _check(type(value) is list and len(value) == length
           and all(type(v) is int for v in value))


def _decode_value(value):
    if type(value) in (str, int, bool, type(None)):
        return value
    _check(type(value) is dict and type(value.get('type')) is str)
    tag = value['type']
    if tag == 'decimal':
        _keys(value, ('type', 'sign', 'digits', 'exponent'))
        _check(type(value['sign']) is int and value['sign'] in (0, 1)
               and type(value['exponent']) is int and -64 <= value['exponent'] <= 64
               and type(value['digits']) is list and 1 <= len(value['digits']) <= 64
               and all(type(d) is int and 0 <= d <= 9 for d in value['digits']))
        return Decimal((value['sign'], tuple(value['digits']), value['exponent']))
    if tag == 'date':
        _keys(value, ('type', 'parts')); _integers(value['parts'], 3)
        return date(*value['parts'])
    if tag == 'datetime':
        _keys(value, ('type', 'parts', 'fold', 'zone')); _integers(value['parts'], 7)
        _check(type(value['fold']) is int and value['fold'] in (0, 1))
        zone = value['zone']
        _check(type(zone) is dict and type(zone.get('offset_us')) is int
               and -86_400_000_000 < zone['offset_us'] < 86_400_000_000)
        if zone.get('kind') == 'fixed':
            _keys(zone, ('kind', 'name', 'offset_us'))
            journal._text(zone['name'])
            tz = timezone(timedelta(microseconds=zone['offset_us']), zone['name'])
        else:
            _keys(zone, ('kind', 'key', 'offset_us'))
            _check(zone['kind'] == 'zoneinfo' and type(zone['key']) is str
                   and 1 <= len(zone['key']) <= 256)
            tz = ZoneInfo(zone['key'])
        result = datetime(*value['parts'], tzinfo=tz, fold=value['fold'])
        journal._timestamp(result)
        _check(_offset_microseconds(result.utcoffset()) == zone['offset_us'])
        return result
    if tag == 'tuple':
        _keys(value, ('type', 'items'))
        _check(type(value['items']) is list)
        return tuple(_decode_value(v) for v in value['items'])
    if tag == 'enum':
        _keys(value, ('type', 'enum', 'member'))
        _check(type(value['enum']) is str and value['enum'] in _ENUM_TAGS
               and type(value['member']) is str)
        return _ENUM_TAGS[value['enum']][value['member']]
    _check(tag in _MODEL_TAGS)
    model = _MODEL_TAGS[tag]
    _keys(value, ('type', 'fields'))
    _keys(value['fields'], (f.name for f in fields(model)))
    decoded = {name: _decode_value(v) for name, v in value['fields'].items()}
    result = model(**{f.name: decoded[f.name] for f in fields(model) if f.init})
    _check(all(getattr(result, f.name) == decoded[f.name] for f in fields(model)))
    return result


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        _check(key not in result)
        result[key] = value
    return result


def _no_float(value):
    _fail(_Code.CORRUPT)


def _encode(value):
    try:
        data = json.dumps(_encode_value(value), ensure_ascii=True, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
        _check(0 < len(data) <= _MAX_PAYLOAD_BYTES, _Code.INVALID_EVENT)
        return data
    except Exception:
        _fail(_Code.INVALID_EVENT)


def _decode(data, expected_type):
    try:
        _check(type(data) is bytes and 0 < len(data) <= _MAX_PAYLOAD_BYTES)
        parsed = json.loads(data.decode('utf-8'), object_pairs_hook=_json_pairs,
                            parse_float=_no_float, parse_constant=_no_float)
        value = _decode_value(parsed)
        _check(type(value) is expected_type and expected_type in _MODELS)
        _check(_encode(value) == data)  # Reject noncanonical alternate encodings.
        return value
    except Exception:
        _fail(_Code.CORRUPT)


def _framed_hash(*parts):
    digest = hashlib.sha256()
    for part in parts:
        _check(type(part) is bytes)
        digest.update(len(part).to_bytes(8, 'big'))
        digest.update(part)
    return digest.digest()


def _genesis(header_bytes):
    return _framed_hash(_DOMAIN, b'genesis', header_bytes)


def _record_digest(header_bytes, sequence, tag, data, previous):
    _check(type(sequence) is int and 1 <= sequence <= _MAX_SQL_INTEGER)
    _check(type(tag) is str and tag in _EVENT_TAGS)
    _check(type(previous) is bytes and len(previous) == 32)
    return _framed_hash(_DOMAIN, b'record', header_bytes,
                        sequence.to_bytes(8, 'big'), tag.encode('ascii'), data, previous)


_SCHEMA = {
    'metadata': ('table', 'metadata', f"""CREATE TABLE metadata (
singleton INTEGER PRIMARY KEY CHECK(singleton=1),
storage_format TEXT NOT NULL CHECK(storage_format='{_STORE_FORMAT}'),
header BLOB NOT NULL CHECK(length(header) BETWEEN 1 AND {_MAX_PAYLOAD_BYTES}),
genesis BLOB NOT NULL CHECK(length(genesis)=32)) STRICT"""),
    'records': ('table', 'records', f"""CREATE TABLE records (
sequence INTEGER PRIMARY KEY CHECK(sequence>0),
event_type TEXT NOT NULL,
encoded BLOB NOT NULL CHECK(length(encoded) BETWEEN 1 AND {_MAX_PAYLOAD_BYTES}),
previous_digest BLOB NOT NULL CHECK(length(previous_digest)=32),
digest BLOB NOT NULL CHECK(length(digest)=32)) STRICT"""),
    'store_state': ('table', 'store_state', """CREATE TABLE store_state (
singleton INTEGER PRIMARY KEY CHECK(singleton=1) REFERENCES metadata(singleton),
tail_sequence INTEGER NOT NULL CHECK(tail_sequence>=0),
tail_digest BLOB NOT NULL CHECK(length(tail_digest)=32),
quarantined INTEGER NOT NULL CHECK(quarantined IN (0,1)),
reason TEXT CHECK((quarantined=0 AND reason IS NULL) OR
(quarantined=1 AND reason='SEMANTIC_INVALID'))) STRICT"""),
}
for _table in ('metadata', 'records'):
    for _operation in ('UPDATE', 'DELETE'):
        _name = f'{_table}_no_{_operation.lower()}'
        _SCHEMA[_name] = ('trigger', _table,
            f"CREATE TRIGGER {_name} BEFORE {_operation} ON {_table} "
            "BEGIN SELECT RAISE(ABORT,'IMMUTABLE_JOURNAL'); END")
for _table in ('metadata', 'store_state'):
    _name = f'{_table}_no_reinsert'
    _SCHEMA[_name] = ('trigger', _table,
        f"CREATE TRIGGER {_name} BEFORE INSERT ON {_table} "
        f"WHEN EXISTS(SELECT 1 FROM {_table}) "
        "BEGIN SELECT RAISE(ABORT,'IMMUTABLE_JOURNAL'); END")
_SCHEMA['state_no_delete'] = ('trigger', 'store_state',
    "CREATE TRIGGER state_no_delete BEFORE DELETE ON store_state "
    "BEGIN SELECT RAISE(ABORT,'IMMUTABLE_JOURNAL'); END")
_SCHEMA['state_monotonic'] = ('trigger', 'store_state',
    "CREATE TRIGGER state_monotonic BEFORE UPDATE ON store_state "
    "WHEN NEW.tail_sequence<OLD.tail_sequence OR NEW.quarantined<OLD.quarantined "
    "BEGIN SELECT RAISE(ABORT,'IMMUTABLE_JOURNAL'); END")
_SCHEMA['records_contiguous'] = ('trigger', 'records',
    "CREATE TRIGGER records_contiguous BEFORE INSERT ON records "
    "WHEN (SELECT quarantined FROM store_state)=1 OR "
    "NEW.sequence!=(SELECT tail_sequence+1 FROM store_state) OR "
    "NEW.previous_digest!=(SELECT tail_digest FROM store_state) "
    "BEGIN SELECT RAISE(ABORT,'INVALID_JOURNAL_APPEND'); END")


class _OpaqueStorageValue:
    __slots__ = ()

    def __repr__(self):
        return '<PrivateSQLiteJournalValue>'


@dataclass(frozen=True, slots=True, repr=False)
class _LoadedJournal(_OpaqueStorageValue):
    header: journal._JournalHeader
    records: tuple[journal._JournalRecord, ...]
    replay: journal._JournalReplayState

    def __post_init__(self):
        journal._exact(self.header, journal._JournalHeader)
        _check(type(self.records) is tuple)
        for record in self.records:
            journal._exact(record, journal._JournalRecord)
        journal._exact(self.replay, journal._JournalReplayState)
        _check(self.replay.header == self.header and self.replay.last_sequence == len(self.records))


@dataclass(frozen=True, slots=True, repr=False)
class _CommittedRecord(_OpaqueStorageValue):
    record: journal._JournalRecord
    digest: bytes

    def __post_init__(self):
        journal._exact(self.record, journal._JournalRecord)
        _check(type(self.digest) is bytes and len(self.digest) == 32)


def _path(value, creating):
    _check(type(value) in (str, type(Path())), _Code.INVALID_PATH)
    raw = os.fspath(value)
    _check(raw and '\x00' not in raw and raw != ':memory:'
           and not raw.startswith(('file:', '//', '\\\\')), _Code.INVALID_PATH)
    path = Path(os.path.abspath(raw))
    try:
        for parent in path.parents:
            info = parent.lstat()
            _check(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode),
                   _Code.INVALID_PATH)
        try:
            info = path.lstat()
        except FileNotFoundError:
            info = None
        if info is not None:
            _check(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode),
                   _Code.INVALID_PATH)
            _check(not creating, _Code.ALREADY_EXISTS)
        else:
            _check(creating, _Code.NOT_FOUND)
        for suffix in ('-wal', '-shm'):
            try:
                sidecar = Path(str(path) + suffix).lstat()
            except FileNotFoundError:
                continue
            _check(stat.S_ISREG(sidecar.st_mode) and not stat.S_ISLNK(sidecar.st_mode),
                   _Code.INVALID_PATH)
            _check(not creating, _Code.ALREADY_EXISTS)
        return path
    except _StorageError:
        raise
    except Exception:
        _fail(_Code.INVALID_PATH)


def _busy(error):
    code = getattr(error, 'sqlite_errorcode', None)
    return type(code) is int and (code & 255) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)


def _connect(path):
    # rw, never rwc: even a race cannot silently recreate an absent store.
    connection = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True,
                                isolation_level=None, timeout=0)
    try:
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _MAX_PAYLOAD_BYTES + 4096)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 65_536)
        return connection
    except BaseException:
        _SQLiteJournalStore._discard_connection(connection)
        raise


def _commit(connection):
    """Private test seam: failure before OR after this call is outcome-unknown."""
    connection.commit()


def _configure(connection, creating):
    _check(sqlite3.sqlite_version_info >= (3, 37, 0), _Code.INVALID_DATABASE)
    connection.execute('PRAGMA busy_timeout=0')
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA synchronous=FULL')
    connection.execute('PRAGMA trusted_schema=OFF')
    query = 'PRAGMA journal_mode=WAL' if creating else 'PRAGMA journal_mode'
    _check(connection.execute(query).fetchall() == [('wal',)], _Code.INVALID_DATABASE)
    for name, expected in (('synchronous', 2), ('foreign_keys', 1),
                           ('busy_timeout', 0), ('trusted_schema', 0)):
        _check(connection.execute('PRAGMA ' + name).fetchall() == [(expected,)],
               _Code.INVALID_DATABASE)


def _initialize(connection, header_bytes):
    connection.execute(f'PRAGMA application_id={_APPLICATION_ID}')
    connection.execute(f'PRAGMA user_version={_USER_VERSION}')
    for _, _, sql in _SCHEMA.values():
        connection.execute(sql)
    genesis = _genesis(header_bytes)
    connection.execute('INSERT INTO metadata VALUES(1,?,?,?)',
                       (_STORE_FORMAT, header_bytes, genesis))
    connection.execute('INSERT INTO store_state VALUES(1,0,?,0,NULL)', (genesis,))


def _inspect(connection, expected_header):
    _check(connection.execute('PRAGMA application_id').fetchall() == [(_APPLICATION_ID,)],
           _Code.INVALID_DATABASE)
    _check(connection.execute('PRAGMA user_version').fetchall() == [(_USER_VERSION,)],
           _Code.INVALID_DATABASE)
    objects = connection.execute('SELECT name,type,tbl_name,sql FROM sqlite_schema').fetchall()
    _check({name: (kind, table, sql) for name, kind, table, sql in objects} == _SCHEMA
           and len(objects) == len(_SCHEMA), _Code.INVALID_DATABASE)
    _check(connection.execute('PRAGMA quick_check(1)').fetchall() == [('ok',)])
    sizes = connection.execute('SELECT singleton,typeof(header),length(header) FROM metadata').fetchall()
    _check(len(sizes) == 1 and sizes[0][0] == 1 and sizes[0][1] == 'blob'
           and type(sizes[0][2]) is int and 0 < sizes[0][2] <= _MAX_PAYLOAD_BYTES)
    metadata = connection.execute('SELECT singleton,storage_format,header,genesis FROM metadata').fetchall()
    _check(len(metadata) == 1)
    _, storage_format, header_bytes, genesis = metadata[0]
    _check(storage_format == _STORE_FORMAT and type(genesis) is bytes and len(genesis) == 32)
    header = _decode(header_bytes, journal._JournalHeader)
    _check(header_bytes == _encode(expected_header), _Code.HEADER_MISMATCH)
    _check(genesis == _genesis(header_bytes))
    states = connection.execute('SELECT singleton,tail_sequence,tail_digest,quarantined,reason FROM store_state').fetchall()
    _check(len(states) == 1)
    singleton, tail, tail_digest, quarantined, reason = states[0]
    _check(type(singleton) is int and singleton == 1 and type(tail) is int
           and 0 <= tail <= _MAX_SQL_INTEGER and type(tail_digest) is bytes
           and len(tail_digest) == 32 and type(quarantined) is int and quarantined in (0, 1)
           and ((quarantined == 0 and reason is None)
                or (quarantined == 1 and reason == 'SEMANTIC_INVALID')))
    records, digests = [], []
    previous = genesis
    # Inspect size/type before selecting bytes; do not trust a corrupt row's length.
    sizes = connection.execute('SELECT sequence,typeof(encoded),length(encoded) FROM records ORDER BY sequence')
    for expected, (sequence, kind, length) in enumerate(sizes, 1):
        _check(type(sequence) is int and sequence == expected and sequence <= _MAX_SQL_INTEGER
               and kind == 'blob' and type(length) is int and 0 < length <= _MAX_PAYLOAD_BYTES)
        tag, data, prior, digest = connection.execute(
            'SELECT event_type,encoded,previous_digest,digest FROM records WHERE sequence=?',
            (sequence,)).fetchone()
        _check(type(tag) is str and tag in _EVENT_TAGS and type(prior) is bytes
               and type(digest) is bytes and len(digest) == 32 and prior == previous)
        _check(digest == _record_digest(header_bytes, sequence, tag, data, prior))
        record = _decode(data, journal._JournalRecord)
        _check(record.sequence == sequence and type(record.payload).__name__ == tag
               and record.schema_version == header.schema_version
               and record.journal_id == header.journal_id
               and record.account_binding == header.account_binding)
        records.append(record); digests.append(digest); previous = digest
    _check(tail == len(records) and tail_digest == previous)
    return header, tuple(records), tuple(digests), bool(quarantined)


class _SQLiteJournalStore:
    """Private, single-thread connection; SQLite serializes independent writers.

    No authority is cached. A fresh full-history read is made inside every
    transaction. Poison/quarantine/close have no reset path.
    """

    def __init__(self, connection, header):
        self._connection = connection
        self._header = header
        self._closed = False
        self._poisoned = False
        self._quarantined = False

    def __repr__(self):
        return '<PrivateSQLiteJournalStore>'

    @classmethod
    def _create(cls, path, header):
        try:
            journal._exact(header, journal._JournalHeader)
            data = _encode(header)
            _decode(data, journal._JournalHeader)
        except Exception:
            _fail(_Code.INVALID_EVENT)
        path = _path(path, True)
        connection = None
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                                 | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            os.close(descriptor)
            identity = path.lstat()
            connection = _connect(path)
            current = path.lstat()
            _check(stat.S_ISREG(current.st_mode) and not stat.S_ISLNK(current.st_mode)
                   and (identity.st_dev, identity.st_ino) == (current.st_dev, current.st_ino),
                   _Code.INVALID_PATH)
            _configure(connection, True)
        except BaseException as error:
            cls._discard_connection(connection)
            if not isinstance(error, Exception):
                raise
            if isinstance(error, _StorageError):
                raise
            _fail(_Code.ALREADY_EXISTS if isinstance(error, FileExistsError)
                  else _Code.BUSY if _busy(error) else _Code.STORAGE_FAILURE)
        store = cls(connection, header)
        committing = False
        try:
            connection.execute('BEGIN IMMEDIATE')
            _initialize(connection, data)
            committing = True
            _commit(connection)
            store._verify_commit(0, _genesis(data), False)
            return store
        except BaseException as error:
            store._poison()
            if not isinstance(error, Exception):
                raise
            _fail(_Code.COMMIT_OUTCOME_UNKNOWN if committing else _Code.STORAGE_FAILURE)

    @classmethod
    def _open(cls, path, header):
        try:
            journal._exact(header, journal._JournalHeader)
            _encode(header)
        except Exception:
            _fail(_Code.INVALID_EVENT)
        path = _path(path, False)
        connection = None
        try:
            identity = path.lstat()
            connection = _connect(path)
            current = path.lstat()
            _check(stat.S_ISREG(current.st_mode) and not stat.S_ISLNK(current.st_mode)
                   and (identity.st_dev, identity.st_ino) == (current.st_dev, current.st_ino),
                   _Code.INVALID_PATH)
            _configure(connection, False)
            store = cls(connection, header)
            store._load()
            return store
        except BaseException as error:
            cls._discard_connection(connection)
            if not isinstance(error, Exception) or isinstance(error, _StorageError):
                raise
            _fail(_Code.BUSY if _busy(error) else _Code.INVALID_DATABASE)

    @staticmethod
    def _discard_connection(connection):
        if connection is not None:
            try:
                connection.close()  # Rolls back only an outstanding transaction.
            except BaseException:
                pass  # Secondary cleanup must not replace the original interruption.

    def _poison(self):
        self._poisoned = True
        self._closed = True
        self._discard_connection(self._connection)

    def _ensure(self):
        if self._poisoned:
            _fail(_Code.POISONED)
        if self._closed:
            _fail(_Code.CLOSED)
        if self._quarantined:
            _fail(_Code.QUARANTINED)

    def _begin(self, writing):
        self._ensure()
        try:
            # Recheck configuration each time; callers cannot weaken a live handle.
            _configure(self._connection, False)
            self._connection.execute('BEGIN IMMEDIATE' if writing else 'BEGIN')
        except BaseException as error:
            if not isinstance(error, Exception):
                self._poison(); raise
            if _busy(error):
                _fail(_Code.BUSY)
            self._poison()
            if isinstance(error, _StorageError):
                raise
            _fail(_Code.STORAGE_FAILURE)

    def _load(self):
        self._begin(False)
        try:
            header, records, _, quarantined = _inspect(self._connection, self._header)
            if quarantined:
                self._quarantined = True
                _fail(_Code.QUARANTINED)
            try:
                replay = journal._replay_paper_execution_journal(header, records)
            except ReadOnlyError:
                _fail(_Code.CORRUPT)
            self._connection.rollback()  # End the coherent read snapshot.
            return _LoadedJournal(header, records, replay)
        except BaseException as error:
            self._poison()
            if not isinstance(error, Exception) or isinstance(error, _StorageError):
                raise
            _fail(_Code.CORRUPT)

    def _verify_commit(self, sequence, digest, quarantined):
        self._connection.execute('BEGIN')
        try:
            header, records, digests, actual_quarantine = _inspect(self._connection, self._header)
            _check(actual_quarantine == quarantined)
            if sequence:
                _check(len(records) >= sequence and digests[sequence - 1] == digest)
            else:
                _check(_genesis(_encode(header)) == digest)
            if not actual_quarantine:
                journal._replay_paper_execution_journal(header, records)
        except BaseException:
            # Closing rolls back the read transaction; preserve its original error
            # even if cleanup itself is interrupted.
            self._discard_connection(self._connection)
            raise
        else:
            self._connection.rollback()

    def _append(self, payload):
        self._ensure()
        try:
            _check(type(payload) in journal._EVENT_TYPES, _Code.INVALID_EVENT)
            _decode(_encode(payload), type(payload))
        except Exception:
            _fail(_Code.INVALID_EVENT)
        self._begin(True)
        committing = False
        try:
            header, records, digests, quarantined = _inspect(self._connection, self._header)
            if quarantined:
                self._quarantined = True
                _fail(_Code.QUARANTINED)
            try:
                journal._replay_paper_execution_journal(header, records)
            except ReadOnlyError:
                _fail(_Code.CORRUPT)
            sequence = len(records) + 1
            _check(sequence <= _MAX_SQL_INTEGER)
            record = journal._JournalRecord(sequence, header.schema_version, header.journal_id,
                                            header.account_binding, payload)
            data, header_bytes = _encode(record), _encode(header)
            previous = digests[-1] if digests else _genesis(header_bytes)
            tag = type(payload).__name__
            digest = _record_digest(header_bytes, sequence, tag, data, previous)
            self._connection.execute('INSERT INTO records VALUES(?,?,?,?,?)',
                                     (sequence, tag, data, previous, digest))
            try:
                journal._replay_paper_execution_journal(header, records + (record,))
            except ReadOnlyError as error:
                _check(error.code == journal._ERROR)
                quarantined = True
                self._quarantined = True
            self._connection.execute(
                'UPDATE store_state SET tail_sequence=?,tail_digest=?,quarantined=?,reason=? WHERE singleton=1',
                (sequence, digest, int(quarantined), 'SEMANTIC_INVALID' if quarantined else None))
            committing = True
            _commit(self._connection)
            self._verify_commit(sequence, digest, quarantined)
        except BaseException as error:
            self._poison()
            if not isinstance(error, Exception):
                raise
            if committing:
                _fail(_Code.COMMIT_OUTCOME_UNKNOWN)
            if isinstance(error, _StorageError):
                raise
            _fail(_Code.STORAGE_FAILURE)
        if quarantined:
            _fail(_Code.QUARANTINED)
        return _CommittedRecord(record, digest)

    def _close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._connection.close()
        except BaseException as error:
            self._poisoned = True
            if not isinstance(error, Exception):
                raise
            _fail(_Code.STORAGE_FAILURE)

    def __enter__(self):
        self._ensure()
        return self

    def __exit__(self, kind, value, traceback):
        if kind is None:
            self._close()
        else:
            self._closed = True
            self._discard_connection(self._connection)
        return False
