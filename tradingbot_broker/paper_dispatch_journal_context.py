"""Private PRE1 reference projection. No execution, storage or recovery authority.

The supplied validation instant is data, not a clock/freshness capability. Later
dispatch must revalidate all mutable gates. Research position effects are ignored.
"""

from dataclasses import dataclass, fields
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum
from fractions import Fraction
import hashlib
import json
from zoneinfo import ZoneInfo

from tradingbot_backtest.config import FROZEN_V1, StrategyConfig
from tradingbot_backtest.entry_validation import EntryApproval, OpeningEligibility, TickReference
from tradingbot_backtest.c_detector import CLockEvidence, ConsolidationMetrics
from tradingbot_backtest.d_detector import BreakoutAttempt
from tradingbot_backtest.entry_context import (ContextBlock, ContextMinute, IntradayContextResult,
    WeeklyBar, WeeklyClassification, WeeklyContextResult, WeeklyEvaluation)
from tradingbot_backtest.codes import ReasonCode
from tradingbot_backtest.volume import VolumeEvidence
from tradingbot_backtest.market import MarketInterval, IntervalClassification
from tradingbot_backtest.sessions import TradingSession, NEW_YORK
from tradingbot_backtest.trade_construction import InitialPosition, PortfolioSnapshot, TickRule, TickPurpose
from .ibkr import IBKRContract
from .models import OrderRequest, RiskPermission, TradingMode, Side, OrderType, OrderIntent, TimeInForce
from .readonly_models import ReadOnlyError
from . import paper_execution_journal as j


_ERROR = 'INVALID_DISPATCH_JOURNAL_CONTEXT'
_CONFIG_DOMAIN = 'paper-dispatch-journal-configuration-v1|strategy-v1.0'
_APPROVAL_RECORDS = frozenset((EntryApproval, OpeningEligibility, TickReference,
    CLockEvidence, ConsolidationMetrics, BreakoutAttempt, ContextBlock, ContextMinute,
    IntradayContextResult, WeeklyBar, WeeklyContextResult, WeeklyEvaluation,
    VolumeEvidence, MarketInterval, TradingSession))


def _require(condition):
    if not condition:
        raise ReadOnlyError(_ERROR)


def _decimal(value, *, positive=False):
    j._amount(value, positive=positive)
    return Decimal(value.as_tuple())


def _timestamp(value):
    # Check tzinfo before invoking arbitrary timezone methods.
    _require(type(value) is datetime and type(value.tzinfo) in (timezone, ZoneInfo))
    j._timestamp(value)
    return datetime(value.year, value.month, value.day, value.hour, value.minute,
                    value.second, value.microsecond, tzinfo=value.tzinfo, fold=value.fold)


def _instant(value):
    return _timestamp(value).astimezone(timezone.utc)


def _identifier(value):
    j._identifier(value)
    return value  # Exact str is immutable; no caller objects are retained.


def _approval_semantics(value, depth=0):
    # Compare reconstructed qualified references without invoking arbitrary
    # __eq__ methods, or datetime equality that ignores a changed DST fold.
    _require(depth < 32)
    kind = type(value)
    if kind in _APPROVAL_RECORDS:
        return (kind, tuple(_approval_semantics(getattr(value, f.name), depth+1) for f in fields(kind)))
    if kind is tuple:
        _require(len(value) <= 1024)
        return (tuple, tuple(_approval_semantics(v, depth+1) for v in value))
    if kind is datetime:
        return (datetime, _instant(value))
    if kind in (str, int, bool, type(None), date, Fraction):
        return (kind, value)
    if kind is Decimal:
        _require(value.is_finite())
        return (kind, value)
    if kind in (ReasonCode, IntervalClassification, WeeklyClassification):
        return (kind, value.value)
    raise ReadOnlyError(_ERROR)


