"""Offline chronological driver: sources provide facts, existing phases trade."""

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

from .ab_detector import ABState, EligibilityInputs
from .audit import AuditRecord
from .c_detector import CState
from .config import FROZEN_V1, StrategyConfig
from .d_detector import DDetector, DState, EntryHandoff
from .entry_context import ContextMinute, WeeklyClassification, WeeklyHistory
from .entry_validation import EntryContext, EntryValidator, OpeningEligibility
from .historical_data import HistoricalDataset, HistoricalInputError, HistoricalMinute
from .historical_results import (BacktestResult, EquityPoint, SessionSummary, TradeLedger,
                                 manifest, serialized, summarize)
from .historical_universe import (UniverseEvaluation, evaluate_universe, historical_reference,
                                  normalize)
from .market import IntervalClassification as K, MarketInterval
from .numerics import multiply
from .portfolio import PortfolioEngine, PortfolioStatus
from .sessions import NEW_YORK
from .volume import volume_evidence


@dataclass(frozen=True, slots=True)
class HistoricalAudit:
    event: str
    available_at: datetime
    security_id: str | None
    details: tuple[tuple[str, object], ...]


class HistoricalDetector(DDetector):
    """Ingestion/ownership adapter; all pattern formulas remain in Phases 2–4.

    Price-history ownership and chronological volume are separate. Verified
    point-in-time static eligibility may change; prior-close corrections or
    share-basis changes require a clean dataset replay, never mixed patterns.
    """
    def reset_prices(self) -> None:
        self.state, self.c_state, self.d_state = ABState(), CState(), DState()
        self._price.clear()
        self._participants.clear()
        self._needs_restoration = False

    def volume_only(self, bar: MarketInterval, *, collect_prices: bool = False) -> None:
        if self._previous is not None and bar.timestamp != self._previous.end:
            raise HistoricalInputError('Volume ingestion cannot bridge eligible minutes')
        self._previous = bar
        if bar.classification in (K.MISSING, K.INVALID):
            self._volume.clear()
            self._price.clear()
        else:
            if collect_prices and bar.classification == K.TRADED:
                # Reference-independent history is not a signal. Original A
                # selection/activation still runs only with available eligibility.
                self._remember_price(volume_evidence(bar,tuple(self._volume),self.config.rvol_previous_intervals))
            self._volume.append(bar)
            self._volume = self._volume[-self.config.rvol_previous_intervals:]

    def seed(self, day: date, inputs: EligibilityInputs | None, history: tuple[MarketInterval, ...],
             *, basis: str) -> None:
        self._date, self._basis, self._eligibility_inputs = day, basis, inputs
        session = self.calendar.session_for(day)
        for bar in history:
            self.volume_only(bar,collect_prices=session.open <= bar.timestamp < session.close)

    def completed(self, bar: MarketInterval, inputs: EligibilityInputs) -> tuple[AuditRecord, ...]:
        old = self._eligibility_inputs
        if old is not None and (old.prior_regular_close != inputs.prior_regular_close
                                or old.share_basis_id != inputs.share_basis_id):
            raise HistoricalInputError('Corrected prior close/share basis requires whole-dataset replay')
        if self._date is None:
            self._date, self._basis = bar.timestamp.astimezone(NEW_YORK).date(), inputs.share_basis_id
        self._eligibility_inputs = inputs
        return self.feed(bar, inputs)


