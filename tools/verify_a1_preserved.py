"""Compare every original A1 table with the extended database, read-only."""
import argparse
import hashlib
import json
from pathlib import Path

import duckdb


def quote(value):
    return '"' + value.replace('"', '""') + '"'


A2_TABLES = {'mqtt_event', 'mqtt_rejection', 'meter_reading', 'current_price', 'charging_session'}


def compare_tables(db, source, exclude=()):
    """Compare rows, including duplicates, against a closed reference database."""
    path = str(Path(source).resolve()).replace("'", "''")
    db.execute(f"ATTACH '{path}' AS original (READ_ONLY)")
    try:
        tables = db.execute("SELECT table_schema,table_name FROM information_schema.tables WHERE table_catalog='original' AND table_type='BASE TABLE' ORDER BY 1,2").fetchall()
        results = []
        for schema, table in tables:
            if schema == 'core' and table in exclude:
                continue
            name = f'{quote(schema)}.{quote(table)}'
            differences = db.execute(f'SELECT count(*) FROM ((SELECT * FROM original.{name} EXCEPT ALL SELECT * FROM {name}) UNION ALL (SELECT * FROM {name} EXCEPT ALL SELECT * FROM original.{name}))').fetchone()[0]
            results.append(dict(table=f'{schema}.{table}', differences=differences,
                                rows=db.execute(f'SELECT count(*) FROM {name}').fetchone()[0]))
        return results
    finally:
        db.execute('DETACH original')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--a1-db', type=Path, required=True)
    parser.add_argument('--db', type=Path, default=Path('database/assignment2.duckdb'))
    args = parser.parse_args()
    before = hashlib.sha256(args.a1_db.read_bytes()).hexdigest()
    with duckdb.connect(str(args.db), read_only=True) as db:
        results = compare_tables(db, args.a1_db)
    after = hashlib.sha256(args.a1_db.read_bytes()).hexdigest()
    result = dict(passed=before == after and bool(results) and all(r['differences'] == 0 for r in results),
                  original_file_unchanged=before == after, original_sha256=after,
                  original_tables=results)
    output = Path('verification/task2/a1_preservation.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
