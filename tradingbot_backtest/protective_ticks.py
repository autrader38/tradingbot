"""Point-in-time DOWN normalization of supplied protective-stop grids only."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction

from .audit import AuditValue
from .numerics import add, multiply, subtract
from .trade_construction import TickDirection, TickQuery, TickRule, TickSource


@dataclass(frozen=True, slots=True)
class TickEvidence:
    event: str
    details: tuple[tuple[str, AuditValue], ...]


@dataclass(frozen=True, slots=True)
class ProtectiveLevel:
    price: Decimal | None
    rule: TickRule | None
    bands: tuple[TickRule, ...]
    failure: str | None
    evidence: tuple[TickEvidence, ...]


def normalize_protective_stop(query: TickQuery, source: TickSource, *,
                              required_until: datetime | None = None) -> ProtectiveLevel:
    """Find the nearest valid DOWN price, never extrapolating a supplied band.

    Intrabar activation has no known sub-minute time: its supplied rule must be
    known at interval start and cover every possible activation instant through
    the interval's exclusive end. Point events require validity at query.timestamp.
    This is a metadata sufficiency check, not an invented historical tick schedule.
    """
    evidence: list[TickEvidence] = []
    bands: list[TickRule] = []
    failure: str | None = None

    def record(event: str, **details: AuditValue) -> None:
        evidence.append(TickEvidence(event, tuple(details.items())))

    def read(found: TickRule | tuple[TickRule, ...] | None, boundary: Decimal | None) -> TickRule | None:
        nonlocal failure
        matches = () if found is None else found if isinstance(found, tuple) else (found,)
        if not matches:
            failure = 'TICK_METADATA_UNAVAILABLE'
            record('PROTECTIVE_TICK_UNAVAILABLE', boundary=boundary, cause=failure)
            return None
        for rule in matches:
            if rule.available_at > query.timestamp:
                failure = 'TICK_METADATA_NOT_YET_AVAILABLE'
                record('PROTECTIVE_TICK_UNAVAILABLE', boundary=boundary, available_at=rule.available_at, cause=failure)
                return None
        for rule in matches:
            record('PROTECTIVE_TICK_METADATA', source_id=rule.source_id, boundary=boundary,
                   security_id=rule.security_id, share_basis_id=rule.share_basis_id, purpose=rule.purpose.value,
                   tick=rule.tick_size, grid_origin=rule.grid_origin, minimum=rule.minimum_price,
                   maximum=rule.maximum_price, verified=rule.verified, valid_from=rule.valid_from,
                   valid_until=rule.valid_until, available_at=rule.available_at,
                   execution_context=rule.execution_context, data_quality_reason=rule.data_quality_reason)
        if len(matches) != 1:
            failure = 'CONFLICTING_TICK_BANDS'
            record('PROTECTIVE_TICK_REJECTED', failures=failure)
            return None
        rule = matches[0]
        checks = (
            (not rule.verified, 'UNVERIFIED_TICK_RULE'),
            (rule.data_quality_reason is not None, 'SOURCE_DATA_QUALITY_FAILURE'),
            (rule.tick_size <= 0, 'NONPOSITIVE_TICK'),
            (rule.security_id != query.security_id, 'SECURITY_ID_MISMATCH'),
            (rule.share_basis_id != query.share_basis_id, 'SHARE_BASIS_MISMATCH'),
            (rule.purpose != query.purpose, 'ORDER_PURPOSE_MISMATCH'),
            (rule.valid_from > query.timestamp, 'RULE_NOT_YET_EFFECTIVE'),
            (rule.valid_until is not None and rule.valid_until <= query.timestamp, 'RULE_EXPIRED'),
            (required_until is not None and rule.valid_until is not None and rule.valid_until < required_until,
             'INTRABAR_TICK_APPLICABILITY_UNAVAILABLE'),
            (rule.minimum_price < 0 or (rule.maximum_price is not None and rule.maximum_price <= rule.minimum_price),
             'INVALID_PRICE_BAND'),
        )
        if boundary is None:
            checks += ((query.raw_price < rule.minimum_price, 'RAW_PRICE_BELOW_VERIFIED_BAND'),
                       (rule.maximum_price is not None and query.raw_price >= rule.maximum_price,
                        'RAW_PRICE_AT_OR_ABOVE_VERIFIED_BAND'))
        else:
            checks += ((rule.maximum_price != boundary or rule.minimum_price >= boundary,
                        'ADJACENT_BAND_GAP_OR_OVERLAP'),)
        failures = tuple(label for failed, label in checks if failed)
        if failures:
            failure = '|'.join(failures)
            record('PROTECTIVE_TICK_REJECTED', failures=failure)
            return None
        bands.append(rule)
        return rule

    rule = read(source.resolve(query), None)
    probe, exclusive = query.raw_price, False
    while rule is not None:
        units = Fraction(subtract(probe, rule.grid_origin)) / Fraction(rule.tick_size)
        count = -(-units.numerator // units.denominator) - 1 if exclusive else units.numerator // units.denominator
        low = Fraction(subtract(rule.minimum_price, rule.grid_origin)) / Fraction(rule.tick_size)
        high = (Fraction(subtract(rule.maximum_price, rule.grid_origin)) / Fraction(rule.tick_size)
                if rule.maximum_price is not None else None)
        if count >= -(-low.numerator // low.denominator) and (high is None or count < -(-high.numerator // high.denominator)):
            price = add(rule.grid_origin, multiply(Decimal(count), rule.tick_size))
            if price <= 0:
                failure = 'NONPOSITIVE_PROTECTIVE_LEVEL'
                record('PROTECTIVE_TICK_REJECTED', failures=failure)
                break
            record('PROTECTIVE_LEVEL_NORMALIZED', raw=query.raw_price, executable=price,
                   direction=TickDirection.DOWN.value, bands_consulted=len(bands))
            return ProtectiveLevel(price, rule, tuple(bands), None, tuple(evidence))
        boundary = rule.minimum_price
        record('PROTECTIVE_TICK_BAND_CROSSING', boundary=boundary, from_source=rule.source_id)
        lookup = getattr(source, 'resolve_adjacent', None)
        next_rule = read(lookup(query, boundary, TickDirection.DOWN) if lookup else None, boundary)
        if next_rule is not None:
            record('PROTECTIVE_TICK_BAND_TRANSITION', boundary=boundary,
                   from_source=rule.source_id, to_source=next_rule.source_id,
                   from_tick=rule.tick_size, to_tick=next_rule.tick_size)
        rule, probe, exclusive = next_rule, boundary, True
    return ProtectiveLevel(None, None, tuple(bands), failure, tuple(evidence))
