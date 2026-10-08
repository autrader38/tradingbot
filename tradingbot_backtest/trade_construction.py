"""Single approved opening entry; no exits, lockout engine or portfolio loop."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from typing import Protocol

from .audit import AuditRecord, AuditValue
from .codes import ReasonCode
from .config import FROZEN_V1, StrategyConfig
from .entry_validation import EntryApproval
from .market import validate_timestamp
from .numerics import add, decimal, multiply, subtract


def _validate_decimal(value: Decimal) -> None:
    if type(value) is not Decimal:
        raise TypeError('Financial metadata requires exact Decimal values')
    decimal(value)


class TickPurpose(StrEnum):
    INITIAL_STOP = 'INITIAL_STOP'
    TARGET_2R = 'TARGET_2R'


class TickDirection(StrEnum):
    DOWN = 'DOWN'
    UP = 'UP'


@dataclass(frozen=True, slots=True)
class TickQuery:
    security_id: str
    ticker: str
    share_basis_id: str
    timestamp: datetime
    purpose: TickPurpose
    raw_price: Decimal


@dataclass(frozen=True, slots=True)
class TickRule:
    """Supplier-verified grid, price band and execution context; no default tick.

    Price bounds are inclusive below/exclusive above. The grid is valid only
    inside this band. Adjacent bands require separate supplied metadata;
    grid_origin is supplied explicitly, never inferred.
    """
    security_id: str
    share_basis_id: str
    purpose: TickPurpose
    tick_size: Decimal
    grid_origin: Decimal
    minimum_price: Decimal
    maximum_price: Decimal | None
    valid_from: datetime
    valid_until: datetime | None
    available_at: datetime
    source_id: str
    execution_context: str
    verified: bool
    data_quality_reason: str | None = None

    def __post_init__(self) -> None:
        for value in (self.tick_size, self.grid_origin, self.minimum_price):
            _validate_decimal(value)
        if self.maximum_price is not None:
            _validate_decimal(self.maximum_price)
        for value in (self.valid_from, self.available_at):
            validate_timestamp(value)
        if self.valid_until is not None:
            validate_timestamp(self.valid_until)
        if type(self.verified) is not bool or not isinstance(self.purpose, TickPurpose):
            raise TypeError('Supply explicit tick purpose and verification')
        if not all((self.security_id, self.share_basis_id, self.source_id, self.execution_context)):
            raise ValueError('Supply tick identity, basis and verified execution-context provenance')


class TickSource(Protocol):
    """Return authoritative metadata for this exact security/date/price/order query.

    Return the unique applicable rule, None when unavailable, or all conflicting
    matches as a tuple (never silently choose one). Historical schedules remain
    external. Exceptions are adapter failures, not guessed tick values.

    Adjacent lookup retains the ORIGINAL query/raw price. DOWN requests the band
    immediately below boundary (its exclusive maximum equals boundary); UP
    requests the band beginning at boundary (its inclusive minimum equals it).
    The source must establish adjacency and report conflicts/gaps. A legacy
    single-band source without resolve_adjacent cannot authorize a crossing.
    """
    def resolve(self, query: TickQuery) -> TickRule | tuple[TickRule, ...] | None: ...

    def resolve_adjacent(self, query: TickQuery, boundary: Decimal,
                         direction: TickDirection) -> TickRule | tuple[TickRule, ...] | None: ...


@dataclass(frozen=True, slots=True)
class HeldPosition:
    trade_id: str
    security_id: str
    ticker: str
    quantity: int
    current_open: Decimal | None
    opening_timestamp: datetime
    available_at: datetime
    source_id: str
    share_basis_id: str
    data_quality_reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.quantity) is not int or self.quantity <= 0:
            raise ValueError('Held quantities must be positive whole shares')
        if self.current_open is not None:
            _validate_decimal(self.current_open)
            if self.current_open <= 0:
                raise ValueError('Held OPEN must be positive, or None when unavailable')
        for value in (self.opening_timestamp, self.available_at):
            validate_timestamp(value)
        if not all((self.trade_id, self.security_id, self.ticker, self.source_id, self.share_basis_id)):
            raise ValueError('Supply held-position identity and current opening-price provenance')


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    """Supplied account state after required OPEN exits, before this entry.

    The owner supplies daily entry permission and later maintains daily counters.
    This record does not calculate lockouts or select/rank simultaneous entries.
    """
    timestamp: datetime
    available_at: datetime
    beginning_of_day_equity: Decimal
    current_equity: Decimal
    available_cash: Decimal
    exposure: Decimal
    positions: tuple[HeldPosition, ...]
    new_entries_permitted: bool
    source_id: str
    permission_reason: str | None = None

    def __post_init__(self) -> None:
        for value in (self.timestamp, self.available_at):
            validate_timestamp(value)
        for value in (self.beginning_of_day_equity, self.current_equity,
                      self.available_cash, self.exposure):
            _validate_decimal(value)
            if value < 0:
                raise ValueError('No negative account values or margin')
        if self.beginning_of_day_equity <= 0 or self.current_equity <= 0:
            raise ValueError('Equity must be positive')
        if not isinstance(self.positions, tuple) or type(self.new_entries_permitted) is not bool:
            raise TypeError('Supply immutable positions and explicit daily permission')
        if not self.source_id:
            raise ValueError('Supply account provenance')


@dataclass(frozen=True, slots=True)
class SizingLimits:
    risk: int
    shares: int
    position_value: int
    cash: int
    exposure: int

    @property
    def final_quantity(self) -> int:
        return min(self.risk, self.shares, self.position_value, self.cash, self.exposure)


def sizing_limits(entry: Decimal, risk_per_share: Decimal, risk_budget: Decimal,
                  equity: Decimal, cash: Decimal, exposure: Decimal,
                  config: StrategyConfig = FROZEN_V1) -> SizingLimits:
    """Exact cap arithmetic; the constructor separately validates account state."""
    e, risk = Fraction(decimal(entry)), Fraction(decimal(risk_per_share))
    budget, eq, available, used = map(Fraction, (decimal(risk_budget), decimal(equity),
                                               decimal(cash), decimal(exposure)))
    if e <= 0 or risk <= 0 or min(budget, eq, available, used) < 0:
        raise ValueError('Invalid sizing inputs')
    def floor(value: Fraction) -> int:
        return value.numerator // value.denominator
    return SizingLimits(floor(budget / risk), *_capital_limits(e, eq, available, used, config))


def _capital_limits(entry: Fraction, equity: Fraction, cash: Fraction, exposure: Fraction,
                    config: StrategyConfig) -> tuple[int, int, int, int]:
    """Caps knowable even when stop/risk metadata cannot yet be evaluated."""
    def floor(value: Fraction) -> int:
        return value.numerator // value.denominator
    capacity = max(Fraction(), equity * Fraction(config.max_total_exposure_equity_fraction) - exposure)
    return (config.max_shares_per_position,
            floor(equity * Fraction(config.max_new_position_equity_fraction) / entry),
            floor(cash / entry), floor(capacity / entry))


@dataclass(frozen=True, slots=True)
class InitialPosition:
    trade_id: str
    run_id: str
    data_version: str
    config: StrategyConfig
    approval: EntryApproval
    quantity: int
    position_value: Decimal
    risk_budget: Decimal
    raw_stop: Decimal
    executable_stop: Decimal
    risk_per_share: Decimal
    raw_target: Decimal
    executable_target: Decimal
    effective_target_r: Fraction
    stop_tick: TickRule
    target_tick: TickRule
    sizing: SizingLimits
    account_before: PortfolioSnapshot
    account_after: PortfolioSnapshot
    full_fill: bool
    commission: Decimal
    fees: Decimal
    slippage: Decimal

    @property
    def entry_price(self) -> Decimal:
        return self.approval.reference_open

    @property
    def entry_timestamp(self) -> datetime:
        return self.approval.scheduled_timestamp


@dataclass(frozen=True, slots=True)
class TradeConstructionResult:
    position: InitialPosition | None
    reason: ReasonCode | None
    description: str
    consumed: bool
    accepted_entry_delta: int
    audit: tuple[AuditRecord, ...]


class TradeConstructor:
    """One construction opportunity per setup; immutable, atomic account effects."""
    def __init__(self, *, run_id: str, data_version: str, config: StrategyConfig = FROZEN_V1):
        if any(value != 0 for value in (config.commission_baseline_usd, config.explicit_fee_baseline_usd,
                                       config.slippage_baseline)):
            raise ValueError('Phase 6 supports only the approved ZERO-FRICTION BASELINE')
        self.run_id, self.data_version, self.config = run_id, data_version, config
        self._seen: set[tuple[str, str]] = set()
        self._trade_ids: set[str] = set()

    def construct(self, approval: EntryApproval, account: PortfolioSnapshot,
                  ticks: TickSource, *, trade_id: str) -> TradeConstructionResult:
        if not isinstance(approval, EntryApproval):
            raise TypeError('Only a Phase 5 EntryApproval can advance to construction')
        if not isinstance(trade_id, str) or not trade_id.strip():
            raise ValueError('Supply a unique trade ID')
        key = approval.security_id, approval.setup_id
        if key in self._seen or trade_id in self._trade_ids:
            raise ValueError('Setup/trade already used its one construction opportunity')
        self._seen.add(key)
        self._trade_ids.add(trade_id)
        at, e = approval.scheduled_timestamp, approval.reference_open
        records: list[AuditRecord] = []
        failed_gates: list[str] = []
        primary_portfolio_failure: str | None = None
        def fail(gate: str) -> None:
            if gate not in failed_gates:
                failed_gates.append(gate)
        def audit(event: str, reason: ReasonCode | None = None, **details: AuditValue) -> None:
            records.append(AuditRecord(self.run_id, self.data_version, event, at,
                security_id=approval.security_id, ticker=approval.ticker, setup_id=approval.setup_id,
                trade_id=trade_id, modeled_event_at=at, interval_start=at, interval_end=at+timedelta(minutes=1),
                reason=reason, details=tuple(details.items())))
        def reject(description: str, reason: ReasonCode | None = None) -> TradeConstructionResult:
            fail(reason.value if reason else description)
            primary = primary_portfolio_failure or description
            primary_code = reason if primary == description else None
            audit('ENTRY_CONSTRUCTION_REJECTED', primary_code, description=primary,
                  failed_gates='|'.join(failed_gates), consumed=True, executed=False)
            return TradeConstructionResult(None, primary_code, primary, True, 0, tuple(records))
        if (decimal(e) <= 0 or decimal(approval.c_price) <= 0 or approval.d.end != at
                or approval.d_confirmation_timestamp != at or approval.intraday.reason is not None
                or approval.weekly.reason is not None or not approval.price_eligible or not approval.change_eligible
                or not (approval.session.open <= at < approval.session.close-self.config.entry_cutoff_before_session_close)):
            return reject('INVALID_PHASE5_APPROVAL', ReasonCode.ENTRY_DATA_INVALID)
        if account.available_at > at:
            audit('ENTRY_ACCOUNT_UNAVAILABLE', available_at=account.available_at)
            return reject('PORTFOLIO_STATE_UNAVAILABLE')
        if account.timestamp != at:
            audit('ENTRY_ACCOUNT_TIMESTAMP', supplied_timestamp=account.timestamp, expected_timestamp=at)
            return reject('PORTFOLIO_TIMESTAMP_MISMATCH')
        if not account.new_entries_permitted:
            audit('ENTRY_PERMISSION', permitted=False, supplied_reason=account.permission_reason)
            fail('SUPPLIED_DAILY_ENTRY_LOCKOUT')
            primary_portfolio_failure = 'SUPPLIED_DAILY_ENTRY_LOCKOUT'
        total = Decimal(0)
        seen_securities, seen_tickers = set(), set()
        for held in account.positions:
            if (held.opening_timestamp != at or held.available_at < held.opening_timestamp
                    or held.available_at > at or held.current_open is None
                    or held.data_quality_reason is not None):
                audit('ENTRY_HELD_OPEN_UNAVAILABLE', held_security=held.security_id,
                      available_at=held.available_at,
                      data_quality_reason=held.data_quality_reason if held.available_at <= at else None)
                return reject('PORTFOLIO_VALUATION_UNAVAILABLE')
            if held.security_id in seen_securities or held.ticker in seen_tickers:
                return reject('INVALID_PORTFOLIO_DUPLICATE_POSITION')
            seen_securities.add(held.security_id)
            seen_tickers.add(held.ticker)
            value = multiply(held.current_open, Decimal(held.quantity))
            total = add(total, value)
            audit('ENTRY_HELD_OPEN_MARK', held_security=held.security_id, held_ticker=held.ticker,
                  quantity=held.quantity, open=held.current_open, value=value, source_id=held.source_id,
                  share_basis=held.share_basis_id, available_at=held.available_at)
        if total != account.exposure or add(account.available_cash, total) != account.current_equity:
            audit('ENTRY_ACCOUNT_VALUATION_CONFLICT', computed_exposure=total, supplied_exposure=account.exposure,
                  computed_equity=add(account.available_cash, total), supplied_equity=account.current_equity,
                  cash=account.available_cash)
            return reject('INCONSISTENT_PORTFOLIO_VALUATION')
        audit('ENTRY_ACCOUNT', bod_equity=account.beginning_of_day_equity, equity=account.current_equity,
              cash=account.available_cash, exposure=total, positions=len(account.positions), source_id=account.source_id)
        if approval.security_id in seen_securities or approval.ticker in seen_tickers:
            fail('SAME_TICKER_POSITION_ALREADY_OPEN')
        if len(account.positions) >= self.config.max_simultaneous_positions:
            fail('MAXIMUM_SIMULTANEOUS_POSITIONS')
        exposure_cap = multiply(account.current_equity, self.config.max_total_exposure_equity_fraction)
        if total >= exposure_cap:
            fail('TOTAL_EXPOSURE_ALREADY_AT_OR_ABOVE_CAP')
        primary_portfolio_failure = failed_gates[0] if failed_gates else None
        capital = _capital_limits(Fraction(e), Fraction(account.current_equity),
                                  Fraction(account.available_cash), Fraction(total), self.config)
        capital_labels = ('SHARE_CAP_BELOW_MINIMUM', 'POSITION_VALUE_CAPACITY_BELOW_MINIMUM',
                          'CASH_CAPACITY_BELOW_MINIMUM', 'EXPOSURE_CAPACITY_BELOW_MINIMUM')
        def collect_quantity_failures(quantities: tuple[int, ...], labels: tuple[str, ...]) -> None:
            for quantity, label in zip(quantities, labels):
                if quantity < self.config.min_entry_shares:
                    fail(label)
            if min(quantities) < self.config.min_entry_shares:
                fail('ENTRY_QUANTITY_BELOW_MINIMUM')
        collect_quantity_failures(capital, capital_labels)
        audit('ENTRY_ACCOUNT_CAPACITIES', share_cap=capital[0], value_quantity=capital[1],
              cash_quantity=capital[2], exposure_quantity=capital[3],
              exposure_capacity=subtract(exposure_cap, total), maximum_quantity_without_risk=min(capital))
        # Preserve the supplied-lockout short circuit: no tick adapter is called.
        # All other available account gates/capital limits are still recorded.
        if not account.new_entries_permitted:
            audit('ENTRY_UNEVALUATED_GATES', gates='STOP_TICKS|RISK_QUANTITY|TARGET_TICKS',
                  cause='SUPPLIED_DAILY_ENTRY_LOCKOUT')
            return reject('SUPPLIED_DAILY_ENTRY_LOCKOUT')

        def normalize(raw: Decimal, purpose: TickPurpose) -> tuple[Decimal, TickRule] | None:
            query = TickQuery(approval.security_id, approval.ticker, approval.eligibility.share_basis_id, at, purpose, raw)
            direction = TickDirection.DOWN if purpose == TickPurpose.INITIAL_STOP else TickDirection.UP
            def read_rule(found: TickRule | tuple[TickRule, ...] | None,
                          boundary: Decimal | None = None) -> TickRule | None:
                matches = () if found is None else found if isinstance(found, tuple) else (found,)
                if not matches:
                    audit('ENTRY_TICK_UNAVAILABLE', purpose=purpose.value, raw_price=raw,
                          direction=direction.value, boundary=boundary, available_at=None)
                    return None
                # Availability precedes inspection of every substantive supplied field.
                for supplied in matches:
                    if supplied.available_at > at:
                        audit('ENTRY_TICK_UNAVAILABLE', purpose=purpose.value, raw_price=raw,
                              direction=direction.value, boundary=boundary, available_at=supplied.available_at)
                        return None
                for supplied in matches:
                    audit('ENTRY_TICK_METADATA', purpose=purpose.value, raw_price=raw,
                          direction=direction.value, boundary=boundary, source_id=supplied.source_id,
                          supplied_security=supplied.security_id, supplied_basis=supplied.share_basis_id,
                          supplied_purpose=supplied.purpose.value,
                          execution_context=supplied.execution_context, tick=supplied.tick_size,
                          grid_origin=supplied.grid_origin, minimum=supplied.minimum_price,
                          maximum=supplied.maximum_price, verified=supplied.verified,
                          available_at=supplied.available_at, valid_from=supplied.valid_from,
                          valid_until=supplied.valid_until, data_quality_reason=supplied.data_quality_reason)
                if len(matches) != 1:
                    audit('ENTRY_TICK_RULE_REJECTED', purpose=purpose.value, failures='CONFLICTING_TICK_BANDS')
                    return None
                rule = matches[0]
                checks = (
                    (not rule.verified, 'UNVERIFIED_TICK_RULE'),
                    (rule.data_quality_reason is not None, 'SOURCE_DATA_QUALITY_FAILURE'),
                    (rule.tick_size <= 0, 'NONPOSITIVE_TICK'),
                    (rule.security_id != query.security_id, 'SECURITY_ID_MISMATCH'),
                    (rule.share_basis_id != query.share_basis_id, 'SHARE_BASIS_MISMATCH'),
                    (rule.purpose != purpose, 'ORDER_PURPOSE_MISMATCH'),
                    (rule.valid_from > at, 'RULE_NOT_YET_EFFECTIVE'),
                    (rule.valid_until is not None and at >= rule.valid_until, 'RULE_EXPIRED'),
                    (rule.minimum_price < 0 or (rule.maximum_price is not None
                                               and rule.maximum_price <= rule.minimum_price), 'INVALID_PRICE_BAND'),
                )
                if boundary is None:
                    checks += ((raw < rule.minimum_price, 'RAW_PRICE_BELOW_VERIFIED_BAND'),
                               (rule.maximum_price is not None and raw >= rule.maximum_price,
                                'RAW_PRICE_AT_OR_ABOVE_VERIFIED_BAND'))
                elif direction == TickDirection.DOWN:
                    checks += ((rule.maximum_price != boundary or rule.minimum_price >= boundary,
                                'ADJACENT_BAND_GAP_OR_OVERLAP'),)
                else:
                    checks += ((rule.minimum_price != boundary, 'ADJACENT_BAND_GAP_OR_OVERLAP'),)
                failures = tuple(reason for failed, reason in checks if failed)
                if failures:
                    audit('ENTRY_TICK_RULE_REJECTED', purpose=purpose.value, failures='|'.join(failures))
                    return None
                return rule

            rule = read_rule(ticks.resolve(query))
            probe, exclusive_upper_edge = raw, False
            bands_consulted = 0
            while rule is not None:
                bands_consulted += 1
                units = Fraction(subtract(probe, rule.grid_origin)) / Fraction(rule.tick_size)
                ceil_count = -(-units.numerator // units.denominator)
                count = (ceil_count - 1 if exclusive_upper_edge else units.numerator // units.denominator
                         if direction == TickDirection.DOWN else ceil_count)
                minimum_units = Fraction(subtract(rule.minimum_price, rule.grid_origin)) / Fraction(rule.tick_size)
                minimum_count = -(-minimum_units.numerator // minimum_units.denominator)
                maximum_units = (Fraction(subtract(rule.maximum_price, rule.grid_origin)) / Fraction(rule.tick_size)
                                 if rule.maximum_price is not None else None)
                inside_band = (count >= minimum_count and (maximum_units is None
                               or count < -(-maximum_units.numerator // maximum_units.denominator)))
                if inside_band:
                    normalized = add(rule.grid_origin, multiply(Decimal(count), rule.tick_size))
                    audit('ENTRY_NORMALIZED_LEVEL', purpose=purpose.value, raw=raw, direction=direction.value,
                          executable=normalized, tick=rule.tick_size, bands_consulted=bands_consulted,
                          source_id=rule.source_id)
                    return normalized, rule
                # No price from this grid is extrapolated outside its verified band.
                boundary = rule.minimum_price if direction == TickDirection.DOWN else rule.maximum_price
                audit('ENTRY_TICK_BAND_CROSSING', purpose=purpose.value, raw=raw,
                      direction=direction.value, boundary=boundary, source_id=rule.source_id)
                adjacent = getattr(ticks, 'resolve_adjacent', None)
                next_rule = read_rule(adjacent(query, boundary, direction) if adjacent is not None else None, boundary)
                if next_rule is not None:
                    audit('ENTRY_TICK_BAND_TRANSITION', purpose=purpose.value, direction=direction.value,
                          boundary=boundary, from_source=rule.source_id, to_source=next_rule.source_id,
                          from_tick=rule.tick_size, to_tick=next_rule.tick_size)
                rule, probe, exclusive_upper_edge = next_rule, boundary, direction == TickDirection.DOWN
            return None

        raw_stop = multiply(approval.c_price, subtract(Decimal(1), self.config.initial_stop_buffer_below_c))
        stop_result = normalize(raw_stop, TickPurpose.INITIAL_STOP)
        if stop_result is None:
            return reject('INITIAL_STOP_TICK_UNAVAILABLE', ReasonCode.ENTRY_TICK_SIZE_UNAVAILABLE)
        stop, stop_tick = stop_result
        risk = subtract(e, stop)
        if stop <= 0 or risk <= 0:
            audit('ENTRY_INVALID_RISK', entry=e, stop=stop, risk_per_share=risk)
            return reject('INVALID_INITIAL_STOP_OR_RISK')
        budget = multiply(account.beginning_of_day_equity, self.config.risk_fraction_bod)
        limits = sizing_limits(e, risk, budget, account.current_equity, account.available_cash, total, self.config)
        quantity = limits.final_quantity
        collect_quantity_failures((limits.risk,), ('RISK_QUANTITY_BELOW_MINIMUM',))
        audit('ENTRY_SIZING', risk_quantity=limits.risk, share_cap=limits.shares,
              value_quantity=limits.position_value, cash_quantity=limits.cash,
              exposure_quantity=limits.exposure, exposure_capacity=subtract(exposure_cap, total), final_quantity=quantity)
        raw_target = add(e, multiply(self.config.partial_target_r_multiple, risk))
        target_result = normalize(raw_target, TickPurpose.TARGET_2R)
        if target_result is None:
            return reject('TARGET_TICK_UNAVAILABLE', ReasonCode.ENTRY_TARGET_TICK_SIZE_UNAVAILABLE)
        target, target_tick = target_result
        effective_r = Fraction(subtract(target, e)) / Fraction(risk)
        if target <= e or effective_r < self.config.partial_target_r_multiple:
            audit('ENTRY_INVALID_TARGET', entry=e, target=target, effective_r=str(effective_r))
            return reject('INVALID_INITIAL_TARGET')
        audit('ENTRY_INITIAL_RISK', entry=e, c=approval.c_price, raw_stop=raw_stop, stop=stop,
              risk_per_share=risk, bod_equity=account.beginning_of_day_equity, risk_budget=budget,
              raw_target=raw_target, target=target, effective_r=str(effective_r))
        if primary_portfolio_failure is not None:
            return reject(primary_portfolio_failure)
        if quantity < self.config.min_entry_shares:
            return reject('ENTRY_QUANTITY_BELOW_MINIMUM')
        value = multiply(e, Decimal(quantity))
        cash_after, exposure_after = subtract(account.available_cash, value), add(total, value)
        held = HeldPosition(trade_id, approval.security_id, approval.ticker, quantity, e, at, at,
                            approval.opening_source_id, approval.eligibility.share_basis_id)
        after = replace(account, available_cash=cash_after, exposure=exposure_after, positions=account.positions+(held,))
        position = InitialPosition(trade_id, self.run_id, self.data_version, self.config, approval, quantity, value, budget, raw_stop, stop, risk,
                                   raw_target, target, effective_r, stop_tick, target_tick, limits,
                                   account, after, True, Decimal(0), Decimal(0), Decimal(0))
        audit('SIMULATED_ENTRY_FILL', side='BUY', requested_quantity=quantity, filled_quantity=quantity,
              reference_price=e, simulated_fill_price=e, gross_value=value, full_fill=True,
              commission=Decimal(0), fees=Decimal(0), slippage=Decimal(0),
              cash_before=account.available_cash, cash_after=cash_after, exposure_before=total, exposure_after=exposure_after,
              equity_before=account.current_equity, equity_after=after.current_equity,
              positions_before=len(account.positions), positions_after=len(after.positions),
              accepted_entry_delta=1, completed_trade_delta=0, realized_pnl_delta=Decimal(0),
              execution_model='RESEARCH BACKTEST MODEL — V1.0', costs='ZERO-FRICTION BASELINE',
              fill_assumption='FULL-FILL RESEARCH ASSUMPTION — MARKET DEPTH AND PARTIAL FILLS NOT MODELED')
        return TradeConstructionResult(position, None, 'SIMULATED_ENTRY_FILLED', False, 1, tuple(records))
