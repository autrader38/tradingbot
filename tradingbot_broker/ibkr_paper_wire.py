"""Fixed paper policy, separate from the permanent read-only connection policy.

No public sender is returned by the transport. These private views are an
application boundary, not a sandbox for hostile Python reflection.
"""

from threading import RLock
import re
import struct

from .ibkr_readonly_wire import sdk_call
from .readonly_models import ReadOnlyError


_LEGACY_READS = frozenset((7, 16, 17, 49, 61, 62, 63, 64, 71, 99))
_PROTOBUF_READS = frozenset((207, 216, 217, 249, 261, 262, 263, 264, 271, 299))
_LEGACY_WRITES = frozenset((3, 4, 58))
_PROTOBUF_WRITES = frozenset((203, 204, 258))
_LEGACY_POLICY = _LEGACY_READS | _LEGACY_WRITES
_PROTOBUF_POLICY = _PROTOBUF_READS | _PROTOBUF_WRITES


def _guarded_socket(raw, socket_type):
    if not isinstance(raw, socket_type):
        raise ReadOnlyError('UNSUPPORTED_IBAPI_CONNECTION')
    # Bind immutable policy to this connection; never consult a mutable flag.
    legacy, protobuf = _LEGACY_POLICY, _PROTOBUF_POLICY
    lock = RLock()
    pending = b''
    poisoned = False
    negotiated = False

    def validate(data):
        nonlocal negotiated
        if poisoned:
            raise ReadOnlyError('PAPER_SOCKET_POISONED')
        if type(data) is not bytes:
            raise ReadOnlyError('UNSUPPORTED_IBAPI_WIRE_ENCODING')
        if pending:
            if data != pending:
                raise ReadOnlyError('PAPER_WIRE_PENDING_MISMATCH')
            return data
        handshake = data.startswith(b'API\0')
        framed = data[4:] if handshake else data
        if len(framed) < 5:
            raise ReadOnlyError('UNSUPPORTED_IBAPI_WIRE_ENCODING')
        length = struct.unpack('!I', framed[:4])[0]
        if length != len(framed) - 4 or length > 65536:
            raise ReadOnlyError('UNSUPPORTED_IBAPI_WIRE_ENCODING')
        body = framed[4:]
        if handshake:
            if negotiated or not re.fullmatch(rb'v[0-9]{1,4}\.\.[0-9]{1,4}', body):
                raise ReadOnlyError('PAPER_WIRE_MESSAGE_FORBIDDEN')
            negotiated = True
        elif body[:1].isdigit():
            # Select exactly one encoding. Never retry failed ASCII as raw.
            if b'\0' not in body:
                raise ReadOnlyError('UNSUPPORTED_IBAPI_WIRE_ENCODING')
            field = body.split(b'\0', 1)[0]
            if not field.isdigit() or int(field) not in legacy:
                raise ReadOnlyError('PAPER_WIRE_MESSAGE_FORBIDDEN')
        else:
            if len(body) < 4:
                raise ReadOnlyError('UNSUPPORTED_IBAPI_WIRE_ENCODING')
            opcode = struct.unpack('!I', body[:4])[0]
            if opcode not in legacy and opcode not in protobuf:
                raise ReadOnlyError('PAPER_WIRE_MESSAGE_FORBIDDEN')
        return data

    class SocketView:
        __slots__ = ()

        def send(self, data, *args):
            nonlocal pending, poisoned
            with lock:
                data = validate(data)
                try:
                    sent = raw.send(data, *args)
                except BaseException as error:
                    poisoned = True
                    if not isinstance(error, Exception):
                        raise
                    raise ReadOnlyError('SDK_SOCKET_SEND_FAILED') from None
                if type(sent) is not int or not 0 <= sent <= len(data):
                    poisoned = True
                    raise ReadOnlyError('SDK_SOCKET_SEND_FAILED')
                pending = data[sent:]
                return sent

        def sendall(self, data, *args):
            nonlocal pending, poisoned
            with lock:
                data = validate(data)
                try:
                    result = raw.sendall(data, *args)
                except BaseException as error:
                    poisoned = True
                    if not isinstance(error, Exception):
                        raise
                    raise ReadOnlyError('SDK_SOCKET_SEND_FAILED') from None
                if result is not None:
                    poisoned = True
                    raise ReadOnlyError('SDK_SOCKET_SEND_FAILED')
                pending = b''

        def connect(self, address):
            return sdk_call('SDK_SOCKET_CONNECT_FAILED', raw.connect, address)

        def recv(self, *args):
            try:
                return raw.recv(*args)
            except TimeoutError:
                raise TimeoutError('SDK_SOCKET_RECEIVE_TIMEOUT') from None
            except BlockingIOError:
                raise BlockingIOError('SDK_SOCKET_RECEIVE_WOULD_BLOCK') from None
            except Exception:
                raise ReadOnlyError('SDK_SOCKET_RECEIVE_FAILED') from None

        def settimeout(self, value):
            return sdk_call('SDK_SOCKET_CONFIGURATION_FAILED', raw.settimeout, value)

        def close(self):
            return sdk_call('SDK_SOCKET_SHUTDOWN_FAILED', raw.close)

        def shutdown(self, *args):
            return sdk_call('SDK_SOCKET_SHUTDOWN_FAILED', raw.shutdown, *args)

        def __repr__(self):
            return '<IsolatedPaperTWSSocket>'

    return SocketView()


def _guarded_connection(connection, api):
    if not isinstance(connection, api.Connection) or not hasattr(connection, '__dict__'):
        raise ReadOnlyError('UNSUPPORTED_IBAPI_CONNECTION')
    initial_socket = connection.__dict__.pop('socket', None)
    view = None

    class GuardedConnection(api.Connection):
        __slots__ = ()

        @property
        def socket(self):
            return view

        @socket.setter
        def socket(self, value):
            nonlocal view
            view = None if value is None else _guarded_socket(value, api.SocketType)

        def __repr__(self):
            return '<IsolatedPaperTWSConnection>'

    try:
        connection.__class__ = GuardedConnection
        if initial_socket is not None:
            connection.socket = initial_socket
    except ReadOnlyError:
        raise
    except Exception:
        raise ReadOnlyError('UNSUPPORTED_IBAPI_CONNECTION') from None
    return connection
