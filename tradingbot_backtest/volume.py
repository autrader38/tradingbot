"""Chronological RVOL evidence; no price signals or execution model."""

from dataclasses import dataclass
from fractions import Fraction

from .market import IntervalClassification as Kind, MarketInterval
from .sessions import NEW_YORK


@dataclass(frozen=True, slots=True)
class VolumeEvidence:
    candle: MarketInterval
    preceding: tuple[MarketInterval, ...]
    baseline: Fraction | None
    ratio: Fraction | None

    def qualifies(self, threshold: Fraction) -> bool:
        return self.ratio is not None and self.ratio >= threshold


def volume_evidence(candle: MarketInterval, history: tuple[MarketInterval, ...],
                    lookback: int) -> VolumeEvidence:
    """Caller supplies continuous eligible same-date history, before candle."""
    if candle.classification != Kind.TRADED:
        raise ValueError("Only a traded candle can qualify relative volume")
    if type(lookback) is not int or lookback <= 0:
        raise ValueError("Lookback must be a positive integer")
    preceding = history[-lookback:]
    if any(bar.timestamp.astimezone(NEW_YORK).date() != candle.timestamp.astimezone(NEW_YORK).date()
           for bar in preceding):
        raise ValueError("Previous-session volume is prohibited")
    if preceding and preceding[-1].end != candle.timestamp:
        raise ValueError("Baseline must immediately precede the evaluated interval")
    if any(right.timestamp != left.end for left, right in zip(preceding, preceding[1:])):
        raise ValueError("Baseline cannot bridge missing chronological slots")
    if any(bar.classification not in (Kind.TRADED, Kind.NO_TRADE) for bar in preceding):
        raise ValueError("Volume history cannot bridge unknown data")
    if any(bar.security_id != candle.security_id or bar.end > candle.timestamp
           for bar in preceding):
        raise ValueError("Baseline must precede the same security's evaluated candle")
    if len(preceding) != lookback:
        return VolumeEvidence(candle, preceding, None, None)
    baseline = sum((Fraction(bar.volume) for bar in preceding), Fraction()) / lookback
    return VolumeEvidence(candle, preceding, baseline,
                          Fraction(candle.volume) / baseline if baseline else None)