def _canonical_configuration_value(value):
    """Typed exact encodings, independent of Decimal context and repr.

    Equal finite Decimals share a canonical numeric encoding, including zero.
    Current StrategyConfig uses int, Decimal, naive time and timedelta only.
    Explicit encodings also prevent ambiguities if a reviewed schema adds types.
    """
    kind = type(value)
    if kind is int:
        return ['int', str(value)]
    if kind is Decimal:
        _require(value.is_finite())
        sign, digits, exponent = value.as_tuple()
        digits = list(digits)
        if not any(digits):
            sign, digits, exponent = 0, [0], 0
        else:
            while len(digits) > 1 and digits[-1] == 0:
                digits.pop()
                exponent += 1
        return ['decimal', sign, ''.join(map(str, digits)), exponent]
    if kind is Fraction:
        return ['fraction', str(value.numerator), str(value.denominator)]
    if kind is timedelta:
        return ['timedelta', value.days, value.seconds, value.microseconds]
    if kind is time:
        tz = value.tzinfo
        _require(tz is None or type(tz) in (timezone, ZoneInfo))
        zone = (['naive'] if tz is None else ['iana', tz.key] if type(tz) is ZoneInfo
                else ['offset', _canonical_configuration_value(tz.utcoffset(None)), tz.tzname(None)])
        return ['time', value.hour, value.minute, value.second, value.microsecond, value.fold, zone]
    if kind is timezone:
        return ['timezone', _canonical_configuration_value(value.utcoffset(None)), value.tzname(None)]
    if kind is ZoneInfo:
        return ['zoneinfo', value.key]
    if kind is datetime:
        value = _timestamp(value)
        return ['datetime', value.year, value.month, value.day,
                _canonical_configuration_value(value.timetz()),
                _canonical_configuration_value(value.utcoffset())]
    if kind is date:
        return ['date', value.year, value.month, value.day]
    if isinstance(value, Enum):
        return ['enum', kind.__module__, kind.__qualname__, _canonical_configuration_value(value.value)]
    if kind is str:
        return ['str', _identifier(value)]
    raise ReadOnlyError(_ERROR)


def _configuration_fingerprint(config):
    """Identity, not approval. The adapter separately requires frozen values."""
    try:
        _require(type(config) is StrategyConfig)
        StrategyConfig.__post_init__(config)
        rows = [[f.name, _canonical_configuration_value(getattr(config, f.name))]
                for f in fields(StrategyConfig)]
        rows.append(['max_total_breakout_attempts',
                     _canonical_configuration_value(config.max_total_breakout_attempts)])
        payload = json.dumps([_CONFIG_DOMAIN, rows], ensure_ascii=True,
                             separators=(',', ':'), allow_nan=False).encode('ascii')
        return 'cfg-v1-' + hashlib.sha256(payload).hexdigest()
    except Exception:
        raise ReadOnlyError(_ERROR) from None


def _copy_order(order):
    _require(type(order) is OrderRequest)
    _require(type(order.mode) is TradingMode and order.mode is TradingMode.PAPER
             and type(order.side) is Side and order.side is Side.BUY
             and type(order.order_type) is OrderType and order.order_type is OrderType.MARKET
             and type(order.intent) is OrderIntent and order.intent is OrderIntent.ENTRY
             and type(order.time_in_force) is TimeInForce and order.time_in_force is TimeInForce.DAY
             and order.limit_price is None and order.stop_price is None)
    return j._JournalOrderIntent(*(_identifier(getattr(order, name)) for name in
        ('client_order_id', 'security_id', 'symbol', 'currency')), 'BUY',
        _decimal(order.quantity, positive=True), 'MKT', 'DAY',
        _timestamp(order.created_at), _timestamp(order.expires_at))


def _witness(kind, origin, level, approval, eligible):
    _require(type(origin) is MarketInterval and type(origin.classification) is IntervalClassification
             and origin.classification is IntervalClassification.TRADED)
    at = _timestamp(origin.timestamp)
    _require(all(type(getattr(origin, name)) is Decimal for name in ('open', 'high', 'low', 'close'))
             and type(origin.volume) in (Decimal, Fraction))
    MarketInterval.__post_init__(origin)
    _require(origin.security_id == approval.security_id and origin.ticker == approval.ticker)
    _require(_instant(at) + timedelta(minutes=1) <= _instant(eligible))
    return j._StrategyWitness(kind, _decimal(level, positive=True), at, _identifier(origin.source_id))


