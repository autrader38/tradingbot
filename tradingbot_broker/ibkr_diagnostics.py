"""Manual, read-only local IB Gateway inspection; no credentials or orders."""

from .broker import BrokerOperationError
from .ibkr_readonly import ReadOnlyTWSTransport, TWSReadOnlyConfig
from .ibkr_readonly_broker import ReadOnlyIBKRBroker
from .readonly_models import ReadOnlyError


def main():
    broker = None
    result = 1
    print('Read-only transport: ENABLED')
    print('Order transmission: DISABLED')
    try:
        broker = ReadOnlyIBKRBroker(ReadOnlyTWSTransport(TWSReadOnlyConfig.from_environment()))
        broker.connect()
        account = broker.account_summary()
        print(f'IBKR connection: {broker.connection_status.value}')
        print(f'Account mode: {broker.account_mode.value} (not independently attested)')
        print('Account: account-1 (masked)')
        print(f'Net liquidation: {account.equity} {account.currency}')
        print(f'Cash: {account.cash} {account.currency}')
        print(f'Buying power: {account.buying_power} {account.currency}')
        print(f'Open positions: {len(account.positions)}')
        print(f'Working orders: {len(broker.working_orders())}')
        print('Trading permission: UNUSABLE — independent PAPER verification unavailable')
        result = 0
    except (ReadOnlyError, BrokerOperationError) as error:
        code = error.code if isinstance(error, ReadOnlyError) else error.reason.value
        print(f'IBKR connection: UNAVAILABLE ({code})')
        print('Install Python ibapi from the official IBKR TWS API distribution locally.')
        print('Log into IB Gateway PAPER; enable the Read-Only API and socket access.')
        print('Check local IBKR_HOST, IBKR_PORT (normally 4002), and positive IBKR_CLIENT_ID.')
        print('See docs/phase-10c2-real-ibkr-readonly.md. No connection success is claimed.')
    except Exception:
        # Last-resort boundary: never print an SDK exception or its traceback.
        print('IBKR connection: UNAVAILABLE (READ_ONLY_DIAGNOSTIC_FAILED)')
    finally:
        if broker is not None:
            try:
                broker.disconnect()
            except (ReadOnlyError, BrokerOperationError) as error:
                code = error.code if isinstance(error, ReadOnlyError) else error.reason.value
                print(f'IBKR cleanup: FAILED ({code})')
                result = 1
            except Exception:
                print('IBKR cleanup: FAILED (READ_ONLY_CLEANUP_FAILED)')
                result = 1
    return result


if __name__ == '__main__':
    raise SystemExit(main())
