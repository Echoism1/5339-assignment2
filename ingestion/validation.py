"""Validate the producer's exact v1 wire contract without coercing identifiers."""
import hashlib
import json
import math
import re
from datetime import datetime

COMMON = {'event_id', 'type', 'ts', 'station_id'}
SESSION = {'session_id', 'connector_id'}
FIELDS = {
    'price_update': {'price_per_kwh', 'wholesale_per_kwh'},
    'session_start': SESSION | {'vehicle_model', 'price_per_kwh'},
    'meter': SESSION | {'voltage_v', 'current_a', 'power_kw', 'energy_kwh', 'soc_pct'},
    'session_end': SESSION | {'charge_end_ts', 'charge_minutes', 'energy_kwh',
                              'avg_kw', 'peak_kw', 'price_per_kwh', 'cost_aud'},
}


class InvalidEvent(ValueError):
    pass


def canonical(event):
    return json.dumps(event, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise InvalidEvent(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def time_value(value):
    if not isinstance(value, str):
        raise InvalidEvent('timestamp must be a string')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise InvalidEvent('invalid ISO timestamp') from exc
    if parsed.utcoffset() is None:
        raise InvalidEvent('timestamp must include timezone')
    return parsed


def decode(payload, stations):
    try:
        event = json.loads(payload.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=lambda value: (_ for _ in ()).throw(
                               InvalidEvent(f'non-finite JSON: {value}')))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise InvalidEvent(f'JSON decoding: {exc}') from exc
    if not isinstance(event, dict):
        raise InvalidEvent('event must be an object')
    kind = event.get('type')
    if not isinstance(kind, str) or kind not in FIELDS:
        raise InvalidEvent('unknown event type')
    if set(event) != COMMON | FIELDS[kind]:
        raise InvalidEvent('missing or unexpected fields')
    sid = event['station_id']
    if not isinstance(sid, str) or sid not in stations:
        raise InvalidEvent('station_id is not a simulated A1 DC record')
    time_value(event['ts'])
    if kind != 'price_update':
        if not isinstance(event['session_id'], str) or not event['session_id']:
            raise InvalidEvent('invalid session_id')
        connector = event['connector_id']
        if type(connector) is not int or not 1 <= connector <= stations[sid]:
            raise InvalidEvent('connector outside A1 plugs_count')
    if kind == 'session_start' and (not isinstance(event['vehicle_model'], str)
                                    or not event['vehicle_model'].strip()):
        raise InvalidEvent('invalid vehicle_model')
    for key in FIELDS[kind] - SESSION - {'vehicle_model', 'charge_end_ts'}:
        value = event[key]
        try:
            finite = type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise InvalidEvent(f'invalid numeric field: {key}')
        # Wholesale electricity prices can legitimately be negative.
        if key != 'wholesale_per_kwh' and value < 0:
            raise InvalidEvent(f'negative field: {key}')
    if kind == 'meter' and event['soc_pct'] > 100:
        raise InvalidEvent('SoC exceeds 100 percent')
    if kind == 'session_end' and time_value(event['charge_end_ts']) > time_value(event['ts']):
        raise InvalidEvent('charge completion is after unplugging')
    identity = event['event_id']
    if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{64}', identity):
        raise InvalidEvent('invalid event_id')
    content = {k: v for k, v in event.items() if k != 'event_id'}
    if hashlib.sha256(canonical(content).encode('utf-8')).hexdigest() != identity:
        raise InvalidEvent('event_id does not match payload SHA-256')
    return event
