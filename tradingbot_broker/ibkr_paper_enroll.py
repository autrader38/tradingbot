"""Explicit local enrollment through the read-only facade; never broker orders."""

import argparse

from .broker import BrokerOperationError
from .ibkr_readonly import ReadOnlyTWSTransport, TWSReadOnlyConfig
from .ibkr_readonly_broker import ReadOnlyIBKRBroker
from .paper_enrollment import CONFIRMATION, PaperEnrollmentStatus, require_confirmation
from .readonly_models import ReadOnlyError


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes arbitrary unknown arguments to stderr.
        raise ReadOnlyError('INVALID_PAPER_ENROLLMENT_ARGUMENTS')


def main(argv=None):
    parser = _SafeArgumentParser(description='Enroll one local IBKR account; no order transmission.')
    parser.add_argument('--replace-existing', action='store_true',
                        help='Explicitly replace an existing enrollment after confirmation.')
    try:
        options = parser.parse_args(argv)
    except ReadOnlyError:
        print('Paper enrollment: FAILED (INVALID_PAPER_ENROLLMENT_ARGUMENTS)')
        return 1
    broker = None
    result = 1
    print('Read-only transport: ENABLED')
    print('Order transmission: DISABLED')
    print('Enrollment is your declaration, not independent broker PAPER attestation.')
    try:
        config = TWSReadOnlyConfig.from_environment()
        confirmation = input(f'Log into IB Gateway SIMULATED/PAPER TRADING. Type exactly: {CONFIRMATION}\n> ')
        require_confirmation(confirmation)
        broker = ReadOnlyIBKRBroker(ReadOnlyTWSTransport(config))
        broker.connect()
        status = broker.enroll_paper_account(confirmation, replace_existing=options.replace_existing)
        if status is not PaperEnrollmentStatus.MATCHED:
            raise ReadOnlyError('PAPER_ENROLLMENT_VERIFICATION_FAILED')
        print(f'Paper enrollment: {status.value}')
        print(f'Account mode: {broker.account_mode.value} (not independently attested)')
        print('Trading permission: UNUSABLE — Phase 10C3A does not enable trading')
        print('Local enrollment saved outside the repository; account identifier not persisted.')
        result = 0
    except (ReadOnlyError, BrokerOperationError) as error:
        code = error.code if isinstance(error, ReadOnlyError) else error.reason.value
        print(f'Paper enrollment: FAILED ({code})')
    except (EOFError, KeyboardInterrupt):
        print('Paper enrollment: FAILED (PAPER_ENROLLMENT_CONFIRMATION_REQUIRED)')
    except Exception:
        print('Paper enrollment: FAILED (PAPER_ENROLLMENT_FAILED)')
    finally:
        if broker is not None:
            try:
                broker.disconnect()
            except Exception:
                print('IBKR cleanup: FAILED (READ_ONLY_CLEANUP_FAILED)')
                result = 1
    return result


if __name__ == '__main__':
    raise SystemExit(main())
