"""Compare every original A1 table with the extended database, read-only."""
import argparse
import hashlib
import json
from pathlib import Path

import duckdb


def quote(value):
    return '"' + value.replace('"', '""') + '"'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--a1-db', type=Path, required=True)
    parser.add_argument('--db', type=Path, default=Path('database/assignment2.duckdb'))
    args = parser.parse_args()
    before = hashlib.sha256(args.a1_db.read_bytes()).hexdigest()
    results = []
    with duckdb.connect(str(args.db), read_only=True) as db:
        source = str(args.a1_db.resolve()).replace("'", "''")
        db.execute(f"ATTACH '{source}' AS original (READ_ONLY)")
        tables = db.execute("SELECT table_schema,table_name FROM information_schema.tables WHERE table_catalog='original' AND table_type='BASE TABLE' ORDER BY 1,2").fetchall()
        for schema, table in tables:
            name = f'{quote(schema)}.{quote(table)}'
            # DuckDB preserves binary GEOMETRY values in the original table copy;
            # EXCEPT ALL compares duplicates as well as values.
            differences = db.execute(f'SELECT count(*) FROM ((SELECT * FROM original.{name} EXCEPT ALL SELECT * FROM {name}) UNION ALL (SELECT * FROM {name} EXCEPT ALL SELECT * FROM original.{name}))').fetchone()[0]
            results.append(dict(table=f'{schema}.{table}', differences=differences,
                                rows=db.execute(f'SELECT count(*) FROM {name}').fetchone()[0]))
    after = hashlib.sha256(args.a1_db.read_bytes()).hexdigest()
    result = dict(passed=before == after and all(r['differences'] == 0 for r in results),
                  original_file_unchanged=before == after, original_sha256=after,
                  original_tables=results)
    output = Path('verification/task2/a1_preservation.json')
    output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
