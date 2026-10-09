"""Point-in-time eligibility and verified split arithmetic, not a live scanner."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

from .codes import ReasonCode
from .config import FROZEN_V1, StrategyConfig
from .historical_data import (Adjustment, HistoricalDataset, HistoricalInputError,
                              DailySession, MarketCapitalization, PriorClose, Provenance,
                              SecurityReference, SecurityType, SplitAction)
from .numerics import multiply


@dataclass(frozen=True, slots=True)
class NormalizationEvidence:
    security_id: str
    evaluated_at: datetime
    source_id: str
    raw_value: Fraction
    value: Fraction
    raw_basis: str
    required_basis: str
    cumulative_factor: Fraction
    action_sources: tuple[str, ...]
    volume: bool
    provenance: Provenance
    actions: tuple[SplitAction, ...]


def normalize(value: Decimal | Fraction, provenance: Provenance, security_id: str,
              required_basis: str, at: datetime, dataset: HistoricalDataset,
              *, volume: bool = False) -> NormalizationEvidence:
    """No rounding: a nonterminating normalized price remains an exact Fraction."""
    if provenance.available_at > at:
        raise HistoricalInputError('Source unavailable at evaluation timestamp')
    if provenance.quality_reason:
        raise HistoricalInputError(provenance.quality_reason)
    if (not provenance.verified or provenance.adjustment not in (Adjustment.RAW, Adjustment.VERIFIED_POINT_IN_TIME)):
        raise HistoricalInputError('Unverified/unavailable/unsafe adjustment methodology')
    if type(value) not in (Decimal, Fraction) or (isinstance(value, Decimal) and not value.is_finite()):
        raise HistoricalInputError('Nonfinite or inexact source value')
    raw = Fraction(value)
    basis, factor, sources, visited = provenance.share_basis_id, Fraction(1), [], set()
    consulted = []
    actions = dataset.split_actions(security_id)
    while basis != required_basis:
        if basis in visited:
            raise HistoricalInputError('Cyclic/conflicting corporate-action history')
        visited.add(basis)
        applicable = [a for a in actions if a.security_id == security_id
                      and a.old_basis == basis and a.effective_at <= at and a.available_at <= at]
        if len(applicable) != 1:
            raise HistoricalInputError('Required compatible corporate-action path missing/conflicting')
        action = applicable[0]
        if action.available_at > at or not action.verified or not action.simple_share_denomination:
            raise HistoricalInputError('Required corporate-action terms unavailable or complex')
        factor *= action.factor
        basis = action.new_basis
        sources.append(action.source_id)
        consulted.append(action)
    # Explicitly verified exact required basis is already normalized, never adjusted twice.
    normalized = raw * factor if volume else raw / factor
    return NormalizationEvidence(security_id, at, provenance.source_id, raw, normalized,
                                 provenance.share_basis_id, basis, factor, tuple(sources), volume,
                                 provenance,tuple(consulted))


@dataclass(frozen=True, slots=True)
class UniverseEvaluation:
    security_id: str
    ticker: str
    evaluated_at: datetime
    static_eligible: bool
    price_eligible: bool | None
    change_eligible: bool | None
    prior_close: Fraction | None
    share_basis_id: str | None
    market_cap: Decimal | None
    adv10: Fraction | None
    adr20_pct: Fraction | None
    adr_category: str | None
    failures: tuple[str, ...]
    history_dates: tuple[date, ...]
    normalization: tuple[NormalizationEvidence, ...]
    source_ids: tuple[str, ...]
    daily_records: tuple[DailySession, ...]
    official_prior_close_record: PriorClose | None
    market_cap_records: tuple[MarketCapitalization, ...]

    @property
    def eligible(self) -> bool:
        return self.static_eligible and self.price_eligible is True and self.change_eligible is True


def historical_reference(dataset: HistoricalDataset, security: str, at: datetime) -> SecurityReference | None:
    matches = [r for r in dataset.security_references() if r.security_id == security
               and r.available_at <= at and r.valid_from <= at
               and (r.valid_until is None or at < r.valid_until)]
    if len(matches) > 1:
        raise HistoricalInputError('Conflicting security-master records')
    return matches[0] if matches else None


def preceding_sessions(dataset: HistoricalDataset, reference: SecurityReference, day: date,
                       count: int) -> tuple[date, ...]:
    expected = []
    candidate = day-timedelta(days=1)
    while candidate >= reference.listing_date and len(expected) < count:
        session = dataset.calendar.session_for(candidate)
        if session is not None:
            expected.append(candidate)
        candidate -= timedelta(days=1)
    return tuple(reversed(expected))


def evaluate_universe(dataset: HistoricalDataset, security: str, at: datetime,
                      price: Decimal | None, config: StrategyConfig = FROZEN_V1) -> UniverseEvaluation:
    """Static gates at their point-in-time applicability; dynamic price is separate.

    ADR20 and daily-history availability are fixed before today's RTH start.
    Market-cap applicability is supplied explicitly, never inferred from age.
    """
    if price is not None and (type(price) is not Decimal or not price.is_finite() or price <= 0):
        raise HistoricalInputError('Point-in-time prices require trustworthy positive exact Decimals')
    from .sessions import NEW_YORK
    day = at.astimezone(NEW_YORK).date()
    session = dataset.calendar.session_for(day)
    failures, evidence, sources = [], [], []
    ref = historical_reference(dataset, security, at)
    basis = dataset.session_basis(security, day)
    prior = None
    cap, adv, adr, category = None, None, None, None
    dates = ()
    known_daily, prior_record, known_caps = (), None, ()
    if (ref is None or not ref.verified or ref.listing_available_at > at
            or ref.listing_date > day or (ref.delisting_at is not None and at >= ref.delisting_at)):
        failures.append('SECURITY_MASTER_UNAVAILABLE')
    elif (ref.security_type != SecurityType.COMMON_STOCK or not ref.us_listed
          or not ref.supported_exchange or ref.otc):
        failures.append('EXCLUDED_SECURITY_TYPE_OR_LISTING')
    if session is None:
        failures.append('NON_SESSION')
    if (basis is None or not basis.verified or basis.available_at > at or basis.effective_at > at
            or basis.security_id != security or basis.trading_date != day):
        failures.append(ReasonCode.CORPORATE_ACTION_DATA_UNAVAILABLE.value)
        basis = None
    if ref is not None and session is not None and basis is not None and not failures:
        sources.extend((ref.source_id, basis.source_id))
        dates = preceding_sessions(dataset, ref, day, config.adr_lookback_sessions)
        # Official prior close uses the immediately preceding applicable exchange session.
        yesterday = day-timedelta(days=1)
        while dataset.calendar.session_for(yesterday) is None:
            yesterday -= timedelta(days=1)
        candidates = [r for r in dataset.prior_closes(security) if r.security_id == security and r.trading_date == yesterday
                      and r.provenance.available_at <= at]
        if len(candidates) == 1:
            prior_record = candidates[0]
        if len(candidates) == 1 and candidates[0].official_regular_close and candidates[0].price is not None:
            record = candidates[0]
            try:
                item = normalize(record.price, record.provenance, security, basis.basis_id, at, dataset)
                if item.value <= 0:
                    raise HistoricalInputError('Official prior close must be positive')
                evidence.append(item); prior = item.value
            except HistoricalInputError as exc:
                failures.append(ReasonCode.CORPORATE_ACTION_DATA_UNAVAILABLE.value+': '+str(exc))
        else:
            failures.append('OFFICIAL_PRIOR_CLOSE_UNAVAILABLE')
        caps = [r for r in dataset.market_caps(security) if r.security_id == security and r.available_at <= at and r.valid_from <= at < r.valid_until]
        known_caps = tuple(sorted(caps,key=repr))
        if len(caps) > 1:
            failures.append('MARKET_CAP_DATA_UNAVAILABLE: conflicting applicability records')
        elif len(caps) == 1 and caps[0].quality_reason:
            failures.append('MARKET_CAP_DATA_UNAVAILABLE: '+caps[0].quality_reason)
        if len(caps) == 1 and caps[0].verified and not caps[0].quality_reason:
            record = caps[0]
            if record.authoritative_value is not None:
                cap = record.authoritative_value
            elif (record.share_count_point_in_time_verified and record.shares_outstanding is not None
                  and record.compatible_price is not None and record.shares_basis_id == record.price_basis_id
                  and record.price_basis_id == basis.basis_id
                  and record.shares_outstanding.is_finite() and record.shares_outstanding > 0
                  and record.compatible_price.is_finite() and record.compatible_price > 0):
                cap = multiply(record.shares_outstanding, record.compatible_price)
            sources.append(record.source_id)
        if cap is None or not cap.is_finite() or cap <= 0:
            failures.append(ReasonCode.MARKET_CAP_DATA_UNAVAILABLE.value)
        elif cap < config.min_market_cap_usd:
            failures.append('MARKET_CAP_BELOW_MINIMUM')
        history = dataset.daily_sessions(security)
        known_daily = tuple(sorted((r for r in history if r.trading_date in dates
            and r.provenance.available_at <= session.open),key=lambda r:(r.trading_date,repr(r))))
        daily = {}
        for expected in dates:
            matches = [r for r in history if r.trading_date == expected
                       and r.provenance.available_at <= session.open]
            if len(matches) > 1:
                failures.append(f'REQUIRED_HISTORY_{expected}: conflicting daily session records')
            if len(matches) == 1:
                r = matches[0]
                historical_session = dataset.calendar.session_for(expected)
                if (r.security_id == security and r.trustworthy and not r.quality_reason
                        and r.completed_at == historical_session.close
                        and r.provenance.available_at >= r.completed_at
                        and r.provenance.available_at <= session.open):
                    daily[expected] = r
                else:
                    failures.append(f'REQUIRED_HISTORY_{expected}: {r.quality_reason or r.provenance.quality_reason or "untrustworthy completed session"}')
        adv_dates = dates[-config.adv_lookback_sessions:]
        if len(adv_dates) < config.adv_lookback_sessions or any(d not in daily for d in adv_dates):
            failures.append(ReasonCode.INSUFFICIENT_ADV_HISTORY.value)
        else:
            volumes = []
            try:
                for d in adv_dates:
                    r = daily[d]
                    if r.volume is None or not r.volume.is_finite() or r.volume < 0:
                        raise HistoricalInputError('Required session share volume unavailable')
                    item = normalize(r.volume, r.provenance, security, basis.basis_id, session.open, dataset, volume=True)
                    evidence.append(item); volumes.append(item.value)
                adv = sum(volumes, Fraction())/config.adv_lookback_sessions
                if adv <= config.minimum_adv_shares:
                    failures.append('ADV10_NOT_ABOVE_MINIMUM')
            except HistoricalInputError as exc:
                failures.extend((ReasonCode.INSUFFICIENT_ADV_HISTORY.value,
                                 ReasonCode.CORPORATE_ACTION_DATA_UNAVAILABLE.value+': '+str(exc)))
        if len(dates) < config.adr_lookback_sessions:
            failures.append(ReasonCode.INSUFFICIENT_ADR20_HISTORY.value)
        elif any(d not in daily for d in dates):
            failures.append(ReasonCode.ADR20_DATA_UNAVAILABLE.value)
        else:
            try:
                ranges = []
                for d in dates:
                    r = daily[d]
                    if (any(v is None or not v.is_finite() or v <= 0 for v in (r.high,r.low,r.close))
                            or r.high < r.low or not r.low <= r.close <= r.high):
                        raise HistoricalInputError('Required trustworthy daily HLC unavailable')
                    # Verify the basis/methodology even though the percentage is scale invariant.
                    normalize(r.close,r.provenance,security,basis.basis_id,session.open,dataset)
                    ranges.append((Fraction(r.high)-Fraction(r.low))/Fraction(r.close)*100)
                adr = sum(ranges,Fraction())/config.adr_lookback_sessions
                category = 'LOW' if adr < config.high_min_adr_pct else ('HIGH' if adr < config.very_high_min_adr_pct else 'VERY_HIGH')
                if category == 'LOW': failures.append('ADR20_BELOW_MINIMUM')
            except HistoricalInputError as exc:
                failures.extend((ReasonCode.ADR20_DATA_UNAVAILABLE.value,
                                 ReasonCode.CORPORATE_ACTION_DATA_UNAVAILABLE.value+': '+str(exc)))
    dynamic_price = price <= config.max_price_usd if price is not None else None
    change = ((Fraction(price)-prior)/prior >= config.min_current_day_change
              if price is not None and prior is not None else None)
    return UniverseEvaluation(security,ref.ticker if ref else '',at,not failures,dynamic_price,change,prior,
                              basis.basis_id if basis else None,cap,adv,adr,category,tuple(failures),dates,
                              tuple(evidence),tuple(sorted(set(sources))),known_daily,prior_record,known_caps)
