"""Pure PRE9 copying into journal-owned values; no persistence or authority.

Only the journal owner invokes this adapter for durable capture. Storage/replay
do not import transport, source adapters or this module.
"""

from decimal import Decimal

from . import ibkr_order_status_evidence as source
from . import ibkr_qualified_status_ledger as memory
from . import paper_execution_journal as journal
from .readonly_models import ReadOnlyError


__all__ = ()
_ERROR = 'INVALID_QUALIFIED_STATUS_JOURNAL_SOURCE'


def _require(condition):
    if not condition:
        raise ReadOnlyError(_ERROR)


def _qualified_callback_values(event):
    """Exact event/generation/sequence and representation-aware source identity."""
    try:
        _require(type(event) is memory._QualifiedOrderStatusEvidence)
        memory._validate_event(event)
        return (type(event), event.generation, event.sequence, memory._source_identity(event))
    except Exception:
        raise ReadOnlyError(_ERROR) from None


def _observation_identity(value, protocol_type, presence_type, quality_type):
    _require(type(value.protocol) is protocol_type and type(value.presence) is presence_type)
    values = []
    for name in journal._QUALIFIED_STATUS_FIELDS:
        item, quality = getattr(value, name), getattr(value.presence, name)
        _require(type(quality) is quality_type)
        values.append((name, (type(item), item.as_tuple() if type(item) is Decimal else item),
                       quality.value))
    supplied = value.presence.wire_supplied
    if supplied is not None:
        supplied = tuple(name for name in journal._QUALIFIED_STATUS_FIELDS if name in supplied)
    return value.protocol.value, tuple(values), supplied


def _adapt_qualified_status(event, session):
    """Independent immutable copy with a proven exact source-representation match."""
    try:
        _qualified_callback_values(event)
        journal._exact(session, journal._SessionRegistered)
        _require(event.generation == session.write_generation)
        observation = event.observation
        supplied = observation.presence.wire_supplied
        presence = journal._QualifiedStatusPresence(
            *(journal._QualifiedStatusSourceQuality[getattr(observation.presence, name).name]
              for name in journal._QUALIFIED_STATUS_FIELDS),
            None if supplied is None else tuple(name for name in journal._QUALIFIED_STATUS_FIELDS
                                               if name in supplied))
        values = []
        for name in journal._QUALIFIED_STATUS_FIELDS:
            value = getattr(observation, name)
            if type(value) is Decimal:
                value = Decimal(value.as_tuple())
            elif type(value) is str:
                value = value.encode('ascii').decode('ascii')
            values.append(value)
        durable = journal._QualifiedStatusObservation(
            journal._QualifiedStatusProtocol[observation.protocol.name], *values, presence)
        _require(_observation_identity(observation, source._OrderStatusProtocol,
                     source._OrderStatusPresence, source._OrderStatusSourceQuality) ==
                 _observation_identity(durable, journal._QualifiedStatusProtocol,
                     journal._QualifiedStatusPresence, journal._QualifiedStatusSourceQuality))
        return journal._QualifiedOrderStatusObserved(journal._CallbackSource(
            session.session_id, event.generation, event.sequence), durable)
    except Exception:
        raise ReadOnlyError(_ERROR) from None
