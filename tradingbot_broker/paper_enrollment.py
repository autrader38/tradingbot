"""Local user enrollment evidence; never broker PAPER attestation or permission."""

from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile

from .readonly_models import ReadOnlyError


CONFIRMATION = 'I CONFIRM IBKR SIMULATED TRADING'
_SCHEMA = 'VelocityTradingGroup.IBKR.PaperEnrollment'
_DOMAIN = 'VelocityTradingGroup|IBKR|PaperEnrollment|v1|'
_FILENAME = 'ibkr-paper-enrollment.json'
_ROOT = Path(__file__).resolve().parent.parent
_KEYS = frozenset(('schema', 'version', 'salt', 'fingerprint', 'created_at', 'authentication'))
_AUTH_DOMAIN = b'VelocityTradingGroup|IBKR|PaperEnrollmentAuthentication|v1|'


def _canonical_record(record):
    return json.dumps(record, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _authentication(key, record):
    unsigned = {name: value for name, value in record.items() if name != 'authentication'}
    return hmac.new(key, _AUTH_DOMAIN + _canonical_record(unsigned), hashlib.sha256).hexdigest()


def _windows_protect(data, *, decrypt=False):
    """Per-user Windows DPAPI; no portable plaintext key fallback on Windows."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN; omit LOCAL_MACHINE so protection is user-bound.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ReadOnlyError('PAPER_ENROLLMENT_KEY_PROTECTION_FAILED')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def _atomic_write(path, data, *, replace):
    temporary = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if sys.platform != 'win32':
            directory = path.parent.stat()
            if directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) != 0o700:
                raise ReadOnlyError('UNSAFE_PAPER_ENROLLMENT_DIRECTORY')
        descriptor, temporary = tempfile.mkstemp(prefix='.paper-enrollment-', suffix='.tmp', dir=path.parent)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError:
                raise ReadOnlyError('PAPER_ENROLLMENT_ALREADY_EXISTS') from None
    except ReadOnlyError:
        raise
    except Exception:
        raise ReadOnlyError('PAPER_ENROLLMENT_WRITE_FAILED') from None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                raise ReadOnlyError('PAPER_ENROLLMENT_CLEANUP_FAILED') from None


def _read_regular_file(path, maximum, *, owner_only=False):
    """Bounded reads from the same validated descriptor; never reopen the path."""
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0)
    if sys.platform != 'win32':
        nonblocking = getattr(os, 'O_NONBLOCK', None)
        if type(nonblocking) is not int or nonblocking <= 0:
            raise ValueError  # No safe POSIX FIFO acquisition without this flag.
        flags |= nonblocking
    flags |= getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
    before = os.lstat(path)
    if stat.S_ISLNK(before.st_mode):
        raise ValueError
    if sys.platform == 'win32' and not stat.S_ISREG(before.st_mode):
        raise ValueError  # Windows has no POSIX nonblocking-open contract.
    descriptor = os.open(path, flags)
    try:
        evidence = os.fstat(descriptor)
        if not stat.S_ISREG(evidence.st_mode) or evidence.st_size > maximum:
            raise ValueError
        if (before.st_dev, before.st_ino) != (evidence.st_dev, evidence.st_ino):
            raise ValueError
        if not getattr(os, 'O_NOFOLLOW', 0):
            current = os.lstat(path)
            if (stat.S_ISLNK(current.st_mode)
                    or (current.st_dev, current.st_ino) != (evidence.st_dev, evidence.st_ino)):
                raise ValueError
        if owner_only and sys.platform != 'win32':
            if evidence.st_uid != os.getuid() or stat.S_IMODE(evidence.st_mode) != 0o600:
                raise ValueError
        data = bytearray()
        while len(data) <= maximum:
            chunk = os.read(descriptor, min(4096, maximum + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > maximum:
            raise ValueError
        return bytes(data)
    finally:
        os.close(descriptor)


class PaperEnrollmentStatus(StrEnum):
    UNENROLLED = 'UNENROLLED'
    MATCHED = 'MATCHED'
    MISMATCH = 'MISMATCH'
    INVALID = 'INVALID'


def enrollment_path(*, environment=None, platform=None, home=None):
    """Per-user app data, never a repository-relative configuration file."""
    environment = os.environ if environment is None else environment
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else Path(home)
    if platform == 'win32':
        base = Path(environment.get('LOCALAPPDATA') or home / 'AppData' / 'Local')
    elif platform == 'darwin':
        base = home / 'Library' / 'Application Support'
    else:
        base = Path(environment.get('XDG_DATA_HOME') or home / '.local' / 'share')
    if not base.is_absolute():
        raise ReadOnlyError('INVALID_PAPER_ENROLLMENT_PATH')
    return base / 'VelocityTradingGroup' / _FILENAME


def require_confirmation(confirmation):
    if type(confirmation) is not str or confirmation != CONFIRMATION:
        raise ReadOnlyError('PAPER_ENROLLMENT_CONFIRMATION_REQUIRED')


def _fingerprint(salt, account):
    if type(account) is not str or not account or '|' in account:
        raise ReadOnlyError('INVALID_PAPER_ENROLLMENT_IDENTITY')
    return hashlib.sha256((_DOMAIN + salt + '|' + account).encode('utf-8')).hexdigest()


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _validate_record(record):
    if (type(record) is not dict or set(record) != _KEYS or record['schema'] != _SCHEMA
            or type(record['version']) is not int or record['version'] != 1):
        raise ValueError
    for field in ('salt', 'fingerprint', 'authentication'):
        if type(record[field]) is not str or re.fullmatch('[0-9a-f]{64}', record[field]) is None:
            raise ValueError
    if type(record['created_at']) is not str:
        raise ValueError
    stamp = datetime.fromisoformat(record['created_at'])
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError
    return record


class PaperEnrollmentStore:
    """Only a salted pseudonym is persisted; callers never receive an account ID."""

    def __init__(self, path=None):
        try:
            self._path = Path(enrollment_path() if path is None else path)
            self._key_path = self._path.with_name('ibkr-paper-enrollment-auth.key')
            self._checked_path()
            self._checked_path(self._key_path)
        except Exception:
            raise ReadOnlyError('INVALID_PAPER_ENROLLMENT_PATH') from None

    def _checked_path(self, path=None):
        path = self._path if path is None else path
        if not path.is_absolute() or path.is_symlink():
            raise ReadOnlyError('INVALID_PAPER_ENROLLMENT_PATH')
        resolved = path.resolve()
        if resolved == _ROOT or _ROOT in resolved.parents:
            raise ReadOnlyError('INVALID_PAPER_ENROLLMENT_PATH')
        if any((parent / '.git').is_file() or (parent / '.git' / 'HEAD').is_file()
               for parent in resolved.parents):
            raise ReadOnlyError('INVALID_PAPER_ENROLLMENT_PATH')
        # Parent symlinks are resolved before both validation and I/O.
        return resolved

    def _read_key(self):
        try:
            path = self._checked_path(self._key_path)
            if sys.platform != 'win32':
                directory = path.parent.stat()
                if directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) != 0o700:
                    raise ValueError
            raw = _read_regular_file(path, 8192, owner_only=True)
            if sys.platform == 'win32':
                if not raw.startswith(b'DPAPI\0'):
                    raise ValueError
                key = _windows_protect(raw[6:], decrypt=True)
            else:
                if not raw.startswith(b'POSIX\0'):
                    raise ValueError
                key = raw[6:]
            if type(key) is not bytes or len(key) != 32:
                raise ValueError
            return key
        except Exception:
            raise ReadOnlyError('INVALID_PAPER_ENROLLMENT_KEY') from None

    def _enrollment_key(self, replace_existing):
        path = self._checked_path(self._key_path)
        if path.exists():
            try:
                return self._read_key()
            except ReadOnlyError:
                if not replace_existing:
                    raise
        key = secrets.token_bytes(32)
        raw = b'DPAPI\0' + _windows_protect(key) if sys.platform == 'win32' else b'POSIX\0' + key
        try:
            _atomic_write(path, raw, replace=replace_existing)
        except ReadOnlyError as error:
            if error.code != 'PAPER_ENROLLMENT_ALREADY_EXISTS':
                raise
            # Another explicit enrollment installed the trust anchor first.
            return self._read_key()
        return key

    def _read_record(self):
        try:
            path = self._checked_path()
            raw = _read_regular_file(path, 4096)
            record = _validate_record(json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_fields))
            if not hmac.compare_digest(_authentication(self._read_key(), record), record['authentication']):
                raise ValueError
            return record
        except FileNotFoundError:
            return None
        except Exception:
            raise ReadOnlyError('INVALID_PAPER_ENROLLMENT') from None

    def _match_account(self, account):
        try:
            record = self._read_record()
            if record is None:
                return PaperEnrollmentStatus.UNENROLLED
            matched = hmac.compare_digest(_fingerprint(record['salt'], account), record['fingerprint'])
            return PaperEnrollmentStatus.MATCHED if matched else PaperEnrollmentStatus.MISMATCH
        except Exception:
            return PaperEnrollmentStatus.INVALID

    def _enroll_account(self, account, created_at, confirmation, *, replace_existing=False):
        """Invoked only inside the transport's private, healthy account boundary."""
        require_confirmation(confirmation)
        if type(replace_existing) is not bool:
            raise ReadOnlyError('INVALID_PAPER_ENROLLMENT_REPLACEMENT')
        try:
            path = self._checked_path()
            if path.exists() and not replace_existing:
                raise ReadOnlyError('PAPER_ENROLLMENT_ALREADY_EXISTS')
            if type(created_at) is not datetime or created_at.tzinfo is None or created_at.utcoffset() is None:
                raise ReadOnlyError('INVALID_PAPER_ENROLLMENT_TIMESTAMP')
            salt = secrets.token_hex(32)
            record = dict(schema=_SCHEMA, version=1, salt=salt,
                fingerprint=_fingerprint(salt, account),
                created_at=created_at.astimezone(timezone.utc).isoformat())
            record['authentication'] = _authentication(self._enrollment_key(replace_existing), record)
            _validate_record(record)
            _atomic_write(path, _canonical_record(record) + b'\n', replace=replace_existing)
        except ReadOnlyError:
            raise
        except Exception:
            raise ReadOnlyError('PAPER_ENROLLMENT_WRITE_FAILED') from None
