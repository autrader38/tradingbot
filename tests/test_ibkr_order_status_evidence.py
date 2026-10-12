"""PRE3 value adapters: qualified schema doubles and optional installed SDK messages."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from decimal import Decimal, Inexact, InvalidOperation, Rounded, localcontext
import importlib
import importlib.util
import inspect
from struct import pack, unpack
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import weakref

from tradingbot_broker import ibkr_order_status_evidence as m
from tradingbot_broker.readonly_models import ReadOnlyError


D = Decimal
P, Q = m._OrderStatusProtocol, m._OrderStatusSourceQuality
# Independent transcription of the inspected Windows descriptor, not an import
# of the adapter's field table. This fixture supports only these optional scalars.
SCHEMA = (
    ('order_id', 'orderId', 1, 5), ('status', 'status', 2, 9),
    ('filled', 'filled', 3, 9), ('remaining', 'remaining', 4, 9),
    ('average_fill_price', 'avgFillPrice', 5, 1), ('perm_id', 'permId', 6, 3),
    ('parent_id', 'parentId', 7, 5), ('last_fill_price', 'lastFillPrice', 8, 1),
    ('client_id', 'clientId', 9, 5),
)
RAW = dict(orderId=10, status='Submitted', filled='4.00', remaining='6.00',
           avgFillPrice=1.25, permId=77, parentId=0, lastFillPrice=1.5, clientId=2)
LEGACY = (10, 'Submitted', D('4.00'), D('6.00'), 1.25, 77, 0, 1.5, 2)
UNSET = D('170141183460469231731687303715884105727')


def descriptor():
    return NS(full_name='protobuf.OrderStatus', file=NS(name='OrderStatus.proto'),
        fields_by_name={sdk: NS(number=number, type=kind, has_presence=True,
                              containing_oneof=NS(name='_' + sdk))
                       for _, sdk, number, kind in SCHEMA})


def varint(value):
    assert type(value) is int and value >= 0
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def read_varint(data, start):
    value, shift = 0, 0
    while True:
        part = data[start]
        start += 1
        value |= (part & 127) << shift
        if not part & 128:
            return value, start
        shift += 7


class Proto:
    """Anonymous minimal optional-scalar wire fixture; no official SDK claim."""
    def __init__(self, **values):
        self.DESCRIPTOR = descriptor()
        self.values = dict(values)
        self.reads = []

    def HasField(self, name):
        if name not in self.DESCRIPTOR.fields_by_name:
            raise ValueError('unsupported fixture field')
        return name in self.values

    def __getattr__(self, name):
        self.reads.append(name)
        if name in self.values:
            return self.values[name]
        field = self.DESCRIPTOR.fields_by_name[name]
        return '' if field.type == 9 else 0.0 if field.type == 1 else 0

    def __repr__(self):
        raise AssertionError('raw source repr must not be evaluated')

    def SerializeToString(self):
        # Real protobuf optional-scalar encodings, including explicit default
        # tags. This small fixture is not a replacement SDK implementation.
        encoded = bytearray()
        for _, name, number, kind in SCHEMA:
            if name not in self.values:
                continue
            value = self.values[name]
            if kind == 9:
                payload = value.encode('utf-8')
                encoded.extend(varint(number << 3 | 2) + varint(len(payload)) + payload)
            elif kind == 1:
                encoded.extend(varint(number << 3 | 1) + pack('<d', value))
            else:
                encoded.extend(varint(number << 3) + varint(value))
        return bytes(encoded)

    @classmethod
    def FromString(cls, data):
        by_number = {number: (name, kind) for _, name, number, kind in SCHEMA}
        values, start = {}, 0
        while start < len(data):
            tag, start = read_varint(data, start)
            name, kind = by_number[tag >> 3]
            if kind == 9:
                size, start = read_varint(data, start)
                values[name] = data[start:start + size].decode('utf-8')
                start += size
            elif kind == 1:
                values[name] = unpack('<d', data[start:start + 8])[0]
                start += 8
            else:
                values[name], start = read_varint(data, start)
        return cls(**values)


def messages(values):
    yield Proto(**values)
    if importlib.util.find_spec('ibapi') is not None:
        # The actual inspected decoder global identifies its generated class.
        # No EClient/EWrapper instance or request is created or invoked.
        decoder = importlib.import_module('ibapi.decoder')
        yield decoder.OrderStatusProto(**values)


def legacy(**changes):
    names = [name for name, _, _, _ in SCHEMA]
    return m._adapt_legacy_order_status(*(changes.get(name, value)
                                        for name, value in zip(names, LEGACY)))


def normalized(values):
    result = []
    for name, sdk, _, kind in SCHEMA:
        if sdk in values:
            value = D(values[sdk]) if name in ('filled', 'remaining') else values[sdk]
        else:
            value = ('' if name == 'status' else UNSET if name in ('filled', 'remaining')
                     else 1.7976931348623157e308 if kind == 1
                     else 9223372036854775807 if name == 'perm_id' else 2147483647)
        result.append(value)
    return tuple(result)


class Case(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('no network')))
        self.enterContext(patch('socket.getaddrinfo', side_effect=AssertionError('no DNS')))

    def invalid(self, function, *args, **kwargs):
        with self.assertRaises(ReadOnlyError) as caught:
            function(*args, **kwargs)
        self.assertEqual(str(caught.exception), 'INVALID_IBKR_ORDER_STATUS_EVIDENCE')
        self.assertIsNone(caught.exception.__cause__)


class ModelTests(Case):
    def test_complete_legacy_same_event_values(self):
        value = legacy()
        self.assertIs(value.protocol, P.LEGACY)
        self.assertEqual(tuple(getattr(value, name) for name, _, _, _ in SCHEMA),
                         (10, 'Submitted', D('4.00'), D('6.00'), D('1.25'), 77, 0, D('1.5'), 2))
        self.assertIsNone(value.presence.wire_supplied)

    def test_frozen_slotted_opaque_observation_and_presence(self):
        value = legacy()
        for item in (value, value.presence):
            self.assertFalse(hasattr(item, '__dict__'))
            self.assertEqual(repr(item), '<PrivateOrderStatusSourceEvidence>')
            with self.assertRaises(FrozenInstanceError):
                setattr(item, fields(item)[0].name, None)

    def test_independent_decimal_values(self):
        original = D('4.0000')
        value = legacy(filled=original)
        self.assertEqual(value.filled.as_tuple(), original.as_tuple())
        self.assertIsNot(value.filled, original)

    def test_model_replacement_cannot_publish_unset_quantity(self):
        self.invalid(replace, legacy(), filled=UNSET)

    def test_protocol_presence_disagreement_rejected(self):
        self.invalid(replace, legacy(), protocol=P.PROTOBUF)
        p = replace(legacy().presence, filled=Q.SUPPLIED)
        self.invalid(replace, legacy(), presence=p)

    def test_missing_value_cannot_claim_supplied_quality(self):
        self.invalid(replace, legacy(), filled=None)
        value = m._adapt_protobuf_order_status(Proto())
        p = replace(value.presence, wire_supplied=frozenset(('order_id',)))
        self.invalid(replace, value, presence=p)

    def test_presence_no_mutable_or_unsupported_fields(self):
        self.invalid(replace, legacy().presence, wire_supplied={'filled'})
        self.invalid(replace, legacy().presence, wire_supplied=frozenset(('request_id',)))
        self.invalid(replace, legacy().presence, filled='SUPPLIED')


class LegacyQuantityTests(Case):
    def test_positive_fractional_zero_and_negative_zero(self):
        for amount in (D('4.25'), D('0'), D('-0.00')):
            with self.subTest(amount=amount):
                value = legacy(filled=amount)
                self.assertEqual(value.filled.as_tuple(), amount.as_tuple())
                self.assertIs(value.presence.filled, Q.LEGACY_DECODED)

    def test_unset_filled_unavailable(self):
        value = legacy(filled=UNSET)
        self.assertIsNone(value.filled)
        self.assertIs(value.presence.filled, Q.UNAVAILABLE)
        self.assertEqual(value.remaining, D('6.00'))

    def test_unset_remaining_unavailable(self):
        value = legacy(remaining=UNSET)
        self.assertIsNone(value.remaining)
        self.assertIs(value.presence.remaining, Q.UNAVAILABLE)

    def test_equivalent_unset_representation_remains_unavailable(self):
        self.assertIsNone(legacy(filled=D(str(UNSET) + '.0')).filled)

    def test_quantity_bounds_endpoints(self):
        for amount in (D((0, (7,) * 64, -64)), D('1E64'), D('1E-64')):
            with self.subTest(amount=amount):
                self.assertEqual(legacy(filled=amount).filled.as_tuple(), amount.as_tuple())

    def test_quantity_representation_not_normalized(self):
        self.assertEqual(legacy(filled=D('0.0012300')).filled.as_tuple(), D('0.0012300').as_tuple())

    def test_source_does_not_infer_requested_quantity(self):
        value = legacy(status='Filled', filled=D(0), remaining=D(19))
        self.assertEqual((value.status, value.filled, value.remaining), ('Filled', D(0), D(19)))
        self.assertFalse(hasattr(value, 'requested_quantity'))


class LegacyPriceTests(Case):
    def test_float_text_conversion_not_binary_decimal_expansion(self):
        value = legacy(average_fill_price=0.1)
        self.assertEqual(value.average_fill_price, D('0.1'))
        self.assertNotEqual(value.average_fill_price, D.from_float(0.1))

    def test_zero_has_legacy_ambiguity(self):
        value = legacy(average_fill_price=0.0, last_fill_price=0.0)
        self.assertEqual((value.average_fill_price, value.last_fill_price), (D('0.0'), D('0.0')))
        self.assertIs(value.presence.average_fill_price, Q.LEGACY_AMBIGUOUS)
        self.assertIs(value.presence.last_fill_price, Q.LEGACY_AMBIGUOUS)

    def test_positive_price_claims_only_decoded_value(self):
        value = legacy()
        self.assertIs(value.presence.average_fill_price, Q.LEGACY_DECODED)
        self.assertIsNone(value.presence.wire_supplied)

    def test_negative_zero_sign_preserved(self):
        value = legacy(last_fill_price=-0.0)
        self.assertEqual(value.last_fill_price.as_tuple(), D('-0.0').as_tuple())
        self.assertIs(value.presence.last_fill_price, Q.LEGACY_AMBIGUOUS)

    def test_unset_double_unavailable(self):
        value = legacy(average_fill_price=1.7976931348623157e308)
        self.assertIsNone(value.average_fill_price)
        self.assertIs(value.presence.average_fill_price, Q.UNAVAILABLE)

    def test_zero_fill_status_prices_are_not_acquisition_evidence(self):
        value = legacy(filled=D(0), average_fill_price=0.0)
        self.assertFalse(hasattr(value, 'execution_price'))
        self.assertFalse(hasattr(value, 'priced_execution_quantity'))


class LegacyIdTests(Case):
    def test_positive_ids_remain_decoded_only(self):
        value = legacy(parent_id=3)
        for name in ('order_id', 'client_id', 'perm_id', 'parent_id'):
            self.assertIs(getattr(value.presence, name), Q.LEGACY_DECODED)

    def test_all_zero_ids_are_presence_ambiguous(self):
        value = legacy(order_id=0, client_id=0, parent_id=0, perm_id=0)
        for name in ('order_id', 'client_id', 'perm_id', 'parent_id'):
            self.assertEqual(getattr(value, name), 0)
            self.assertIs(getattr(value.presence, name), Q.LEGACY_AMBIGUOUS)

    def test_supported_id_maxima_not_redefined_as_absence(self):
        value = legacy(order_id=2147483647, client_id=2147483647,
                       parent_id=2147483647, perm_id=9223372036854775807)
        self.assertEqual(value.order_id, 2147483647)
        self.assertIs(value.presence.order_id, Q.LEGACY_DECODED)


_BAD_LEGACY = (
    ('quantity_types', ('filled', 'remaining'), (True, 1, 1.0, '4', None)),
    ('quantity_negative', ('filled', 'remaining'), (D('-1'),)),
    ('quantity_nonfinite', ('filled', 'remaining'), (D('NaN'), D('sNaN'), D('Infinity'))),
    ('quantity_digits', ('filled', 'remaining'), (D((0, (1,) * 65, 0)),)),
    ('quantity_exponents', ('filled', 'remaining'), (D('1E65'), D('1E-65'))),
    ('price_types', ('average_fill_price', 'last_fill_price'), (True, 0, D('1'), '1', None)),
    ('price_negative', ('average_fill_price', 'last_fill_price'), (-0.01,)),
    ('price_nonfinite', ('average_fill_price', 'last_fill_price'), (float('nan'), float('inf'), -float('inf'))),
    ('price_bounds', ('average_fill_price', 'last_fill_price'), (1e65, 1e-65)),
    ('id_types', ('order_id', 'client_id', 'parent_id', 'perm_id'), (True, 1.0, '1', None)),
    ('id_negative', ('order_id', 'client_id', 'parent_id', 'perm_id'), (-1,)),
    ('status_malformed', ('status',), (True, None, 'line\ntext', 'é', 'x' * 101)),
)


class LegacyMalformedTests(Case):
    def test_id_ranges(self):
        for name, bad in (('order_id', 2**31), ('client_id', 2**31),
                          ('parent_id', 2**31), ('perm_id', 2**63)):
            with self.subTest(field=name):
                self.invalid(legacy, **{name: bad})

    def test_exact_primitive_subclasses(self):
        class Number(int): pass
        class Quantity(Decimal): pass
        class Price(float): pass
        class Status(str): pass
        for name, bad in (('order_id', Number(10)), ('filled', Quantity('4')),
                          ('average_fill_price', Price(1.25)), ('status', Status('Submitted'))):
            with self.subTest(field=name):
                self.invalid(legacy, **{name: bad})


def bad_legacy_test(names, values):
    def test(self):
        for name in names:
            for value in values:
                with self.subTest(field=name, value=repr(value)):
                    self.invalid(legacy, **{name: value})
    return test


for label, names, values in _BAD_LEGACY:
    setattr(LegacyMalformedTests, 'test_' + label, bad_legacy_test(names, values))


class ProtobufPresenceTests(Case):
    def test_every_field_absent_without_reading_scalar_defaults(self):
        raw = Proto()
        value = m._adapt_protobuf_order_status(raw)
        self.assertEqual(raw.reads, [])
        self.assertEqual(value.presence.wire_supplied, frozenset())
        for name, _, _, _ in SCHEMA:
            self.assertIsNone(getattr(value, name))
            self.assertIs(getattr(value.presence, name), Q.UNAVAILABLE)

    def test_all_fields_same_event_supplied(self):
        for raw in messages(RAW):
            value = m._adapt_protobuf_order_status(raw, normalized=LEGACY)
            self.assertEqual(value.presence.wire_supplied, frozenset(name for name, _, _, _ in SCHEMA))
            self.assertEqual(value.filled.as_tuple(), D('4.00').as_tuple())
            self.assertIs(value.presence.parent_id, Q.SUPPLIED)

    def test_unavailable_sentinel_retains_raw_supplied_fact(self):
        raw = Proto(filled=str(UNSET), avgFillPrice=1.7976931348623157e308)
        value = m._adapt_protobuf_order_status(raw, normalized=normalized(raw.values))
        self.assertIsNone(value.filled)
        self.assertIsNone(value.average_fill_price)
        self.assertEqual(value.presence.wire_supplied, frozenset(('filled', 'average_fill_price')))
        self.assertIs(value.presence.filled, Q.UNAVAILABLE)

    def test_supplied_max_id_distinguished_from_absent_fallback(self):
        absent = m._adapt_protobuf_order_status(Proto(), normalized=normalized({}))
        present = m._adapt_protobuf_order_status(Proto(orderId=2147483647))
        self.assertIsNone(absent.order_id)
        self.assertEqual(present.order_id, 2147483647)
        self.assertIs(present.presence.order_id, Q.SUPPLIED)


def field_presence_test(name, sdk, number, kind):
    def test(self):
        default = '' if kind == 9 else 0.0 if kind == 1 else 0
        supplied = '0' if name in ('filled', 'remaining') else default
        nonzero = '1.25' if name in ('filled', 'remaining') else 'Submitted' if name == 'status' else 1.25 if kind == 1 else 1
        for source in messages({sdk: supplied}):
            with self.subTest(field=name, source=type(source).__name__):
                absent = m._adapt_protobuf_order_status(type(source)())
                observed = m._adapt_protobuf_order_status(source)
                self.assertIsNone(getattr(absent, name))
                self.assertIs(getattr(observed.presence, name), Q.SUPPLIED)
                self.assertIn(name, observed.presence.wire_supplied)
                restored = type(source).FromString(source.SerializeToString())
                self.assertTrue(restored.HasField(sdk))
                self.assertEqual(m._adapt_protobuf_order_status(restored), observed)
                if name == 'status':
                    self.assertEqual(observed.status, 'UNKNOWN')
                else:
                    self.assertEqual(getattr(observed, name), 0)
                other = type(source)(**{sdk: nonzero})
                self.assertNotEqual(m._adapt_protobuf_order_status(other), observed)
        if name in ('filled', 'remaining'):
            self.invalid(m._adapt_protobuf_order_status, Proto(**{sdk: ''}))
    return test


for row in SCHEMA:
    setattr(ProtobufPresenceTests, 'test_presence_and_wire_roundtrip_' + row[0], field_presence_test(*row))


class ProtobufQuantityTests(Case):
    def test_exact_supported_numeric_text(self):
        for text in ('0', '-0.00', '4', '4.000', '0.00001', '.5', '+1.25e+2'):
            for raw in messages(dict(RAW, filled=text)):
                with self.subTest(text=text, source=type(raw).__name__):
                    value = m._adapt_protobuf_order_status(raw)
                    self.assertEqual(value.filled.as_tuple(), D(text).as_tuple())

    def test_quantity_boundary_shapes(self):
        for text in ('7' * 64, '1e64', '1e-64'):
            self.assertEqual(m._adapt_protobuf_order_status(Proto(filled=text)).filled, D(text))

    def test_raw_representation_preserved_when_decoded_numeric_value_equal(self):
        raw = Proto(**RAW)
        args = list(LEGACY); args[2] = D(4)
        value = m._adapt_protobuf_order_status(raw, normalized=tuple(args))
        self.assertEqual(value.filled.as_tuple(), D('4.00').as_tuple())


_BAD_TEXT = (
    ('empty', ''), ('whitespace', ' '), ('non_numeric', 'malformed'),
    ('negative', '-0.1'), ('nan', 'NaN'), ('infinity', 'Infinity'),
    ('digits', '1' * 65), ('high_exponent', '1e65'), ('low_exponent', '1e-65'),
    ('unbounded_text', '0' * 161), ('unsupported_underscore', '1_000'),
    ('wrong_type', True),
)


def bad_text_test(value):
    def test(self):
        for sdk in ('filled', 'remaining'):
            with self.subTest(field=sdk):
                self.invalid(m._adapt_protobuf_order_status, Proto(**{sdk: value}))
    return test


for label, value in _BAD_TEXT:
    setattr(ProtobufQuantityTests, 'test_reject_' + label, bad_text_test(value))


class ProtobufPriceTests(Case):
    def test_negative_zero_preserved_and_decoded_sign_must_match(self):
        raw = Proto(**dict(RAW, avgFillPrice=-0.0))
        value = m._adapt_protobuf_order_status(raw, normalized=normalized(raw.values))
        self.assertEqual(value.average_fill_price.as_tuple(), D('-0.0').as_tuple())
        args = list(normalized(raw.values)); args[4] = 0.0
        self.invalid(m._adapt_protobuf_order_status, raw, normalized=tuple(args))

    def test_float_text_and_fractional_price(self):
        value = m._adapt_protobuf_order_status(Proto(avgFillPrice=0.1))
        self.assertEqual(value.average_fill_price, D('0.1'))

    def test_price_malformed(self):
        for bad in (True, 0, D(1), '1', None, -1.0, float('nan'), float('inf'), -float('inf'), 1e65, 1e-65):
            for sdk in ('avgFillPrice', 'lastFillPrice'):
                with self.subTest(field=sdk, value=repr(bad)):
                    self.invalid(m._adapt_protobuf_order_status, Proto(**{sdk: bad}))


class RawDecodedTests(Case):
    def test_absence_checked_before_fallback(self):
        value = m._adapt_protobuf_order_status(Proto(), normalized=normalized({}))
        self.assertIsNone(value.filled)
        self.assertIsNone(value.order_id)
        self.assertEqual(value.presence.wire_supplied, frozenset())

    def test_wrong_absent_fallback_cannot_be_promoted(self):
        for index in range(9):
            args = list(normalized({}))
            args[index] = 'UNKNOWN' if index == 1 else D(0) if index in (2, 3) else 0.0 if index in (4, 7) else 0
            with self.subTest(index=index):
                self.invalid(m._adapt_protobuf_order_status, Proto(), normalized=tuple(args))

    def test_normalized_tuple_shape_and_primitive_types(self):
        raw = Proto(**RAW)
        for bad in (list(LEGACY), LEGACY[:-1], LEGACY + (None,), object()):
            self.invalid(m._adapt_protobuf_order_status, raw, normalized=bad)
        for index, bad in ((0, True), (1, None), (2, 4), (4, D('1.25'))):
            args = list(LEGACY); args[index] = bad
            self.invalid(m._adapt_protobuf_order_status, raw, normalized=tuple(args))

    def test_unknown_status_raw_values_compare_before_normalization(self):
        raw = Proto(**dict(RAW, status='PendingCancel'))
        args = list(LEGACY); args[1] = 'other-unknown'
        self.invalid(m._adapt_protobuf_order_status, raw, normalized=tuple(args))


def mismatch_test(index, row):
    def test(self):
        args = list(LEGACY)
        args[index] = ('Cancelled' if row[0] == 'status' else D('4.1') if row[0] == 'filled'
                       else D('6.1') if row[0] == 'remaining' else 2.5 if row[3] == 1 else 99)
        self.invalid(m._adapt_protobuf_order_status, Proto(**RAW), normalized=tuple(args))
    return test


for index, row in enumerate(SCHEMA):
    setattr(RawDecodedTests, 'test_mismatch_' + row[0], mismatch_test(index, row))


class SchemaAndSourceTests(Case):
    def test_wrong_message_identity_or_file(self):
        for attr, value in (('full_name', 'other.Message'), ('file', NS(name='other.proto'))):
            raw = Proto(**RAW); setattr(raw.DESCRIPTOR, attr, value)
            self.invalid(m._adapt_protobuf_order_status, raw)

    def test_wrong_field_number_type_presence_or_oneof(self):
        for attr, value in (('number', 99), ('type', 1), ('has_presence', False),
                            ('containing_oneof', None), ('containing_oneof', NS(name='wrong'))):
            raw = Proto(**RAW); setattr(raw.DESCRIPTOR.fields_by_name['filled'], attr, value)
            self.invalid(m._adapt_protobuf_order_status, raw)

    def test_hasfield_requires_exact_bool(self):
        raw = Proto(**RAW)
        for bad in (1, 'yes', None):
            raw.HasField = lambda name, result=bad: result
            self.invalid(m._adapt_protobuf_order_status, raw)

    def test_raw_object_requires_qualified_schema(self):
        self.invalid(m._adapt_protobuf_order_status, NS(**RAW))
        raw = Proto(**RAW); del raw.DESCRIPTOR.fields_by_name['filled']
        self.invalid(m._adapt_protobuf_order_status, raw)

    def test_unqualified_id_types_ranges_and_status(self):
        for sdk, bad in (('orderId', True), ('clientId', 2**31), ('parentId', -1),
                         ('permId', 2**63), ('status', None), ('status', 'é'), ('status', 'x' * 101)):
            with self.subTest(field=sdk):
                self.invalid(m._adapt_protobuf_order_status, Proto(**{sdk: bad}))


class StatusAndEventTests(Case):
    def test_all_recognized_statuses_preserved_without_finality(self):
        for status in ('PendingSubmit', 'PreSubmitted', 'Submitted', 'Filled',
                       'Cancelled', 'ApiCancelled', 'Inactive', 'Expired'):
            with self.subTest(status=status):
                self.assertEqual(legacy(status=status).status, status)
                self.assertEqual(m._adapt_protobuf_order_status(Proto(status=status)).status, status)

    def test_unknown_and_empty_status_normalized_without_retaining_text(self):
        for status in ('', 'PendingCancel', 'private-account-fixture'):
            with self.subTest(status=status):
                self.assertEqual(legacy(status=status).status, 'UNKNOWN')
                self.assertEqual(m._adapt_protobuf_order_status(Proto(status=status)).status, 'UNKNOWN')

    def test_terminal_zero_does_not_produce_authority(self):
        value = legacy(status='Cancelled', filled=D(0), remaining=D(10))
        names = {f.name for f in fields(value)}
        self.assertTrue(names.isdisjoint({'terminal_final', 'proven_zero_fill', 'no_late_fill',
            'current', 'current_remainder', 'working_now', 'safe_to_cancel', 'safe_to_protect'}))

    def test_two_observations_do_not_merge_or_resolve_regression(self):
        a = legacy(status='Submitted', filled=D(4), remaining=D(6))
        b = legacy(status='Cancelled', filled=D(2), remaining=D(8))
        self.assertEqual((a.filled, b.filled), (D(4), D(2)))
        self.assertEqual((a.status, b.status), ('Submitted', 'Cancelled'))

    def test_repeat_creates_independent_equal_value_without_dedup_economics(self):
        a, b = legacy(), legacy()
        self.assertEqual(a, b)
        self.assertIsNot(a, b)
        self.assertIsNot(a.presence, b.presence)
        self.assertFalse(hasattr(a, 'execution_count'))

    def test_raw_mutation_after_projection_cannot_change_observation(self):
        raw = Proto(**RAW)
        value = m._adapt_protobuf_order_status(raw)
        raw.values['filled'] = '9'; raw.values.clear()
        raw.DESCRIPTOR.full_name = 'modified'
        self.assertEqual(value.filled.as_tuple(), D('4.00').as_tuple())
        self.assertEqual(len(value.presence.wire_supplied), 9)


class ContextTests(Case):
    def test_valid_adapters_preserve_low_precision_traps_and_flags(self):
        raw = Proto(**RAW)
        expected = (legacy(), m._adapt_protobuf_order_status(raw, normalized=LEGACY))
        for flagged in (False, True):
            with self.subTest(flags=flagged), localcontext() as context:
                context.prec = 2
                context.traps[Inexact] = context.traps[Rounded] = True
                context.flags[Inexact] = context.flags[Rounded] = flagged
                before = dict(context.flags)
                self.assertEqual((legacy(), m._adapt_protobuf_order_status(raw, normalized=LEGACY)), expected)
                self.assertEqual(dict(context.flags), before)

    def test_malformed_decimal_does_not_change_ambient_flags(self):
        for trapped in (False, True):
            with localcontext() as context:
                context.traps[InvalidOperation] = trapped
                before = dict(context.flags)
                for text in ('NaN', 'bad', '', '1e999999999999999999999'):
                    self.invalid(m._adapt_protobuf_order_status, Proto(filled=text))
                self.assertEqual(dict(context.flags), before)

    def test_snan_rejection_does_not_depend_on_ambient_trap(self):
        for trapped in (False, True):
            with localcontext() as context:
                context.traps[InvalidOperation] = trapped
                before = dict(context.flags)
                self.invalid(legacy, filled=D('sNaN'))
                self.assertEqual(dict(context.flags), before)


class BoundaryTests(Case):
    def test_no_io_clock_sdk_storage_or_thread_creation(self):
        with patch('builtins.open', side_effect=AssertionError('no I/O')), \
             patch('sqlite3.connect', side_effect=AssertionError('no SQLite')), \
             patch('time.time', side_effect=AssertionError('no clock')), \
             patch('time.monotonic', side_effect=AssertionError('no clock')), \
             patch('threading.Thread.start', side_effect=AssertionError('no worker')):
            self.assertIs(legacy().protocol, P.LEGACY)
            self.assertIs(m._adapt_protobuf_order_status(Proto(**RAW)).protocol, P.PROTOBUF)

    def test_no_raw_source_retained(self):
        raw = Proto(**RAW); reference = weakref.ref(raw)
        value = m._adapt_protobuf_order_status(raw)
        del raw
        self.assertIsNone(reference())
        def primitive(item):
            if is_dataclass(item):
                for field in fields(item): primitive(getattr(item, field.name))
            elif type(item) is frozenset:
                for child in item: primitive(child)
            else:
                self.assertIn(type(item), (str, int, Decimal, type(None), P, Q))
        primitive(value)

    def test_excluded_sdk_fields_are_never_read(self):
        class WithPrivateFields(Proto):
            @property
            def whyHeld(self): raise AssertionError('raw text forbidden')
            @property
            def mktCapPrice(self): raise AssertionError('unused field forbidden')
            @property
            def account(self): raise AssertionError('account forbidden')
        m._adapt_protobuf_order_status(WithPrivateFields(**RAW))

    def test_public_exports_and_authority_fields_absent(self):
        import tradingbot_broker
        self.assertFalse(hasattr(tradingbot_broker, '_OrderStatusObservation'))
        self.assertEqual({field.name for field in fields(legacy())},
            {'protocol', 'presence'} | {row[0] for row in SCHEMA})
        self.assertTrue(all(name.startswith('_') for name, item in vars(m).items()
            if inspect.isfunction(item) and item.__module__ == m.__name__))

    def test_thread_neutral_deterministic_projection(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            values = tuple(pool.map(lambda _: legacy(), range(12)))
        self.assertEqual(values, (legacy(),) * 12)

    def test_sanitized_error_does_not_evaluate_source_repr(self):
        raw = Proto(**RAW)
        raw.HasField = lambda name: (_ for _ in ()).throw(RuntimeError('private source text'))
        self.invalid(m._adapt_protobuf_order_status, raw)


class InterruptionTests(Case):
    pass


def interruption_test(kind):
    def test(self):
        for seam in ('legacy_quantity', 'schema', 'presence', 'value'):
            original = kind('private interruption')
            raw = Proto(**RAW)
            with self.subTest(seam=seam):
                if seam == 'legacy_quantity':
                    with patch.object(m, '_quantity', side_effect=original):
                        with self.assertRaises(kind) as caught: legacy()
                elif seam == 'schema':
                    with patch.object(m, '_schema', side_effect=original):
                        with self.assertRaises(kind) as caught: m._adapt_protobuf_order_status(raw)
                elif seam == 'presence':
                    raw.HasField = lambda name, error=original: (_ for _ in ()).throw(error)
                    with self.assertRaises(kind) as caught: m._adapt_protobuf_order_status(raw)
                else:
                    with patch.object(m, '_quantity_text', side_effect=original):
                        with self.assertRaises(kind) as caught: m._adapt_protobuf_order_status(raw)
                self.assertIs(caught.exception, original)
    return test


for kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
    setattr(InterruptionTests, 'test_' + kind.__name__, interruption_test(kind))


if __name__ == '__main__':
    unittest.main()