def _tick_source(rule, purpose, price, approval, eligible):
    _require(type(rule) is TickRule and type(rule.purpose) is TickPurpose and rule.purpose is purpose)
    for at in (rule.valid_from, rule.available_at):
        _timestamp(at)
    if rule.valid_until is not None:
        _timestamp(rule.valid_until)
    _require(type(rule.execution_context) is str)
    TickRule.__post_init__(rule)
    _require(rule.verified is True and rule.data_quality_reason is None
             and rule.security_id == approval.security_id
             and rule.share_basis_id == approval.eligibility.share_basis_id)
    _require(_instant(rule.available_at) <= _instant(eligible)
             and _instant(rule.valid_from) <= _instant(eligible)
             and (rule.valid_until is None or _instant(eligible) < _instant(rule.valid_until)))
    tick, grid, minimum = (_decimal(getattr(rule, name)) for name in
                           ('tick_size', 'grid_origin', 'minimum_price'))
    _require(tick > 0 and minimum <= price
             and (rule.maximum_price is None or price < _decimal(rule.maximum_price)))
    # Validate the supplied final-band result, never look up or normalize a price.
    _require(((Fraction(price) - Fraction(grid)) / Fraction(tick)).denominator == 1)
    return _identifier(rule.source_id)


@dataclass(frozen=True, slots=True, repr=False)
class _DispatchJournalContext:
    order: j._JournalOrderIntent
    contract: j._JournalContractSnapshot
    strategy: j._JournalStrategyContext
    risk: j._JournalRiskContext
    validated_at: datetime

    def __repr__(self):
        return '<PrivateDispatchJournalContext>'


