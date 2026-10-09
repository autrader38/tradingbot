"""Backend paper interlocks; no UI flag can bypass these checks."""

from dataclasses import dataclass
from datetime import datetime

from .models import (AccountSnapshot, BrokerReason as R, ConnectionStatus, OrderIntent,
                     OrderRequest, RiskPermission, TradingMode)


@dataclass(frozen=True, slots=True)
class Controls:
    trading_enabled: bool = False
    entries_paused: bool = False
    emergency_stopped: bool = False

    def __post_init__(self):
        if any(type(v) is not bool for v in (self.trading_enabled, self.entries_paused, self.emergency_stopped)):
            raise TypeError('Safety controls require actual booleans')


def account_gates(mode: TradingMode, connection: ConnectionStatus,
                  account: AccountSnapshot | None, at: datetime) -> tuple[R, ...]:
    failures = []
    if mode != TradingMode.PAPER:
        failures.append(R.LIVE_EXECUTION_DISABLED)
    if connection != ConnectionStatus.CONNECTED:
        failures.append(R.BROKER_DISCONNECTED)
    if account is None:
        failures.append(R.ACCOUNT_DATA_UNAVAILABLE)
    elif account.available_at > at:
        # Do not inspect unavailable substantive mode/verification values.
        failures.append(R.ACCOUNT_DATA_UNAVAILABLE)
    else:
        if at >= account.valid_until:
            failures.append(R.ACCOUNT_DATA_EXPIRED)
        if not account.verified or account.mode is None:
            failures.append(R.ACCOUNT_MODE_UNVERIFIED)
        elif account.mode != TradingMode.PAPER:
            failures.append(R.ACCOUNT_MODE_MISMATCH)
    return tuple(failures)


def submission_gates(mode: TradingMode, connection: ConnectionStatus,
                     account: AccountSnapshot | None, controls: Controls,
                     order: OrderRequest, permission: RiskPermission | None,
                     at: datetime) -> tuple[R, ...]:
    failures = list(account_gates(mode, connection, account, at))
    if order.mode == TradingMode.LIVE and R.LIVE_EXECUTION_DISABLED not in failures:
        failures.insert(0, R.LIVE_EXECUTION_DISABLED)
    if order.mode != mode:
        failures.append(R.REQUEST_MODE_MISMATCH)
    if not controls.trading_enabled:
        failures.append(R.TRADING_DISABLED)
    if controls.emergency_stopped:
        failures.append(R.EMERGENCY_STOP)
    if controls.entries_paused and order.intent == OrderIntent.ENTRY:
        failures.append(R.ENTRIES_PAUSED)
    if at < order.created_at:
        failures.append(R.ORDER_NOT_YET_VALID)
    if at >= order.expires_at:
        failures.append(R.ORDER_EXPIRED)
    if permission is None:
        failures.append(R.RISK_PERMISSION_UNAVAILABLE)
    elif permission.available_at > at:
        failures.append(R.RISK_PERMISSION_NOT_YET_AVAILABLE)
    else:
        if at >= permission.valid_until:
            failures.append(R.RISK_PERMISSION_EXPIRED)
        if permission.order != order:
            failures.append(R.RISK_PERMISSION_MISMATCH)
        if not permission.permits_entry:
            failures.append(R.PORTFOLIO_ENTRY_DENIED)
    return tuple(failures)
