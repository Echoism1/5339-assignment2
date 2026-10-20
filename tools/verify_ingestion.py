"""Read-only source-to-DuckDB verification after stopping the subscriber."""
import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import duckdb


def verify(db_path, events_path):
    source = [json.loads(line) for line in events_path.read_text(encoding='utf-8').splitlines()]
    expected = {e['event_id']: e for e in source}
    with duckdb.connect(str(db_path), read_only=True) as db:
        rows = db.execute('SELECT event_id, ev_id, payload FROM core.mqtt_event ORDER BY received_at, event_id').fetchall()
        actual = {identity: json.loads(payload) for identity, sid, payload in rows}
        count = db.execute('SELECT count(*) FROM core.mqtt_charger_events').fetchone()[0]
        rejects = db.execute('SELECT count(*) FROM core.mqtt_rejection').fetchone()[0]
        current_prices = db.execute("SELECT count(distinct ev_id) FROM core.mqtt_event WHERE event_type='price_update'").fetchone()[0]
        active_sessions = db.execute("""SELECT count(*) FROM core.mqtt_event s
            WHERE s.event_type='session_start' AND NOT EXISTS
            (SELECT 1 FROM core.mqtt_event e WHERE e.event_type='session_end' AND e.session_id=s.session_id)""").fetchone()[0]
        counts = dict(db.execute('SELECT event_type,count(*) FROM core.mqtt_event GROUP BY event_type').fetchall())
        a1_count = db.execute('SELECT count(*) FROM core.charger_record').fetchone()[0]
    checks = {
        'source_ids_unique': len(source) == len(expected),
        'all_ids_and_payloads_equal': actual == expected,
        'all_event_ids_join_a1': count == len(rows),
        'all_ev_ids_match_wire_station_ids': all(str(sid) == json.loads(payload)['station_id'] for _, sid, payload in rows),
        'arrival_order_matches_source': [r[0] for r in rows] == [e['event_id'] for e in source],
        'event_type_counts_match': counts == dict(Counter(e['type'] for e in source)),
        'no_rejected_source_events': rejects == 0,
    }
    return dict(checks=checks, passed=all(checks.values()), events=len(rows), counts=counts,
                a1_chargers=a1_count, chargers_with_received_price=current_prices,
                active_sessions_at_window_end=active_sessions, rejected=rejects,
                source_sha256=hashlib.sha256(events_path.read_bytes()).hexdigest(),
                database_sha256=hashlib.sha256(db_path.read_bytes()).hexdigest())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=Path('database/assignment2.duckdb'))
    parser.add_argument('--events', type=Path, default=Path('data/window.jsonl'))
    parser.add_argument('--output', type=Path, default=Path('verification/task2/full_verification.json'))
    args = parser.parse_args()
    result = verify(args.db, args.events)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    sys.exit(0 if result['passed'] else 1)