def _adapt_dispatch_journal_context(order, permission, approval, position, contract, *, at):
    """Copy supplied Phase 5/6 references; no eligibility/sizing recalculation.

    `at` must be explicitly supplied. It grants no future freshness guarantee.
    Account totals beyond required reference BOD equity remain absent in risk.
    """
    try:
        at = _timestamp(at)
        intent = _copy_order(order)
        _require(type(permission) is RiskPermission and type(permission.permits_entry) is bool)
        risk_order = _copy_order(permission.order)
        _require(risk_order == intent
                 and _instant(risk_order.created_at) == _instant(intent.created_at)
                 and _instant(risk_order.expires_at) == _instant(intent.expires_at))
        risk = j._JournalRiskContext(risk_order, permission.permits_entry,
            _timestamp(permission.available_at), _timestamp(permission.valid_until),
            _identifier(permission.source_id))
        _identifier(permission.reason_code)
        _require(_instant(intent.created_at) <= _instant(at) < _instant(intent.expires_at)
                 and _instant(risk.available_at) <= _instant(at) < _instant(risk.valid_until))
        _require(type(contract) is IBKRContract)
        copied_contract = j._JournalContractSnapshot(_identifier(contract.security_id),
            contract.con_id, contract.security_type, contract.exchange, _identifier(contract.symbol),
            _identifier(contract.currency), _timestamp(contract.valid_from),
            _timestamp(contract.valid_until), _timestamp(contract.available_at),
            _identifier(contract.source_id), contract.verified)
        _require(copied_contract.verified is True
                 and (intent.security_id, intent.symbol, intent.currency) ==
                     (copied_contract.security_id, copied_contract.symbol, copied_contract.currency)
                 and _instant(copied_contract.available_at) <= _instant(at)
                 and _instant(copied_contract.valid_from) <= _instant(at) < _instant(copied_contract.valid_until))
        _require(type(approval) is EntryApproval and type(position) is InitialPosition
                 and type(position.approval) is EntryApproval
                 and type(position.config) is StrategyConfig)
        if position.approval is not approval:
            _require(_approval_semantics(position.approval) == _approval_semantics(approval))
        configuration = StrategyConfig(**{f.name: getattr(position.config, f.name)
                                         for f in fields(StrategyConfig)})
        _require(configuration == FROZEN_V1)
        _require(type(position.quantity) is int
                 and configuration.min_entry_shares <= position.quantity <= configuration.max_shares_per_position)
        quantity = _decimal(Decimal(position.quantity), positive=True)
        _require(quantity == intent.quantity and approval.security_id == intent.security_id
                 and approval.ticker == intent.symbol)
        eligible = _timestamp(approval.scheduled_timestamp)
        _require(_instant(eligible) <= _instant(intent.created_at)
                 and eligible.second == 0 and eligible.microsecond == 0)
        _identifier(approval.opening_source_id)
        _require(type(approval.session) is TradingSession and type(approval.eligibility) is OpeningEligibility)
        _timestamp(approval.session.open)
        _timestamp(approval.session.close)
        TradingSession.__post_init__(approval.session)
        _timestamp(approval.eligibility.available_at)
        _require(type(approval.eligibility.normalization_effective_at) is tuple)
        for normalized_at in approval.eligibility.normalization_effective_at:
            _timestamp(normalized_at)
        OpeningEligibility.__post_init__(approval.eligibility)
        _identifier(approval.eligibility.source_id)
        _identifier(approval.eligibility.share_basis_id)
        _require(_instant(approval.eligibility.available_at) <= _instant(eligible))
        _identifier(approval.session.source_id)
        day = approval.session.trading_date
        _require(type(day) is date and eligible.astimezone(NEW_YORK).date() == day
                 and _instant(approval.session.open) <= _instant(eligible) < _instant(approval.session.close))
        _require(type(approval.c_evidence) is CLockEvidence)
        witnesses = (
            _witness(j._WitnessKind.A, approval.a, approval.a.low, approval, eligible),
            _witness(j._WitnessKind.B, approval.b_origin, approval.b_price, approval, eligible),
            _witness(j._WitnessKind.C, approval.c_evidence.c_origin, approval.c_price, approval, eligible),
            _witness(j._WitnessKind.D, approval.d, approval.d.close, approval, eligible))
        _require(approval.b_origin.high == witnesses[1].level
                 and approval.c_evidence.c_origin.low == witnesses[2].level
                 and _instant(approval.d.timestamp) + timedelta(minutes=1) == _instant(eligible)
                 and _instant(approval.d_confirmation_timestamp) == _instant(eligible))
        _require(_instant(witnesses[0].timestamp) <= _instant(witnesses[1].timestamp)
                 <= _instant(witnesses[2].timestamp) < _instant(witnesses[3].timestamp))
        _require(type(position.account_before) is PortfolioSnapshot)
        account = position.account_before
        _timestamp(account.timestamp)
        _timestamp(account.available_at)
        PortfolioSnapshot.__post_init__(account)
        _identifier(account.source_id)
        _require(_instant(account.available_at) <= _instant(eligible)
                 and _instant(account.timestamp) <= _instant(eligible))
        stop = _decimal(position.executable_stop, positive=True)
        target = _decimal(position.executable_target, positive=True)
        entry = _decimal(approval.reference_open, positive=True)
        raw_stop = _decimal(position.raw_stop, positive=True)
        raw_target = _decimal(position.raw_target, positive=True)
        budget = _decimal(position.risk_budget, positive=True)
        bod = _decimal(account.beginning_of_day_equity, positive=True)
        # Check supplied Phase 6 results with exact rational comparisons. Never
        # calculate replacement fields, rerun sizing, or query a tick supplier.
        reference_risk = Fraction(entry) - Fraction(stop)
        _require(Fraction(raw_stop) == Fraction(witnesses[2].level) *
                 (1 - Fraction(configuration.initial_stop_buffer_below_c))
                 and Fraction(raw_target) == Fraction(entry) +
                 Fraction(configuration.partial_target_r_multiple) * reference_risk
                 and Fraction(_decimal(position.risk_per_share, positive=True)) == reference_risk
                 and Fraction(budget) == Fraction(bod) * Fraction(configuration.risk_fraction_bod))
        stop_source = _tick_source(position.stop_tick, TickPurpose.INITIAL_STOP, stop, approval, eligible)
        target_source = _tick_source(position.target_tick, TickPurpose.TARGET_2R, target, approval, eligible)
        strategy = j._JournalStrategyContext('v1.0', _configuration_fingerprint(configuration),
            _identifier(approval.setup_id), intent.security_id, intent.symbol,
            date(day.year, day.month, day.day), witnesses, eligible,
            entry, raw_stop, stop, raw_target, target, quantity, budget, bod, stop_source, target_source)
        return _DispatchJournalContext(intent, copied_contract, strategy, risk, at)
    except Exception:
        # No raw values, broker text or source repr/cause escapes. Interruptions
        # inherit BaseException and propagate unchanged, without a partial result.
        raise ReadOnlyError(_ERROR) from None
