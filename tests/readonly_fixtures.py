"""Official SDK-shaped offline callbacks; no sockets or authentication."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import patch
import struct

from tradingbot_broker.ibkr_readonly import ReadOnlyTWSTransport, TWSReadOnlyConfig, _READ_MESSAGES
from tradingbot_broker.ibkr_readonly_broker import ReadOnlyIBKRBroker

AT = datetime(2026, 7, 1, 14, 0, tzinfo=timezone.utc)
D = Decimal


class Clock:
    def __init__(self): self.now = AT
    def __call__(self):
        self.now += timedelta(microseconds=1)
        return self.now
    def advance(self, seconds): self.now += timedelta(seconds=seconds)


def sdk(**options):
    calls, clients = [], []
    account = 'anonymous-fixture'
    contract = NS(conId=17, symbol='DEMO', currency='USD')
    OUT = NS(**{name: index + 30 for index, name in enumerate(_READ_MESSAGES)})

    class Wrapper: pass

    class Socket:
        def connect(self, address): pass
        def settimeout(self, value): pass
        def send(self, data):
            calls.append(('socket-send', bytes(data)))
            return len(data)
        def sendall(self, data): self.send(data)
        def recv(self, size): return b''
        def close(self): pass
        def shutdown(self, how): pass

    class Connection:
        def __init__(self): self.socket = None
        def connect(self): self.socket = Socket()
        def sendMsg(self, message): return self.socket.send(message)
        def disconnect(self):
            if self.socket is not None: self.socket.close()
            self.socket = None

    class Client:
        def __init__(self, wrapper):
            self.wrapper = wrapper
            self.conn = None
            self.connected = False
            clients.append(self)
        def isConnected(self): return self.connected
        def serverVersion(self): return options.get('server_version', 200)
        def connect(self, host, port, clientId):
            calls.append(('connect', host, port, clientId))
            if options.get('connect_error'): raise RuntimeError('private fixture value')
            if options.get('connect_wait'): options['connect_wait'].wait(5)
            self.conn = Connection()
            self.conn.connect()
            self.connected = True
            if not options.get('no_ready'): self.wrapper.nextValidId(7)
        def disconnect(self):
            calls.append(('disconnect',))
            self.connected = False
            if self.conn is not None: self.conn.disconnect()
        def run(self): pass
        def sendMsg(self, message):
            data = message.encode()
            self.conn.sendMsg(struct.pack('!I',len(data)) + data)
            calls.append(('send', message))
        def placeOrder(self, *args): self.sendMsg('3\0forbidden')
        def cancelOrder(self, *args): self.sendMsg('4\0forbidden')
        def reqGlobalCancel(self, *args): self.sendMsg('58\0forbidden')
        def reqManagedAccts(self):
            calls.append(('accounts',))
            self.wrapper.managedAccounts(options.get('accounts', account))
        def reqAccountSummary(self, req, group, tags):
            calls.append(('summary', req))
            values = options.get('values', {'TotalCashValue': '10000.01', 'NetLiquidation': '10012.31', 'BuyingPower': '20000.02'})
            for tag, value in values.items():
                self.wrapper.accountSummary(req, account, tag, value, 'USD')
                if options.get('duplicate'): self.wrapper.accountSummary(req, account, tag, value, 'USD')
                if options.get('conflict'): self.wrapper.accountSummary(req, account, tag, '4', 'USD')
            if options.get('error_code'):
                self.wrapper.error(req, options['error_code'], 'private fixture value')
            self.wrapper.accountSummaryEnd(req)
        def reqPositions(self):
            calls.append(('positions',))
            if options.get('position', True):
                self.wrapper.position(account, contract, options.get('position_qty', '10'), options.get('cost', 1.231))
                if options.get('duplicate'): self.wrapper.position(account, contract, '10', 1.231)
            self.wrapper.positionEnd()
        def reqAllOpenOrders(self):
            calls.append(('orders',))
            self.order = NS(account=account, permId=77, clientId=1, orderId=7, action=options.get('side', 'BUY'),
                            orderType=options.get('order_type', 'LMT'), totalQuantity='10', lmtPrice=1.23, auxPrice=1.1)
            state = NS(status=options.get('status', 'Submitted'))
            self.wrapper.openOrder(7, contract, self.order, state)
            if options.get('duplicate'): self.wrapper.openOrder(7, contract, self.order, state)
            self.wrapper.openOrderEnd()
        def reqCompletedOrders(self, apiOnly):
            calls.append(('completed', apiOnly))
            order = NS(**vars(self.order))
            order.permId = 78
            self.wrapper.completedOrder(contract, order, NS(status='Filled'))
            self.wrapper.completedOrdersEnd()
        def reqExecutions(self, req, executionFilter):
            calls.append(('executions', req))
            execution = NS(acctNumber=account, execId='execution-1', permId=78, clientId=1, orderId=8,
                           side='BOT', shares='4', price=1.23, time=options.get('execution_time', '20260701 13:59:59 UTC'))
            self.wrapper.execDetails(req, contract, execution)
            if options.get('duplicate'): self.wrapper.execDetails(req, contract, execution)
            report = NS(execId='execution-1', commission='0.31', currency='USD')
            self.wrapper.commissionReport(report)
            if options.get('duplicate'): self.wrapper.commissionReport(report)
            self.wrapper.execDetailsEnd(req)
        def reqCurrentTime(self):
            calls.append(('time',))
            self.wrapper.currentTime(int(AT.timestamp()))

    return NS(Client=Client, Wrapper=Wrapper, Connection=Connection, SocketType=Socket,
              ExecutionFilter=lambda: NS(), OUT=OUT,
              completed_min_version=150, calls=calls, clients=clients)


def transport(api=None, clock=None, **config):
    api = api or sdk()
    clock = clock or Clock()
    with patch('tradingbot_broker.ibkr_readonly._load_official_api', return_value=api):
        result = ReadOnlyTWSTransport(TWSReadOnlyConfig(**config), clock=clock)
    return result, api, clock


def broker(api=None, clock=None, **config):
    t, api, clock = transport(api, clock, **config)
    return ReadOnlyIBKRBroker(t, clock=clock), t, api, clock
