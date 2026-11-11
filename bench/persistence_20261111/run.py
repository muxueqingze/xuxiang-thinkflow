"""Local storage probe: identical history, 40 new receipts, then session listing."""
import argparse
import copy
import inspect
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.session import SessionStore


def run():
    root = Path(__file__).resolve().parents[2]
    work = root / 'temp' / 'persistence-20261111'
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as temporary:
        directory = Path(temporary)
        messages = [{'role': 'user' if n % 2 == 0 else 'assistant',
                     'content': f'History {n}\n' + ('source and observed result\n' * 400)}
                    for n in range(120)]
        snapshot = {'version': 1, 'session_id': 'probe', 'messages': messages,
                    'context': {'records': [], 'next_stamp': 1},
                    'turn_count': 60, 'executed_ids': [], 'command_fingerprints': {},
                    'desktop': {'id': 'probe', 'cwd': temporary, 'title': 'Persistence probe',
                                'updated_at': 1, 'transcript': copy.deepcopy(messages)}}
        stores = [SessionStore(str(directory / f'session-{n}.json'), cwd=temporary) for n in range(12)]
        for n, store in enumerate(stores):
            snapshot['session_id'] = str(n)
            store.save(snapshot, history=False)
        store = stores[0]
        snapshot['session_id'] = '0'
        store.save(snapshot, history=False)
        initial_bytes = store.path.stat().st_size
        counters = {'written_bytes': 0, 'atomic_writes': 0, 'delta_appends': 0}
        write_atomic = SessionStore._write_json_atomic

        def measured_write(path, data):
            write_atomic(path, data)
            counters['atomic_writes'] += 1
            counters['written_bytes'] += path.stat().st_size

        incremental = 'incremental' in inspect.signature(store.save).parameters
        original_append = getattr(SessionStore, '_append_changes', None)

        def measured_append(instance, changes):
            before = instance.changes_path.stat().st_size
            original_append(instance, changes)
            counters['delta_appends'] += 1
            counters['written_bytes'] += instance.changes_path.stat().st_size - before

        durations = []
        with patch.object(SessionStore, '_write_json_atomic', side_effect=measured_write):
            append_patch = patch.object(SessionStore, '_append_changes', measured_append) if incremental else None
            if append_patch:
                append_patch.start()
            try:
                for n in range(40):
                    snapshot['context']['records'].append({'id': str(n), 'tool': 'read',
                        'status': 'success', 'output_preview': f'new result {n}'})
                    snapshot['context']['next_stamp'] += 1
                    snapshot['executed_ids'].append(str(n))
                    snapshot['desktop']['updated_at'] += 1
                    started = time.perf_counter()
                    store.save(snapshot, history=False, **({'incremental': True} if incremental else {}))
                    durations.append((time.perf_counter() - started) * 1000)
            finally:
                if append_patch:
                    append_patch.stop()
        restored = SessionStore(str(store.path), cwd=temporary).load()
        assert restored['context']['records'] == snapshot['context']['records']
        assert restored['messages'] == messages
        assert restored['desktop']['transcript'] == messages
        read_bytes = 0
        snapshot_reads = 0
        original_open = Path.open

        def measured_open(path, mode='r', *args, **kwargs):
            nonlocal read_bytes, snapshot_reads
            if 'r' in mode and path.is_file():
                read_bytes += path.stat().st_size
                snapshot_reads += path.name.endswith('.json')
            return original_open(path, mode, *args, **kwargs)

        started = time.perf_counter()
        with patch.object(Path, 'open', measured_open):
            items = [store.inspect(path) for path in store.discover()]
        result = {'initial_snapshot_bytes': initial_bytes, 'receipt_updates': 40, **counters,
                  'save_total_ms': round(sum(durations), 3),
                  'save_median_ms': round(statistics.median(durations), 3),
                  'list_ms': round((time.perf_counter() - started) * 1000, 3),
                  'list_read_bytes': read_bytes, 'list_full_snapshot_reads': snapshot_reads,
                  'listed_sessions': len(items), 'full_history_and_receipts_equal': True}
    work.rmdir()
    return result


if __name__ == '__main__':
    args = argparse.ArgumentParser()
    args.add_argument('--output', type=Path)
    options = args.parse_args()
    result = run()
    text = json.dumps(result, indent=2) + '\n'
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(text, encoding='utf-8')
    print(text)
