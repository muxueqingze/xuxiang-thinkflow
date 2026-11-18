"""Persistence contracts across hot tool receipts, checkpoints and session lists."""
import copy
import json
import os
from io import StringIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import renderer
from src.cli import build_resume_selection_items, render_session_choices, render_session_transcript
from src.session import SessionStore
from src.desktop_service import DesktopService


class IncrementalSessionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='thinkflow-incremental-'))).resolve()
        self.store = SessionStore(str(self.root / 'session.json'), cwd=str(self.root))
        self.snapshot = {'version': 1, 'session_id': 'first',
            'messages': [{'role': 'user', 'content': 'Keep the original request'},
                         {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': '', 'signature': 'opaque'}]}],
            'context': {'records': [], 'next_stamp': 1}, 'executed_ids': [],
            'command_fingerprints': {}, 'turn_count': 1,
            'desktop': {'title': 'First', 'cwd': str(self.root), 'updated_at': 1,
                        'transcript': [{'role': 'assistant', 'content': 'Working'}]}}

    def test_receipts_and_transcript_restore_without_rewriting_the_base(self):
        self.store.save(self.snapshot, history=False)
        base = self.store.path.read_bytes()
        self.snapshot['context']['records'].append({'id': '1', 'status': 'success', 'output_preview': 'whole result'})
        self.snapshot['executed_ids'].append('1')
        self.snapshot['desktop']['transcript'][0]['content'] += ' and done'
        self.store.save(self.snapshot, history=False, incremental=True)
        self.assertEqual(self.store.path.read_bytes(), base)
        self.assertEqual(SessionStore(str(self.store.path)).load(), self.snapshot)
        self.snapshot['context']['records'][0]['status'] = 'changed only in memory'
        self.assertEqual(self.store.load()['context']['records'][0]['status'], 'success')
        self.snapshot['messages'] = self.snapshot['messages'][-1:]  # A compacted view replaces a list tail.
        del self.snapshot['command_fingerprints']
        self.store.save(self.snapshot, history=False, incremental=True)
        self.assertEqual(self.store.load(), self.snapshot)
        self.store.save(self.snapshot, history=False)
        self.assertEqual(self.store.changes_path.read_bytes(), b'')
        self.assertEqual(self.store.load(), self.snapshot)

    def test_killed_append_and_checkpoint_replacement_keep_committed_state(self):
        self.store.save(self.snapshot, history=False)
        self.snapshot['turn_count'] = 2
        self.store.save(self.snapshot, history=False, incremental=True)
        with self.store.changes_path.open('ab') as stream:
            stream.write(b'{"checkpoint":"unfinished')
        restored = SessionStore(str(self.store.path))
        self.assertEqual(restored.load(), self.snapshot)
        self.snapshot['turn_count'] = 3
        restored.save(self.snapshot, history=False, incremental=True)
        self.assertEqual(restored.load(), self.snapshot)
        old_changes = restored.changes_path.read_bytes()
        self.snapshot['turn_count'] = 4
        restored.save(self.snapshot, history=False)
        restored.changes_path.write_bytes(old_changes)  # Crash after base replace, before old log truncation.
        self.assertEqual(SessionStore(str(restored.path)).load(), self.snapshot)

    def test_legacy_snapshot_and_path_switch_use_the_right_base(self):
        self.store.path.write_text(json.dumps(self.snapshot), encoding='utf-8')
        self.assertEqual(self.store.load(), self.snapshot)
        self.snapshot['turn_count'] = 2
        self.store.save(self.snapshot, history=False, incremental=True)
        self.assertEqual(self.store.load(), self.snapshot)
        first_bytes = self.store.path.read_bytes()
        second = SessionStore(str(self.root / 'second.json'))
        other = copy.deepcopy(self.snapshot)
        other['session_id'] = 'second'
        other['messages'] = [{'role': 'user', 'content': 'Second session'}]
        second.save(other, history=False)
        self.store.path = second.path
        other['turn_count'] = 3
        self.store.save(other, history=False, incremental=True)
        self.assertEqual(second.load(), other)
        self.assertEqual((self.root / 'session.json').read_bytes(), first_bytes)

    def test_lists_use_indexes_and_refresh_after_an_external_snapshot_change(self):
        self.store.save(self.snapshot)
        self.snapshot['messages'].append({'role': 'user', 'content': 'New phase'})
        self.store.save(self.snapshot)
        self.assertEqual(len(self.store.discover()), 2)  # Current snapshot and archive are deduplicated.
        original = Path.open

        def only_index(path, mode='r', *args, **kwargs):
            if 'r' in mode and path.suffix == '.json':
                raise AssertionError(f'Listing read full history: {path.name}')
            return original(path, mode, *args, **kwargs)

        with patch.object(Path, 'open', only_index):
            items = [self.store.inspect(path) for path in self.store.discover()]
        self.assertEqual(len(items), 2)
        self.assertTrue(all('error' not in item for item in items))
        external = json.loads(self.store.path.read_text(encoding='utf-8'))
        external['desktop']['title'] = 'Edited externally'
        self.store.path.write_text(json.dumps(external), encoding='utf-8')
        self.assertEqual(self.store.inspect(self.store.path)['desktop']['title'], 'Edited externally')

    def test_resume_uses_live_file_even_when_its_history_copy_is_newer(self):
        self.store.save(self.snapshot)
        archive, = (self.root / 'history').glob('*.json')
        archive_bytes = archive.read_bytes()
        newer = self.store.path.stat().st_mtime + 10
        os.utime(archive, (newer, newer))
        selected, = self.store.discover()
        resumed = SessionStore(str(selected), cwd=str(self.root))
        data = resumed.load()
        data['messages'].append({'role': 'user', 'content': 'Continue after restart'})
        resumed.save(data)

        self.assertEqual(self.store.load()['messages'], data['messages'])
        self.assertEqual(archive.read_bytes(), archive_bytes)
        self.assertCountEqual(self.store.discover(), [self.store.path, archive])

    def test_desktop_lists_metadata_without_loading_transcripts(self):
        service = DesktopService()
        service.cwd = str(self.root)
        service.data_dir = self.root / 'desktop'
        identifier = 'a' * 32
        store = SessionStore(str(service._session_path(identifier)))
        self.snapshot['desktop'].update(id=identifier, pinned=True)
        store.save(self.snapshot, history=False)
        self.snapshot['desktop']['title'] = 'Latest title'
        self.snapshot['desktop']['updated_at'] = 2
        store.save(self.snapshot, history=False, incremental=True)
        original = Path.open

        def no_history(path, mode='r', *args, **kwargs):
            if path == store.path and 'r' in mode:
                raise AssertionError('Desktop list opened the full transcript')
            return original(path, mode, *args, **kwargs)

        with patch.object(Path, 'open', no_history):
            items = service.sessions()
        self.assertEqual(items, [{'id': identifier, 'title': 'Latest title', 'pinned': True,
                                  'archived': False, 'updated_at': 2}])

    def test_transcript_renders_visible_blocks_as_literal_multiline_text(self):
        output = StringIO()
        blocks = [{'type': 'thinking', 'signature': 'opaque-signature'},
                  {'type': 'text', 'text': '[bold]中文[/bold]\r\n  line two'},
                  {'type': 'tool_result', 'content': [{'type': 'text', 'text': 'tool result'}]},
                  {'type': 'image', 'source': {'data': 'encoded-image'}},
                  {'type': 'tool_use', 'name': 'native_read'}]
        messages = [{'role': 'assistant', 'content': blocks},
                    {'role': 'assistant', 'content': 'Before calling',
                     'tool_calls': [{'function': {'name': 'get_file'}}]},
                    {'role': 'tool', 'content': '\x1b[31mvalue\x1b[0m\x07'
                     '\x1b]8;;https://invalid.example\x07文\x9d8;;\x9c'
                     '\x1bPprivate-control\x1b\\字'}]
        with patch.object(renderer, 'console', Console(file=output, width=100)):
            render_session_transcript(messages)
        transcript = output.getvalue()
        self.assertIn('[bold]中文[/bold]\n  line two\ntool result\ntool_call: native_read', transcript)
        self.assertIn('Before calling\ntool_call: get_file', transcript)
        self.assertIn('value文字', transcript)
        for hidden in ('opaque-signature', 'encoded-image', 'private-control', 'invalid.example',
                       "'type':", '\x1b', '\x07', '\x9d', '\x9c'):
            self.assertNotIn(hidden, transcript)

    def test_resume_previews_show_literal_text_without_terminal_controls(self):
        self.snapshot['messages'][0]['content'] = [
            {'type': 'image', 'source': {'data': 'hidden-image'}},
            {'type': 'text', 'text': '\x1b[35m[bold]中文[/bold]\x1b[0m\x07'},
        ]
        self.store.save(self.snapshot, history=False)
        index_path = self.store.path.with_suffix('.index')
        index = json.loads(index_path.read_text(encoding='utf-8'))
        del index['version']
        index['info']['start'] = str(self.snapshot['messages'][0]['content'])[:90]
        index_path.write_text(json.dumps(index), encoding='utf-8')
        output = StringIO()
        with patch.object(renderer, 'console', Console(file=output, width=100, theme=renderer.THEME)):
            items = build_resume_selection_items(self.store, [self.store.path])
            render_session_choices(self.store, [self.store.path])
        for text in (items[0].label, output.getvalue()):
            self.assertIn('[bold]中文[/bold]', text)
            self.assertNotIn('hidden-image', text)
            self.assertNotIn("'type'", text)
            self.assertNotIn('\x1b', text)
            self.assertNotIn('\x07', text)


if __name__ == '__main__':
    unittest.main()
