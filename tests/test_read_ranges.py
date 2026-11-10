"""Read the needed code, edit it exactly, and keep the rest of the file intact."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent_loop import AgentConfig, AgentLoop
from src.changes import ChangeStore, content_revision
from src.executor import Executor
from src.parser import Command
from src.provider import ProviderConfig
from src.security import SecurityPolicy
from test_output_recall import SSE


class ReadRangeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='thinkflow-read-ranges-')
        self.root = Path(self.temp.name).resolve()
        self.executor = Executor(cwd=str(self.root))
        self.counter = 0

    def tearDown(self):
        self.executor.close()
        self.temp.cleanup()
        self.assertFalse(self.root.exists())

    def seed(self, content, path='source.txt'):
        target = self.root / path
        target.write_bytes(content.encode('utf-8'))
        return target

    async def command(self, tool, path='source.txt', **kwargs):
        self.counter += 1
        return await self.executor.execute(Command(str(self.counter), tool, path=path, **kwargs))

    async def edit(self, old_text, new_text, **kwargs):
        result = await self.command('edit', old_text=old_text, new_text=new_text, **kwargs)
        self.assertTrue(result.success, result.error)
        return result

    async def test_disjoint_reads_support_sequential_exact_edits_without_full_write_grant(self):
        original = 'untouched header\r\nfirst 中文 = 1\r\nunseen gap\r\nsecond = 2\r\nuntouched tail'
        path = self.seed(original)
        for line in (2, 4):
            read = await self.executor.read('source.txt', offset=line, limit=1)
            self.assertTrue(read.success, read.error)
            self.assertFalse(read.truncated)
            self.assertEqual(read.revision, '')
            self.assertIn(f'Lines {line}-{line} of 5', read.read_note)
        await self.edit('first 中文 = 1', 'first 中文 = 100\r\nextra = 9')
        await self.edit('second = 2', 'second = 3')
        await self.edit('extra = 9\r\n', '')
        expected = original.replace('first 中文 = 1', 'first 中文 = 100').replace('second = 2', 'second = 3')
        self.assertEqual(path.read_bytes(), expected.encode())
        self.seed('copy source', 'copy.txt')
        for tool, kwargs in (('write', {'content': 'replacement'}), ('append', {'content': 'tail'}),
                             ('touch', {}), ('copy', {'path': 'copy.txt', 'dest': 'source.txt'})):
            with self.subTest(tool=tool):
                result = await self.command(tool, **kwargs)
                self.assertIn('read_required', result.error)
        self.assertEqual(path.read_bytes(), expected.encode())

    async def test_adjacent_ranges_merge_but_gaps_and_duplicate_text_remain_uneditable(self):
        path = self.seed('first\nsecond\nthird\nlast\n')
        await self.executor.read('source.txt', offset=1, limit=1)
        await self.executor.read('source.txt', offset=3, limit=1)
        refused = await self.command('edit', old_text='first\nsecond\nthird', new_text='combined')
        self.assertIn('read_required', refused.error)
        await self.executor.read('source.txt', offset=2, limit=1)
        await self.edit('first\nsecond\nthird', 'combined')
        self.assertEqual(path.read_text(), 'combined\nlast\n')
        path.write_bytes(b'duplicate\nspacer\nduplicate\n')
        await self.executor.read('source.txt', offset=1, limit=1)
        refused = await self.command('edit', old_text='duplicate', new_text='unique')
        self.assertIn('唯一', refused.error)
        self.assertEqual(path.read_bytes(), b'duplicate\nspacer\nduplicate\n')
        path.write_bytes(b'ababab\nUNSEEN')
        await self.executor.read('source.txt', offset=1, limit=1)
        refused = await self.command('edit', old_text='abab', new_text='EDIT')
        self.assertIn('唯一', refused.error)
        self.assertEqual(path.read_bytes(), b'ababab\nUNSEEN')

    async def test_changed_version_replaces_observations_even_when_size_and_mtime_match(self):
        path = self.seed('head\nfirst\nlast\n')
        await self.executor.read('source.txt', offset=1, limit=2)
        info = path.stat()
        path.write_bytes(b'head\nother\nlast\n')
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        refused = await self.command('edit', old_text='head', new_text='HEAD')
        self.assertIn('revision_conflict', refused.error)
        await self.executor.read('source.txt', offset=3, limit=1)
        refused = await self.command('edit', old_text='head', new_text='HEAD')
        self.assertIn('read_required', refused.error)
        await self.edit('last', 'LAST')
        self.assertEqual(path.read_bytes(), b'head\nother\nLAST\n')

    async def test_partial_read_keeps_matching_full_baseline_and_new_full_read_restores_it(self):
        path = self.seed('first\nsecond\n')
        full = await self.executor.read('source.txt')
        self.assertEqual(full.revision, content_revision(path.read_bytes()))
        await self.executor.read('source.txt', offset=2, limit=1)
        self.assertTrue((await self.command('append', content='third\n')).success)
        path.write_bytes(b'external\nsecond\n')
        await self.executor.read('source.txt', offset=2, limit=1)
        self.assertIn('read_required', (await self.command('write', content='replacement')).error)
        await self.executor.read('source.txt', offset=1, limit=2)
        self.assertTrue((await self.command('write', content='replacement')).success)

    async def test_clipped_and_redacted_views_only_authorize_actual_unredacted_characters(self):
        path = self.seed('header\n中文abcd\nlast')
        self.executor.max_read_chars = 3
        result = await self.executor.read('source.txt', offset=2, limit=1)
        self.assertEqual(result.content, '中文a')
        self.assertTrue(result.truncated)
        self.assertIn('Line 2 clipped', result.read_note)
        self.assertNotIn('read offset=2', result.read_note)
        refused = await self.command('edit', old_text='中文abcd', new_text='full')
        self.assertIn('read_required', refused.error)
        await self.edit('中文a', '看过')
        self.assertEqual(path.read_bytes(), 'header\n看过bcd\nlast'.encode())
        self.executor.max_read_chars = 200000
        path.write_bytes(b'header\npassword=fixture-secret\nlast')
        result = await self.executor.read('source.txt', offset=2, limit=1)
        self.assertIn('[REDACTED]', result.content)
        refused = await self.command('edit', old_text='password=fixture-secret', new_text='changed')
        self.assertIn('read_required', refused.error)

    async def test_range_bounds_empty_file_and_large_file_tail(self):
        self.seed('')
        result = await self.executor.read('source.txt', offset=1, limit=1)
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.content, '')
        for arguments in ({'offset': 0}, {'offset': True}, {'limit': 0}, {'limit': '1'}, {'offset': 2}):
            with self.subTest(arguments=arguments):
                self.assertFalse((await self.executor.read('source.txt', **arguments)).success)
        self.seed('x' * (8 * 1024 * 1024 + 1) + '\nTARGET 中文')
        result = await self.executor.read('source.txt', offset=2, limit=1)
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.content, 'TARGET 中文')
        self.assertEqual(result.read_note, '[Lines 2-2 of 2]')

    async def test_unconfirmed_change_and_revert_require_a_fresh_range(self):
        store = ChangeStore(self.root / 'private', self.root)
        self.executor.set_change_store(store)
        path = self.seed('head\nfirst\ntail')
        await self.executor.read('source.txt', offset=2, limit=1)
        with patch.object(store, 'complete', side_effect=OSError('receipt fault')):
            result = await self.command('edit', old_text='first', new_text='second')
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(path.read_bytes(), b'head\nsecond\ntail')
        self.assertIn('revision_conflict', (await self.command('edit', old_text='second', new_text='third')).error)
        await self.executor.read('source.txt', offset=2, limit=1)
        changed = await self.edit('second', 'third')
        store.revert(changed.change_id, content_revision(path.read_bytes()))
        refused = await self.command('edit', old_text='second', new_text='fourth')
        self.assertIn('revision_conflict', refused.error)

    async def test_text_and_native_paths_batch_reads_then_edit_the_visible_ranges(self):
        originals = {name: ('unchanged\n' * 500 + f'{name} = 1\n' + 'tail\n' * 500) for name in ('alpha', 'beta')}
        for name, value in originals.items():
            self.seed(value, name + '.txt')
        agent = AgentLoop(AgentConfig(cwd=str(self.root),
            provider=ProviderConfig(base_url='https://example.test', model='fixture', api_key='', enable_native_tools=False),
            security=SecurityPolicy(allowed_roots=[str(self.root)], bash_policy='unrestricted', approval_mode='approve_all')),
            event_sink=lambda event: None)
        requests = []
        def receive(request):
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                text = ('<tf-read id="1" path="alpha.txt" offset="501" limit="1" />'
                        '<tf-read id="2" path="beta.txt" offset="501" limit="1" />')
            elif len(requests) == 2:
                feedback = str(body['messages'][-1]['content'])
                self.assertIn('alpha = 1', feedback)
                self.assertIn('beta = 1', feedback)
                self.assertNotIn('unchanged', feedback)
                self.assertIn('Lines 501-501 of 1001', feedback)
                text = ('<tf-edit id="3" path="alpha.txt"><old>alpha = 1</old><new>alpha = 2</new></tf-edit>'
                        '<tf-edit id="4" path="beta.txt"><old>beta = 1</old><new>beta = 2</new></tf-edit>Done.')
            else:
                self.fail('unnecessary extra model request')
            return httpx.Response(200, stream=SSE(text))
        try:
            await agent.client.aclose()
            agent.client = httpx.AsyncClient(base_url=agent.config.provider.base_url, transport=httpx.MockTransport(receive))
            await agent.run('Update both target lines.')
            self.assertEqual(len(requests), 2)
            self.assertEqual([record.tool for record in agent.context.records], ['read', 'read', 'edit', 'edit'])
            self.assertTrue(all(record.status == 'success' for record in agent.context.records))
            for name, original in originals.items():
                self.assertEqual((self.root / (name + '.txt')).read_bytes(), original.replace(f'{name} = 1', f'{name} = 2').encode())
            agent.config.provider.enable_native_tools = True
            for provider in ('openai', 'anthropic'):
                agent.config.provider.format = provider
                name = provider + '.txt'
                self.seed('before\nNATIVE = 1\nafter', name)
                await agent._handle_traditional_tools([{'id': 'read-' + provider, 'name': 'read',
                    'input': json.dumps({'path': name, 'offset': 2, 'limit': 1})}])
                content = agent.messages[-1]['content']
                text = content if provider == 'openai' else content[0]['content']
                self.assertIn('NATIVE = 1\n', text)
                self.assertIn('[Lines 2-2 of 3]', text)
                await agent._handle_traditional_tools([{'id': 'edit-' + provider, 'name': 'edit',
                    'input': json.dumps({'path': name, 'old_text': 'NATIVE = 1', 'new_text': 'NATIVE = 2'})}])
                self.assertEqual((self.root / name).read_bytes(), b'before\nNATIVE = 2\nafter')
            agent.reset_session()
            refused = await agent.executor.execute(Command('1', 'edit', path='openai.txt', old_text='NATIVE = 2', new_text='NATIVE = 3'))
            self.assertIn('read_required', refused.error)
        finally:
            await agent.close()


if __name__ == '__main__':
    unittest.main()
