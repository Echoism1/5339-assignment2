"""Inventory every tracked handoff file; independently reconstruct its replay."""
import ast
import csv
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from build_events import build


def main():
    revision = '2d99c516d65503e0ebe0118e33f6c8bc20ba7b39'
    paths = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', revision], cwd=ROOT, text=True).splitlines()
    manifest = []
    for name in paths:
        path = ROOT / name
        content = path.read_bytes()
        item = dict(path=name, bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
        baseline = subprocess.check_output(['git', 'show', f'{revision}:{name}'], cwd=ROOT)
        item['baseline_sha256'] = hashlib.sha256(baseline).hexdigest()
        item['changed_since_handoff_ignoring_newlines'] = content.replace(b'\r\n', b'\n') != baseline.replace(b'\r\n', b'\n')
        text = content.decode('utf-8-sig')
        if path.suffix == '.csv':
            with path.open(encoding='utf-8-sig', newline='') as stream:
                reader = csv.DictReader(stream)
                count = 0
                for row in reader:
                    if None in row:
                        raise ValueError(f'CSV malformed: {name}')
                    count += 1
            item['rows'] = count
            item['columns'] = reader.fieldnames
        elif path.suffix == '.json':
            json.loads(text)
        elif path.suffix == '.jsonl':
            item['events'] = sum(1 for line in text.splitlines() if isinstance(json.loads(line), dict))
        elif path.suffix == '.py':
            ast.parse(text)
        manifest.append(item)
    actual = [json.loads(line) for line in (ROOT / 'data/window.jsonl').read_text(encoding='utf-8').splitlines()]
    rebuilt, summary = build(ROOT / 'data', 3)
    assert rebuilt == actual
    data_checks = json.loads((ROOT / 'verification/data_checks.json').read_text())
    hashes = {item['path']: item['sha256'] for item in manifest}
    newline_only = []
    for path, digest in data_checks['sha256'].items():
        if hashes[path] != digest:
            normalised = (ROOT / path).read_bytes().replace(b'\r\n', b'\n')
            assert hashlib.sha256(normalised).hexdigest() == digest, path
            newline_only.append(path)
    result = dict(base_commit=revision, files_reviewed=len(manifest), manifest=manifest,
                  replay_rebuilt_equal=True, inherited_checksums_match_after_newline_normalisation=True,
                  windows_checkout_newline_only_differences=newline_only,
                  event_counts=dict(Counter(e['type'] for e in actual)), summary=summary,
                  findings=[
                      'Member 1 covers assignment tasks 1 and 2; group member 2 owns subscription.',
                      'Main contains an A1 CSV snapshot but no A1 DuckDB database.',
                      'Three-hour window has no price changes after initial prices; occupancy and meter curves must demonstrate dynamics.',
                      'Nine sessions remain active; do not synthesise end events.',
                      'Original verification scripts were not included; review_member1.py and Task 2 tests add reproducible checks.',
                  ])
    output = ROOT / 'verification/task2/member1_review.json'
    output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(f'{len(manifest)} files inventoried; replay reconstructed exactly; all recorded source hashes match')


if __name__ == '__main__':
    main()
