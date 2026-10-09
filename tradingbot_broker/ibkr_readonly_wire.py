"""Read-only enforcement at the actual framed-byte socket boundary.

Socket/client views expose no raw socket, descriptor, SDK class or generic sender.
This is an application boundary, not a sandbox for arbitrary hostile Python code.
"""

from threading import RLock
import re
import struct

from .readonly_models import ReadOnlyError


def sdk_call(code, function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except ReadOnlyError:
        raise
    except Exception:
        raise ReadOnlyError(code) from None


def guarded_socket(raw, socket_type, allowed):
    if not isinstance(raw, socket_type):
        raise ReadOnlyError('UNSUPPORTED_IBAPI_CONNECTION')
    lock = RLock()
    pending = b''
    negotiated = False

    def validate(data):
        nonlocal negotiated
        if type(data) not in (bytes, bytearray, memoryview):
            raise ReadOnlyError('UNSUPPORTED_IBAPI_WIRE_ENCODING')
        data = bytes(data)
        if pending:
            if data != pending:
                raise ReadOnlyError('READ_ONLY_BROKER_TRANSPORT')
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
                raise ReadOnlyError('READ_ONLY_BROKER_TRANSPORT')
            negotiated = True
        else:
            if b'\0' not in body:
                raise ReadOnlyError('UNSUPPORTED_IBAPI_WIRE_ENCODING')
            opcode = body.split(b'\0', 1)[0]
            if not opcode.isdigit() or int(opcode) not in allowed:
                raise ReadOnlyError('READ_ONLY_BROKER_TRANSPORT')
        return data

    class SocketView:
        __slots__ = ()

        def send(self, data, *args):
            nonlocal pending
            with lock:
                data = validate(data)
                sent = sdk_call('SDK_SOCKET_SEND_FAILED', raw.send, data, *args)
                if type(sent) is not int or not 0 <= sent <= len(data):
                    raise ReadOnlyError('SDK_SOCKET_SEND_FAILED')
                pending = data[sent:]
                return sent

        def sendall(self, data, *args):
            nonlocal pending
            with lock:
                data = validate(data)
                sdk_call('SDK_SOCKET_SEND_FAILED', raw.sendall, data, *args)
                pending = b''

        def connect(self, address):
            return sdk_call('SDK_SOCKET_CONNECT_FAILED', raw.connect, address)
        def recv(self, *args):
            try:
                return raw.recv(*args)
            except TimeoutError:
                # Preserve SDK polling control flow, but discard external text.
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
        def __repr__(self): return '<ReadOnlyTWSSocket>'

    return SocketView()


def guarded_connection(connection, api, allowed):
    if not isinstance(connection, api.Connection) or not hasattr(connection, '__dict__'):
        raise ReadOnlyError('UNSUPPORTED_IBAPI_CONNECTION')
    initial_socket = connection.__dict__.pop('socket', None)
    view = None

    class GuardedConnection(api.Connection):
        __slots__ = ()

        @property
        def socket(self): return view
        @socket.setter
        def socket(self, value):
            nonlocal view
            view = None if value is None else guarded_socket(value, api.SocketType, allowed)
        def __repr__(self): return '<ReadOnlyTWSConnection>'

    # Install before SDK Connection.connect creates a socket or sends any bytes.
    # Even base Connection.sendMsg/connect calls resolve the guarded descriptor.
    try:
        connection.__class__ = GuardedConnection
        if initial_socket is not None:
            connection.socket = initial_socket
    except ReadOnlyError:
        raise
    except Exception:
        raise ReadOnlyError('UNSUPPORTED_IBAPI_CONNECTION') from None
    return connection


def client_view(client):
    class ReadClientView:
        __slots__ = ()
        def __repr__(self): return '<ReadOnlyTWSClient>'

    def bind(method):
        def call(self, *args, **kwargs):
            # The method name is closure-owned, never a caller dispatch argument.
            return sdk_call('SDK_' + method.upper() + '_FAILED',
                            lambda: getattr(client, method)(*args, **kwargs))
        return call

    for method in ('connect', 'disconnect', 'run', 'isConnected', 'serverVersion',
                   'reqManagedAccts', 'reqAccountSummary', 'reqPositions',
                   'reqAllOpenOrders', 'reqCompletedOrders', 'reqExecutions', 'reqCurrentTime'):
        setattr(ReadClientView, method, bind(method))
    return ReadClientView()