class HistoricalBacktest:
    """Each run owns fresh engines, with no mutable state shared between runs.

    Missing provider facts fail closed. Calendar unknowns/contract conflicts
    raise HistoricalInputError; explicit open-position gaps stop incompletely.
    No real provider, calendar, tick increment or supported venue is selected.
    """
    def __init__(self, dataset: HistoricalDataset, *, config: StrategyConfig = FROZEN_V1,
                 code_version: str | None = None, run_id: str = 'historical-run'):
        if not isinstance(run_id,str) or not run_id.strip():
            raise ValueError('Supply a nonempty research run identifier')
        self.dataset, self.config, self.code_version = dataset, config, code_version
        self.run_id = run_id

    def _index(self, security: str, day: date) -> dict[datetime, tuple[HistoricalMinute, ...]]:
        result = {}
        for record in self.dataset.minutes(security, day):
            if record.interval.security_id != security or record.interval.timestamp.astimezone(NEW_YORK).date() != day:
                raise HistoricalInputError('Minute dataset query returned incompatible identity/session')
            result.setdefault(record.interval.timestamp, []).append(record)
        return {at: tuple(records) for at, records in result.items()}

    @staticmethod
    def _known_source_ticker(index: dict[datetime, tuple[HistoricalMinute, ...]],
                             at: datetime, known: datetime, fallback: str) -> str:
        """Read an independently available minute identity, never a future alias.

        Used for historical seeding and volume-only reference-gap delivery.
        Normal eligible processing still checks the applicable security master.
        Conflicts/unavailable records remain rejected by _minute().
        """
        available = [r for r in index.get(at, ()) if r.opening_available_at <= known
                     and r.provenance.available_at <= known and r.provenance.verified]
        return available[0].interval.ticker if len(available) == 1 else fallback

    def _minute(self, index: dict[datetime, tuple[HistoricalMinute, ...]], security: str,
                ticker: str, at: datetime, basis: str | None, *, completed: bool,
                evaluation_at: datetime | None = None) -> tuple[MarketInterval, HistoricalMinute | None]:
        """Return source evidence only when available in the required phase."""
        known = evaluation_at or (at+timedelta(minutes=1) if completed else at)
        records = index.get(at, ())
        cause, kind, selected = 'missing source candle', K.MISSING, None
        # Envelope availability is distinct from later candle information.
        available = [r for r in records if r.opening_available_at <= known]
        if len(available) > 1:
            cause, kind = 'conflicting source intervals', K.INVALID
        elif available:
            selected = available[0]
            bar, p = selected.interval, selected.provenance
            if bar.ticker != ticker:
                cause, kind = 'historical symbol/source identity conflict', K.INVALID
            elif p.available_at > known:
                cause = 'source methodology unavailable at required event'
            elif not p.verified or p.quality_reason:
                cause, kind = p.quality_reason or 'unverified minute source', K.INVALID
            elif completed and selected.completed_available_at > known:
                cause = 'completed candle unavailable at interval completion'
            elif bar.classification in (K.MISSING, K.INVALID):
                return bar, selected
            elif bar.classification == K.NO_TRADE:
                # Verified zero volume has no price/share transformation to
                # determine. Preserve its classification, including at EOD.
                return bar, selected
            else:
                try:
                    # Current RTH prices must already be genuine execution-basis
                    # trades. Only historical/premarket share volume is rescaled.
                    if basis is None:
                        raise HistoricalInputError('verified session share basis unavailable')
                    session = self.dataset.calendar.session_for(at.astimezone(NEW_YORK).date())
                    if p.share_basis_id != basis and (evaluation_at is None or at >= session.open):
                        raise HistoricalInputError('current execution prices have incompatible units')
                    if not completed:
                        # Verify units without inspecting future completed volume.
                        normalize(Decimal(1),p,security,basis,known,self.dataset)
                        return bar, selected
                    item = normalize(bar.volume, p, security, basis, known, self.dataset, volume=True)
                    return replace(bar, volume=item.value) if bar.classification == K.TRADED else bar, selected
                except HistoricalInputError as exc:
                    cause, kind = 'CORPORATE_ACTION_DATA_UNAVAILABLE: '+str(exc), K.INVALID
        elif records:
            cause = 'opening information unavailable at required event'
        return MarketInterval(security, ticker, at, kind, 'historical-contract', data_quality_reason=cause), selected

    def _weekly(self, security: str, day: date, basis: str, opening: datetime) -> WeeklyHistory:
        ref = historical_reference(self.dataset, security, opening)
        bars = []
        current = day-timedelta(days=day.weekday())
        for record in self.dataset.weekly_history(security, day):
            bar = record.bar
            if bar.security_id != security:
                raise HistoricalInputError('Weekly query returned incompatible security identity')
            if bar.week_start >= current:
                continue
            if bar.available_at > opening or record.provenance.available_at > opening:
                expected_sessions = [self.dataset.calendar.session_for(bar.week_start+timedelta(days=i))
                                     for i in range(5)]
                expected_sessions = [s for s in expected_sessions if s is not None]
                if not expected_sessions:
                    continue
                bars.append(replace(bar, classification=WeeklyClassification.MISSING, high=None,
                    open=None, low=None, close=None, share_basis_id=basis,
                    completed_at=max(s.close for s in expected_sessions),
                    available_at=max(bar.available_at,record.provenance.available_at),
                    data_quality_reason='weekly source unavailable at OPEN'))
                continue
            if bar.classification != WeeklyClassification.VALID:
                bars.append(bar)
                continue
            try:
                values = {name: normalize(value, record.provenance, security, basis, opening, self.dataset).value
                          for name in ('high','open','low','close')
                          if (value := getattr(bar, name)) is not None}
                bars.append(replace(bar, share_basis_id=basis, **values))
            except HistoricalInputError as exc:
                bars.append(replace(bar, classification=WeeklyClassification.INVALID, high=None,
                    open=None, low=None, close=None,
                    data_quality_reason='CORPORATE_ACTION_DATA_UNAVAILABLE: '+str(exc)))
        return WeeklyHistory(ref.listing_date, ref.listing_available_at, ref.source_id, tuple(
            sorted(bars,key=lambda b:(b.week_start,b.source_id,serialized(b)))))

    def run(self, start: date, end: date) -> BacktestResult:
        if type(start) is not date or type(end) is not date or end < start:
            raise ValueError('Supply a chronological inclusive date range')
        ds, config = self.dataset, self.config
        identity = manifest(ds,start,end,config,self.code_version)
        run_id = self.run_id
        portfolio = PortfolioEngine(ds.calendar,ds.ticks,run_id=run_id,
                                    data_version=identity.dataset_sha256,config=config)
        validator = EntryValidator(ds.calendar,run_id=run_id,data_version=identity.dataset_sha256,config=config)
        audit, universes, points, sessions = [], [], [], []
        last_confirmed, incomplete_reason = config.starting_equity_usd, None
        day = start
        while day <= end:
            session = ds.calendar.session_for(day)
            if session is None:
                day += timedelta(days=1)
                continue
            audit.extend(portfolio.start_session(day).audit)
            points.append(EquityPoint(session.open,portfolio.state.bod_equity,'SESSION_START'))
            indices = {security:self._index(security,day) for security in identity.security_ids}
            detectors, contexts, last_inputs = {}, {}, {}
            max_positions, completed_before = 0, len(portfolio.completed)
            at = session.open
            while at < session.close:
                bars, evaluations, consumed = {}, {}, set()
                unavailable_references = set()
                held = {p.entry.approval.security_id:p for p in portfolio.state.positions}
                for security in identity.security_ids:
                    ref = historical_reference(ds,security,at)
                    if ref is None or not ref.verified or ref.listing_available_at > at or ref.listing_date > day or (
                            ref.delisting_at is not None and at >= ref.delisting_at):
                        if security in held:
                            raise HistoricalInputError('Held security identity/action unresolved; supply verified treatment')
                        exclusion = evaluate_universe(ds,security,at,None,config)
                        universes.append(exclusion)
                        audit.append(HistoricalAudit('UNIVERSE_EXCLUDED_AT_OPEN',at,security,
                            (('evaluation',exclusion),('reference_source',ref.source_id if ref else None),
                             ('delisted',ref is not None and ref.delisting_at is not None and at >= ref.delisting_at))))
                        if security in detectors:
                            # An existing detector must observe every chronological
                            # slot even while its eligibility reference is unavailable.
                            # Independently verified volume remains usable; prices
                            # cannot seed or advance a setup across this reset.
                            unavailable_references.add(security)
                            basis = ds.session_basis(security,day)
                            basis_id = (basis.basis_id if basis and basis.verified
                                and basis.available_at <= at and basis.effective_at <= at else None)
                            ticker = self._known_source_ticker(indices[security],at,at,detectors[security].ticker)
                            bar, _ = self._minute(indices[security],security,ticker,at,basis_id,completed=False)
                            bars[security], evaluations[security], last_inputs[security] = bar, exclusion, None
                        continue
                    if security in held and held[security].entry.approval.ticker != ref.ticker:
                        raise HistoricalInputError('Intraday held-symbol/action treatment requires verified input replay')
                    basis = ds.session_basis(security,day)
                    basis_id = basis.basis_id if basis and basis.verified and basis.available_at <= at and basis.effective_at <= at else None
                    bar, source = self._minute(indices[security],security,ref.ticker,at,basis_id,completed=False)
                    bars[security] = bar
                    result = evaluate_universe(ds,security,at,bar.open,config)
                    evaluations[security] = result
                    universes.append(result)
                    audit.append(HistoricalAudit('UNIVERSE_AT_OPEN',at,security,(('evaluation',result),)))
                    inputs = (EligibilityInputs(result.prior_close,result.static_eligible,result.share_basis_id)
                              if result.prior_close is not None and result.share_basis_id is not None else None)
                    if security in detectors and detectors[security].ticker != ref.ticker:
                        detector = detectors[security]
                        audit.append(HistoricalAudit('SECURITY_SYMBOL_CHANGED',at,security,
                            (('previous_ticker',detector.ticker),('ticker',ref.ticker),('source',ref.source_id))))
                        # Stable-ID history remains valid; source candles retain
                        # the symbols actually applicable at their timestamps.
                        detector.ticker = ref.ticker
                    if security not in detectors:
                        detector = HistoricalDetector(security,ref.ticker,ds.calendar,run_id=run_id,
                            data_version=identity.dataset_sha256,config=config)
                        detectors[security], contexts[security] = detector, []
                        if result.share_basis_id is not None:
                            history, seed_sources, cursor = [], [], datetime.combine(day,config.premarket_volume_history_start_et,NEW_YORK)
                            while cursor < at:
                                ticker = self._known_source_ticker(indices[security],cursor,at,ref.ticker)
                                seed, supplied = self._minute(indices[security],security,ticker,cursor,
                                    result.share_basis_id,completed=True,evaluation_at=at)
                                # Premarket seeds volume only. Known prior RTH
                                # candles may seed lookback, never retrospective signals.
                                history.append(seed)
                                if supplied and supplied.completed_available_at <= at and supplied.provenance.available_at <= at:
                                    seed_sources.append(supplied)
                                if cursor >= session.open:
                                    available = max(seed.end,supplied.completed_available_at,
                                                    supplied.provenance.available_at) if supplied else seed.end
                                    contexts[security].append(ContextMinute(seed,available,result.share_basis_id))
                                cursor += timedelta(minutes=1)
                            detector.seed(day,inputs,tuple(history),basis=result.share_basis_id)
                            audit.append(HistoricalAudit('VOLUME_HISTORY_SEEDED',at,security,
                                (('intervals',tuple(detector._volume)),
                                 ('sources',tuple(seed_sources)),
                                 ('price_lookback',tuple(item.candle for item in detector._price)),
                                 ('retrospective_signals',False))))
                    last_inputs[security] = inputs
                    old_inputs = detectors[security]._eligibility_inputs
                    if inputs is not None and old_inputs is not None and (
                            old_inputs.prior_regular_close != inputs.prior_regular_close or
                            old_inputs.share_basis_id != inputs.share_basis_id):
                        raise HistoricalInputError('Corrected day reference requires input replay before OPEN allocation')
                approvals = []
                for security, detector in sorted(detectors.items()):
                    signal, inputs = detector.d_state.signal, last_inputs.get(security)
                    bar = bars.get(security)
                    if signal is None or bar is None or detector.d_state.cancellation_reason is not None:
                        continue
                    if signal.scheduled_interval != at:
                        raise HistoricalInputError('Unresolved scheduled opportunity cannot be delayed')
                    if bar.classification != K.TRADED:
                        continue  # Canonical Phase 4 cancellation runs at completion.
                    if inputs is None:
                        # Unavailable references cannot be repaired by using an old close.
                        audit.append(HistoricalAudit('ENTRY_INPUT_UNAVAILABLE',at,security,
                            (('failures',evaluations[security].failures),('consumed',True))))
                        detector.reset_prices(); consumed.add(security)
                        continue
                    result = evaluations[security]
                    handoff = EntryHandoff(signal,bar,inputs,at)
                    context = EntryContext(OpeningEligibility(result.prior_close,result.static_eligible,at,
                        '|'.join(result.source_ids) or 'historical-contract',result.share_basis_id,True),
                        tuple(contexts[security]),self._weekly(security,day,result.share_basis_id,at))
                    validation = validator.evaluate(handoff,context)
                    audit.extend(validation.audit)
                    if validation.approval is not None:
                        approvals.append(validation.approval)
                    detector.reset_prices(); consumed.add(security)
                step = portfolio.on_open(at,tuple(bars[s] for s in sorted(bars)),tuple(approvals))
                audit.extend(step.audit)
                max_positions = max(max_positions,step.state.open_position_count)
                if step.state.current_equity is not None and not step.requires_shared_stop and not step.requires_shared_pause:
                    last_confirmed = step.state.current_equity
                    points.append(EquityPoint(at,last_confirmed,'OPEN'))
                if step.requires_shared_pause:
                    audit.extend(portfolio.mark_data_incomplete().audit)
                if step.requires_shared_stop or step.requires_shared_pause:
                    break
                completed_bars, completed_evaluations = {}, {}
                for security, opening_bar in bars.items():
                    completion = at+timedelta(minutes=1)
                    basis = ds.session_basis(security,day)
                    basis_id = basis.basis_id if basis and basis.verified and basis.available_at <= completion and basis.effective_at <= completion else None
                    bar, source = self._minute(indices[security],security,opening_bar.ticker,at,
                        basis_id,completed=True)
                    completed_bars[security] = bar
                    result = evaluate_universe(ds,security,completion,bar.close,config)
                    completed_evaluations[security] = result
                    universes.append(result)
                    audit.append(HistoricalAudit('UNIVERSE_AT_COMPLETION',completion,security,(('evaluation',result),)))
                    audit.append(HistoricalAudit('INTERVAL_COMPLETED_SOURCE',bar.end,security,
                        (('interval',bar),('provenance',source.provenance
                            if source and source.provenance.available_at <= bar.end else None),
                         ('provenance_available_at',source.provenance.available_at if source else None),
                         ('completed_available_at',source.completed_available_at if source else None))))
                step = portfolio.on_close(at+timedelta(minutes=1),tuple(
                    completed_bars[s] for s in sorted(completed_bars)))
                audit.extend(step.audit)
                if step.requires_shared_pause:
                    audit.extend(portfolio.mark_data_incomplete().audit)
                if step.requires_shared_stop or step.requires_shared_pause:
                    break
                for security, bar in sorted(completed_bars.items()):
                    result = completed_evaluations[security]
                    detector = detectors[security]
                    inputs = (EligibilityInputs(result.prior_close,result.static_eligible,result.share_basis_id)
                              if security not in unavailable_references and result.prior_close is not None
                              and result.share_basis_id is not None else None)
                    contexts[security].append(ContextMinute(bar,bar.end,result.share_basis_id or 'UNAVAILABLE'))
                    blocked = (security in consumed or security in held
                               or any(p.entry.approval.security_id == security for p in portfolio.state.positions)
                               or (bar.classification == K.TRADED and not portfolio.price_pattern_permitted(bar)))
                    if blocked or inputs is None:
                        active_before = detector.active
                        setup_before = detector.state.setup_id
                        if blocked or active_before or security in unavailable_references:
                            detector.reset_prices()
                        detector.volume_only(bar,collect_prices=not blocked and not active_before
                                             and security not in unavailable_references)
                        audit.append(HistoricalAudit('PRICE_PATTERN_EXCLUDED',bar.end,security,
                            (('interval',bar.timestamp),('occupied_or_consumed',blocked),('volume_retained',True),
                             ('eligibility_failures',result.failures if inputs is None else ()),
                             ('reference_unavailable',security in unavailable_references),
                             ('discarded_setup_id',setup_before if active_before else None),
                             ('reference_independent_history_only',not blocked and not active_before
                              and security not in unavailable_references))))
                    else:
                        # The CLOSE gates are evaluated by the existing detector.
                        opening_bar = bars[security]
                        pending = detector.d_state.signal
                        delivery = opening_bar if (pending is not None and pending.scheduled_interval == at
                            and opening_bar.classification in (K.MISSING,K.INVALID)) else bar
                        audit.extend(detector.completed(delivery,inputs))
                if portfolio.state.current_equity is not None:
                    last_confirmed = portfolio.state.current_equity
                    points.append(EquityPoint(at+timedelta(minutes=1),last_confirmed,'RESOLVED_COMPLETION'))
                at += timedelta(minutes=1)
            state = portfolio.state
            completed = portfolio.completed[completed_before:]
            values = [t.net_pnl for t in completed]
            sessions.append(SessionSummary(state,sum(v>0 for v in values),sum(v<0 for v in values),
                sum(v==0 for v in values),(Fraction(state.ending_equity)-Fraction(state.bod_equity))/Fraction(state.bod_equity)
                if state.ending_equity is not None else None,max_positions))
            if state.status != PortfolioStatus.SESSION_CLOSED:
                incomplete_reason = state.run_status.value if state.run_status else 'INCOMPLETE_UNRESOLVED_HISTORICAL_DATA'
                break
            day += timedelta(days=1)
        entry_events = {a.setup_id:a for a in portfolio.audit if a.event_type=='PORTFOLIO_ALLOCATION'
                        and dict(a.details)['accepted']}
        candidate_events = {a.setup_id:a for a in portfolio.audit if a.event_type=='PORTFOLIO_CANDIDATE'}
        trades = tuple(TradeLedger(t,Fraction(t.net_pnl)/(
            Fraction(t.entry.risk_per_share)*t.entry.quantity),
            multiply(t.entry.risk_per_share,Decimal(t.entry.quantity)),
            (('entry_count_after',dict(entry_events[t.entry.approval.setup_id].details)['entries']),
             ('lockout_reasons_before',dict(candidate_events[t.entry.approval.setup_id].details)['lockout_reasons'])))
            for t in portfolio.completed)
        all_audit = tuple(audit)
        summary = summarize(tuple(sessions),trades,tuple(points),config,incomplete_reason is not None)
        return BacktestResult(identity,trades,tuple(sessions),summary,tuple(points),
            portfolio.state.positions if portfolio.state and incomplete_reason else (),tuple(universes),all_audit,
            last_confirmed,portfolio.state.last_event_at if portfolio.state else None,incomplete_reason,
            ('RESEARCH BACKTEST MODEL — V1.0','ZERO-FRICTION BASELINE',
             'FULL-FILL RESEARCH ASSUMPTION — MARKET DEPTH AND PARTIAL FILLS NOT MODELED',
             'Historical data coverage and realistic live execution are not established by this model.'))
